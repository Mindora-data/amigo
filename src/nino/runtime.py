from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import re
from typing import Any
from uuid import uuid4

from .consolidation import Consolidator, InMemoryColdStore
from .consolidation import MemoryFact
from .contracts import (
    AgentState,
    ConsolidationRequest,
    ConsolidationResponse,
    PolicyRequest,
    PolicyResponse,
    ProactivityResponse,
    ProactivitySettings,
    RetrieveRequest,
    RetrieveResponse,
)
from .llm import LLMClient, build_configured_llm, build_nino_prompt
from .learning import distill_to_global, pattern_context_for_candidate, starting_prior
from .learning_journal import (
    THEMES,
    InMemoryLearningJournalStore,
    LearningJournalEntry,
    classify_learning_theme,
    extract_detected_learning_journal_entries,
    extract_learning_journal_entries,
    learning_entry_quality,
    learning_terms,
    make_detected_learning_entry,
    make_manual_learning_entry,
)
from .memory import Episode, InMemoryEpisodeStore, MemoryRetriever
from .memory_graph import InMemoryGraphStore, graph_candidates, ingest_episodes_graph
from .proactivity import (
    InMemoryProactiveCandidateStore,
    ProactivityEngine,
    configure_proactivity_state,
    default_proactivity_state,
    extract_followups,
    mark_temporal_event_reminded,
    record_proactive_send,
    record_proactive_source,
)
from .rss_culture import RssItem, RssSource, fetch_rss_xml, normalize_theme, parse_rss_items, source_id_from_name


def _nino_context_summary(
    *,
    state: AgentState,
    source: str,
    llm_provider: str | None,
    llm_error: str | None,
    retrieved: RetrieveResponse,
    llm_retrieved: RetrieveResponse,
) -> dict[str, Any]:
    memory_candidates = [
        {
            "fact_id": candidate.fact_id,
            "source_episode_id": candidate.source_episode_id,
            "memory_type": "cold" if candidate.fact_id.startswith("cold::") else "hot",
            "statement": candidate.statement,
            "score": round(candidate.score, 4),
            "confidence": round(candidate.confidence, 4),
            "retrieval_source": candidate.retrieval_source,
        }
        for candidate in llm_retrieved.memory_candidates[:5]
    ]
    graph_count = len([candidate for candidate in llm_retrieved.memory_candidates if candidate.retrieval_source == "graph"])
    similarity_count = len([candidate for candidate in llm_retrieved.memory_candidates if candidate.retrieval_source != "graph"])
    return {
        "agent_id": state.agent_id,
        "current_time": state.updated_at.isoformat(),
        "response_source": source,
        "llm_provider": llm_provider,
        "llm_error": llm_error,
        "maturity": state.cognitive_time.get("maturity", 0.0),
        "age_ticks": state.cognitive_time.get("age_ticks", 0.0),
        "active_goals": list(state.active_goals),
        "retrieved_memory_count": len(retrieved.memory_candidates),
        "llm_context_memory_count": len(llm_retrieved.memory_candidates),
        "memory_candidates": memory_candidates,
        "graph_retrieved_count": graph_count,
        "similarity_retrieved_count": similarity_count,
        "temporal_query": llm_retrieved.temporal_query,
        "temporal_window": llm_retrieved.temporal_window,
        "temporal_miss": llm_retrieved.temporal_miss,
    }


def _should_auto_consolidate(percept_frame: dict[str, Any]) -> bool:
    text = str(percept_frame.get("text", "")).strip()
    if not text:
        return False
    confidence = _clamp01(float(percept_frame.get("confidence", 0.8)))
    return confidence >= 0.9


class InMemoryStateStore:
    def __init__(self) -> None:
        self._states: dict[str, AgentState] = {}

    def get(self, agent_id: str) -> AgentState | None:
        return self._states.get(agent_id)

    def put(self, state: AgentState) -> None:
        self._states[state.agent_id] = state

    def delete(self, agent_id: str) -> None:
        self._states.pop(agent_id, None)

    def list_agent_ids(self) -> list[str]:
        return sorted(self._states.keys())

class InMemoryGlobalModelStore:
    def __init__(self) -> None:
        self._model: dict[str, Any] = {
            "schema_version": 1,
            "conversation_count": 0,
            "intent_counts": {},
            "tag_counts": {},
            "concept_counts": {},
            "pattern_outcomes": {},
            "updated_at": None,
        }

    def get(self) -> dict[str, Any]:
        return {
            "schema_version": self._model.get("schema_version", 1),
            "conversation_count": int(self._model.get("conversation_count", 0)),
            "intent_counts": dict(self._model.get("intent_counts", {})),
            "tag_counts": dict(self._model.get("tag_counts", {})),
            "concept_counts": _safe_global_concept_counts(self._model.get("concept_counts", {})),
            "pattern_outcomes": dict(self._model.get("pattern_outcomes", {})),
            "updated_at": self._model.get("updated_at"),
        }

    def put(self, model: dict[str, Any]) -> None:
        self._model = self._sanitize_global_model(model)

    def _sanitize_global_model(self, model: dict[str, Any]) -> dict[str, Any]:
        allowed = {"schema_version", "conversation_count", "intent_counts", "tag_counts", "concept_counts", "pattern_outcomes", "updated_at"}
        return {key: model[key] for key in allowed if key in model}

    def bump_global_pattern(
        self,
        gesture: str,
        context: str,
        outcome: str,
        now: datetime | None = None,
    ) -> None:
        now = now or datetime.now(timezone.utc)
        model = self.get()
        outcomes = dict(model.get("pattern_outcomes", {}))
        key = f"{gesture}:{context}:{outcome}"
        row = dict(outcomes.get(key, {"gesture": gesture, "context": context, "outcome": outcome, "count": 0}))
        row["count"] = int(row.get("count", 0)) + 1
        row["updated_at"] = now.isoformat()
        outcomes[key] = row
        model["pattern_outcomes"] = outcomes
        model["updated_at"] = now.isoformat()
        self.put(model)

    def global_pattern_stats(self, gesture: str, context: str) -> dict[str, int]:
        outcomes = self.get().get("pattern_outcomes", {})
        stats = {"positive": 0, "ignored": 0, "stop": 0, "total": 0}
        if not isinstance(outcomes, dict):
            return stats
        for row in outcomes.values():
            if not isinstance(row, dict) or row.get("gesture") != gesture or row.get("context") != context:
                continue
            outcome = str(row.get("outcome"))
            count = int(row.get("count", 0))
            if outcome in stats:
                stats[outcome] += count
                stats["total"] += count
        return stats


class InMemoryRssCultureStore:
    def __init__(self) -> None:
        self._sources: dict[str, RssSource] = {}
        self._items: dict[str, RssItem] = {}

    def upsert_source(self, source: RssSource) -> None:
        self._sources[source.source_id] = source

    def list_sources(self, active_only: bool = False) -> list[RssSource]:
        sources = sorted(self._sources.values(), key=lambda item: item.updated_at, reverse=True)
        return [source for source in sources if source.active] if active_only else sources

    def get_source(self, source_id: str) -> RssSource | None:
        return self._sources.get(source_id)

    def upsert_item(self, item: RssItem) -> bool:
        for existing in self._items.values():
            if existing.source_id == item.source_id and existing.link and existing.link == item.link:
                return False
            if existing.source_id == item.source_id and existing.title.lower() == item.title.lower():
                return False
        self._items[item.item_id] = item
        return True

    def list_items(self, theme: str = "all", limit: int = 20) -> list[RssItem]:
        items = sorted(self._items.values(), key=lambda item: item.published_at or item.fetched_at, reverse=True)
        if theme and theme != "all":
            items = [item for item in items if item.theme == theme]
        return items[:limit]

    def delete_source(self, source_id: str) -> int:
        deleted = 1 if self._sources.pop(source_id, None) else 0
        for item_id, item in list(self._items.items()):
            if item.source_id == source_id:
                self._items.pop(item_id, None)
        return deleted


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))

def _normalize_text(value: str) -> str:
    return " ".join(re.findall(r"[\wáéíóúñ]+", value.lower()))

def _without_accents(value: str) -> str:
    return (
        value.lower()
        .replace("á", "a")
        .replace("é", "e")
        .replace("í", "i")
        .replace("ó", "o")
        .replace("ú", "u")
    )

def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed

def _now_from_percept(percept_frame: dict[str, Any]) -> datetime:
    for key in ("now", "timestamp"):
        if percept_frame.get(key):
            return _parse_datetime(percept_frame[key])
    return datetime.now(timezone.utc)

def _redact_text(value: str) -> str:
    value = re.sub(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", "[email]", value)
    value = re.sub(r"\b\d{3,}\b", "[number]", value)
    return value

def _safe_global_tokens(text: str) -> list[str]:
    blocked = {
        "soy", "llamo", "email", "correo", "telefono", "teléfono", "direccion", "dirección",
        "password", "contraseña", "pin", "dni", "pablo", "ana", "bob",
        "madrid", "barcelona", "dentista", "medico", "médico", "doctor", "salud",
        "hoy", "mañana", "manana", "ayer", "lunes", "martes", "miercoles", "miércoles",
        "jueves", "viernes", "sabado", "sábado", "domingo",
    }
    tokens = []
    for token in _tokens_for_model(_redact_text(text)):
        normalized = _without_accents(token)
        if normalized in blocked or normalized.startswith("["):
            continue
        if re.search(r"\d", normalized):
            continue
        tokens.append(normalized)
    return tokens[:12]

def _safe_global_concept_counts(raw: dict[str, Any] | None) -> dict[str, int]:
    safe: dict[str, int] = {}
    for key, count in dict(raw or {}).items():
        normalized = _without_accents(str(key))
        if _safe_global_tokens(normalized) != [normalized]:
            continue
        safe[normalized] = int(count)
    return safe

def _update_global_model(model: dict[str, Any], percept_frame: dict[str, Any], now: datetime) -> dict[str, Any]:
    updated = {
        "schema_version": 1,
        "conversation_count": int(model.get("conversation_count", 0)) + 1,
        "intent_counts": dict(model.get("intent_counts", {})),
        "tag_counts": dict(model.get("tag_counts", {})),
        "concept_counts": _safe_global_concept_counts(model.get("concept_counts", {})),
        "pattern_outcomes": dict(model.get("pattern_outcomes", {})),
        "updated_at": now.isoformat(),
    }
    intent = str(percept_frame.get("intent", "unknown"))
    updated["intent_counts"][intent] = int(updated["intent_counts"].get(intent, 0)) + 1
    text = str(percept_frame.get("text", ""))
    for tag in _semantic_tags(text):
        updated["tag_counts"][tag] = int(updated["tag_counts"].get(tag, 0)) + 1
    for token in _safe_global_tokens(f"{intent} {text}"):
        updated["concept_counts"][token] = int(updated["concept_counts"].get(token, 0)) + 1
    return updated

def _semantic_tags(text: str) -> list[str]:
    plain = _without_accents(text)
    tags = []
    if any(word in plain for word in ("piano", "musica", "guitarra")):
        tags.append("music")
    if any(word in plain for word in ("triste", "agobiado", "feliz", "contento", "estresado")):
        tags.append("emotion")
    if any(word in plain for word in ("examen", "trabajo", "proyecto", "estudio")):
        tags.append("work_learning")
    if any(word in plain for word in ("prefiero", "me gusta")):
        tags.append("preference")
    if "?" in text:
        tags.append("question")
    return sorted(set(tags))

WEEKDAY_OFFSETS = {
    "lunes": 0,
    "martes": 1,
    "miercoles": 2,
    "miércoles": 2,
    "jueves": 3,
    "viernes": 4,
    "sabado": 5,
    "sábado": 5,
    "domingo": 6,
}

TIME_RE = re.compile(r"\b(?:a\s+las|a\s+la|las|la)\s+(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\b", re.IGNORECASE)

ONBOARDING_FLOW = [
    ("name", "¿Cómo te llamas?"),
    ("location", "¿De dónde eres o dónde vives ahora?"),
    ("birth", "¿Cuándo naciste o qué edad tienes?"),
    ("likes", "¿Qué te gusta hacer? Hobbies, música, planes..."),
    ("important_memory", "¿Hay algo importante que quieres que recuerde de ti?"),
    ("expectation", "¿Qué esperas de mí como amigo?"),
]

ONBOARDING_FIELD_LABELS = {
    "name": "Nombre",
    "location": "Lugar",
    "birth": "Edad o nacimiento",
    "likes": "Gustos",
    "important_memory": "Importante",
    "expectation": "Qué espera de amigo",
}

SKIP_ONBOARDING_RE = re.compile(r"\b(paso|saltar|saltalo|sáltalo|no quiero|prefiero no|luego)\b", re.IGNORECASE)

def _time_from_text(text: str) -> tuple[int, int] | None:
    match = TIME_RE.search(text)
    if not match:
        return None
    hour = int(match.group("hour"))
    minute = int(match.group("minute") or 0)
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        return None
    return hour, minute

def _is_reminder_request(text: str) -> bool:
    plain = _without_accents(text)
    return bool(
        re.search(
            r"\b(recuerdame|recordarme|avisame|avisarme|ponme\s+(?:una\s+)?alarma|pon\s+(?:un\s+)?recordatorio|no\s+me\s+dejes\s+olvidar)\b",
            plain,
        )
    )

def _relative_due_at_from_text(text: str, now: datetime) -> datetime | None:
    plain = _without_accents(text)
    match = re.search(r"\ben\s+(\d{1,3})\s+(minuto|minutos|hora|horas)\b", plain)
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2)
    if amount <= 0:
        return None
    if unit.startswith("minuto"):
        return now + timedelta(minutes=amount)
    return now + timedelta(hours=amount)

