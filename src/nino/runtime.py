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
from .memory import Episode, InMemoryEpisodeStore, MemoryRetriever
from .proactivity import (
    InMemoryProactiveCandidateStore,
    ProactivityEngine,
    configure_proactivity_state,
    default_proactivity_state,
    extract_followups,
    mark_temporal_event_reminded,
    record_proactive_send,
)


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
        }
        for candidate in llm_retrieved.memory_candidates[:5]
    ]
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
    return bool(re.search(r"\b(recuerdame|recordarme|avisame|avisarme|recordatorio|alarma)\b", plain))

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
        r"\b(recuerdame|recuérdame|avisame|avísame|recordatorio|alarma)\b",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    plain = re.sub(r"\ben\s+\d{1,3}\s+(minuto|minutos|hora|horas)\b", "", plain, flags=re.IGNORECASE)
    plain = TIME_RE.sub("", plain)
    plain = plain.strip(" ,.")
    return (plain or cleaned)[:180]

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

def _update_relation_from_percept(
    relation_state: dict[str, Any],
    percept_frame: dict[str, Any],
    now: datetime,
) -> dict[str, Any]:
    relation = dict(relation_state)
    intent = str(percept_frame.get("intent", "unknown"))
    text = str(percept_frame.get("text", "")).strip()
    relation["interaction_count"] = int(relation.get("interaction_count", 0)) + 1
    relation["last_interaction_at"] = now.isoformat()

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
    }

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

class NinoRuntime:
    def __init__(
        self,
        state_store: InMemoryStateStore,
        episode_store: InMemoryEpisodeStore | None = None,
        cold_store: InMemoryColdStore | None = None,
        global_model_store: InMemoryGlobalModelStore | None = None,
        proactive_candidate_store: InMemoryProactiveCandidateStore | None = None,
        llm_client: LLMClient | None = None,
    ) -> None:
        self.state_store = state_store
        self.episode_store = episode_store or InMemoryEpisodeStore()
        self.cold_store = cold_store or InMemoryColdStore()
        self.global_model_store = global_model_store or InMemoryGlobalModelStore()
        self.retriever = MemoryRetriever(self.episode_store, self.cold_store)
        self.consolidator = Consolidator(self.cold_store)
        self.proactive_candidate_store = proactive_candidate_store or InMemoryProactiveCandidateStore()
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
            world_model=_default_world_model(),
            updated_at=datetime.now(timezone.utc),
        )
        self.state_store.put(initial)
        return initial

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

    def retrieve_memory(self, agent_id: str, request: RetrieveRequest) -> RetrieveResponse:
        return self.retriever.retrieve(agent_id=agent_id, request=request, top_k=5)

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
                return PolicyResponse(
                    chosen_action={
                        "type": "external_message",
                        "payload": {"text": f"Hecho, te doy un toque a las {due_at.strftime('%H:%M')}: {event_text}."},
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
        if intent not in GENERIC_INTENTS and text.strip():
            query_intent = f"{intent} {text}".strip()

        retrieve_req = RetrieveRequest(
            query_intent=query_intent,
            self_state=asdict(state),
            relation_state=state.relation_state,
            time_scope="recent",
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
                prompt = build_nino_prompt(
                    agent_id=agent_id,
                    text=text,
                    intent=intent,
                    relation_state=state.relation_state,
                    self_model=state.self_model,
                    world_model=state.world_model,
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
            if auto_consolidation["cold_memory_updates"] or auto_consolidation["contradictions"]:
                decision.reason_trace = [*decision.reason_trace, "auto_memory_consolidation"]

        state.tick += 1
        state.updated_at = now
        state.relation_state = _update_relation_from_percept(state.relation_state, percept_frame, now)
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
            },
        )
        _regulate_drives(state, percept_frame)
        _update_cognitive_models(state, percept_frame, now)
        self.global_model_store.put(_update_global_model(self.global_model_store.get(), percept_frame, now))
        state.active_goals = _derive_active_goals(state)
        state.energy = _clamp01(state.drive_vector.get("energy", state.energy))
        self.state_store.put(state)

        return {
            "tick": state.tick,
            "action": decision.chosen_action,
            "confidence": decision.confidence,
            "reason_trace": decision.reason_trace,
            "retrieved_memory_count": len(retrieved.memory_candidates),
            "auto_consolidated_count": len(auto_consolidation["cold_memory_updates"]),
            "auto_consolidation": auto_consolidation,
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