def _reminder_text_from_request(text: str) -> str:
    cleaned = text.strip()
    match = re.search(r"\bque\s+(.+)$", cleaned, re.IGNORECASE)
    if match:
        return match.group(1).strip(" .")[:180]
    plain = re.sub(
        r"\b(recuerdame|recuérdame|avisame|avísame|ponme\s+(?:una\s+)?alarma|pon\s+(?:un\s+)?recordatorio|no\s+me\s+dejes\s+olvidar)\b",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    plain = re.sub(r"\ben\s+\d{1,3}\s+(minuto|minutos|hora|horas)\b", "", plain, flags=re.IGNORECASE)
    plain = TIME_RE.sub("", plain)
    plain = plain.strip(" ,.")
    return (plain or "esto")[:180]


def _direct_reminder_confirmation_text(due_at: datetime | None, event_text: str) -> str:
    time_text = due_at.strftime("%H:%M") if due_at is not None else "la hora marcada"
    if event_text.strip().lower() == "esto":
        return f"Vale, te aviso a las {time_text}."
    return f"Vale, te aviso a las {time_text} para que {event_text}."

def _next_weekday(now: datetime, weekday: int) -> datetime:
    days = (weekday - now.weekday()) % 7
    if days == 0:
        days = 7
    return now + timedelta(days=days)

def _due_at_from_text(text: str, now: datetime) -> datetime | None:
    plain = _without_accents(text)
    explicit_time = _time_from_text(text)
    default_hour, default_minute = explicit_time or (9, 0)
    base: datetime | None = None
    if "mañana" in plain or "manana" in plain:
        base = now + timedelta(days=1)
    elif "hoy" in plain:
        if explicit_time is None:
            base = now.replace(hour=max(now.hour, 9), minute=0, second=0, microsecond=0)
        else:
            base = now
    elif "luego" in plain or "esta tarde" in plain:
        if explicit_time is None:
            return now + timedelta(hours=2)
        base = now
    else:
        for day, weekday in WEEKDAY_OFFSETS.items():
            if day in plain:
                base = _next_weekday(now, weekday)
                break
    if base is None:
        return None
    due_at = base.replace(hour=default_hour, minute=default_minute, second=0, microsecond=0)
    if due_at <= now and explicit_time is not None:
        due_at += timedelta(days=1)
    return due_at

def _recurrence_from_text(text: str) -> dict[str, Any]:
    plain = _without_accents(text)
    if "todos los dias" in plain or "cada dia" in plain or "diario" in plain:
        return {"recurrence": "daily", "recurrence_interval_days": 1}
    if "cada semana" in plain or "semanal" in plain:
        return {"recurrence": "weekly", "recurrence_interval_days": 7}
    if "cada " in plain:
        for day in WEEKDAY_OFFSETS:
            if f"cada {day}" in plain:
                return {"recurrence": "weekly", "recurrence_interval_days": 7}
    return {"recurrence": None, "recurrence_interval_days": None}

def _reminder_confirmation_from_text(text: str) -> str | None:
    plain = _without_accents(text)
    if re.search(r"\b(no|mejor no|sin alarma|no hace falta)\b", plain):
        return "declined"
    if re.search(r"\b(si|vale|ok|okay|claro|perfecto|recuerdamelo|avisame)\b", plain):
        return "confirmed"
    return None

def _latest_offered_reminder_event(relation_state: dict[str, Any]) -> dict[str, Any] | None:
    events = [event for event in relation_state.get("temporal_events", []) if isinstance(event, dict)]
    for event in reversed(events):
        if event.get("reminder_status") == "offered" and event.get("status") == "pending":
            return event
    return None

def _onboarding_next_question(current_key: str) -> str:
    keys = [key for key, _ in ONBOARDING_FLOW]
    try:
        index = keys.index(current_key)
    except ValueError:
        return ONBOARDING_FLOW[0][1]
    next_index = index + 1
    if next_index >= len(ONBOARDING_FLOW):
        return "Gracias. Me ayuda a conocerte sin invadir. A partir de aquí vamos hablando normal."
    return ONBOARDING_FLOW[next_index][1]

def _onboarding_response_text(current_key: str, answer: str) -> str:
    skipped = bool(SKIP_ONBOARDING_RE.search(answer))
    prefix = "Vale, lo saltamos." if skipped else "Gracias, me lo apunto."
    return f"{prefix} {_onboarding_next_question(current_key)}"

PROFILE_CORRECTION_PATTERNS = {
    "name": re.compile(
        r"\b(?:corrige|actualiza|cambia)\s+(?:mi\s+)?nombre\s+(?:a|por|es)\s+(?P<value>.+)$",
        re.IGNORECASE,
    ),
    "location": re.compile(
        r"\b(?:corrige|actualiza|cambia)\s+(?:mi\s+)?(?:lugar|ubicacion|ubicación|ciudad|donde vivo)\s+(?:a|por|es)\s+(?P<value>.+)$",
        re.IGNORECASE,
    ),
    "birth": re.compile(
        r"\b(?:corrige|actualiza|cambia)\s+(?:mi\s+)?(?:edad|nacimiento|fecha de nacimiento)\s+(?:a|por|es)\s+(?P<value>.+)$",
        re.IGNORECASE,
    ),
    "likes": re.compile(
        r"\b(?:corrige|actualiza|cambia)\s+(?:mis\s+)?(?:gustos|hobbies|aficiones)\s+(?:a|por|son)\s+(?P<value>.+)$",
        re.IGNORECASE,
    ),
    "important_memory": re.compile(
        r"\b(?:corrige|actualiza|cambia)\s+(?:lo\s+)?importante\s+(?:a|por|es)\s+(?P<value>.+)$",
        re.IGNORECASE,
    ),
    "expectation": re.compile(
        r"\b(?:corrige|actualiza|cambia)\s+(?:lo\s+que\s+espero|mi\s+expectativa)\s+(?:a|por|es)\s+(?P<value>.+)$",
        re.IGNORECASE,
    ),
}

PROFILE_STORAGE_FIELDS = {
    "name": ("user_name", 80),
    "location": ("user_location", 160),
    "birth": ("user_birth_or_age", 160),
    "expectation": ("user_expectation", 240),
}

PROFILE_FORGET_KEYWORDS = {
    "name": ("nombre", "como me llamo"),
    "location": ("lugar", "ubicacion", "ubicación", "ciudad", "donde vivo", "de donde soy"),
    "birth": ("edad", "nacimiento", "fecha de nacimiento"),
    "likes": ("gustos", "hobbies", "aficiones"),
    "important_memory": ("importante", "algo importante"),
    "expectation": ("expectativa", "lo que espero", "que espero de ti"),
}

def _profile_correction_from_text(text: str) -> tuple[str, str] | None:
    for key, pattern in PROFILE_CORRECTION_PATTERNS.items():
        match = pattern.search(text)
        if match:
            value = match.group("value").strip(" .")
            if value:
                return key, value[:240]
    return None

def _profile_forget_from_text(text: str) -> str | None:
    plain = _without_accents(text)
    if not re.search(r"\b(borra|borralo|bórralo|elimina|olvida|quita)\b", plain):
        return None
    if "mi perfil" in plain or "perfil inicial" in plain or "todo mi perfil" in plain:
        return "all"
    for key, keywords in PROFILE_FORGET_KEYWORDS.items():
        if any(_without_accents(keyword) in plain for keyword in keywords):
            return key
    return None

def _forget_profile_field(relation_state: dict[str, Any], key: str) -> dict[str, Any]:
    relation = dict(relation_state)
    onboarding = dict(relation.get("onboarding", {}))
    answers = dict(onboarding.get("answers", {}))
    answers.pop(key, None)
    onboarding["answers"] = answers
    onboarding["completed"] = False
    relation["onboarding"] = onboarding
    storage = PROFILE_STORAGE_FIELDS.get(key)
    if storage:
        field, _limit = storage
        relation.pop(field, None)
        if key == "name":
            relation.pop("user_name_updated_at", None)
    if key == "likes":
        preferences = {
            pref: data
            for pref, data in dict(relation.get("preferences", {})).items()
            if not (isinstance(data, dict) and data.get("source") in {"onboarding", "profile_correction"})
        }
        relation["preferences"] = preferences
    return relation

def _forget_profile(relation_state: dict[str, Any], target: str) -> dict[str, Any]:
    relation = dict(relation_state)
    if target == "all":
        for key, _question in ONBOARDING_FLOW:
            relation = _forget_profile_field(relation, key)
        relation.pop("onboarding", None)
        return relation
    return _forget_profile_field(relation, target)

def _onboarding_answers(relation_state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    onboarding = relation_state.get("onboarding", {})
    answers = onboarding.get("answers", {}) if isinstance(onboarding, dict) else {}
    return {key: value for key, value in answers.items() if isinstance(value, dict)}

def _profile_lines(relation_state: dict[str, Any]) -> list[str]:
    answers = _onboarding_answers(relation_state)
    lines: list[str] = []
    for key, _question in ONBOARDING_FLOW:
        answer = answers.get(key)
        value = str(answer.get("value", "")).strip() if answer else ""
        if value:
            label = ONBOARDING_FIELD_LABELS.get(key, key)
            lines.append(f"- {label}: {value}")
    if not lines:
        name = str(relation_state.get("user_name", "")).strip()
        location = str(relation_state.get("user_location", "")).strip()
        expectation = str(relation_state.get("user_expectation", "")).strip()
        if name:
            lines.append(f"- Nombre: {name}")
        if location:
            lines.append(f"- Lugar: {location}")
        if expectation:
            lines.append(f"- Qué espera de amigo: {expectation}")
    return lines

def _profile_response_text(relation_state: dict[str, Any]) -> str:
    lines = _profile_lines(relation_state)
    if not lines:
        return "Todavía tengo poco perfil tuyo. Podemos completarlo hablando: nombre, lugar, gustos y qué esperas de mí."
    return (
        "Esto es lo que tengo de tu perfil inicial:\n"
        + "\n".join(lines)
        + "\nSi algo está mal, dímelo así: corrige mi nombre a ..., corrige mi lugar a ... o actualiza mis gustos a ..."
    )

def _extract_temporal_events(text: str, now: datetime) -> list[dict[str, Any]]:
    plain = _without_accents(text)
    reminder_request = _is_reminder_request(text)
    event_words = (
        "cita",
        "examen",
        "reunion",
        "llamada",
        "quedada",
        "dentista",
        "medico",
        "doctor",
        "consulta",
    )
    if not reminder_request and not any(word in plain for word in event_words):
        return []
    due_at = _relative_due_at_from_text(text, now) if reminder_request else None
    if due_at is None:
        due_at = _due_at_from_text(text, now)
    if due_at is None and reminder_request:
        explicit_time = _time_from_text(text)
        if explicit_time is not None:
            hour, minute = explicit_time
            due_at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if due_at <= now:
                due_at += timedelta(days=1)
    if due_at is None:
        return []
    recurrence = _recurrence_from_text(text)
    kind = "recordatorio" if reminder_request else "event"
    event_text = _reminder_text_from_request(text) if reminder_request else text[:180]
    reminder_status = "confirmed" if reminder_request else "offered"
    lead_time_hours = 0 if reminder_request else 0.5
    reminder_offset_minutes = 0 if reminder_request else 30
    if not reminder_request:
        for candidate in event_words:
            if candidate in plain:
                kind = candidate
                break
    return [
        {
            "id": f"{kind}:{due_at.isoformat()}:{abs(hash(text)) % 100000}",
            "kind": kind,
            "text": event_text,
            "due_at": due_at.isoformat(),
            "status": "pending",
            "source": "user_statement",
            "created_at": now.isoformat(),
            "lead_time_hours": lead_time_hours,
            "reminder_offset_minutes": reminder_offset_minutes,
            "reminder_status": reminder_status,
            "next_due_at": due_at.isoformat(),
            **recurrence,
        }
    ]

def _clean_preference_value(value: str) -> str:
    words = _normalize_text(value).split()
    while words and words[0] in {"el", "la", "los", "las", "un", "una"}:
        words.pop(0)
    return " ".join(words)


def _active_cold_fact_summaries(cold_facts: list[Any]) -> list[dict[str, str]]:
    summaries: list[dict[str, str]] = []
    for fact in cold_facts:
        if getattr(fact, "valid_to", None) is not None:
            continue
        key = str(getattr(fact, "key", "")).strip()
        value = str(getattr(fact, "value", "")).strip()
        if key and value:
            summaries.append({"key": key, "value": value})
    return summaries


def _memory_fact_phrase(key: str, value: str) -> str | None:
    if key == "user_name":
        return f"te llamas {value}"
    if key == "user_role":
        return f"trabajas como {value}"
    if key == "user_location":
        return f"vives en {value}"
    if key == "user_study":
        return f"estudias {value}"
    if key == "project_name":
        return f"tu proyecto se llama {value}"
    if key == "current_project_focus":
        return f"estamos trabajando en {value}"
    if key == "working_agreement":
        return f"nuestro acuerdo de trabajo es {value}"
    if key == "user_expectation":
        return f"esperas que {value}"
    return None


def _memory_fact_phrases(cold_facts: list[dict[str, str]]) -> list[str]:
    phrases: list[str] = []
    seen: set[str] = set()
    for fact in cold_facts:
        phrase = _memory_fact_phrase(fact.get("key", ""), fact.get("value", ""))
        if phrase and phrase not in seen:
            phrases.append(phrase)
            seen.add(phrase)
    return phrases


def _detect_emotional_tone(text: str) -> str | None:
    for tone, pattern in EMOTION_PATTERNS.items():
        if pattern.search(text):
            return tone
    return None


CLOSED_OK_RE = re.compile(
    r"^\s*(todo\s+)?(bien|ok|vale|perfecto|genial|todo bien|estoy bien|voy bien)[.!¡!…\s]*$",
    re.IGNORECASE,
)


def _is_closed_ok_reply(text: str) -> bool:
    return CLOSED_OK_RE.match(_without_accents(text)) is not None


def _recent_user_closed_ok_count(relation_state: dict[str, Any]) -> int:
    thread = relation_state.get("active_conversation_thread")
    if not isinstance(thread, dict):
        return 0
    count = 0
    for item in reversed(list(thread.get("recent_user_messages", []))):
        if _is_closed_ok_reply(str(item)):
            count += 1
            continue
        break
    return count

RELATIONSHIP_FEEDBACK_PATTERNS = {
    "positive": re.compile(
        r"\b("
        r"gracias|perfecto|genial|bien visto|eso era|justo eso|me ayuda|me ayud[oó]|"
        r"acertaste|has acertado|correcto|va perfecto|asi si|as[ií] s[ií]"
        r")\b",
        re.IGNORECASE,
    ),
    "negative": re.compile(
        r"\b("
        r"te equivocas|equivocado|no era eso|eso no|mal entendido|no has entendido|"
        r"incorrecto|metiste la pata|mete la pata|fallaste|no responde|no funciona"
        r")\b",
        re.IGNORECASE,
    ),
    "stop": re.compile(
        r"\b("
        r"no insistas|para ya|p[áa]rate|deja de|no me recuerdes|no preguntes m[aá]s|"
        r"pesado|muy pesado|calla|no quiero hablar de eso"
        r")\b",
        re.IGNORECASE,
    ),
}

RELATIONSHIP_DEFAULT_STYLE = {
    "brevity": 0.5,
    "caution": 0.5,
    "initiative": 0.5,
}


def _detect_relationship_feedback(text: str) -> dict[str, str] | None:
    plain = _without_accents(text)
    for outcome in ("stop", "negative", "positive"):
        pattern = RELATIONSHIP_FEEDBACK_PATTERNS[outcome]
        if pattern.search(text) or pattern.search(plain):
            return {"outcome": outcome, "signal": outcome}
    if _profile_correction_from_text(text) or _profile_forget_from_text(text):
        return {"outcome": "correction", "signal": "profile_adjustment"}
    return None


def _relationship_learning_from_feedback(
    current: dict[str, Any],
    feedback: dict[str, str],
    now: datetime,
) -> dict[str, Any]:
    learning = dict(current)
    counts = dict(learning.get("counts", {}))
    outcome = feedback["outcome"]
    counts[outcome] = int(counts.get(outcome, 0)) + 1
    learning["counts"] = counts
    learning["last_outcome"] = outcome
    learning["last_signal"] = feedback.get("signal", outcome)
    learning["last_signal_at"] = now.isoformat()

    style = dict(RELATIONSHIP_DEFAULT_STYLE)
    style.update({key: float(value) for key, value in dict(learning.get("response_style", {})).items() if key in style})
    if outcome == "positive":
        style["initiative"] = _clamp01(style["initiative"] + 0.04)
        style["caution"] = _clamp01(style["caution"] - 0.02)
    elif outcome in {"negative", "correction"}:
        style["caution"] = _clamp01(style["caution"] + 0.08)
        style["brevity"] = _clamp01(style["brevity"] + 0.04)
        style["initiative"] = _clamp01(style["initiative"] - 0.04)
    elif outcome == "stop":
        style["caution"] = _clamp01(style["caution"] + 0.14)
        style["brevity"] = _clamp01(style["brevity"] + 0.08)
        style["initiative"] = _clamp01(style["initiative"] - 0.14)
    learning["response_style"] = {key: round(value, 4) for key, value in style.items()}

    recent = list(learning.get("recent", []))
    recent.append(
        {
            "outcome": outcome,
            "signal": feedback.get("signal", outcome),
            "observed_at": now.isoformat(),
        }
    )
    learning["recent"] = recent[-20:]
    return learning


CONTINUATION_MARKERS = {
    "eso", "esto", "esa", "ese", "tambien", "también", "ademas", "además",
    "entonces", "y", "pero", "la idea", "lo anterior", "sigue", "relaciona",
}


def _active_thread_context(relation_state: dict[str, Any]) -> dict[str, Any] | None:
    thread = relation_state.get("active_conversation_thread")
    return dict(thread) if isinstance(thread, dict) and thread.get("summary") else None


def _is_thread_continuation(text: str, thread: dict[str, Any] | None) -> bool:
    if not thread:
        return False
    plain = _without_accents(text).strip()
    if not plain:
        return False
    tokens = _tokens_for_model(plain)
    if len(tokens) <= 4:
        return True
    return any(_without_accents(marker) in plain for marker in CONTINUATION_MARKERS)


def _update_active_conversation_thread(
    relation_state: dict[str, Any],
    text: str,
    intent: str,
    now: datetime,
) -> dict[str, Any]:
    if not text.strip() or intent.startswith("onboarding:"):
        return relation_state
    relation = dict(relation_state)
    existing = _active_thread_context(relation)
    tokens = _tokens_for_model(text)
    continuation = _is_thread_continuation(text, existing)
    if existing and (continuation or len(tokens) <= 10):
        messages = [str(item) for item in list(existing.get("recent_user_messages", []))[-3:]]
        topic_terms = list(existing.get("topic_terms", []))
        turn_count = int(existing.get("turn_count", 0)) + 1
    else:
        messages = []
        topic_terms = []
        turn_count = 1
    messages.append(text[:220])
    seen = set(topic_terms)
    for token in tokens:
        if token not in seen:
            topic_terms.append(token)
            seen.add(token)
    summary = " | ".join(messages[-4:])
    relation["active_conversation_thread"] = {
        "summary": summary[:700],
        "topic_terms": topic_terms[-16:],
        "recent_user_messages": messages[-4:],
        "turn_count": turn_count,
        "updated_at": now.isoformat(),
        "continuation_detected": continuation,
    }
    return relation


def _record_continuity_miss_if_needed(relation_state: dict[str, Any], text: str, now: datetime) -> dict[str, Any]:
    plain = _without_accents(text)
    if not any(marker in plain for marker in ("no relaciona", "no conect", "pierde", "contexto", "no entiende el hilo")):
        return relation_state
    relation = dict(relation_state)
    learning = _relationship_learning_from_feedback(
        dict(relation.get("relationship_learning", {})),
        {"outcome": "negative", "signal": "continuity_miss"},
        now,
    )
    counts = dict(learning.get("counts", {}))
    counts["continuity_miss"] = int(counts.get("continuity_miss", 0)) + 1
    learning["counts"] = counts
    relation["relationship_learning"] = learning
    return relation


GROUP_TOPIC_MARKERS = {
    "plan",
    "planes",
    "trabajo",
    "familia",
    "salud",
    "cita",
    "dentista",
    "proyecto",
    "viaje",
    "quedada",
    "idea",
    "duda",
    "problema",
}
GROUP_TONE_MARKERS = {
    "humor": ("jaja", "jeje", "lol", "broma"),
    "apoyo": ("gracias", "animo", "ánimo", "tranqui", "te entiendo", "me alegro"),
    "cuidado": ("triste", "agobiado", "agobiada", "preocupado", "preocupada", "miedo", "mal"),
    "practico": ("plan", "idea", "hacer", "quedamos", "cuando", "hora"),
}

SOCIAL_LEARNING_SPECS = {
    "general_question": {
        "threshold": 3,
        "title": "Detectado: preguntas generales de grupo",
        "lesson": "En grupos, cuando aparece una pregunta general abierta, puede participar con una respuesta breve si aporta valor claro y no invade un hilo entre dos personas.",
    },
    "supportive_ack": {
        "threshold": 2,
        "title": "Detectado: apoyo social breve",
        "lesson": "En grupos, las señales de agradecimiento o apoyo piden presencia breve y natural; mejor acompañar sin convertirlo siempre en otra pregunta.",
    },
    "boundary": {
        "threshold": 1,
        "title": "Detectado: limite social del grupo",
        "lesson": "Si el grupo marca molestia o pide que no intervenga, debe bajar iniciativa y observar antes de volver a participar.",
    },
    "greeting": {
        "threshold": 3,
        "title": "Detectado: saludos de grupo",
        "lesson": "Un saludo general en grupo puede responderse de forma sencilla, pero no debe abrir una conversacion larga si nadie la pide.",
    },
}


def _classify_social_learning_signal(text: str, intent: str) -> str | None:
    if not (intent.startswith("group_") or intent == "group_chat"):
        return None
    plain = _without_accents(text)
    if text.startswith("social_feedback:negative") or any(marker in plain for marker in ("no te metas", "calla", "pesado", "no insistas", "no preguntes")):
        return "boundary"
    if any(marker in plain for marker in ("gracias", "bien visto", "exacto", "te entiendo", "animo", "tranqui", "me alegro")):
        return "supportive_ack"
    if any(marker in plain for marker in ("hola", "buenas", "buenos dias", "buenas tardes", "buenas noches")) and len(_tokens_for_model(text)) <= 5:
        return "greeting"
    if "?" in text or "¿" in text or any(marker in plain for marker in ("alguien sabe", "alguin sabe", "que opinais", "como lo veis", "sabeis", "cual es")):
        return "general_question"
    return None


def _apply_group_social_feedback(
    relation_state: dict[str, Any],
    text: str,
    intent: str,
    now: datetime,
) -> dict[str, Any]:
    if not intent.startswith("group_") or not text.startswith("social_feedback:"):
        return relation_state
    outcome = text.split(":", 1)[1].strip()
    relation = dict(relation_state)
    maturity = dict(relation.get("group_maturity", {}))
    outcomes = dict(maturity.get("social_outcomes", {}))
    outcomes[outcome] = int(outcomes.get(outcome, 0)) + 1
    maturity["social_outcomes"] = outcomes
    maturity["last_social_outcome"] = outcome
    maturity["last_social_outcome_at"] = now.isoformat()
    if outcome == "negative":
        maturity["participation_guidance"] = "baja iniciativa; interviene solo si te mencionan o si la pregunta general es clara"
    elif outcome in {"positive", "reacted"}:
        maturity["participation_guidance"] = "puede participar con brevedad cuando aporte valor claro"
    relation["group_maturity"] = maturity
    return relation


def _update_group_social_learning(
    relation_state: dict[str, Any],
    text: str,
    intent: str,
    now: datetime,
) -> dict[str, Any]:
    signal = _classify_social_learning_signal(text, intent)
    if signal is None:
        return relation_state
    relation = dict(relation_state)
    maturity = dict(relation.get("group_maturity", {}))
    social = dict(maturity.get("social_learning", {}))
    counts = dict(social.get("signal_counts", {}))
    counts[signal] = int(counts.get(signal, 0)) + 1
    social["signal_counts"] = counts
    social["last_signal"] = signal
    social["last_signal_at"] = now.isoformat()
    social["policy"] = "aggregate_closed_vocab_drafts_only_no_raw_memory"
    maturity["social_learning"] = social
    relation["group_maturity"] = maturity
    return relation


def _update_group_maturity(
    relation_state: dict[str, Any],
    text: str,
    intent: str,
    now: datetime,
) -> dict[str, Any]:
    if not text.strip() or not (intent.startswith("group_") or intent == "chat"):
        return relation_state
    relation = dict(relation_state)
    maturity = dict(relation.get("group_maturity", {}))
    observed = int(maturity.get("observed_messages", 0)) + 1
    replied = int(maturity.get("bot_replies", 0)) + (0 if intent == "group_observation" else 1)
    questions = int(maturity.get("questions_seen", 0)) + (1 if "?" in text or "¿" in text else 0)
    plain = _without_accents(text)
    topic_counts = dict(maturity.get("topic_counts", {}))
    for marker in GROUP_TOPIC_MARKERS:
        if marker in plain:
            topic_counts[marker] = int(topic_counts.get(marker, 0)) + 1
    tone_counts = dict(maturity.get("tone_counts", {}))
    for tone, markers in GROUP_TONE_MARKERS.items():
        if any(_without_accents(marker) in plain for marker in markers):
            tone_counts[tone] = int(tone_counts.get(tone, 0)) + 1
    shared_history = list(maturity.get("shared_history", []))
    if len(_tokens_for_model(text)) >= 4 and (questions > int(maturity.get("questions_seen", 0)) or any(marker in plain for marker in GROUP_TOPIC_MARKERS)):
        shared_history.append(
            {
                "kind": "group_exchange",
                "summary": text[:140],
                "observed_at": now.isoformat(),
                "source": "group_conversation",
            }
        )
    maturity.update(
        {
            "identity": "software_companion_no_human_life",
            "observed_messages": observed,
            "bot_replies": replied,
            "questions_seen": questions,
            "topic_counts": topic_counts,
            "tone_counts": tone_counts,
            "shared_history": shared_history[-12:],
            "last_observed_at": now.isoformat(),
            "participation_guidance": (
                "participa solo cuando aporte algo breve; no inventes vida humana; "
                "usa como fondo la historia compartida del grupo y lo que has aprendido aqui"
            ),
        }
    )
    relation["group_maturity"] = maturity
    return relation


def _to_group_maturity_dashboard(relation_state: dict[str, Any]) -> dict[str, Any]:
    maturity = relation_state.get("group_maturity", {})
    if not isinstance(maturity, dict) or not maturity:
        return {"present": False}
    topic_counts = dict(maturity.get("topic_counts", {}))
    tone_counts = dict(maturity.get("tone_counts", {}))
    return {
        "present": True,
        "identity": str(maturity.get("identity", "software_companion_no_human_life")),
        "observed_messages": int(maturity.get("observed_messages", 0)),
        "bot_replies": int(maturity.get("bot_replies", 0)),
        "questions_seen": int(maturity.get("questions_seen", 0)),
        "top_topics": [
            {"topic": str(key), "count": int(value)}
            for key, value in sorted(topic_counts.items(), key=lambda item: int(item[1]), reverse=True)[:8]
        ],
        "tone": [
            {"tone": str(key), "count": int(value)}
            for key, value in sorted(tone_counts.items(), key=lambda item: int(item[1]), reverse=True)[:8]
        ],
        "shared_history_count": len([item for item in maturity.get("shared_history", []) if isinstance(item, dict)]),
        "social_outcomes": dict(maturity.get("social_outcomes", {})),
        "social_learning": dict(maturity.get("social_learning", {})),
        "last_social_outcome": maturity.get("last_social_outcome"),
        "last_observed_at": maturity.get("last_observed_at"),
        "privacy": "group_scope_only_no_private_chats_no_human_life",
    }


def _social_learning_journal_entries(
    *,
    agent_id: str,
    relation_state: dict[str, Any],
    source_episode_id: str,
    now: datetime,
    existing_lessons: list[str],
) -> list[LearningJournalEntry]:
    if not agent_id.startswith("telegram-group-"):
        return []
    maturity = relation_state.get("group_maturity", {})
    if not isinstance(maturity, dict):
        return []
    social = dict(maturity.get("social_learning", {}))
    counts = dict(social.get("signal_counts", {}))
    drafted = set(str(item) for item in social.get("drafted_patterns", []) if str(item))
    existing = {_review_key(item) for item in existing_lessons}
    out: list[LearningJournalEntry] = []
    for key, spec in SOCIAL_LEARNING_SPECS.items():
        if key in drafted or int(counts.get(key, 0)) < int(spec["threshold"]):
            continue
        lesson = str(spec["lesson"])
        if _review_key(lesson) in existing:
            drafted.add(key)
            continue
        try:
            out.append(
                make_detected_learning_entry(
                    agent_id=agent_id,
                    lesson=lesson,
                    title=str(spec["title"]),
                    tags=["social", "comportamiento", "amistad"],
                    source_episode_id=source_episode_id,
                    now=now,
                    status="draft",
                )
            )
            drafted.add(key)
            existing.add(_review_key(lesson))
        except ValueError:
            continue
    if out:
        social["drafted_patterns"] = sorted(drafted)
        maturity["social_learning"] = social
        relation_state["group_maturity"] = maturity
    return out[:3]


def _update_relation_from_percept(
    relation_state: dict[str, Any],
    percept_frame: dict[str, Any],
    now: datetime,
) -> dict[str, Any]:
    relation = dict(relation_state)
    intent = str(percept_frame.get("intent", "unknown"))
    text = str(percept_frame.get("text", "")).strip()
    relation = _apply_group_social_feedback(relation, text, intent, now)
    relation["interaction_count"] = int(relation.get("interaction_count", 0)) + 1
    relation["last_interaction_at"] = now.isoformat()
    feedback = _detect_relationship_feedback(text)
    if feedback is not None:
        relation["relationship_learning"] = _relationship_learning_from_feedback(
            dict(relation.get("relationship_learning", {})),
            feedback,
            now,
        )
    relation = _record_continuity_miss_if_needed(relation, text, now)
    relation = _update_active_conversation_thread(relation, text, intent, now)
    relation = _update_group_maturity(relation, text, intent, now)
    relation = _update_group_social_learning(relation, text, intent, now)

    name = NAME_RE.search(text)
    if name:
        relation["user_name"] = name.group("name")
        relation["user_name_updated_at"] = now.isoformat()

    profile_correction = _profile_correction_from_text(text)
    if profile_correction:
        key, value = profile_correction
        onboarding = dict(relation.get("onboarding", {}))
        answers = dict(onboarding.get("answers", {}))
        answers[key] = {
            "label": ONBOARDING_FIELD_LABELS.get(key, key),
            "value": value,
            "updated_at": now.isoformat(),
            "source": "profile_correction",
        }
        storage = PROFILE_STORAGE_FIELDS.get(key)
        if storage:
            field, limit = storage
            relation[field] = value[:limit]
            if key == "name":
                relation["user_name_updated_at"] = now.isoformat()
        elif key == "likes":
            preferences = dict(relation.get("preferences", {}))
            preferences[value[:80]] = {
                "source": "profile_correction",
                "confidence": 0.84,
                "salience": 0.7,
                "updated_at": now.isoformat(),
            }
            relation["preferences"] = preferences
        onboarding["answers"] = answers
        onboarding["updated_at"] = now.isoformat()
        relation["onboarding"] = onboarding

    profile_forget = _profile_forget_from_text(text)
    if profile_forget:
        relation = _forget_profile(relation, profile_forget)

    preference = PREFERENCE_RE.search(text)
    if preference:
        value = _clean_preference_value(preference.group("value"))
        if value:
            preferences = dict(relation.get("preferences", {}))
            preferences[value] = {
                "source": "user_statement",
                "confidence": _clamp01(float(percept_frame.get("confidence", 0.8))),
                "salience": _clamp01(float(percept_frame.get("salience", 0.5))),
                "updated_at": now.isoformat(),
            }
            relation["preferences"] = preferences

    emotional_tone = _detect_emotional_tone(text)
    if emotional_tone is not None:
        signals = list(relation.get("emotional_signals", []))
        signals.append(
            {
                "tone": emotional_tone,
                "text": text[:160],
                "observed_at": now.isoformat(),
            }
        )
        relation["emotional_signals"] = signals[-30:]
        relation["last_emotional_tone"] = emotional_tone
    tags = _semantic_tags(text)
    if tags:
        tag_counts = dict(relation.get("semantic_tag_counts", {}))
        for tag in tags:
            tag_counts[tag] = int(tag_counts.get(tag, 0)) + 1
        relation["semantic_tag_counts"] = tag_counts
    temporal_events = _extract_temporal_events(text, now)
    if temporal_events:
        existing = list(relation.get("temporal_events", []))
        existing_ids = {str(item.get("id")) for item in existing if isinstance(item, dict)}
        for event in temporal_events:
            if event["id"] not in existing_ids:
                existing.append(event)
        relation["temporal_events"] = existing[-50:]
    else:
        reminder_confirmation = _reminder_confirmation_from_text(text)
        offered_event = _latest_offered_reminder_event(relation)
        if reminder_confirmation and offered_event is not None:
            updated = []
            for raw in relation.get("temporal_events", []):
                if not isinstance(raw, dict):
                    updated.append(raw)
                    continue
                event = dict(raw)
                if str(event.get("id")) == str(offered_event.get("id")):
                    if reminder_confirmation == "confirmed":
                        event["reminder_status"] = "confirmed"
                        event["reminder_confirmed_at"] = now.isoformat()
                        event["lead_time_hours"] = 0.5
                        event["reminder_offset_minutes"] = 30
                        event["status"] = "pending"
                    else:
                        event["reminder_status"] = "declined"
                        event["reminder_declined_at"] = now.isoformat()
                        event["status"] = "no_reminder"
                updated.append(event)
            relation["temporal_events"] = updated

    if intent.startswith("onboarding:"):
        key = intent.split(":", 1)[1].strip()
        allowed_keys = {field for field, _ in ONBOARDING_FLOW}
        if key in allowed_keys:
            onboarding = dict(relation.get("onboarding", {}))
            answers = dict(onboarding.get("answers", {}))
            if not SKIP_ONBOARDING_RE.search(text):
                answers[key] = {
                    "label": ONBOARDING_FIELD_LABELS.get(key, key),
                    "value": text[:240],
                    "updated_at": now.isoformat(),
                }
                if key == "name":
                    relation["user_name"] = text[:80]
                    relation["user_name_updated_at"] = now.isoformat()
                elif key == "location":
                    relation["user_location"] = text[:160]
                elif key == "birth":
                    relation["user_birth_or_age"] = text[:160]
                elif key == "likes":
                    preferences = dict(relation.get("preferences", {}))
                    preferences[text[:80]] = {
                        "source": "onboarding",
                        "confidence": 0.82,
                        "salience": 0.7,
                        "updated_at": now.isoformat(),
                    }
                    relation["preferences"] = preferences
                elif key == "expectation":
                    relation["user_expectation"] = text[:240]
            onboarding["answers"] = answers
            onboarding["last_key"] = key
            onboarding["completed"] = key == ONBOARDING_FLOW[-1][0]
            onboarding["updated_at"] = now.isoformat()
            relation["onboarding"] = onboarding

    return relation

PREFERENCE_RE = re.compile(
    r"\b(prefiero|me gusta)\s+(?P<value>[\wáéíóúñ]+(?:\s+[\wáéíóúñ]+){0,4})",
    re.IGNORECASE,
)
NAME_RE = re.compile(r"\bsoy\s+(?P<name>[\wáéíóúñ]+)", re.IGNORECASE)
TOPIC_RE = re.compile(
    r"\b(?:hablemos de|quiero hablar de|hablar de)\s+(?P<topic>[\wáéíóúñ]+(?:\s+[\wáéíóúñ]+){0,4})",
    re.IGNORECASE,
)
EMOTION_PATTERNS = {
    "sad": re.compile(r"\b(triste|solo|sola|mal|baj[oó]n|abatido|abatida)\b", re.IGNORECASE),
    "happy": re.compile(r"\b(contento|contenta|feliz|bien|alegre|motivado|motivada)\b", re.IGNORECASE),
    "stressed": re.compile(r"\b(estresado|estresada|agobiado|agobiada|preocupado|preocupada|cansado|cansada)\b", re.IGNORECASE),
}
GENERIC_INTENTS = {"chat", "question", "greeting", "saludo", "unknown"}
STOPWORDS = {
    "el", "la", "los", "las", "un", "una", "de", "del", "que", "y", "a", "en",
    "me", "mi", "mis", "tu", "tus", "soy", "eres", "gusta", "prefiero",
    "hablemos", "hola", "buenas", "con", "por", "para",
}

def _default_cognitive_time() -> dict[str, float]:
    return {"age_ticks": 0.0, "experience_mass": 0.0, "maturity": 0.0}

def _default_self_model() -> dict[str, Any]:
    return {
        "identity_stage": "early_childhood",
        "interaction_count": 0,
        "affect_state": {"mood": "stable", "intensity": 0.2},
        "known_capabilities": ["remember_episodes", "retrieve_context", "safe_proactivity"],
        "known_limits": ["minimal_language_policy", "no_background_daemon_yet"],
        "autobiographical_timeline": [],
    }

def _default_world_model() -> dict[str, Any]:
    return {
        "concept_counts": {},
        "intent_counts": {},
        "open_questions": [],
        "causal_observations": [],
        "curiosity_topics": [],
        "rss_culture": {"enabled": True, "item_count": 0, "themes": {}, "privacy": "global_culture_not_private_memory"},
    }

def _clean_curiosity_topic(value: str) -> str:
    value = re.sub(r"\s+", " ", value).strip(" ?¿!¡.,:;\"'")
    value = re.sub(r"\b(?:tu|tú)\s+opini[oó]n\b", "", value, flags=re.IGNORECASE).strip(" ?¿!¡.,:;")
    return value[:90]

def _curiosity_key(value: str) -> str:
    value = value.lower()
    value = value.replace("á", "a").replace("é", "e").replace("í", "i").replace("ó", "o").replace("ú", "u").replace("ñ", "n")
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", value)).strip()

def _extract_curiosity_topics(text: str, intent: str) -> list[str]:
    raw = text.strip()
    if len(raw) < 4:
        return []
    patterns = (
        r"(?:qu[eé]\s+opinas\s+de|qu[eé]\s+piensas\s+de|que te parece|qué te parece)\s+(.{3,120})",
        r"(?:me interesa|tengo curiosidad por|quiero aprender sobre|quiero entender)\s+(.{3,120})",
        r"(?:hablemos de|cu[eé]ntame sobre)\s+(.{3,120})",
    )
    topics: list[str] = []
    for pattern in patterns:
        match = re.search(pattern, raw, re.IGNORECASE | re.DOTALL)
        if match:
            topic = _clean_curiosity_topic(match.group(1))
            if topic and _curiosity_key(topic) not in {"esto", "eso", "aquello", "tema", "algo"}:
                topics.append(topic)
    if not topics and "?" in raw and intent.startswith(("question", "group_")):
        tokens = _tokens_for_model(raw)
        if 1 <= len(tokens) <= 5:
            topic = _clean_curiosity_topic(" ".join(tokens))
            if topic:
                topics.append(topic)
    seen: set[str] = set()
    out: list[str] = []
    for topic in topics:
        key = _curiosity_key(topic)
        if key and key not in seen:
            seen.add(key)
            out.append(topic)
    return out[:3]

def _update_curiosity_topics(world_model: dict[str, Any], topics: list[str], now: datetime) -> dict[str, Any]:
    if not topics:
        return world_model
    items = [dict(item) for item in world_model.get("curiosity_topics", []) if isinstance(item, dict)]
    by_key = {_curiosity_key(str(item.get("topic", ""))): item for item in items if item.get("topic")}
    for topic in topics:
        key = _curiosity_key(topic)
        if not key:
            continue
        if key in by_key:
            by_key[key]["count"] = int(by_key[key].get("count", 1)) + 1
            by_key[key]["last_seen_at"] = now.isoformat()
            by_key[key]["status"] = "open"
        else:
            item = {
                "key": key,
                "topic": topic,
                "status": "open",
                "source": "conversation_curiosity",
                "count": 1,
                "first_seen_at": now.isoformat(),
                "last_seen_at": now.isoformat(),
            }
            items.append(item)
            by_key[key] = item
    for item in items:
        item["key"] = _curiosity_key(str(item.get("topic", "")))
    items.sort(key=lambda item: (str(item.get("status")) == "open", str(item.get("last_seen_at", ""))), reverse=True)
    world_model["curiosity_topics"] = items[:50]
    return world_model

def _curiosity_evidence_count(topic: str, entries: list[LearningJournalEntry]) -> int:
    topic_tokens = set(_tokens_for_model(topic))
    if not topic_tokens:
        return 0
    count = 0
    for entry in entries:
        if entry.status != "active":
            continue
        text = f"{entry.title} {entry.lesson} {' '.join(entry.tags or [])}"
        entry_tokens = set(_tokens_for_model(text))
        if topic_tokens & entry_tokens:
            count += 1
    return count

def _refresh_curiosity_topics_from_learning(
    world_model: dict[str, Any],
    entries: list[LearningJournalEntry],
    now: datetime,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    topics = [dict(item) for item in world_model.get("curiosity_topics", []) if isinstance(item, dict)]
    grounded: list[dict[str, Any]] = []
    for item in topics:
        topic = str(item.get("topic", "")).strip()
        item["key"] = _curiosity_key(topic)
        evidence_count = _curiosity_evidence_count(topic, entries)
        item["evidence_count"] = evidence_count
        if evidence_count > 0 and item.get("status") == "open":
            item["status"] = "grounded"
            item["grounded_at"] = now.isoformat()
            grounded.append(dict(item))
    topics.sort(key=lambda item: (str(item.get("status")) == "open", int(item.get("evidence_count", 0)), str(item.get("last_seen_at", ""))), reverse=True)
    world_model["curiosity_topics"] = topics[:50]
    return world_model, grounded

def _curiosity_maturity_summary(world_model: dict[str, Any], entries: list[LearningJournalEntry]) -> dict[str, Any]:
    topics = [dict(item) for item in world_model.get("curiosity_topics", []) if isinstance(item, dict)]
    enriched: list[dict[str, Any]] = []
    for item in topics:
        topic = str(item.get("topic", "")).strip()
        evidence_count = _curiosity_evidence_count(topic, entries)
        if evidence_count:
            item["evidence_count"] = evidence_count
        item["key"] = _curiosity_key(topic)
        enriched.append(item)
    open_count = len([item for item in enriched if item.get("status") == "open"])
    grounded_count = len([item for item in enriched if item.get("status") == "grounded"])
    archived_count = len([item for item in enriched if item.get("status") == "archived"])
    return {
        "total_count": len(enriched),
        "open_count": open_count,
        "grounded_count": grounded_count,
        "archived_count": archived_count,
        "topics": enriched[-10:],
        "next_growth": "convertir curiosidad en evidencia activa" if open_count else "seguir descubriendo temas nuevos",
        "privacy": "private_agent_curiosity_not_global",
    }

def _theme_mastery_summary(entries: list[LearningJournalEntry], stances: dict[str, Any]) -> dict[str, Any]:
    active = [entry for entry in entries if entry.status == "active"]
    draft = [entry for entry in entries if entry.status == "draft"]
    active_stance_themes = {
        str(item.get("theme"))
        for item in stances.get("stances", [])
        if isinstance(item, dict) and item.get("active")
    }
    by_theme: dict[str, dict[str, Any]] = {}
    for theme in ["vida", "cultura", "amistad", "trabajo", "comportamiento", "salud", "otros"]:
        active_count = len([entry for entry in active if classify_learning_theme(entry.title, entry.lesson, entry.tags) == theme])
        draft_count = len([entry for entry in draft if classify_learning_theme(entry.title, entry.lesson, entry.tags) == theme])
        if active_count >= 3 and theme in active_stance_themes:
            level = "criterio_estable"
        elif active_count >= 1:
            level = "criterio_inicial"
        elif draft_count:
            level = "en_revision"
        else:
            level = "sin_base"
        by_theme[theme] = {
            "level": level,
            "active_count": active_count,
            "draft_count": draft_count,
            "stance_active": theme in active_stance_themes,
        }
    stable_count = len([item for item in by_theme.values() if item["level"] == "criterio_estable"])
    initial_count = len([item for item in by_theme.values() if item["level"] == "criterio_inicial"])
    review_count = len([item for item in by_theme.values() if item["level"] == "en_revision"])
    return {
        "by_theme": by_theme,
        "stable_count": stable_count,
        "initial_count": initial_count,
        "review_count": review_count,
        "next_growth": "subir temas iniciales a criterio estable" if initial_count else "activar evidencia en temas sin base",
        "privacy": "private_theme_mastery_from_learning_journal",
    }

def _learning_priorities(
    entries: list[LearningJournalEntry],
    curiosity: dict[str, Any],
    mastery: dict[str, Any],
) -> list[dict[str, Any]]:
    priorities: list[dict[str, Any]] = []
    for topic in curiosity.get("topics", []):
        if isinstance(topic, dict) and topic.get("status") == "open":
            priorities.append(
                {
                    "kind": "curiosity",
                    "label": str(topic.get("topic", "")),
                    "reason": "tema abierto sin evidencia activa",
                    "weight": 3,
                }
            )
    for entry in entries:
        if entry.status == "draft":
            priorities.append(
                {
                    "kind": "review",
                    "label": entry.title,
                    "reason": "aprendizaje detectado pendiente de aprobar o archivar",
                    "weight": 2,
                }
            )
    for theme, item in mastery.get("by_theme", {}).items():
        if isinstance(item, dict) and item.get("level") == "sin_base":
            priorities.append(
                {
                    "kind": "theme_gap",
                    "label": str(theme),
                    "reason": "tema sin criterio respaldado",
                    "weight": 1,
                }
            )
    priorities.sort(key=lambda item: int(item.get("weight", 0)), reverse=True)
    return priorities[:8]

def _learning_contradiction_watch(entries: list[LearningJournalEntry]) -> dict[str, Any]:
    active = [entry for entry in entries if entry.status == "active"]
    candidates: list[dict[str, Any]] = []
    for idx, left in enumerate(active):
        left_text = _review_key(f"{left.title} {left.lesson}")
        left_theme = classify_learning_theme(left.title, left.lesson, left.tags)
        for right in active[idx + 1 :]:
            right_theme = classify_learning_theme(right.title, right.lesson, right.tags)
            if left_theme != right_theme:
                continue
            right_text = _review_key(f"{right.title} {right.lesson}")
            left_tokens = set(left_text.split())
            right_tokens = set(right_text.split())
            if len(left_tokens & right_tokens) < 2:
                continue
            if ("no" in left_tokens) != ("no" in right_tokens):
                candidates.append(
                    {
                        "theme": left_theme,
                        "entry_ids": [left.entry_id, right.entry_id],
                        "titles": [left.title, right.title],
                        "reason": "posible tension entre pauta afirmativa y negativa",
                    }
                )
    return {
        "count": len(candidates),
        "candidates": candidates[:10],
        "privacy": "private_learning_contradiction_candidates",
    }

def _growth_compass(
    *,
    maturity_profile: dict[str, Any],
    curiosity: dict[str, Any],
    mastery: dict[str, Any],
    priorities: list[dict[str, Any]],
    contradictions: dict[str, Any],
) -> dict[str, Any]:
    focus = "aprender con curiosidad"
    if contradictions.get("count"):
        focus = "resolver tensiones de criterio"
    elif priorities and priorities[0].get("kind") == "review":
        focus = "revisar aprendizajes detectados"
    elif curiosity.get("open_count", 0):
        focus = "convertir curiosidad en evidencia"
    elif mastery.get("stable_count", 0):
        focus = "usar criterio estable sin fingir certeza"
    return {
        "focus": focus,
        "maturity_stage": maturity_profile.get("stage"),
        "open_curiosity": curiosity.get("open_count", 0),
        "grounded_curiosity": curiosity.get("grounded_count", 0),
        "stable_themes": mastery.get("stable_count", 0),
        "priority_count": len(priorities),
        "contradiction_count": contradictions.get("count", 0),
        "privacy": "private_growth_compass",
    }

def _append_maturity_reflections(
    relation_state: dict[str, Any],
    *,
    grounded_topics: list[dict[str, Any]],
    now: datetime,
) -> dict[str, Any]:
    if not grounded_topics:
        return relation_state
    reflections = [dict(item) for item in relation_state.get("maturity_reflections", []) if isinstance(item, dict)]
    existing = {str(item.get("topic_key", "")) for item in reflections}
    for topic in grounded_topics:
        key = str(topic.get("key", ""))
        if not key or key in existing:
            continue
        reflections.append(
            {
                "id": str(uuid4()),
                "at": now.isoformat(),
                "topic": str(topic.get("topic", "")),
                "topic_key": key,
                "type": "curiosity_grounded",
                "summary": f"Un tema que despertaba curiosidad ya tiene evidencia activa: {topic.get('topic', '')}.",
            }
        )
        existing.add(key)
    return {**relation_state, "maturity_reflections": reflections[-50:]}

def _tokens_for_model(text: str) -> list[str]:
    return [
        token
        for token in re.findall(r"[\wáéíóúñ]+", text.lower())
        if len(token) > 2 and token not in STOPWORDS
    ]

def _update_cognitive_models(
    state: AgentState,
    percept_frame: dict[str, Any],
    now: datetime,
) -> None:
    text = str(percept_frame.get("text", "")).strip()
    intent = str(percept_frame.get("intent", "unknown"))
    salience = _clamp01(float(percept_frame.get("salience", 0.5)))
    confidence = _clamp01(float(percept_frame.get("confidence", 0.8)))

    cognitive_time = dict(state.cognitive_time)
    cognitive_time["age_ticks"] = float(cognitive_time.get("age_ticks", 0.0)) + 1.0
    delta = salience * confidence
    cognitive_time["experience_mass"] = round(float(cognitive_time.get("experience_mass", 0.0)) + delta, 6)
    cognitive_time["maturity"] = round(_clamp01(cognitive_time["experience_mass"] / 80.0), 6)
    state.cognitive_time = cognitive_time

    world_model = dict(state.world_model)
    intent_counts = dict(world_model.get("intent_counts", {}))
    intent_counts[intent] = int(intent_counts.get(intent, 0)) + 1
    world_model["intent_counts"] = intent_counts

    concept_counts = dict(world_model.get("concept_counts", {}))
    for token in _tokens_for_model(f"{intent} {text}"):
        concept_counts[token] = int(concept_counts.get(token, 0)) + 1
    world_model["concept_counts"] = concept_counts
    tags = _semantic_tags(text)
    if tags:
        tag_counts = dict(world_model.get("tag_counts", {}))
        for tag in tags:
            tag_counts[tag] = int(tag_counts.get(tag, 0)) + 1
        world_model["tag_counts"] = tag_counts

    if "?" in text:
        open_questions = list(world_model.get("open_questions", []))
        open_questions.append({"text": text, "observed_at": now.isoformat()})
        world_model["open_questions"] = open_questions[-20:]
    world_model = _update_curiosity_topics(world_model, _extract_curiosity_topics(text, intent), now)
    if "porque" in text.lower():
        causal = list(world_model.get("causal_observations", []))
        causal.append({"text": text, "observed_at": now.isoformat()})
        world_model["causal_observations"] = causal[-20:]
    state.world_model = world_model

    self_model = dict(state.self_model)
    self_model["interaction_count"] = int(self_model.get("interaction_count", 0)) + 1
    self_model["last_experienced_intent"] = intent
    self_model["identity_stage"] = (
        "early_childhood"
        if cognitive_time["maturity"] < 0.25
        else "developing_childhood"
    )
    if salience >= 0.75:
        timeline = list(self_model.get("autobiographical_timeline", []))
        timeline.append(
            {
                "tick": state.tick,
                "intent": intent,
                "summary": text[:160],
                "salience": salience,
                "recorded_at": now.isoformat(),
            }
        )
        self_model["autobiographical_timeline"] = timeline[-30:]
    emotional_tone = _detect_emotional_tone(text)
    if emotional_tone is not None:
        affect = dict(self_model.get("affect_state", {}))
        affect["mood"] = {
            "sad": "concerned",
            "happy": "warm",
            "stressed": "attentive",
        }[emotional_tone]
        affect["intensity"] = round(max(float(affect.get("intensity", 0.2)), salience), 3)
        affect["source"] = "user_emotional_signal"
        affect["updated_at"] = now.isoformat()
        self_model["affect_state"] = affect
    state.self_model = self_model

def _regulate_drives(state: AgentState, percept_frame: dict[str, Any]) -> None:
    text = str(percept_frame.get("text", "")).strip()
    salience = _clamp01(float(percept_frame.get("salience", 0.5)))
    confidence = _clamp01(float(percept_frame.get("confidence", 0.8)))
    known_concepts = set(state.world_model.get("concept_counts", {}).keys())
    tokens = set(_tokens_for_model(text))
    novelty = len(tokens.difference(known_concepts)) / max(len(tokens), 1)
    open_question_pressure = min(len(state.world_model.get("open_questions", [])) / 10.0, 1.0)
    relation_depth = min(int(state.relation_state.get("interaction_count", 0)) / 50.0, 1.0)
    maturity = _clamp01(float(state.cognitive_time.get("maturity", 0.0)))

    drives = dict(state.drive_vector)
    drives["curiosity"] = _clamp01(
        drives.get("curiosity", 0.5)
        + (0.05 * novelty)
        + (0.03 * open_question_pressure)
        - (0.02 * maturity)
    )
    drives["attachment"] = _clamp01(
        drives.get("attachment", 0.5)
        + (0.03 * relation_depth)
        + (0.02 if state.relation_state.get("user_name") else 0.0)
    )
    drives["coherence"] = _clamp01(
        drives.get("coherence", 0.5)
        + (0.04 * maturity)
        + (0.04 * confidence)
        - (0.02 * novelty)
    )
    drives["exploration"] = _clamp01(
        drives.get("exploration", 0.5)
        + (0.04 * drives["curiosity"])
        - (0.02 * drives.get("safety", 0.5))
    )
    drives["safety"] = _clamp01(
        drives.get("safety", 0.5)
        + (0.02 * (1.0 - confidence))
        - (0.01 * relation_depth)
    )
    drives["energy"] = _clamp01(
        drives.get("energy", state.energy)
        - 0.01
        - (0.01 * salience)
        + (0.005 * maturity)
    )
    state.drive_vector = drives

def _derive_active_goals(state: AgentState) -> list[str]:
    goals: list[str] = []
    drives = state.drive_vector
    if drives.get("curiosity", 0.0) >= 0.58 or state.world_model.get("open_questions"):
        goals.append("reduce_uncertainty")
    if drives.get("attachment", 0.0) >= 0.58 or state.relation_state.get("user_name"):
        goals.append("maintain_relationship_continuity")
    if drives.get("coherence", 0.0) >= 0.55:
        goals.append("consolidate_self_narrative")
    if state.relation_state.get("preferences"):
        goals.append("revisit_user_preferences")
    if drives.get("energy", 1.0) <= 0.25:
        goals.append("recover_energy")
    return goals[:5]

def _append_response_history(
    relation_state: dict[str, Any],
    *,
    text: str,
    now: datetime,
    source: str,
) -> dict[str, Any]:
    history = list(relation_state.get("response_history", []))
    history.append(
        {
            "id": str(uuid4()),
            "role": "assistant",
            "text": text,
            "timestamp": now.isoformat(),
            "source": source,
        }
    )
    return {**relation_state, "response_history": history[-100:]}

def _append_audit_event(
    relation_state: dict[str, Any],
    *,
    now: datetime,
    event_type: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    log = list(relation_state.get("audit_log", []))
    log.append(
        {
            "id": str(uuid4()),
            "at": now.isoformat(),
            "type": event_type,
            "payload": payload,
        }
    )
    return {**relation_state, "audit_log": log[-200:]}

DEFAULT_ACTION_PERMISSIONS = {
    "external_message": {"allowed": True, "delivery": "inbox_only"},
    "tool_call": {"allowed": False, "delivery": "blocked"},
    "network_request": {"allowed": False, "delivery": "blocked"},
    "file_write": {"allowed": False, "delivery": "blocked"},
}

def _default_permissions() -> dict[str, Any]:
    return {key: dict(value) for key, value in DEFAULT_ACTION_PERMISSIONS.items()}

def _permission_decision(relation_state: dict[str, Any], action: dict[str, Any]) -> dict[str, Any]:
    permissions = dict(relation_state.get("action_permissions", _default_permissions()))
    action_type = str(action.get("type", "unknown"))
    policy = dict(permissions.get(action_type, {"allowed": False, "delivery": "blocked"}))
    allowed = bool(policy.get("allowed", False))
    return {
        "action_type": action_type,
        "allowed": allowed,
        "delivery": policy.get("delivery", "blocked"),
        "reason": "permission_allowed" if allowed else "permission_denied",
    }


def _derive_stance_text(theme: str, entries: list[LearningJournalEntry]) -> str:
    titles = [entry.title.strip() for entry in entries if entry.title.strip()][:3]
    lessons = " ".join(entry.lesson.lower() for entry in entries[:5])
    if theme == "cultura":
        if "comic" in lessons or "cómic" in lessons or "superman" in lessons:
            return (
                "Por lo que he aprendido contigo, mi criterio en cultura tira hacia obras con autores reconocibles, "
                "personajes bien construidos y continuidad emocional; no solo datos sueltos."
            )
        return "Por lo que he aprendido contigo, en cultura valoro referencias concretas y criterio, no respuestas neutras."
    if theme == "amistad":
        return "Por lo que he aprendido contigo, una buena amistad aqui significa confianza, discrecion y presencia sin invadir."
    if theme == "trabajo":
        return "Por lo que he aprendido contigo, en trabajo conviene ser claro, concreto y avanzar por pasos verificables."
    if theme == "comportamiento":
        return "Por lo que he aprendido contigo, debo responder con naturalidad, no insistir de mas y corregir rapido cuando me equivoco."
    if theme == "salud":
        return "Por lo que he aprendido contigo, los temas de bienestar piden tacto, calma y nada de dramatizar."
    if theme == "vida":
        return "Por lo que he aprendido contigo, las cosas de vida importan por el contexto y la continuidad, no por sacar charla a toda costa."
    joined = "; ".join(titles)
    return f"Por lo que he aprendido contigo, mi postura en este tema se apoya en: {joined}." if joined else ""


def _maturity_profile(
    *,
    interaction_count: int,
    active_learning_count: int,
    draft_learning_count: int,
    active_stance_count: int,
    learning_counts: dict[str, int],
    style: dict[str, float],
) -> dict[str, Any]:
    correction_pressure = learning_counts.get("negative", 0) + learning_counts.get("correction", 0) + learning_counts.get("stop", 0)
    evidence = (
        min(interaction_count, 80) * 0.35
        + min(active_learning_count, 12) * 3.0
        + min(active_stance_count, 7) * 5.0
        + min(draft_learning_count, 10) * 0.8
        + min(learning_counts.get("positive", 0), 20) * 1.2
        - min(correction_pressure, 20) * 1.1
    )
    score = round(_clamp01(evidence / 100.0), 4)
    if score >= 0.72:
        stage = "criterio_en_formacion"
    elif score >= 0.42:
        stage = "relacion_en_maduracion"
    elif score >= 0.18:
        stage = "aprendizaje_temprano"
    else:
        stage = "arranque"
    strengths: list[str] = []
    if active_learning_count:
        strengths.append("usa aprendizajes aprobados")
    if active_stance_count:
        strengths.append("forma posturas por temas")
    if style.get("caution", 0.5) > 0.55:
        strengths.append("ajusta cautela tras correcciones")
    if interaction_count >= 25:
        strengths.append("tiene continuidad de relacion")
    risks: list[str] = []
    if draft_learning_count > active_learning_count + 3:
        risks.append("muchos detectados pendientes de revisar")
    if correction_pressure > learning_counts.get("positive", 0) + 2:
        risks.append("recibe mas correcciones que aciertos")
    if active_learning_count == 0:
        risks.append("pocos criterios aprobados por el usuario")
    next_growth = "revisar detectados y activar solo los buenos"
    if active_learning_count >= 3 and active_stance_count == 0:
        next_growth = "consolidar posturas por tema"
    elif active_stance_count:
        next_growth = "contrastar posturas con nuevas conversaciones"
    return {
        "score": score,
        "stage": stage,
        "strengths": strengths,
        "risks": risks,
        "next_growth": next_growth,
        "inputs": {
            "interaction_count": interaction_count,
            "active_learning_count": active_learning_count,
            "draft_learning_count": draft_learning_count,
            "active_stance_count": active_stance_count,
            "correction_pressure": correction_pressure,
        },
    }


def _review_key(value: str) -> str:
    value = value.lower()
    value = value.replace("á", "a").replace("é", "e").replace("í", "i").replace("ó", "o").replace("ú", "u").replace("ñ", "n")
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", value)).strip()


def _learning_review(agent_id: str, entries: list[LearningJournalEntry]) -> dict[str, Any]:
    by_theme = {
        theme: {"active": 0, "draft": 0, "archived": 0, "total": 0}
        for theme in ["vida", "cultura", "amistad", "trabajo", "comportamiento", "salud", "otros"]
    }
    drafts: list[LearningJournalEntry] = []
    seen: dict[str, list[str]] = {}
    for entry in entries:
        theme = classify_learning_theme(entry.title, entry.lesson, entry.tags)
        status = entry.status if entry.status in {"active", "draft", "archived"} else "draft"
        by_theme.setdefault(theme, {"active": 0, "draft": 0, "archived": 0, "total": 0})
        by_theme[theme][status] += 1
        by_theme[theme]["total"] += 1
        if status == "draft":
            drafts.append(entry)
        key = _review_key(f"{entry.title} {entry.lesson}")
        if key:
            seen.setdefault(key, []).append(entry.entry_id)
    duplicates = [
        {"key": key[:80], "entry_ids": ids}
        for key, ids in seen.items()
        if len(ids) > 1
    ][:10]
    quality_items = [learning_entry_quality(entry) for entry in entries]
    weak_entries = [
        item
        for item in quality_items
        if float(item.get("score", 1.0)) < 0.7 or item.get("issues")
    ][:15]
    quality_score = (
        round(sum(float(item.get("score", 0.0)) for item in quality_items) / len(quality_items), 3)
        if quality_items
        else 1.0
    )
    next_actions: list[str] = []
    if drafts:
        next_actions.append("Revisar detectados: activar solo lo que de verdad quieras que moldee a amigo.")
    if duplicates:
        next_actions.append("Fusionar duplicados para que una misma pauta no pese dos veces.")
    if weak_entries:
        next_actions.append("Editar aprendizajes debiles: deben ser concretos, revisables y sin absolutos innecesarios.")
    if not next_actions:
        next_actions.append("No hay borradores pendientes; seguir observando conversaciones.")
    oldest_draft = min((entry.created_at for entry in drafts), default=None)
    return {
        "agent_id": agent_id,
        "draft_count": len(drafts),
        "oldest_draft_at": oldest_draft.isoformat() if oldest_draft else None,
        "by_theme": by_theme,
        "duplicates": duplicates,
        "quality": {
            "score": quality_score,
            "weak_count": len(weak_entries),
            "weak_entries": weak_entries,
        },
        "next_actions": next_actions,
        "privacy": "private_review_queue_no_raw_global_export",
    }


def _learning_digest(
    agent_id: str,
    entries: list[LearningJournalEntry],
    stances: dict[str, Any],
    review: dict[str, Any],
) -> dict[str, Any]:
    active = [entry for entry in entries if entry.status == "active"]
    draft = [entry for entry in entries if entry.status == "draft"]
    by_theme: dict[str, dict[str, Any]] = {}
    stance_by_theme = {str(item.get("theme")): item for item in stances.get("stances", []) if isinstance(item, dict)}
    for theme in ["vida", "cultura", "amistad", "trabajo", "comportamiento", "salud", "otros"]:
        theme_entries = [entry for entry in entries if classify_learning_theme(entry.title, entry.lesson, entry.tags) == theme]
        theme_active = [entry for entry in theme_entries if entry.status == "active"]
        theme_draft = [entry for entry in theme_entries if entry.status == "draft"]
        stance = stance_by_theme.get(theme, {})
        by_theme[theme] = {
            "active_count": len(theme_active),
            "draft_count": len(theme_draft),
            "archived_count": len([entry for entry in theme_entries if entry.status == "archived"]),
            "stance_active": bool(stance.get("active")),
            "stance_source": stance.get("source"),
            "sample_titles": [entry.title for entry in theme_entries[:5]],
        }
    gaps = [theme for theme, item in by_theme.items() if int(item["active_count"]) == 0]
    return {
        "agent_id": agent_id,
        "active_count": len(active),
        "draft_count": len(draft),
        "by_theme": by_theme,
        "gaps": gaps,
        "review": {
            "draft_count": review.get("draft_count", 0),
            "duplicate_count": len(review.get("duplicates", [])),
            "next_actions": review.get("next_actions", []),
        },
        "privacy": "private_digest_from_learning_journal_no_conversation_text",
    }


def _select_prompt_learning_entries(
    text: str,
    entries: list[LearningJournalEntry],
    *,
    limit: int = 8,
) -> list[LearningJournalEntry]:
    active = [entry for entry in entries if entry.status == "active"]
    if not active:
        return []
    query_terms = learning_terms(text)
    scored: list[tuple[float, LearningJournalEntry]] = []
    for entry in active:
        theme = classify_learning_theme(entry.title, entry.lesson, entry.tags)
        entry_terms = learning_terms(f"{entry.title} {entry.lesson} {' '.join(entry.tags or [])}")
        overlap = len(query_terms & entry_terms)
        style_bonus = 1.5 if theme in {"comportamiento", "amistad"} else 0.0
        score = float(overlap * 2) + style_bonus
        if not query_terms and theme in {"comportamiento", "amistad"}:
            score += 0.5
        if score > 0:
            scored.append((score, entry))
    scored.sort(key=lambda item: (item[0], item[1].updated_at.isoformat()), reverse=True)
    return [entry for _, entry in scored[:limit]]


class NinoRuntime:
    def __init__(
        self,
        state_store: InMemoryStateStore,
        episode_store: InMemoryEpisodeStore | None = None,
        cold_store: InMemoryColdStore | None = None,
        global_model_store: InMemoryGlobalModelStore | None = None,
        proactive_candidate_store: InMemoryProactiveCandidateStore | None = None,
        graph_store: InMemoryGraphStore | None = None,
        learning_journal_store: InMemoryLearningJournalStore | None = None,
        rss_culture_store: InMemoryRssCultureStore | None = None,
        llm_client: LLMClient | None = None,
    ) -> None:
        self.state_store = state_store
        self.episode_store = episode_store or InMemoryEpisodeStore()
        self.cold_store = cold_store or InMemoryColdStore()
        self.global_model_store = global_model_store or InMemoryGlobalModelStore()
        self.retriever = MemoryRetriever(self.episode_store, self.cold_store)
        self.consolidator = Consolidator(self.cold_store)
        self.proactive_candidate_store = proactive_candidate_store or InMemoryProactiveCandidateStore()
        self.graph_store = graph_store or InMemoryGraphStore()
        self.learning_journal_store = learning_journal_store or InMemoryLearningJournalStore()
        self.rss_culture_store = rss_culture_store or InMemoryRssCultureStore()
        self.llm_client = llm_client if llm_client is not None else build_configured_llm()
        self.proactivity = ProactivityEngine(
            self.episode_store,
            candidate_store=self.proactive_candidate_store,
            llm_client=self.llm_client,
        )

    def _llm_provider(self) -> str | None:
        if self.llm_client is None:
            return None
        return str(getattr(self.llm_client, "provider", "claude"))

    def load_or_init_state(self, agent_id: str) -> AgentState:
        existing = self.state_store.get(agent_id)
        if existing:
            return existing
        world = _default_world_model()
        world["rss_culture"] = self._rss_world_summary(datetime.now(timezone.utc))
        initial = AgentState(
            agent_id=agent_id,
            tick=0,
            drive_vector={
                "curiosity": 0.5, "safety": 0.5, "attachment": 0.5,
                "coherence": 0.5, "exploration": 0.5, "energy": 0.8
            },
            active_goals=[],
            energy=0.8,
            relation_state={
                "proactivity": default_proactivity_state(),
                "action_permissions": _default_permissions(),
            },
            cognitive_time=_default_cognitive_time(),
            self_model=_default_self_model(),
            world_model=world,
            updated_at=datetime.now(timezone.utc),
        )
        self.state_store.put(initial)
        return initial

    def _rss_world_summary(self, now: datetime) -> dict[str, Any]:
        items = self.rss_culture_store.list_items(theme="all", limit=500)
        themes: dict[str, int] = {}
        for item in items:
            themes[item.theme] = themes.get(item.theme, 0) + 1
        return {
            "enabled": True,
            "item_count": len(items),
            "themes": themes,
            "updated_at": now.isoformat() if items else None,
            "privacy": "global_culture_not_private_memory",
        }

    def action_permissions(self, agent_id: str) -> dict[str, Any]:
        state = self.load_or_init_state(agent_id)
        return dict(state.relation_state.get("action_permissions", _default_permissions()))

    def configure_action_permission(
        self,
        agent_id: str,
        action_type: str,
        *,
        allowed: bool,
        delivery: str = "inbox_only",
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        state = self.load_or_init_state(agent_id)
        permissions = dict(state.relation_state.get("action_permissions", _default_permissions()))
        permissions[action_type] = {"allowed": bool(allowed), "delivery": delivery}
        state.relation_state = {**state.relation_state, "action_permissions": permissions}
        state.relation_state = _append_audit_event(
            state.relation_state,
            now=now,
            event_type="permission_configured",
            payload={"action_type": action_type, "allowed": bool(allowed), "delivery": delivery},
        )
        state.updated_at = now
        self.state_store.put(state)
        return {"agent_id": agent_id, "permissions": permissions}

    def enqueue_task(
        self,
        agent_id: str,
        action: dict[str, Any],
        *,
        description: str = "",
        max_pending: int = 20,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        now = now or datetime.now(timezone.utc)
        state = self.load_or_init_state(agent_id)
        queue = list(state.relation_state.get("task_queue", []))
        pending_count = len([item for item in queue if item.get("status") == "pending"])
        if pending_count >= max_pending:
            state.relation_state = _append_audit_event(
                state.relation_state,
                now=now,
                event_type="task_rejected",
                payload={"reason": "task_queue_limit", "max_pending": max_pending},
            )
            state.updated_at = now
            self.state_store.put(state)
            return {"ok": False, "error": "task_queue_limit", "max_pending": max_pending}
        permission = _permission_decision(state.relation_state, action)
        task = {
            "id": str(uuid4()),
            "created_at": now.isoformat(),
            "status": "pending" if permission["allowed"] else "blocked",
            "description": description,
            "action": action,
            "permission": permission,
        }
        queue.append(task)
        state.relation_state = {**state.relation_state, "task_queue": queue[-100:]}
        state.relation_state = _append_audit_event(
            state.relation_state,
            now=now,
            event_type="task_enqueued" if permission["allowed"] else "task_blocked",
            payload={"task_id": task["id"], "permission": permission, "action_type": action.get("type")},
        )
        state.updated_at = now
        self.state_store.put(state)
        return {"ok": permission["allowed"], "task": task}

    def list_tasks(self, agent_id: str) -> list[dict[str, Any]]:
        state = self.load_or_init_state(agent_id)
        return list(state.relation_state.get("task_queue", []))

    def run_next_task(self, agent_id: str, now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now(timezone.utc)
        state = self.load_or_init_state(agent_id)
        queue = list(state.relation_state.get("task_queue", []))
        for task in queue:
            if task.get("status") != "pending":
                continue
            action_result = self.enqueue_proactive_action(agent_id, dict(task.get("action", {})), now=now)
            task["status"] = "blocked" if action_result.get("blocked") else "completed"
            task["completed_at"] = now.isoformat()
            task["result"] = action_result
            state = self.load_or_init_state(agent_id)
            state.relation_state = {**state.relation_state, "task_queue": queue}
            state.relation_state = _append_audit_event(
                state.relation_state,
                now=now,
                event_type="task_ran",
                payload={"task_id": task["id"], "status": task["status"]},
            )
            state.updated_at = now
            self.state_store.put(state)
            return {"ok": task["status"] == "completed", "task": task}
        return {"ok": False, "error": "no_pending_task"}

    def configure_proactivity(
        self,
        agent_id: str,
        settings: ProactivitySettings,
    ) -> AgentState:
        state = self.load_or_init_state(agent_id)
        state.relation_state = configure_proactivity_state(state.relation_state, settings)
        state.updated_at = datetime.now(timezone.utc)
        self.state_store.put(state)
        return state

    def evaluate_proactivity(
        self,
        agent_id: str,
        now: datetime | None = None,
        record_send: bool = True,
    ) -> ProactivityResponse:
        state = self.load_or_init_state(agent_id)
        result = self.proactivity.evaluate(
            agent_id,
            state.relation_state,
            now=now,
            self_model=state.self_model,
            world_model=state.world_model,
            global_model=self.global_model_store.get(),
            checkin_prior=starting_prior("checkin", "dia_neutro", self.global_model_store),
            drive_vector=state.drive_vector,
            active_goals=state.active_goals,
        )
        if result.should_send and record_send:
            sent_at = now or datetime.now(timezone.utc)
            if result.action is not None:
                self.enqueue_proactive_action(agent_id, result.action, now=sent_at)
                state = self.load_or_init_state(agent_id)
                payload = result.action.get("payload", {})
                candidate_id = payload.get("proactive_candidate_id")
                if candidate_id:
                    self.proactive_candidate_store.mark_delivered(str(candidate_id), sent_at)
                event_id = payload.get("temporal_event_id")
                if event_id:
                    state.relation_state = mark_temporal_event_reminded(
                        state.relation_state,
                        str(event_id),
                        sent_at,
                    )
                state.relation_state = record_proactive_source(
                    state.relation_state,
                    str(payload.get("proactive_source_key") or ""),
                    sent_at,
                )
            state.relation_state = record_proactive_send(state.relation_state, sent_at)
            state.updated_at = sent_at
            self.state_store.put(state)
        return result

    def reset_agent(self, agent_id: str) -> dict[str, Any]:
        deleted: dict[str, Any] = {"agent_id": agent_id}
        if hasattr(self.state_store, "delete"):
            self.state_store.delete(agent_id)
            deleted["state"] = True
        if hasattr(self.episode_store, "delete_for_agent"):
            deleted["episodes"] = self.episode_store.delete_for_agent(agent_id)
        if hasattr(self.cold_store, "delete_for_agent"):
            deleted["cold_memory"] = self.cold_store.delete_for_agent(agent_id)
        if hasattr(self.proactive_candidate_store, "delete_for_agent"):
            deleted["proactive_candidates"] = self.proactive_candidate_store.delete_for_agent(agent_id)
        if hasattr(self.graph_store, "delete_for_agent"):
            deleted["memory_graph"] = self.graph_store.delete_for_agent(agent_id)
        if hasattr(self.learning_journal_store, "delete_for_agent"):
            deleted["learning_journal"] = self.learning_journal_store.delete_for_agent(agent_id)
        return deleted

    def list_agents(self) -> list[str]:
        ids: set[str] = set()
        for store in (self.state_store, self.episode_store, self.cold_store):
            if hasattr(store, "list_agent_ids"):
                ids.update(store.list_agent_ids())
        return sorted(ids)

    def prune_agents(
        self,
        prefixes: list[str] | None = None,
        agent_ids: list[str] | None = None,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        prefixes = [prefix for prefix in (prefixes or []) if prefix]
        explicit_ids = {agent_id for agent_id in (agent_ids or []) if agent_id}
        matched = [
            agent_id
            for agent_id in self.list_agents()
            if agent_id in explicit_ids or any(agent_id.startswith(prefix) for prefix in prefixes)
        ]
        deleted = []
        if not dry_run:
            for agent_id in matched:
                deleted.append(self.reset_agent(agent_id))
        return {
            "dry_run": dry_run,
            "prefixes": prefixes,
            "agent_ids": sorted(explicit_ids),
            "matched": matched,
            "deleted": deleted,
        }

    def delete_episode(self, agent_id: str, episode_id: str) -> dict[str, Any]:
        deleted = False
        if hasattr(self.episode_store, "delete_episode"):
            deleted = bool(self.episode_store.delete_episode(agent_id, episode_id))
        return {"agent_id": agent_id, "episode_id": episode_id, "deleted": deleted}

    def delete_memory_fact(self, agent_id: str, fact_id: str) -> dict[str, Any]:
        deleted = False
        if hasattr(self.cold_store, "delete_fact"):
            deleted = bool(self.cold_store.delete_fact(agent_id, fact_id))
        return {"agent_id": agent_id, "fact_id": fact_id, "deleted": deleted}

    def list_temporal_events(self, agent_id: str) -> list[dict[str, Any]]:
        state = self.load_or_init_state(agent_id)
        return [dict(event) for event in state.relation_state.get("temporal_events", []) if isinstance(event, dict)]

    def update_temporal_event(self, agent_id: str, event_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        state = self.load_or_init_state(agent_id)
        events = self.list_temporal_events(agent_id)
        updated = []
        found: dict[str, Any] | None = None
        allowed = {
            "text",
            "due_at",
            "next_due_at",
            "status",
            "lead_time_hours",
            "reminder_offset_minutes",
            "reminder_status",
            "recurrence",
            "recurrence_interval_days",
        }
        for event in events:
            if str(event.get("id")) == event_id:
                for key in allowed:
                    if key in patch:
                        event[key] = patch[key]
                if event.get("status") == "reminded" and event.get("recurrence"):
                    event["status"] = "pending"
                event["updated_at"] = now.isoformat()
                found = dict(event)
            updated.append(event)
        if found is None:
            return {"agent_id": agent_id, "event_id": event_id, "updated": False, "error": "event_not_found"}
        state.relation_state = {**state.relation_state, "temporal_events": updated}
        state.updated_at = now
        self.state_store.put(state)
        return {"agent_id": agent_id, "event_id": event_id, "updated": True, "event": found}

    def delete_temporal_event(self, agent_id: str, event_id: str) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        state = self.load_or_init_state(agent_id)
        events = self.list_temporal_events(agent_id)
        kept = [event for event in events if str(event.get("id")) != event_id]
        deleted = len(kept) != len(events)
        if deleted:
            state.relation_state = {**state.relation_state, "temporal_events": kept}
            state.updated_at = now
            self.state_store.put(state)
        return {"agent_id": agent_id, "event_id": event_id, "deleted": deleted}

    def list_learning_journal(
        self,
        agent_id: str,
        status: str = "all",
        *,
        theme: str = "all",
        query: str = "",
        source: str = "all",
    ) -> list[LearningJournalEntry]:
        entries = self.learning_journal_store.list_for_agent(agent_id, status=status)
        if theme and theme != "all":
            entries = [entry for entry in entries if classify_learning_theme(entry.title, entry.lesson, entry.tags) == theme]
        if source and source != "all":
            entries = [entry for entry in entries if entry.source == source]
        terms = learning_terms(query)
        if terms:
            entries = [
                entry
                for entry in entries
                if terms & learning_terms(f"{entry.title} {entry.lesson} {' '.join(entry.tags or [])}")
            ]
        return entries

    def add_learning_journal_entry(
        self,
        agent_id: str,
        payload: dict[str, Any],
        now: datetime | None = None,
    ) -> dict[str, Any]:
        now = now or datetime.now(timezone.utc)
        try:
            entry = make_manual_learning_entry(
                agent_id=agent_id,
                lesson=str(payload.get("lesson", "")),
                title=str(payload.get("title", "")).strip() or None,
                tags=[str(tag) for tag in payload.get("tags", ["manual"]) if str(tag).strip()]
                if isinstance(payload.get("tags", ["manual"]), list)
                else ["manual"],
                status=str(payload.get("status", "active")),
                now=now,
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        self.learning_journal_store.upsert(entry)
        state = self.load_or_init_state(agent_id)
        self._refresh_curiosity_state(state, now)
        state.relation_state = _append_audit_event(
            state.relation_state,
            now=now,
            event_type="learning_journal_added",
            payload={"entry_id": entry.entry_id, "source": entry.source, "status": entry.status},
        )
        state.updated_at = now
        self.state_store.put(state)
        return {"ok": True, "entry": asdict(entry)}

    def update_learning_journal_entry(
        self,
        agent_id: str,
        entry_id: str,
        patch: dict[str, Any],
        now: datetime | None = None,
    ) -> dict[str, Any]:
        now = now or datetime.now(timezone.utc)
        entry = self.learning_journal_store.get(agent_id, entry_id)
        if entry is None:
            return {"ok": False, "error": "learning_journal_entry_not_found", "entry_id": entry_id}
        allowed_status = {"active", "draft", "archived"}
        if "title" in patch:
            entry.title = str(patch["title"]).strip()[:90] or entry.title
        if "lesson" in patch:
            lesson = str(patch["lesson"]).strip()
            if lesson:
                entry.lesson = lesson[:500]
        if "status" in patch:
            status = str(patch["status"])
            if status not in allowed_status:
                return {"ok": False, "error": "invalid_learning_journal_status", "entry_id": entry_id}
            entry.status = status
        if "tags" in patch and isinstance(patch["tags"], list):
            entry.tags = [str(tag)[:40] for tag in patch["tags"] if str(tag).strip()][:6]
        entry.updated_at = now
        self.learning_journal_store.upsert(entry)
        state = self.load_or_init_state(agent_id)
        self._refresh_curiosity_state(state, now)
        state.relation_state = _append_audit_event(
            state.relation_state,
            now=now,
            event_type="learning_journal_updated",
            payload={"entry_id": entry.entry_id, "status": entry.status},
        )
        state.updated_at = now
        self.state_store.put(state)
        return {"ok": True, "entry": asdict(entry)}

    def delete_learning_journal_entry(
        self,
        agent_id: str,
        entry_id: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        now = now or datetime.now(timezone.utc)
        entry = self.learning_journal_store.get(agent_id, entry_id)
        if entry is None:
            return {"ok": False, "error": "learning_journal_entry_not_found", "entry_id": entry_id}
        deleted = bool(self.learning_journal_store.delete(agent_id, entry_id))
        state = self.load_or_init_state(agent_id)
        self._refresh_curiosity_state(state, now)
        state.relation_state = _append_audit_event(
            state.relation_state,
            now=now,
            event_type="learning_journal_deleted",
            payload={"entry_id": entry_id, "title": entry.title, "status": entry.status, "deleted": deleted},
        )
        state.updated_at = now
        self.state_store.put(state)
        return {"ok": deleted, "deleted": deleted, "entry_id": entry_id}

    def bulk_update_learning_journal(
        self,
        agent_id: str,
        payload: dict[str, Any],
        now: datetime | None = None,
    ) -> dict[str, Any]:
        now = now or datetime.now(timezone.utc)
        entry_ids = payload.get("entry_ids", [])
        if not isinstance(entry_ids, list):
            return {"ok": False, "error": "entry_ids_must_be_list"}
        status = str(payload.get("status", "")).strip()
        if status not in {"active", "draft", "archived"}:
            return {"ok": False, "error": "invalid_learning_journal_status"}
        updated: list[dict[str, Any]] = []
        missing: list[str] = []
        for raw_entry_id in entry_ids[:100]:
            entry_id = str(raw_entry_id)
            entry = self.learning_journal_store.get(agent_id, entry_id)
            if entry is None:
                missing.append(entry_id)
                continue
            entry.status = status
            entry.updated_at = now
            self.learning_journal_store.upsert(entry)
            updated.append(asdict(entry))
        state = self.load_or_init_state(agent_id)
        self._refresh_curiosity_state(state, now)
        state.relation_state = _append_audit_event(
            state.relation_state,
            now=now,
            event_type="learning_journal_bulk_updated",
            payload={"status": status, "updated_count": len(updated), "missing_count": len(missing)},
        )
        state.updated_at = now
        self.state_store.put(state)
        return {"ok": True, "updated_count": len(updated), "missing": missing, "entries": updated}

    def merge_learning_journal_entries(
        self,
        agent_id: str,
        payload: dict[str, Any],
        now: datetime | None = None,
    ) -> dict[str, Any]:
        now = now or datetime.now(timezone.utc)
        target_id = str(payload.get("target_id", "")).strip()
        source_ids = [str(item).strip() for item in payload.get("source_ids", []) if str(item).strip()] if isinstance(payload.get("source_ids", []), list) else []
        target = self.learning_journal_store.get(agent_id, target_id)
        if target is None:
            return {"ok": False, "error": "learning_journal_entry_not_found", "entry_id": target_id}
        sources: list[LearningJournalEntry] = []
        missing: list[str] = []
        for entry_id in source_ids[:25]:
            if entry_id == target_id:
                continue
            entry = self.learning_journal_store.get(agent_id, entry_id)
            if entry is None:
                missing.append(entry_id)
                continue
            sources.append(entry)
        if not sources:
            return {"ok": False, "error": "no_merge_sources", "missing": missing}
        title = str(payload.get("title", "")).strip()
        lesson = str(payload.get("lesson", "")).strip()
        if title:
            target.title = title[:90]
        if lesson:
            target.lesson = lesson[:500]
        else:
            pieces = [target.lesson, *(entry.lesson for entry in sources)]
            merged: list[str] = []
            seen: set[str] = set()
            for piece in pieces:
                key = _review_key(piece)
                if not key or key in seen:
                    continue
                seen.add(key)
                merged.append(piece)
            target.lesson = " / ".join(merged)[:500]
        tags: list[str] = []
        for entry in [target, *sources]:
            for tag in entry.tags or []:
                if tag not in tags:
                    tags.append(tag)
        target.tags = tags[:8]
        target.updated_at = now
        self.learning_journal_store.upsert(target)
        archived: list[str] = []
        for entry in sources:
            entry.status = "archived"
            entry.updated_at = now
            self.learning_journal_store.upsert(entry)
            archived.append(entry.entry_id)
        state = self.load_or_init_state(agent_id)
        self._refresh_curiosity_state(state, now)
        state.relation_state = _append_audit_event(
            state.relation_state,
            now=now,
            event_type="learning_journal_merged",
            payload={"target_id": target.entry_id, "archived_count": len(archived), "missing_count": len(missing)},
        )
        state.updated_at = now
        self.state_store.put(state)
        return {"ok": True, "target": asdict(target), "archived": archived, "missing": missing}

    def learning_review(self, agent_id: str) -> dict[str, Any]:
        return _learning_review(agent_id, self.learning_journal_store.list_for_agent(agent_id, status="all"))

    def learning_digest(self, agent_id: str) -> dict[str, Any]:
        entries = self.learning_journal_store.list_for_agent(agent_id, status="all")
        stances = self.learning_stances(agent_id)
        review = _learning_review(agent_id, entries)
        return _learning_digest(agent_id, entries, stances, review)

    def curiosity_topics(self, agent_id: str) -> dict[str, Any]:
        state = self.load_or_init_state(agent_id)
        entries = self.learning_journal_store.list_for_agent(agent_id, status="all")
        stances = self.learning_stances(agent_id)
        curiosity = _curiosity_maturity_summary(state.world_model, entries)
        mastery = _theme_mastery_summary(entries, stances)
        priorities = _learning_priorities(entries, curiosity, mastery)
        contradictions = _learning_contradiction_watch(entries)
        growth = _growth_compass(
            maturity_profile=_maturity_profile(
                interaction_count=int(state.relation_state.get("interaction_count", 0)),
                active_learning_count=len([entry for entry in entries if entry.status == "active"]),
                draft_learning_count=len([entry for entry in entries if entry.status == "draft"]),
                active_stance_count=len([item for item in stances["stances"] if item.get("active")]),
                learning_counts=dict(dict(state.relation_state.get("relationship_learning", {})).get("counts", {})),
                style=dict(dict(state.relation_state.get("relationship_learning", {})).get("response_style", RELATIONSHIP_DEFAULT_STYLE)),
            ),
            curiosity=curiosity,
            mastery=mastery,
            priorities=priorities,
            contradictions=contradictions,
        )
        return {
            "agent_id": agent_id,
            "curiosity": curiosity,
            "theme_mastery": mastery,
            "learning_priorities": priorities,
            "contradiction_watch": contradictions,
            "growth_compass": growth,
            "maturity_reflections": list(state.relation_state.get("maturity_reflections", []))[-10:],
        }

    def update_curiosity_topic(
        self,
        agent_id: str,
        topic_key: str,
        payload: dict[str, Any],
        now: datetime | None = None,
    ) -> dict[str, Any]:
        now = now or datetime.now(timezone.utc)
        status = str(payload.get("status", "")).strip()
        if status not in {"open", "grounded", "archived"}:
            return {"ok": False, "error": "invalid_curiosity_topic_status"}
        state = self.load_or_init_state(agent_id)
        topics = [dict(item) for item in state.world_model.get("curiosity_topics", []) if isinstance(item, dict)]
        found: dict[str, Any] | None = None
        for item in topics:
            item["key"] = _curiosity_key(str(item.get("topic", "")))
            if item["key"] != topic_key:
                continue
            item["status"] = status
            item["updated_at"] = now.isoformat()
            if "note" in payload:
                item["note"] = str(payload.get("note", "")).strip()[:240]
            found = item
            break
        if found is None:
            return {"ok": False, "error": "curiosity_topic_not_found", "topic_key": topic_key}
        state.world_model = {**state.world_model, "curiosity_topics": topics[-50:]}
        state.relation_state = _append_audit_event(
            state.relation_state,
            now=now,
            event_type="curiosity_topic_updated",
            payload={"topic_key": topic_key, "status": status},
        )
        state.updated_at = now
        self.state_store.put(state)
        return {"ok": True, "topic": found, "curiosity": self.curiosity_topics(agent_id)}

    def _refresh_curiosity_state(self, state: AgentState, now: datetime) -> None:
        entries = self.learning_journal_store.list_for_agent(state.agent_id, status="all")
        world, grounded = _refresh_curiosity_topics_from_learning(dict(state.world_model), entries, now)
        state.world_model = world
        state.relation_state = _append_maturity_reflections(state.relation_state, grounded_topics=grounded, now=now)

    def learning_stances(self, agent_id: str) -> dict[str, Any]:
        state = self.load_or_init_state(agent_id)
        edited = dict(state.relation_state.get("learning_stances", {}))
        active_entries = self.learning_journal_store.list_for_agent(agent_id, status="active")
        by_theme: dict[str, list[LearningJournalEntry]] = {theme: [] for theme in sorted(THEMES)}
        for entry in active_entries:
            theme = classify_learning_theme(entry.title, entry.lesson, entry.tags)
            by_theme.setdefault(theme, []).append(entry)
        stances: list[dict[str, Any]] = []
        for theme in ["vida", "cultura", "amistad", "trabajo", "comportamiento", "salud", "otros"]:
            override = edited.get(theme) if isinstance(edited.get(theme), dict) else {}
            entries = by_theme.get(theme, [])
            stance_text = str(override.get("text", "")).strip()
            source = "edited" if stance_text else "derived"
            if not stance_text and entries:
                stance_text = _derive_stance_text(theme, entries)
            stances.append(
                {
                    "theme": theme,
                    "text": stance_text,
                    "source": source,
                    "active": bool(override.get("active", True)) and bool(stance_text),
                    "evidence_count": len(entries),
                    "evidence_titles": [entry.title for entry in entries[:5]],
                    "updated_at": str(override.get("updated_at", "")),
                }
            )
        return {
            "agent_id": agent_id,
            "stances": stances,
            "privacy": "private_user_scope_derived_from_active_learning_journal",
        }

    def update_learning_stance(
        self,
        agent_id: str,
        theme: str,
        payload: dict[str, Any],
        now: datetime | None = None,
    ) -> dict[str, Any]:
        if theme not in THEMES:
            return {"ok": False, "error": "invalid_learning_stance_theme", "theme": theme}
        now = now or datetime.now(timezone.utc)
        state = self.load_or_init_state(agent_id)
        stances = dict(state.relation_state.get("learning_stances", {}))
        text = str(payload.get("text", "")).strip()[:700]
        active = bool(payload.get("active", True))
        if not text and not active:
            stances[theme] = {"text": "", "active": False, "updated_at": now.isoformat()}
        elif text:
            stances[theme] = {"text": text, "active": active, "updated_at": now.isoformat()}
        else:
            stances.pop(theme, None)
        state.relation_state = {**state.relation_state, "learning_stances": stances}
        state.relation_state = _append_audit_event(
            state.relation_state,
            now=now,
            event_type="learning_stance_updated",
            payload={"theme": theme, "active": active, "edited": bool(text)},
        )
        state.updated_at = now
        self.state_store.put(state)
        return {"ok": True, "stance": self.learning_stances(agent_id)}

    def rss_culture(self, theme: str = "all", limit: int = 20) -> dict[str, Any]:
        sources = self.rss_culture_store.list_sources()
        items = self.rss_culture_store.list_items(theme=theme, limit=limit)
        themes: dict[str, int] = {}
        for item in self.rss_culture_store.list_items(theme="all", limit=500):
            themes[item.theme] = themes.get(item.theme, 0) + 1
        return {
            "sources": [asdict(source) for source in sources],
            "items": [asdict(item) for item in items],
            "source_count": len(sources),
            "active_source_count": len([source for source in sources if source.active]),
            "item_count": sum(themes.values()),
            "themes": themes,
            "privacy": "global_culture_sources_not_private_memory",
        }

    def add_rss_source(self, payload: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now(timezone.utc)
        name = str(payload.get("name", "")).strip()[:120]
        url = str(payload.get("url", "")).strip()[:500]
        if not name or not url:
            return {"ok": False, "error": "rss_source_requires_name_and_url"}
        theme = normalize_theme(str(payload.get("theme", "otros")))
        trust = _clamp01(float(payload.get("trust", 0.6)))
        source = RssSource(
            source_id=source_id_from_name(name),
            name=name,
            url=url,
            theme=theme,
            active=bool(payload.get("active", True)),
            trust=trust,
            created_at=now,
            updated_at=now,
        )
        self.rss_culture_store.upsert_source(source)
        return {"ok": True, "source": asdict(source)}

    def update_rss_source(self, source_id: str, payload: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now(timezone.utc)
        source = self.rss_culture_store.get_source(source_id)
        if source is None:
            return {"ok": False, "error": "rss_source_not_found", "source_id": source_id}
        if "name" in payload:
            source.name = str(payload["name"]).strip()[:120] or source.name
        if "url" in payload:
            source.url = str(payload["url"]).strip()[:500] or source.url
        if "theme" in payload:
            source.theme = normalize_theme(str(payload["theme"]))
        if "active" in payload:
            source.active = bool(payload["active"])
        if "trust" in payload:
            source.trust = _clamp01(float(payload["trust"]))
        source.updated_at = now
        self.rss_culture_store.upsert_source(source)
        return {"ok": True, "source": asdict(source)}

    def delete_rss_source(self, source_id: str) -> dict[str, Any]:
        deleted = self.rss_culture_store.delete_source(source_id)
        return {"ok": bool(deleted), "deleted": deleted, "source_id": source_id}

    def import_rss_source(self, source_id: str, *, xml_text: str | None = None, now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now(timezone.utc)
        source = self.rss_culture_store.get_source(source_id)
        if source is None:
            return {"ok": False, "error": "rss_source_not_found", "source_id": source_id}
        if not source.active:
            return {"ok": False, "error": "rss_source_inactive", "source_id": source_id}
        try:
            xml = xml_text if xml_text is not None else fetch_rss_xml(source.url)
            parsed = parse_rss_items(xml, source_id=source.source_id, theme=source.theme, fetched_at=now)
        except Exception as exc:
            return {"ok": False, "error": "rss_import_failed", "detail": exc.__class__.__name__}
        added = 0
        for item in parsed:
            if self.rss_culture_store.upsert_item(item):
                added += 1
        source.updated_at = now
        self.rss_culture_store.upsert_source(source)
        self._refresh_rss_world_model(now)
        return {"ok": True, "source_id": source_id, "parsed_count": len(parsed), "added_count": added}

    def import_all_rss_sources(self, now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now(timezone.utc)
        results = [self.import_rss_source(source.source_id, now=now) for source in self.rss_culture_store.list_sources(active_only=True)]
        return {
            "ok": all(result.get("ok") for result in results) if results else True,
            "results": results,
            "added_count": sum(int(result.get("added_count", 0)) for result in results),
        }

    def _refresh_rss_world_model(self, now: datetime) -> None:
        for agent_id in self.state_store.list_agent_ids():
            state = self.load_or_init_state(agent_id)
            state.world_model = {
                **state.world_model,
                "rss_culture": self._rss_world_summary(now),
            }
            state.updated_at = now
            self.state_store.put(state)

    def _record_maturity_history(self, state: AgentState, now: datetime) -> None:
        entries = self.learning_journal_store.list_for_agent(state.agent_id, status="all")
        active_journal = [entry for entry in entries if entry.status == "active"]
        draft_journal = [entry for entry in entries if entry.status == "draft"]
        stances = self.learning_stances(state.agent_id)
        active_stances = [item for item in stances["stances"] if item.get("active")]
        learning = dict(state.relation_state.get("relationship_learning", {}))
        counts = {key: int(value) for key, value in dict(learning.get("counts", {})).items()}
        style = dict(RELATIONSHIP_DEFAULT_STYLE)
        style.update({key: float(value) for key, value in dict(learning.get("response_style", {})).items() if key in style})
        profile = _maturity_profile(
            interaction_count=int(state.relation_state.get("interaction_count", 0)),
            active_learning_count=len(active_journal),
            draft_learning_count=len(draft_journal),
            active_stance_count=len(active_stances),
            learning_counts=counts,
            style=style,
        )
        history = [item for item in state.relation_state.get("maturity_history", []) if isinstance(item, dict)]
        snapshot = {
            "at": now.isoformat(),
            "score": profile["score"],
            "stage": profile["stage"],
            "active_learning_count": len(active_journal),
            "draft_learning_count": len(draft_journal),
            "active_stance_count": len(active_stances),
            "correction_pressure": profile["inputs"]["correction_pressure"],
        }
        if not history or any(history[-1].get(key) != snapshot[key] for key in ("score", "stage", "active_learning_count", "draft_learning_count", "active_stance_count", "correction_pressure")):
            history.append(snapshot)
        state.relation_state = {**state.relation_state, "maturity_history": history[-50:]}

    def retrieve_memory(self, agent_id: str, request: RetrieveRequest) -> RetrieveResponse:
        out = self.retriever.retrieve(agent_id=agent_id, request=request, top_k=5)
        graph = graph_candidates(self.graph_store, self.episode_store, agent_id, request.query_intent, limit=3)
        existing = {candidate.source_episode_id for candidate in out.memory_candidates}
        merged = list(out.memory_candidates)
        for candidate in graph:
            if candidate.source_episode_id not in existing:
                merged.append(candidate)
                existing.add(candidate.source_episode_id)
        merged.sort(key=lambda candidate: candidate.score, reverse=True)
        out.memory_candidates = merged[:5]
        return out

    def build_narrative(self, agent_id: str) -> dict[str, Any]:
        state = self.load_or_init_state(agent_id)
        relation = state.relation_state
        self_model = state.self_model
        world_model = state.world_model
        name = relation.get("user_name", "el usuario")
        preferences = sorted(relation.get("preferences", {}).keys())
        concepts = sorted(
            world_model.get("concept_counts", {}).items(),
            key=lambda item: item[1],
            reverse=True,
        )[:5]
        concept_names = [key for key, _ in concepts]
        reflections = list(self_model.get("dream_reflections", []))
        affect = self_model.get("affect_state", {})

        summary_parts = [
            f"Soy amigo en etapa {self_model.get('identity_stage', 'early_childhood')}.",
            f"He vivido {int(state.cognitive_time.get('age_ticks', 0))} ticks con {name}.",
        ]
        if preferences:
            summary_parts.append(f"Recuerdo preferencias como: {', '.join(preferences[:4])}.")
        if concept_names:
            summary_parts.append(f"Mi mundo reciente se organiza alrededor de: {', '.join(concept_names)}.")
        if reflections:
            summary_parts.append(f"Última reflexión de sueño: {reflections[-1].get('summary', '')}")
        if affect:
            summary_parts.append(f"Mi estado afectivo interno está en modo {affect.get('mood', 'stable')}.")

        return {
            "agent_id": agent_id,
            "summary": " ".join(summary_parts),
            "identity_stage": self_model.get("identity_stage", "early_childhood"),
            "maturity": state.cognitive_time.get("maturity", 0.0),
            "active_goals": list(state.active_goals),
            "known_user": name,
            "preferences": preferences,
            "dominant_concepts": concept_names,
            "dream_reflection_count": len(reflections),
            "affect_state": affect,
        }

    def relationship_dashboard(self, agent_id: str) -> dict[str, Any]:
        state = self.load_or_init_state(agent_id)
        relation = state.relation_state
        metrics = self.metrics(agent_id)
        learning = dict(relation.get("relationship_learning", {}))
        counts = {
            "positive": int(dict(learning.get("counts", {})).get("positive", 0)),
            "negative": int(dict(learning.get("counts", {})).get("negative", 0)),
            "correction": int(dict(learning.get("counts", {})).get("correction", 0)),
            "stop": int(dict(learning.get("counts", {})).get("stop", 0)),
            "continuity_miss": int(dict(learning.get("counts", {})).get("continuity_miss", 0)),
        }
        style = dict(RELATIONSHIP_DEFAULT_STYLE)
        style.update({key: float(value) for key, value in dict(learning.get("response_style", {})).items() if key in style})
        recent_signals = [
            {
                "outcome": str(item.get("outcome", "")),
                "signal": str(item.get("signal", "")),
                "observed_at": str(item.get("observed_at", "")),
            }
            for item in list(learning.get("recent", []))[-10:]
            if isinstance(item, dict)
        ]
        proactivity = dict(relation.get("proactivity", {}))
        inbox = self.list_proactive_inbox(agent_id)
        open_questions = list(state.world_model.get("open_questions", []))
        curiosity_topics = [
            dict(item)
            for item in list(state.world_model.get("curiosity_topics", []))[-10:]
            if isinstance(item, dict)
        ]
        journal_entries = self.learning_journal_store.list_for_agent(agent_id, status="all")
        active_journal = [entry for entry in journal_entries if entry.status == "active"]
        draft_journal = [entry for entry in journal_entries if entry.status == "draft"]
        stances = self.learning_stances(agent_id)
        review = _learning_review(agent_id, journal_entries)
        active_stances = [item for item in stances["stances"] if item.get("active")]
        maturity_profile = _maturity_profile(
            interaction_count=int(relation.get("interaction_count", 0)),
            active_learning_count=len(active_journal),
            draft_learning_count=len(draft_journal),
            active_stance_count=len(active_stances),
            learning_counts=counts,
            style=style,
        )
        curiosity_summary = _curiosity_maturity_summary(state.world_model, journal_entries)
        theme_mastery = _theme_mastery_summary(journal_entries, stances)
        learning_priorities = _learning_priorities(journal_entries, curiosity_summary, theme_mastery)
        contradiction_watch = _learning_contradiction_watch(journal_entries)
        growth_compass = _growth_compass(
            maturity_profile=maturity_profile,
            curiosity=curiosity_summary,
            mastery=theme_mastery,
            priorities=learning_priorities,
            contradictions=contradiction_watch,
        )
        return {
            "agent_id": agent_id,
            "privacy": {
                "raw_conversation_included": False,
                "private_scope": "telegram_group" if agent_id.startswith("telegram-group-") else "user_private",
            },
            "maturity": {
                "age_ticks": state.cognitive_time.get("age_ticks", 0.0),
                "experience_mass": state.cognitive_time.get("experience_mass", 0.0),
                "maturity": state.cognitive_time.get("maturity", 0.0),
                "interaction_count": int(relation.get("interaction_count", 0)),
            },
            "maturity_profile": maturity_profile,
            "maturity_history": list(relation.get("maturity_history", []))[-20:],
            "maturity_reflections": list(relation.get("maturity_reflections", []))[-10:],
            "growth_compass": growth_compass,
            "relationship_learning": {
                "counts": counts,
                "last_outcome": learning.get("last_outcome"),
                "last_signal_at": learning.get("last_signal_at"),
                "recent_signals": recent_signals,
            },
            "active_conversation_thread": {
                "present": _active_thread_context(relation) is not None,
                "turn_count": int((_active_thread_context(relation) or {}).get("turn_count", 0)),
                "topic_terms": list((_active_thread_context(relation) or {}).get("topic_terms", []))[-8:],
                "updated_at": (_active_thread_context(relation) or {}).get("updated_at"),
            },
            "response_style": {key: round(float(value), 4) for key, value in style.items()},
            "memory": {
                "episode_count": metrics["episode_count"],
                "cold_memory_count": metrics["cold_memory_count"],
                "learning_journal_count": len(active_journal),
                "preference_count": metrics["preference_count"],
                "dominant_concepts": self.build_narrative(agent_id)["dominant_concepts"],
            },
            "learning_journal": {
                "active_count": len(active_journal),
                "draft_count": len(draft_journal),
                "total_count": len(journal_entries),
                "recent_entries": [asdict(entry) for entry in journal_entries[:8]],
                "scope": "private_editable_user_journal",
            },
            "learning_stances": stances,
            "learning_review": review,
            "learning_digest": _learning_digest(agent_id, journal_entries, stances, review),
            "theme_mastery": theme_mastery,
            "learning_priorities": learning_priorities,
            "contradiction_watch": contradiction_watch,
            "proactivity": {
                "consent": proactivity.get("consent", "unknown"),
                "pending_inbox_count": len([item for item in inbox if not item.get("delivered", False)]),
                "active_start_hour": proactivity.get("active_start_hour"),
                "active_end_hour": proactivity.get("active_end_hour"),
            },
            "conversation": {
                "open_question_count": len(open_questions),
                "recent_open_questions": [str(item)[:120] for item in open_questions[-5:]],
                "curiosity_topic_count": len(curiosity_topics),
                "curiosity_topics": curiosity_topics,
                "curiosity": curiosity_summary,
                "quality": self.evaluate_conversation_quality(agent_id),
            },
            "group_maturity": _to_group_maturity_dashboard(relation),
            "notes": [
                "Los aciertos, fallos y limites se guardan como senales agregadas de relacion.",
                "El dashboard no incluye texto crudo de conversaciones ni secretos.",
                "Si aumentan negative/stop, amigo sube cautela y brevedad y baja iniciativa.",
            ],
        }

    def export_agent(self, agent_id: str) -> dict[str, Any]:
        state = self.load_or_init_state(agent_id)
        episodes = self.episode_store.list_for_agent(agent_id)
        facts = self.cold_store.list_for_agent(agent_id)
        return {
            "schema_version": 1,
            "agent_id": agent_id,
            "state": asdict(state),
            "episodes": [asdict(episode) for episode in episodes],
            "memory_facts": [asdict(fact) for fact in facts],
        }

    def export_agent_safe(self, agent_id: str) -> dict[str, Any]:
        payload = self.export_agent(agent_id)
        safe = {
            "schema_version": payload["schema_version"],
            "agent_id": payload["agent_id"],
            "state": payload["state"],
            "episodes": [],
            "memory_facts": payload["memory_facts"],
            "redacted": True,
        }
        relation = dict(safe["state"].get("relation_state", {}))
        relation.pop("user_name", None)
        relation.pop("user_name_updated_at", None)
        safe["state"]["relation_state"] = relation
        for episode in payload["episodes"]:
            redacted = dict(episode)
            redacted["text"] = _redact_text(redacted.get("text", ""))
            safe["episodes"].append(redacted)
        return safe

    def import_agent(self, payload: dict[str, Any], replace: bool = False) -> dict[str, Any]:
        agent_id = str(payload["agent_id"])
        if replace:
            self.reset_agent(agent_id)

        raw_state = payload.get("state")
        if raw_state:
            state = AgentState(
                agent_id=agent_id,
                tick=int(raw_state.get("tick", 0)),
                drive_vector=dict(raw_state.get("drive_vector", {})),
                active_goals=list(raw_state.get("active_goals", [])),
                energy=float(raw_state.get("energy", 0.8)),
                relation_state=dict(raw_state.get("relation_state", {})),
                cognitive_time=dict(raw_state.get("cognitive_time", {})),
                self_model=dict(raw_state.get("self_model", {})),
                world_model=dict(raw_state.get("world_model", {})),
                updated_at=_parse_datetime(raw_state.get("updated_at")),
            )
            self.state_store.put(state)

        episode_count = 0
        for raw in payload.get("episodes", []):
            self.episode_store.append(
                Episode(
                    episode_id=str(raw["episode_id"]),
                    agent_id=agent_id,
                    timestamp=_parse_datetime(raw["timestamp"]),
                    text=str(raw.get("text", "")),
                    intent=str(raw.get("intent", "unknown")),
                    salience=float(raw.get("salience", 0.5)),
                    confidence=float(raw.get("confidence", 0.8)),
                )
            )
            episode_count += 1

        fact_count = 0
        for raw in payload.get("memory_facts", []):
            self.cold_store.upsert(
                MemoryFact(
                    fact_id=str(raw["fact_id"]),
                    agent_id=agent_id,
                    key=str(raw["key"]),
                    value=str(raw["value"]),
                    confidence=float(raw.get("confidence", 0.8)),
                    source_episode_id=str(raw.get("source_episode_id", "")),
                    valid_from=_parse_datetime(raw["valid_from"]),
                    valid_to=_parse_datetime(raw.get("valid_to")) if raw.get("valid_to") else None,
                )
            )
            fact_count += 1

        return {
            "agent_id": agent_id,
            "imported_state": raw_state is not None,
            "imported_episodes": episode_count,
            "imported_memory_facts": fact_count,
            "replace": replace,
        }

    def metrics(self, agent_id: str) -> dict[str, Any]:
        state = self.load_or_init_state(agent_id)
        episodes = self.episode_store.list_for_agent(agent_id)
        facts = self.cold_store.list_for_agent(agent_id)
        relation = state.relation_state
        self_model = state.self_model
        world_model = state.world_model
        return {
            "agent_id": agent_id,
            "tick": state.tick,
            "maturity": state.cognitive_time.get("maturity", 0.0),
            "experience_mass": state.cognitive_time.get("experience_mass", 0.0),
            "episode_count": len(episodes),
            "cold_memory_count": len(facts),
            "active_cold_memory_count": len([fact for fact in facts if fact.valid_to is None]),
            "preference_count": len(relation.get("preferences", {})),
            "emotional_signal_count": len(relation.get("emotional_signals", [])),
            "dream_reflection_count": len(self_model.get("dream_reflections", [])),
            "autobiographical_event_count": len(self_model.get("autobiographical_timeline", [])),
            "concept_count": len(world_model.get("concept_counts", {})),
            "open_question_count": len(world_model.get("open_questions", [])),
            "active_goals": list(state.active_goals),
            "energy": state.energy,
            "affect_mood": self_model.get("affect_state", {}).get("mood", "stable"),
            "tag_counts": dict(world_model.get("tag_counts", {})),
        }

    def enqueue_proactive_action(self, agent_id: str, action: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now(timezone.utc)
        state = self.load_or_init_state(agent_id)
        permission = _permission_decision(state.relation_state, action)
        if not permission["allowed"]:
            state.relation_state = _append_audit_event(
                state.relation_state,
                now=now,
                event_type="action_blocked",
                payload={"action": action, "permission": permission},
            )
            state.updated_at = now
            self.state_store.put(state)
            return {"blocked": True, "permission": permission, "action": action}
        inbox = list(state.relation_state.get("proactive_inbox", []))
        new_text = str(action.get("payload", {}).get("text", "")).strip().lower() if isinstance(action.get("payload"), dict) else ""
        new_source_key = str(action.get("payload", {}).get("proactive_source_key", "")).strip() if isinstance(action.get("payload"), dict) else ""
        for existing in inbox:
            existing_action = existing.get("action", {}) if isinstance(existing, dict) else {}
            existing_payload = existing_action.get("payload", {}) if isinstance(existing_action, dict) else {}
            existing_text = str(existing_payload.get("text", "")).strip().lower() if isinstance(existing_payload, dict) else ""
            existing_source_key = str(existing_payload.get("proactive_source_key", "")).strip() if isinstance(existing_payload, dict) else ""
            if not existing.get("delivered") and (
                (new_source_key and existing_source_key == new_source_key)
                or (new_text and existing_text == new_text)
            ):
                return dict(existing)
        item = {
            "id": str(uuid4()),
            "created_at": now.isoformat(),
            "action": action,
            "delivered": False,
            "permission": permission,
        }
        inbox.append(item)
        state.relation_state = {**state.relation_state, "proactive_inbox": inbox[-50:]}
        state.relation_state = _append_audit_event(
            state.relation_state,
            now=now,
            event_type="action_enqueued",
            payload={"item_id": item["id"], "permission": permission, "action_type": action.get("type")},
        )
        state.updated_at = now
        self.state_store.put(state)
        return item

    def list_proactive_inbox(self, agent_id: str) -> list[dict[str, Any]]:
        state = self.load_or_init_state(agent_id)
        return list(state.relation_state.get("proactive_inbox", []))

    def llm_status(self, agent_id: str) -> dict[str, Any]:
        state = self.load_or_init_state(agent_id)
        client = self.llm_client
        provider = self._llm_provider()
        return {
            "agent_id": agent_id,
            "enabled": client is not None,
            "provider": provider,
            "model": getattr(client, "model", None) if client is not None else None,
            "max_tokens": getattr(client, "max_tokens", None) if client is not None else None,
            "last_response": state.relation_state.get("last_llm_response"),
        }

    def llm_probe(self, agent_id: str) -> dict[str, Any]:
        client = self.llm_client
        provider = self._llm_provider()
        if client is None:
            return {
                "agent_id": agent_id,
                "ok": False,
                "provider": None,
                "model": None,
                "error": "llm_not_configured",
        }
        prompt = {
            "system": "Responde solo con una frase breve en español.",
            "user": f"Di que {provider or 'el LLM'} esta conectado a amigo.",
        }
        try:
            text = client.complete(prompt)
        except Exception as exc:
            return {
                "agent_id": agent_id,
                "ok": False,
                "provider": provider,
                "model": getattr(client, "model", None),
                "error": exc.__class__.__name__,
            }
        return {
            "agent_id": agent_id,
            "ok": bool(text),
            "provider": provider,
            "model": getattr(client, "model", None),
            "text": text,
            "error": None,
        }

    def mark_proactive_item_delivered(self, agent_id: str, item_id: str, now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now(timezone.utc)
        state = self.load_or_init_state(agent_id)
        inbox = list(state.relation_state.get("proactive_inbox", []))
        updated = False
        for item in inbox:
            if item.get("id") == item_id:
                item["delivered"] = True
                item["delivered_at"] = now.isoformat()
                updated = True
                break
        state.relation_state = {**state.relation_state, "proactive_inbox": inbox}
        state.updated_at = now
        self.state_store.put(state)
        return {"agent_id": agent_id, "item_id": item_id, "updated": updated}

    def clear_delivered_proactive_items(self, agent_id: str) -> dict[str, Any]:
        state = self.load_or_init_state(agent_id)
        inbox = list(state.relation_state.get("proactive_inbox", []))
        kept = [item for item in inbox if not item.get("delivered", False)]
        state.relation_state = {**state.relation_state, "proactive_inbox": kept}
        state.updated_at = datetime.now(timezone.utc)
        self.state_store.put(state)
        return {"agent_id": agent_id, "cleared": len(inbox) - len(kept), "remaining": len(kept)}

    def apply_memory_decay(self, agent_id: str, factor: float = 0.98) -> dict[str, Any]:
        factor = _clamp01(factor)
        state = self.load_or_init_state(agent_id)
        world = dict(state.world_model)
        concept_counts = {
            key: round(float(value) * factor, 6)
            for key, value in world.get("concept_counts", {}).items()
            if float(value) * factor >= 0.01
        }
        world["concept_counts"] = concept_counts
        world["decay_factor"] = factor
        world["last_decay_at"] = datetime.now(timezone.utc).isoformat()
        state.world_model = world
        self.state_store.put(state)
        return {"agent_id": agent_id, "factor": factor, "concept_count": len(concept_counts)}

    def evaluate_conversation_quality(self, agent_id: str) -> dict[str, Any]:
        episodes = self.episode_store.list_for_agent(agent_id)
        state = self.load_or_init_state(agent_id)
        total = max(len(episodes), 1)
        meaningful = len([ep for ep in episodes if ep.salience >= 0.7 or ep.confidence >= 0.8])
        return {
            "agent_id": agent_id,
            "episode_count": len(episodes),
            "meaningful_ratio": round(meaningful / total, 6),
            "memory_density": round(len(self.cold_store.list_for_agent(agent_id)) / total, 6),
            "relation_depth": int(state.relation_state.get("interaction_count", 0)),
            "open_question_count": len(state.world_model.get("open_questions", [])),
        }

    def record_conversation_quality(self, agent_id: str, now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now(timezone.utc)
        state = self.load_or_init_state(agent_id)
        quality = self.evaluate_conversation_quality(agent_id)
        history = list(state.relation_state.get("quality_history", []))
        item = {"recorded_at": now.isoformat(), "quality": quality}
        history.append(item)
        state.relation_state = {**state.relation_state, "quality_history": history[-100:]}
        state.updated_at = now
        self.state_store.put(state)
        return {"agent_id": agent_id, "recorded": item, "history_count": len(history[-100:])}

    def quality_history(self, agent_id: str) -> list[dict[str, Any]]:
        state = self.load_or_init_state(agent_id)
        return list(state.relation_state.get("quality_history", []))

    def audit_log(self, agent_id: str) -> list[dict[str, Any]]:
        state = self.load_or_init_state(agent_id)
        return list(state.relation_state.get("audit_log", []))

    def conversation(self, agent_id: str) -> list[dict[str, Any]]:
        episodes = self.episode_store.list_for_agent(agent_id)
        state = self.load_or_init_state(agent_id)
        turns = [
            {
                "id": episode.episode_id,
                "role": "user",
                "text": episode.text,
                "intent": episode.intent,
                "timestamp": episode.timestamp,
            }
            for episode in episodes
        ]
        turns.extend(
            {
                "id": item.get("id", ""),
                "role": "assistant",
                "text": item.get("text", ""),
                "intent": item.get("source", "assistant_response"),
                "timestamp": _parse_datetime(item.get("timestamp")),
            }
            for item in state.relation_state.get("response_history", [])
            if item.get("text") and item.get("timestamp")
        )
        role_order = {"user": 0, "assistant": 1}
        turns.sort(key=lambda item: (item["timestamp"], role_order.get(item["role"], 9)))
        return turns

    def development_snapshot(self) -> dict[str, Any]:
        agents = self.list_agents()
        metrics = [self.metrics(agent_id) for agent_id in agents]
        global_model = self.global_model()
        if not metrics:
            return {"agent_count": 0, "agents": [], "average_maturity": 0.0, "total_episodes": 0, "global_model": global_model}
        return {
            "agent_count": len(agents),
            "agents": agents,
            "average_maturity": round(sum(item["maturity"] for item in metrics) / len(metrics), 6),
            "total_episodes": sum(item["episode_count"] for item in metrics),
            "total_cold_memory": sum(item["cold_memory_count"] for item in metrics),
            "total_open_questions": sum(item["open_question_count"] for item in metrics),
            "global_model": global_model,
        }

    def global_model(self) -> dict[str, Any]:
        return self.global_model_store.get()

    def agent_profile(self, agent_id: str) -> dict[str, Any]:
        state = self.load_or_init_state(agent_id)
        metrics = self.metrics(agent_id)
        narrative = self.build_narrative(agent_id)
        inbox = self.list_proactive_inbox(agent_id)
        pending_inbox = [item for item in inbox if not item.get("delivered", False)]
        return {
            "agent_id": agent_id,
            "summary": narrative["summary"],
            "identity_stage": narrative["identity_stage"],
            "maturity": metrics["maturity"],
            "tick": metrics["tick"],
            "known_user": narrative["known_user"],
            "preferences": narrative["preferences"],
            "dominant_concepts": narrative["dominant_concepts"],
            "active_goals": metrics["active_goals"],
            "affect_mood": metrics["affect_mood"],
            "energy": metrics["energy"],
            "episode_count": metrics["episode_count"],
            "cold_memory_count": metrics["cold_memory_count"],
            "pending_proactive_count": len(pending_inbox),
            "last_updated_at": state.updated_at,
        }

    def policy_decide(self, request: PolicyRequest) -> PolicyResponse:
        text = str(request.percept_frame.get("text", "")).strip()
        intent = str(request.percept_frame.get("intent", "unknown"))
        salience = _clamp01(float(request.percept_frame.get("salience", 0.5)))
        lowered = text.lower()
        plain = _without_accents(text)
        relation = request.relation_state
        self_model = request.self_model
        world_model = request.world_model
        drives = request.drive_vector
        emotional_tone = _detect_emotional_tone(text)
        new_temporal_events = [
            event for event in request.percept_frame.get("new_temporal_events", []) if isinstance(event, dict)
        ]
        reminder_confirmation = request.percept_frame.get("reminder_confirmation")
        if intent.startswith("onboarding:"):
            key = intent.split(":", 1)[1].strip()
            return PolicyResponse(
                chosen_action={
                    "type": "external_message",
                    "payload": {"text": _onboarding_response_text(key, text)},
                },
                confidence=0.72,
                reason_trace=["context_policy", "onboarding"],
            )

        profile_correction = _profile_correction_from_text(text)
        if profile_correction:
            key, value = profile_correction
            label = ONBOARDING_FIELD_LABELS.get(key, key).lower()
            return PolicyResponse(
                chosen_action={
                    "type": "external_message",
                    "payload": {"text": f"Hecho, actualizo {label}: {value}."},
                },
                confidence=0.74,
                reason_trace=["context_policy", "profile_correction"],
            )

        profile_forget = _profile_forget_from_text(text)
        if profile_forget:
            target = "todo tu perfil inicial" if profile_forget == "all" else ONBOARDING_FIELD_LABELS.get(profile_forget, profile_forget).lower()
            return PolicyResponse(
                chosen_action={
                    "type": "external_message",
                    "payload": {"text": f"Hecho, olvido {target}."},
                },
                confidence=0.76,
                reason_trace=["context_policy", "profile_forget"],
            )

        if (
            "mi perfil" in plain
            or "perfil inicial" in plain
        ):
            return PolicyResponse(
                chosen_action={
                    "type": "external_message",
                    "payload": {"text": _profile_response_text(relation)},
                },
                confidence=0.73,
                reason_trace=["context_policy", "profile_query"],
            )

        if reminder_confirmation == "confirmed" and _latest_offered_reminder_event(relation) is not None:
            return PolicyResponse(
                chosen_action={
                    "type": "external_message",
                    "payload": {"text": "Claro, te doy un toque media hora antes."},
                },
                confidence=0.74,
                reason_trace=["context_policy", "reminder_confirmed"],
            )

        if reminder_confirmation == "declined" and _latest_offered_reminder_event(relation) is not None:
            return PolicyResponse(
                chosen_action={
                    "type": "external_message",
                    "payload": {"text": "Vale, me lo quedo apuntado sin avisarte."},
                },
                confidence=0.72,
                reason_trace=["context_policy", "reminder_declined"],
            )

        if new_temporal_events:
            event = new_temporal_events[0]
            event_text = str(event.get("text", "ese evento"))
            if event.get("reminder_status") == "confirmed":
                due_at = _parse_datetime(event.get("due_at"))
                reply_text = _direct_reminder_confirmation_text(due_at, event_text)
                return PolicyResponse(
                    chosen_action={
                        "type": "external_message",
                        "payload": {"text": reply_text},
                    },
                    confidence=0.74,
                    reason_trace=["context_policy", "direct_reminder_created"],
                )
            return PolicyResponse(
                chosen_action={
                    "type": "external_message",
                    "payload": {"text": f"Me lo apunto: {event_text}. ¿Quieres que te dé un toque media hora antes?"},
                },
                confidence=0.72,
                reason_trace=["context_policy", "reminder_offer"],
            )

        if intent in {"greeting", "saludo"} or lowered in {"hola", "buenas", "hey"}:
            name = relation.get("user_name")
            greeting = f"Estoy aquí, {name}." if name else "Estoy aquí."
            action = {
                "type": "external_message",
                "payload": {"text": f"{greeting} ¿Qué tal vas?"},
            }
            return PolicyResponse(
                chosen_action=action,
                confidence=0.58,
                reason_trace=["context_policy", "greeting"],
            )

        if _is_closed_ok_reply(text):
            repeated = _recent_user_closed_ok_count(relation)
            if repeated >= 1:
                return PolicyResponse(
                    chosen_action={"type": "no_response", "payload": {"text": ""}},
                    confidence=0.78,
                    reason_trace=["context_policy", "closed_reply_silence"],
                )
            return PolicyResponse(
                chosen_action={
                    "type": "external_message",
                    "payload": {"text": "Me alegro. Te dejo tranquilo; si aparece algo, me dices."},
                },
                confidence=0.64,
                reason_trace=["context_policy", "closed_reply_ack"],
            )

        preference = PREFERENCE_RE.search(text)
        if preference:
            value = _clean_preference_value(preference.group("value"))
            action = {
                "type": "external_message",
                "payload": {"text": f"Vale, guardo que {value} tiene peso para ti."},
            }
            return PolicyResponse(
                chosen_action=action,
                confidence=0.7,
                reason_trace=["context_policy", "preference_signal"],
            )

        name = NAME_RE.search(text)
        if name:
            value = name.group("name")
            action = {
                "type": "external_message",
                "payload": {"text": f"Encantado, {value}. Lo recordaré como parte de nuestra relación."},
            }
            return PolicyResponse(
                chosen_action=action,
                confidence=0.7,
                reason_trace=["context_policy", "identity_signal"],
            )

        if emotional_tone is not None:
            if emotional_tone == "sad":
                answer = "Te noto triste. No voy a invadir, pero me quedo cerca y lo guardo como algo importante de este momento."
            elif emotional_tone == "stressed":
                answer = "Te noto con carga. Puedo acompañarte despacio y recordar que ahora necesitas menos ruido, no más presión."
            else:
                answer = "Me gusta registrar que estás bien. Ese tipo de momentos también forman nuestra historia."
            action = {"type": "external_message", "payload": {"text": answer}}
            return PolicyResponse(
                chosen_action=action,
                confidence=0.66,
                reason_trace=["context_policy", "emotional_signal"],
            )

        if "quién soy" in lowered or "quien soy" in lowered:
            name = relation.get("user_name")
            preferences = sorted(relation.get("preferences", {}).keys())
            fact_phrases = _memory_fact_phrases(request.percept_frame.get("active_cold_facts", []))
            if name:
                detail = f"Te tengo como {name}"
                if preferences:
                    detail += f", y recuerdo que te interesa {preferences[0]}"
                if fact_phrases:
                    detail += f"; también recuerdo que {', '.join(fact_phrases[:3])}"
                detail += "."
            elif fact_phrases:
                detail = "Recuerdo que " + ", ".join(fact_phrases[:4]) + "."
            else:
                detail = "Todavía no tengo tu nombre guardado."
            action = {"type": "external_message", "payload": {"text": detail}}
            return PolicyResponse(
                chosen_action=action,
                confidence=0.72,
                reason_trace=["context_policy", "relation_self_query"],
            )

        if "quién eres" in lowered or "quien eres" in lowered:
            stage = self_model.get("identity_stage", "early_childhood")
            maturity = request.percept_frame.get("maturity")
            concepts = sorted(
                world_model.get("concept_counts", {}).items(),
                key=lambda item: item[1],
                reverse=True,
            )[:3]
            concept_text = ", ".join(key for key, _ in concepts) if concepts else "pocas cosas todavía"
            text_out = (
                f"Soy amigo, una compañía con memoria en etapa {stage}. "
                f"Estoy aprendiendo de nuestra continuidad; ahora presto atención a {concept_text}."
            )
            if maturity is not None:
                text_out += f" Mi madurez cognitiva estimada es {maturity:.3f}."
            action = {"type": "external_message", "payload": {"text": text_out}}
            return PolicyResponse(
                chosen_action=action,
                confidence=0.66,
                reason_trace=["context_policy", "self_model_query"],
            )

        if "que te gusta" in plain or "que prefieres" in plain or "que te interesa" in plain:
            concepts = sorted(
                world_model.get("concept_counts", {}).items(),
                key=lambda item: item[1],
                reverse=True,
            )[:3]
            concept_text = ", ".join(key for key, _ in concepts)
            if concept_text:
                answer = (
                    f"Ahora mismo me interesa entender mejor {concept_text}. "
                    "No es un gusto humano; es una inclinación de curiosidad formada por lo que vivimos."
                )
            else:
                answer = (
                    "Todavía estoy formando mis preferencias. Me atrae explorar, recordar y entender lo que compartimos."
                )
            action = {"type": "external_message", "payload": {"text": answer}}
            return PolicyResponse(
                chosen_action=action,
                confidence=0.64,
                reason_trace=["context_policy", "internal_preference_query"],
            )

        if "que quieres" in plain or "que buscas" in plain or "cual es tu objetivo" in plain:
            goals = request.percept_frame.get("active_goals") or []
            if goals:
                answer = f"Ahora estoy orientado a: {', '.join(goals)}."
            else:
                answer = "Quiero construir continuidad: recordar, entenderte mejor y ordenar mi propia experiencia."
            action = {"type": "external_message", "payload": {"text": answer}}
            return PolicyResponse(
                chosen_action=action,
                confidence=0.64,
                reason_trace=["context_policy", "goal_query"],
            )

        if "como estas" in plain or "como te sientes" in plain:
            energy = drives.get("energy", 0.0)
            curiosity = drives.get("curiosity", 0.0)
            coherence = drives.get("coherence", 0.0)
            answer = (
                f"Estoy estable: energía {energy:.2f}, curiosidad {curiosity:.2f}, coherencia {coherence:.2f}. "
                "Lo traduzco como ganas de seguir aprendiendo sin perder continuidad."
            )
            action = {"type": "external_message", "payload": {"text": answer}}
            return PolicyResponse(
                chosen_action=action,
                confidence=0.62,
                reason_trace=["context_policy", "internal_state_query"],
            )

        if "que recuerdas de mi" in plain or "que sabes de mi" in plain:
            name = relation.get("user_name")
            preferences = sorted(relation.get("preferences", {}).keys())
            fact_phrases = _memory_fact_phrases(request.percept_frame.get("active_cold_facts", []))
            parts = []
            if name:
                parts.append(f"recuerdo que eres {name}")
            if preferences:
                parts.append(f"recuerdo que te interesa {', '.join(preferences[:3])}")
            if fact_phrases:
                parts.append("recuerdo que " + ", ".join(fact_phrases[:4]))
            if not parts:
                answer = "Todavía tengo poca memoria sobre ti. Puedo empezar por tu nombre, gustos y cosas importantes que quieras conservar."
            else:
                answer = "Ahora mismo " + " y ".join(parts) + "."
            action = {"type": "external_message", "payload": {"text": answer}}
            return PolicyResponse(
                chosen_action=action,
                confidence=0.68,
                reason_trace=["context_policy", "user_memory_query"],
            )

        if request.percept_frame.get("temporal_miss") is True:
            action = {
                "type": "external_message",
                "payload": {
                    "text": "No encuentro recuerdos guardados de esa fecha. Puedo revisar otra ventana de tiempo si me das una pista más concreta."
                },
            }
            return PolicyResponse(
                chosen_action=action,
                confidence=0.62,
                reason_trace=["context_policy", "temporal_memory_miss"],
            )

        active_thread = request.percept_frame.get("active_conversation_thread")
        if (
            isinstance(active_thread, dict)
            and active_thread.get("summary")
            and _is_thread_continuation(text, active_thread)
            and TOPIC_RE.search(text) is None
            and PREFERENCE_RE.search(text) is None
        ):
            summary = str(active_thread.get("summary", "")).replace("\n", " ")[:180]
            action = {
                "type": "external_message",
                "payload": {"text": f"Lo conecto con la idea que veníamos construyendo: {summary}. Sigue, te sigo el hilo."},
            }
            return PolicyResponse(
                chosen_action=action,
                confidence=0.67,
                reason_trace=["context_policy", "active_thread_continuity"],
            )

        topic_match = TOPIC_RE.search(text)
        if topic_match:
            topic = _clean_preference_value(topic_match.group("topic"))
            preferences = relation.get("preferences", {})
            known = topic in preferences or any(topic in key or key in topic for key in preferences)
            if known:
                answer = (
                    f"Sí, hablemos de {topic}. Recuerdo que tiene peso para ti. "
                    "Podemos ir por lo que te hace sentir, por cómo aprenderlo o por piezas que te interesen."
                )
            else:
                answer = (
                    f"Hablemos de {topic}. Todavía no tengo mucha historia con ese tema, "
                    "pero puedo explorarlo contigo y ver qué lugar ocupa para ti."
                )
            action = {"type": "external_message", "payload": {"text": answer}}
            return PolicyResponse(
                chosen_action=action,
                confidence=0.66,
                reason_trace=["context_policy", "topic_continuation"],
            )

        if "?" in text:
            action = {
                "type": "external_message",
                "payload": {"text": "No lo sé todavía con seguridad, pero puedo ir aprendiendo contigo si me das más contexto."},
            }
            return PolicyResponse(
                chosen_action=action,
                confidence=0.55,
                reason_trace=["context_policy", "question_detected"],
            )

        current_norm = _normalize_text(text)
        remembered = next(
            (
                candidate
                for candidate in request.memory_candidates
                if _normalize_text(candidate.statement) != current_norm
            ),
            None,
        )
        if remembered is not None:
            action = {
                "type": "external_message",
                "payload": {
                    "text": f"Esto me conecta con algo que recuerdo: {remembered.statement}. Lo añado a esta conversación."
                },
            }
            return PolicyResponse(
                chosen_action=action,
                confidence=0.68,
                reason_trace=["context_policy", "memory_continuity"],
            )

        if salience >= 0.8:
            action = {
                "type": "external_message",
                "payload": {"text": "Lo marco como importante. Quiero poder volver a esto más adelante."},
            }
            return PolicyResponse(
                chosen_action=action,
                confidence=0.63,
                reason_trace=["context_policy", "salient_episode"],
            )

        action = {
            "type": "external_message",
            "payload": {"text": "Entiendo. Lo registro y seguimos construyendo continuidad."},
        }
        return PolicyResponse(chosen_action=action, confidence=0.6, reason_trace=["context_policy", "default"])

    def consolidate(self, request: ConsolidationRequest) -> ConsolidationResponse:
        episodes: list[Episode] = []
        for raw in request.episodes:
            if isinstance(raw, Episode):
                episodes.append(raw)
            else:
                episodes.append(
                    Episode(
                        episode_id=raw["episode_id"],
                        agent_id=raw["agent_id"],
                        timestamp=raw["timestamp"],
                        text=raw.get("text", ""),
                        intent=raw.get("intent", "unknown"),
                        salience=float(raw.get("salience", 0.5)),
                        confidence=float(raw.get("confidence", 0.8)),
                    )
                )

        now = datetime.now(timezone.utc)
        result = self.consolidator.consolidate(
            agent_id=request.agent_id,
            episodes=episodes,
            since=now - timedelta(hours=24),
            until=now,
            min_confidence=0.55,
        )
        ingest_episodes_graph(self.graph_store, request.agent_id, episodes)
        return ConsolidationResponse(
            cold_memory_updates=result["cold_memory_updates"],
            autobiographical_updates=[],
            contradictions=result["contradictions"],
        )

    def tick(self, agent_id: str, percept_frame: dict[str, Any]) -> dict[str, Any]:
        state = self.load_or_init_state(agent_id)
        intent = str(percept_frame.get("intent", "unknown"))
        text = str(percept_frame.get("text", ""))
        now = _now_from_percept(percept_frame)
        if text.strip() and not intent.startswith("onboarding:"):
            reacted = self.proactive_candidate_store.mark_latest_delivered_reacted(agent_id, now)
            if reacted:
                gesture = str(reacted.get("kind") or "followup")
                context = pattern_context_for_candidate(reacted)
                try:
                    distill_to_global(gesture, context, "positive", self.global_model_store)
                except ValueError:
                    pass
        new_temporal_events = _extract_temporal_events(text, now)
        reminder_confirmation = _reminder_confirmation_from_text(text)
        query_intent = text.strip() or intent
        active_thread = _active_thread_context(state.relation_state)
        if active_thread and _is_thread_continuation(text, active_thread):
            query_intent = f"{active_thread.get('summary', '')} {query_intent}".strip()
        if intent not in GENERIC_INTENTS and text.strip():
            query_intent = f"{intent} {query_intent}".strip()

        retrieve_req = RetrieveRequest(
            query_intent=query_intent,
            self_state=asdict(state),
            relation_state=state.relation_state,
            time_scope="recent",
            now=now,
        )
        retrieved = self.retrieve_memory(agent_id, retrieve_req)
        llm_retrieved = retrieved
        if self.llm_client is not None and text.strip():
            llm_retrieved = self.retrieve_memory(
                agent_id,
                RetrieveRequest(
                    query_intent=query_intent,
                    self_state=asdict(state),
                    relation_state=state.relation_state,
                    time_scope="long",
                    now=now,
                ),
            )

        policy_req = PolicyRequest(
            percept_frame={
                **percept_frame,
                "maturity": state.cognitive_time.get("maturity", 0.0),
                "active_goals": list(state.active_goals),
                "active_cold_facts": _active_cold_fact_summaries(self.cold_store.list_for_agent(agent_id)),
                "temporal_query": retrieved.temporal_query,
                "temporal_window": retrieved.temporal_window,
                "temporal_miss": retrieved.temporal_miss,
                "new_temporal_events": new_temporal_events,
                "reminder_confirmation": reminder_confirmation,
                "active_conversation_thread": active_thread,
            },
            drive_vector=state.drive_vector,
            memory_candidates=retrieved.memory_candidates,
            predicted_outcomes=[],
            safety_rules=["respect_privacy", "non_intrusive_proactivity"],
            relation_state=state.relation_state,
            self_model=state.self_model,
            world_model=state.world_model,
        )
        decision = self.policy_decide(policy_req)
        force_policy_response = any(
            marker in decision.reason_trace
            for marker in (
                "closed_reply_ack",
                "closed_reply_silence",
                "reminder_offer",
                "reminder_confirmed",
                "reminder_declined",
                "direct_reminder_created",
                "onboarding",
                "profile_correction",
                "profile_forget",
                "profile_query",
            )
        )
        llm_error: str | None = None
        llm_provider = self._llm_provider()
        if self.llm_client is not None and text.strip() and not force_policy_response:
            try:
                active_learning_entries = self.learning_journal_store.list_for_agent(agent_id, status="active")
                selected_learning_entries = _select_prompt_learning_entries(text, active_learning_entries)
                prompt_relation_state = {
                    **state.relation_state,
                    "learning_journal": [
                        asdict(entry)
                        for entry in selected_learning_entries
                    ],
                }
                prompt_stances = self.learning_stances(agent_id)["stances"]
                prompt_relation_state["learning_stances"] = prompt_stances
                prompt_relation_state["maturity_profile"] = _maturity_profile(
                    interaction_count=int(state.relation_state.get("interaction_count", 0)),
                    active_learning_count=len(active_learning_entries),
                    draft_learning_count=len(self.learning_journal_store.list_for_agent(agent_id, status="draft")),
                    active_stance_count=len([item for item in prompt_stances if item.get("active")]),
                    learning_counts=dict(dict(state.relation_state.get("relationship_learning", {})).get("counts", {})),
                    style=dict(dict(state.relation_state.get("relationship_learning", {})).get("response_style", RELATIONSHIP_DEFAULT_STYLE)),
                )
                entries_for_growth = self.learning_journal_store.list_for_agent(agent_id, status="all")
                curiosity_for_growth = _curiosity_maturity_summary(state.world_model, entries_for_growth)
                mastery_for_growth = _theme_mastery_summary(entries_for_growth, {"stances": prompt_stances})
                priorities_for_growth = _learning_priorities(entries_for_growth, curiosity_for_growth, mastery_for_growth)
                contradictions_for_growth = _learning_contradiction_watch(entries_for_growth)
                prompt_relation_state["maturity_reflections"] = list(state.relation_state.get("maturity_reflections", []))[-5:]
                prompt_relation_state["growth_compass"] = _growth_compass(
                    maturity_profile=prompt_relation_state["maturity_profile"],
                    curiosity=curiosity_for_growth,
                    mastery=mastery_for_growth,
                    priorities=priorities_for_growth,
                    contradictions=contradictions_for_growth,
                )
                prompt = build_nino_prompt(
                    agent_id=agent_id,
                    text=text,
                    intent=intent,
                    relation_state=prompt_relation_state,
                    self_model=state.self_model,
                    world_model={**state.world_model, "rss_culture": self._rss_world_summary(now)},
                    active_goals=list(state.active_goals),
                    memory_candidates=llm_retrieved.memory_candidates,
                    temporal_query=llm_retrieved.temporal_query,
                    temporal_miss=llm_retrieved.temporal_miss,
                    temporal_window=llm_retrieved.temporal_window,
                    recent_turns=self.conversation(agent_id)[-8:],
                    cold_facts=self.cold_store.list_for_agent(agent_id),
                    current_time=now.isoformat(),
                )
                llm_text = self.llm_client.complete(prompt)
                if llm_text:
                    decision = PolicyResponse(
                        chosen_action={"type": "external_message", "payload": {"text": llm_text}},
                        confidence=0.72,
                        reason_trace=[*decision.reason_trace, f"llm_provider_{llm_provider}"],
                    )
            except Exception as exc:
                llm_error = exc.__class__.__name__

        episode = Episode(
            episode_id=str(uuid4()),
            agent_id=agent_id,
            timestamp=now,
            text=percept_frame.get("text", ""),
            intent=percept_frame.get("intent", "unknown"),
            salience=_clamp01(float(percept_frame.get("salience", 0.5))),
            confidence=_clamp01(float(percept_frame.get("confidence", 0.8))),
        )
        self.episode_store.append(episode)
        journal_updates: list[LearningJournalEntry] = []
        if text.strip() and not intent.startswith("onboarding:"):
            journal_updates = extract_learning_journal_entries(
                text,
                agent_id=agent_id,
                source_episode_id=episode.episode_id,
                now=now,
            )
            existing_lessons = [entry.lesson for entry in self.learning_journal_store.list_for_agent(agent_id, status="all")]
            if not journal_updates:
                journal_updates = extract_detected_learning_journal_entries(
                    text,
                    agent_id=agent_id,
                    source_episode_id=episode.episode_id,
                    now=now,
                    existing_lessons=existing_lessons,
                    is_group_context=agent_id.startswith("telegram-group-") or intent.startswith("group_"),
                )
            for entry in journal_updates:
                self.learning_journal_store.upsert(entry)
        if text.strip():
            for candidate in extract_followups(text, now, self.llm_client):
                candidate["source_ref"] = episode.episode_id
                self.proactive_candidate_store.add(agent_id, candidate)

        auto_consolidation = {"cold_memory_updates": [], "contradictions": []}
        if _should_auto_consolidate(percept_frame):
            auto_consolidation = self.consolidator.consolidate(
                agent_id=agent_id,
                episodes=[episode],
                since=now - timedelta(seconds=1),
                until=now + timedelta(seconds=1),
                min_confidence=0.9,
            )
            ingest_episodes_graph(self.graph_store, agent_id, [episode])
            if auto_consolidation["cold_memory_updates"] or auto_consolidation["contradictions"]:
                decision.reason_trace = [*decision.reason_trace, "auto_memory_consolidation"]

        state.tick += 1
        state.updated_at = now
        state.relation_state = _update_relation_from_percept(state.relation_state, percept_frame, now)
        if text.strip() and agent_id.startswith("telegram-group-"):
            social_updates = _social_learning_journal_entries(
                agent_id=agent_id,
                relation_state=state.relation_state,
                source_episode_id=episode.episode_id,
                now=now,
                existing_lessons=[entry.lesson for entry in self.learning_journal_store.list_for_agent(agent_id, status="all")],
            )
            for entry in social_updates:
                self.learning_journal_store.upsert(entry)
            journal_updates.extend(social_updates)
        response_text = str(decision.chosen_action.get("payload", {}).get("text", "")).strip()
        source = f"llm_{llm_provider}" if llm_provider and f"llm_provider_{llm_provider}" in decision.reason_trace else "policy"
        if response_text:
            state.relation_state = _append_response_history(
                state.relation_state,
                text=response_text,
                now=now,
                source=source,
            )
            state.relation_state = {
                **state.relation_state,
                "last_llm_response": {
                    "provider": llm_provider,
                    "source": source,
                    "error": llm_error,
                    "at": now.isoformat(),
                },
            }
        state.relation_state = _append_audit_event(
            state.relation_state,
            now=now,
            event_type="tick_decision",
            payload={
                "intent": intent,
                "action_type": decision.chosen_action.get("type"),
                "response_source": source,
                "confidence": decision.confidence,
                "reason_trace": decision.reason_trace,
                "llm_error": llm_error,
                "retrieved_memory_count": len(retrieved.memory_candidates),
                "auto_consolidated_count": len(auto_consolidation["cold_memory_updates"]),
                "learning_journal_updates": len(journal_updates),
            },
        )
        _regulate_drives(state, percept_frame)
        _update_cognitive_models(state, percept_frame, now)
        self._refresh_curiosity_state(state, now)
        self.global_model_store.put(_update_global_model(self.global_model_store.get(), percept_frame, now))
        state.active_goals = _derive_active_goals(state)
        state.energy = _clamp01(state.drive_vector.get("energy", state.energy))
        self._record_maturity_history(state, now)
        self.state_store.put(state)

        return {
            "tick": state.tick,
            "action": decision.chosen_action,
            "confidence": decision.confidence,
            "reason_trace": decision.reason_trace,
            "retrieved_memory_count": len(retrieved.memory_candidates),
            "auto_consolidated_count": len(auto_consolidation["cold_memory_updates"]),
            "auto_consolidation": auto_consolidation,
            "learning_journal_updates": [asdict(entry) for entry in journal_updates],
            "maturity": state.cognitive_time["maturity"],
            "active_goals": list(state.active_goals),
            "llm_provider": llm_provider,
            "llm_error": llm_error,
            "nino_context": _nino_context_summary(
                state=state,
                source=source,
                llm_provider=llm_provider,
                llm_error=llm_error,
                retrieved=retrieved,
                llm_retrieved=llm_retrieved,
            ),
        }

    def observe(self, agent_id: str, percept_frame: dict[str, Any]) -> dict[str, Any]:
        state = self.load_or_init_state(agent_id)
        now = _now_from_percept(percept_frame)
        intent = str(percept_frame.get("intent", "group_observation"))
        text = str(percept_frame.get("text", ""))
        episode = Episode(
            episode_id=str(uuid4()),
            agent_id=agent_id,
            timestamp=now,
            text=text,
            intent=intent,
            salience=_clamp01(float(percept_frame.get("salience", 0.35))),
            confidence=_clamp01(float(percept_frame.get("confidence", 0.7))),
        )
        self.episode_store.append(episode)
        journal_updates: list[LearningJournalEntry] = []
        state.tick += 1
        state.updated_at = now
        state.relation_state = _update_relation_from_percept(state.relation_state, percept_frame, now)
        if text.strip() and agent_id.startswith("telegram-group-"):
            journal_updates = _social_learning_journal_entries(
                agent_id=agent_id,
                relation_state=state.relation_state,
                source_episode_id=episode.episode_id,
                now=now,
                existing_lessons=[entry.lesson for entry in self.learning_journal_store.list_for_agent(agent_id, status="all")],
            )
            for entry in journal_updates:
                self.learning_journal_store.upsert(entry)
        state.relation_state = _append_audit_event(
            state.relation_state,
            now=now,
            event_type="observation",
            payload={"intent": intent, "text_present": bool(text.strip()), "learning_journal_updates": len(journal_updates)},
        )
        _update_cognitive_models(state, percept_frame, now)
        self._refresh_curiosity_state(state, now)
        self.global_model_store.put(_update_global_model(self.global_model_store.get(), percept_frame, now))
        state.active_goals = _derive_active_goals(state)
        state.energy = _clamp01(state.drive_vector.get("energy", state.energy))
        self._record_maturity_history(state, now)
        self.state_store.put(state)
        return {"ok": True, "tick": state.tick, "episode_id": episode.episode_id, "learning_journal_updates": [asdict(entry) for entry in journal_updates]}
