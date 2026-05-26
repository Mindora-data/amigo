from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json
import re
from typing import Any
from uuid import uuid4

from .contracts import ProactivityResponse, ProactivitySettings
from .llm import AMIGO_ETHICS, LLMClient
from .memory import Episode, InMemoryEpisodeStore

SENSITIVE_TERMS = {
    "contraseña",
    "password",
    "secreto",
    "privado",
    "médico",
    "medico",
    "salud",
    "depresión",
    "depresion",
    "ansiedad",
}

FOLLOWUP_EXTRACTION_SYSTEM = """Extraes posibles seguimientos de un mensaje.
Un seguimiento es un evento futuro concreto sobre el que tendría sentido preguntar después
(una cita, examen, viaje, decisión, algo con carga personal).
NO incluyas tareas que el usuario ya pidió recordar explícitamente.
Devuelve SOLO un array JSON, sin texto ni markdown. Vacío si no hay nada:
[{"topic": "<resumen breve>", "when_iso": "<fecha estimada o null>", "weight": <1-3>}]
weight: 1 trivial, 2 normal, 3 claramente importante para la persona."""

FOLLOWUP_DAILY_CAP = 1
FOLLOWUP_COOLDOWN_HOURS = 6
FOLLOWUP_ACTIVE_HOURS_START = 9
FOLLOWUP_ACTIVE_HOURS_END = 22
CHECKIN_BASE_THRESHOLD_DAYS = 7


class InMemoryProactiveCandidateStore:
    def __init__(self) -> None:
        self._items: list[dict[str, Any]] = []

    def add(self, user_id: str, candidate: dict[str, Any]) -> dict[str, Any]:
        item = {
            "id": str(candidate.get("id") or uuid4()),
            "user_id": user_id,
            "kind": str(candidate.get("kind", "followup")),
            "source_ref": candidate.get("source_ref"),
            "topic": str(candidate.get("topic", ""))[:120],
            "earliest_at": str(candidate.get("earliest_at")),
            "expires_at": candidate.get("expires_at"),
            "status": str(candidate.get("status", "pending")),
            "attempts": int(candidate.get("attempts", 0)),
            "weight": max(1, min(3, int(candidate.get("weight", 2)))),
            "created_at": str(candidate.get("created_at") or datetime.now(timezone.utc).isoformat()),
            "delivered_at": candidate.get("delivered_at"),
            "user_reacted": candidate.get("user_reacted"),
        }
        self._items.append(item)
        return dict(item)

    def pending(self, user_id: str, now: datetime) -> list[dict[str, Any]]:
        rows = []
        for item in self._items:
            if item.get("user_id") != user_id or item.get("status") != "pending":
                continue
            earliest = _parse_dt(item.get("earliest_at"))
            expires = _parse_dt(item.get("expires_at"))
            if earliest is None or earliest > now:
                continue
            if expires is not None and expires < now:
                continue
            rows.append(dict(item))
        rows.sort(key=lambda item: (-int(item.get("weight", 2)), str(item.get("earliest_at")), str(item.get("id"))))
        return rows

    def expire_stale(self, user_id: str, now: datetime) -> int:
        count = 0
        for item in self._items:
            expires = _parse_dt(item.get("expires_at"))
            if item.get("user_id") == user_id and item.get("status") == "pending" and expires is not None and expires < now:
                item["status"] = "expired"
                count += 1
        return count

    def mark_delivered(self, candidate_id: str, now: datetime) -> dict[str, Any] | None:
        for item in self._items:
            if str(item.get("id")) == str(candidate_id):
                item["status"] = "delivered"
                item["delivered_at"] = now.isoformat()
                item["attempts"] = int(item.get("attempts", 0)) + 1
                item["user_reacted"] = 0
                return dict(item)
        return None

    def recent_delivered(self, user_id: str, limit: int = 2) -> list[dict[str, Any]]:
        rows = [
            dict(item)
            for item in self._items
            if item.get("user_id") == user_id and item.get("status") == "delivered" and item.get("delivered_at")
        ]
        rows.sort(key=lambda item: str(item.get("delivered_at")), reverse=True)
        return rows[:limit]

    def count_delivered_since(self, user_id: str, since: datetime) -> int:
        count = 0
        for item in self._items:
            delivered_at = _parse_dt(item.get("delivered_at"))
            if item.get("user_id") == user_id and item.get("status") == "delivered" and delivered_at and delivered_at >= since:
                count += 1
        return count

    def last_delivered_at(self, user_id: str) -> datetime | None:
        delivered = [_parse_dt(item.get("delivered_at")) for item in self._items if item.get("user_id") == user_id]
        values = [value for value in delivered if value is not None]
        return max(values) if values else None

    def mark_latest_delivered_reacted(self, user_id: str, now: datetime) -> dict[str, Any] | None:
        rows = [
            item
            for item in self._items
            if item.get("user_id") == user_id and item.get("status") == "delivered" and item.get("user_reacted") == 0
        ]
        rows.sort(key=lambda item: str(item.get("delivered_at")), reverse=True)
        if not rows:
            return None
        rows[0]["user_reacted"] = 1
        rows[0]["reacted_at"] = now.isoformat()
        return dict(rows[0])

    def has_recent_checkin_candidate(self, user_id: str, since: datetime) -> bool:
        for item in self._items:
            created_at = _parse_dt(item.get("created_at"))
            if item.get("user_id") == user_id and item.get("kind") == "checkin" and created_at and created_at >= since:
                return True
        return False

    def delete_for_agent(self, user_id: str) -> int:
        before = len(self._items)
        self._items = [item for item in self._items if item.get("user_id") != user_id]
        return before - len(self._items)


def default_proactivity_state() -> dict[str, Any]:
    return {
        "settings": asdict(ProactivitySettings()),
        "sent_at": [],
    }


def normalize_settings(raw: dict[str, Any] | None) -> ProactivitySettings:
    raw = raw or {}
    consent = raw.get("consent", "unknown")
    if consent not in {"unknown", "allowed", "paused", "denied"}:
        consent = "unknown"
    return ProactivitySettings(
        consent=consent,
        max_messages_per_day=max(0, int(raw.get("max_messages_per_day", 1))),
        min_hours_between=max(0.0, float(raw.get("min_hours_between", 24.0))),
        active_hours_start=min(23, max(0, int(raw.get("active_hours_start", 0)))),
        active_hours_end=min(24, max(1, int(raw.get("active_hours_end", 24)))),
    )


def configure_proactivity_state(
    relation_state: dict[str, Any],
    settings: ProactivitySettings,
) -> dict[str, Any]:
    relation = dict(relation_state)
    proactivity = dict(relation.get("proactivity", default_proactivity_state()))
    proactivity["settings"] = asdict(settings)
    proactivity.setdefault("sent_at", [])
    relation["proactivity"] = proactivity
    return relation


def record_proactive_send(relation_state: dict[str, Any], now: datetime) -> dict[str, Any]:
    relation = dict(relation_state)
    proactivity = dict(relation.get("proactivity", default_proactivity_state()))
    sent_at = list(proactivity.get("sent_at", []))
    sent_at.append(now.isoformat())
    proactivity["sent_at"] = sent_at[-100:]
    relation["proactivity"] = proactivity
    return relation


def _parse_sent_at(values: list[str]) -> list[datetime]:
    parsed: list[datetime] = []
    for value in values:
        try:
            parsed.append(datetime.fromisoformat(value))
        except ValueError:
            continue
    return parsed

def _inside_active_hours(now: datetime, settings: ProactivitySettings) -> bool:
    start = settings.active_hours_start
    end = settings.active_hours_end
    if start == 0 and end == 24:
        return True
    current = now.hour
    if start < end:
        return start <= current < end
    if start > end:
        return current >= start or current < end
    return False

def _next_active_hour(now: datetime, settings: ProactivitySettings) -> datetime | None:
    if _inside_active_hours(now, settings):
        return None
    start = settings.active_hours_start
    candidate = now.replace(hour=start, minute=0, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    if settings.active_hours_start > settings.active_hours_end and now.hour < settings.active_hours_end:
        candidate -= timedelta(days=1)
    return candidate


def _latest_candidate(episodes: list[Episode], now: datetime) -> Episode | None:
    horizon = now - timedelta(days=14)
    candidates = [
        ep
        for ep in episodes
        if ep.timestamp >= horizon and ep.salience >= 0.7 and ep.confidence >= 0.55
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda ep: (ep.timestamp, ep.episode_id), reverse=True)
    return candidates[0]


def _contains_sensitive_term(text: str) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in SENSITIVE_TERMS)

def _latest_open_question(world_model: dict[str, Any]) -> dict[str, Any] | None:
    questions = list(world_model.get("open_questions", []))
    return questions[-1] if questions else None

def _top_preference(relation_state: dict[str, Any]) -> str | None:
    preferences = relation_state.get("preferences", {})
    if not isinstance(preferences, dict) or not preferences:
        return None
    ranked = sorted(
        preferences.items(),
        key=lambda item: (
            float(item[1].get("salience", 0.0)) if isinstance(item[1], dict) else 0.0,
            str(item[0]),
        ),
        reverse=True,
    )
    return str(ranked[0][0])

def _latest_autobiographical_memory(self_model: dict[str, Any]) -> dict[str, Any] | None:
    timeline = list(self_model.get("autobiographical_timeline", []))
    return timeline[-1] if timeline else None

def _parse_dt(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed

def _parse_candidate_when(value: Any, now: datetime) -> datetime | None:
    if value in {None, "", "null"}:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=now.tzinfo or timezone.utc)
    return parsed

def extract_followups(user_text: str, now: datetime, llm: LLMClient | None) -> list[dict[str, Any]]:
    if llm is None or not user_text.strip():
        return []
    plain = user_text.lower()
    if not any(
        marker in plain
        for marker in (
            "mañana", "manana", "pasado mañana", "la semana", "el lunes", "el martes",
            "el miércoles", "el miercoles", "el jueves", "el viernes", "el sábado",
            "el sabado", "el domingo", "tengo", "voy a", "entrevista", "examen",
            "viaje", "cita", "reunión", "reunion",
        )
    ):
        return []
    if re.search(r"\b(recuerdame|recuérdame|avisame|avísame|recordatorio|alarma)\b", user_text, re.IGNORECASE):
        return []
    prompt = {
        "system": FOLLOWUP_EXTRACTION_SYSTEM,
        "user": f"Ahora: {now.isoformat()}\nMensaje del usuario:\n{user_text}",
    }
    try:
        raw = llm.complete(prompt)
        items = json.loads(str(raw).strip())
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(items, list):
        return []
    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict) or not str(item.get("topic", "")).strip():
            continue
        when = _parse_candidate_when(item.get("when_iso"), now)
        earliest = when + timedelta(hours=3) if when else now + timedelta(days=1)
        try:
            weight = int(item.get("weight", 2))
        except (TypeError, ValueError):
            weight = 2
        out.append(
            {
                "kind": "followup",
                "topic": str(item["topic"]).strip()[:120],
                "earliest_at": earliest.isoformat(),
                "expires_at": (earliest + timedelta(days=3)).isoformat(),
                "weight": max(1, min(3, weight)),
                "created_at": now.isoformat(),
            }
        )
    return out

def should_deliver_candidate(
    store: InMemoryProactiveCandidateStore,
    user_id: str,
    now_local: datetime,
    *,
    daily_cap: int = FOLLOWUP_DAILY_CAP,
    cooldown_hours: int = FOLLOWUP_COOLDOWN_HOURS,
    active_hours_start: int = FOLLOWUP_ACTIVE_HOURS_START,
    active_hours_end: int = FOLLOWUP_ACTIVE_HOURS_END,
) -> tuple[bool, str]:
    if not (active_hours_start <= now_local.hour < active_hours_end):
        return False, "outside_human_proactivity_hours"
    start_day = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    if store.count_delivered_since(user_id, start_day) >= daily_cap:
        return False, "human_proactivity_daily_cap"
    last = store.last_delivered_at(user_id)
    if last is not None and now_local - last < timedelta(hours=cooldown_hours):
        return False, "human_proactivity_cooldown"
    recent = store.recent_delivered(user_id, limit=2)
    if len(recent) == 2 and all(int(item.get("user_reacted") or 0) == 0 for item in recent):
        return False, "human_proactivity_low_receptivity"
    return True, "human_proactivity_allowed"

def redact_proactive(candidate: dict[str, Any], llm: LLMClient | None = None) -> str:
    topic = str(candidate.get("topic", "eso que me contaste")).strip()[:120]
    kind = str(candidate.get("kind", "followup"))
    fallback = (
        "Pasaba por aquí. No hace falta que respondas ahora; seguimos cuando te apetezca."
        if kind == "checkin"
        else f"Me acordé de {topic}. Si te apetece, cuéntame cómo fue."
    )
    if llm is None:
        return fallback
    prompt = {
        "system": (
            f"{AMIGO_ETHICS}\n"
            "Redacta un único mensaje proactivo muy breve en español. Sin culpa, sin reproche, "
            "sin decir que echas de menos al usuario, sin fingir emoción humana. "
            "Para check-ins, no hagas una pregunta que obligue a responder."
        ),
        "user": f"Tipo: {kind}\nTema: {topic}",
    }
    try:
        text = llm.complete(prompt).strip()
    except Exception:
        return fallback
    if not text:
        return fallback
    blocked = ("te echo de menos", "me preocupa no saber", "por qué no", "porque no")
    if any(marker in text.lower() for marker in blocked):
        return fallback
    return text[:220]

def ensure_checkin_candidate(
    store: InMemoryProactiveCandidateStore,
    user_id: str,
    relation_state: dict[str, Any],
    now: datetime,
    *,
    checkin_prior: float = 0.5,
) -> dict[str, Any] | None:
    last_interaction = _parse_dt(relation_state.get("last_interaction_at"))
    if last_interaction is None:
        return None
    ignored = [
        item
        for item in store.recent_delivered(user_id, limit=3)
        if item.get("kind") == "checkin" and int(item.get("user_reacted") or 0) == 0
    ]
    prior_multiplier = 2.0 if checkin_prior < 0.35 else 0.8 if checkin_prior > 0.7 else 1.0
    threshold_days = int(CHECKIN_BASE_THRESHOLD_DAYS * prior_multiplier * (2 ** len(ignored)))
    threshold = timedelta(days=threshold_days)
    if now - last_interaction < threshold:
        return None
    if store.has_recent_checkin_candidate(user_id, now - threshold):
        return None
    return store.add(
        user_id,
        {
            "kind": "checkin",
            "topic": "abrir una puerta tranquila tras varios días sin hablar",
            "earliest_at": now.isoformat(),
            "expires_at": (now + timedelta(days=2)).isoformat(),
            "weight": 1,
            "created_at": now.isoformat(),
        },
    )

def _due_temporal_event(relation_state: dict[str, Any], now: datetime) -> dict[str, Any] | None:
    events = [event for event in relation_state.get("temporal_events", []) if isinstance(event, dict)]
    due: list[tuple[datetime, dict[str, Any]]] = []
    for event in events:
        recurrence = event.get("recurrence")
        reminder_status = event.get("reminder_status")
        if reminder_status and reminder_status != "confirmed":
            continue
        if event.get("status") not in {None, "pending", "reminded"}:
            continue
        if event.get("status") == "reminded" and not recurrence:
            continue
        due_at = _parse_dt(event.get("next_due_at") or event.get("due_at"))
        if due_at is None:
            continue
        raw_lead_hours = event.get("lead_time_hours")
        lead_hours = 24.0 if raw_lead_hours is None else max(0.0, float(raw_lead_hours))
        if now - timedelta(hours=6) <= due_at <= now + timedelta(hours=lead_hours):
            due.append((due_at, event))
    if not due:
        return None
    due.sort(key=lambda item: item[0])
    return due[0][1]

def mark_temporal_event_reminded(relation_state: dict[str, Any], event_id: str, now: datetime) -> dict[str, Any]:
    relation = dict(relation_state)
    updated = []
    for raw in relation.get("temporal_events", []):
        if not isinstance(raw, dict):
            updated.append(raw)
            continue
        event = dict(raw)
        if str(event.get("id")) == event_id:
            event["reminded_at"] = now.isoformat()
            interval_days = event.get("recurrence_interval_days")
            due_at = _parse_dt(event.get("next_due_at") or event.get("due_at"))
            if event.get("recurrence") and interval_days and due_at is not None:
                next_due = due_at + timedelta(days=int(interval_days))
                while next_due <= now:
                    next_due += timedelta(days=int(interval_days))
                event["status"] = "pending"
                event["next_due_at"] = next_due.isoformat()
            else:
                event["status"] = "reminded"
        updated.append(event)
    relation["temporal_events"] = updated
    return relation

def _top_global_concept(global_model: dict[str, Any]) -> str | None:
    counts = global_model.get("concept_counts", {})
    if not isinstance(counts, dict):
        return None
    blocked = {"chat", "question", "unknown", "email", "correo", "password", "contraseña", "pin"}
    ranked = sorted(
        (
            (str(concept), int(count))
            for concept, count in counts.items()
            if str(concept) not in blocked and int(count) >= 2
        ),
        key=lambda item: (item[1], item[0]),
        reverse=True,
    )
    return ranked[0][0] if ranked else None


class ProactivityEngine:
    def __init__(
        self,
        episode_store: InMemoryEpisodeStore,
        candidate_store: InMemoryProactiveCandidateStore | None = None,
        llm_client: LLMClient | None = None,
    ) -> None:
        self.episode_store = episode_store
        self.candidate_store = candidate_store or InMemoryProactiveCandidateStore()
        self.llm_client = llm_client

    def evaluate(
        self,
        agent_id: str,
        relation_state: dict[str, Any],
        now: datetime | None = None,
        self_model: dict[str, Any] | None = None,
        world_model: dict[str, Any] | None = None,
        global_model: dict[str, Any] | None = None,
        checkin_prior: float = 0.5,
        drive_vector: dict[str, float] | None = None,
        active_goals: list[str] | None = None,
    ) -> ProactivityResponse:
        now = now or datetime.now(timezone.utc)
        self_model = self_model or {}
        world_model = world_model or {}
        global_model = global_model or {}
        drive_vector = drive_vector or {}
        active_goals = active_goals or []
        proactivity = dict(relation_state.get("proactivity", default_proactivity_state()))
        settings = normalize_settings(proactivity.get("settings"))
        reason_trace = ["safe_proactivity_policy"]

        if settings.consent != "allowed":
            reason_trace.append(
                "proactivity_consent_required"
                if settings.consent == "unknown"
                else f"proactivity_{settings.consent}"
            )
            return ProactivityResponse(False, None, reason_trace)

        if not _inside_active_hours(now, settings):
            reason_trace.append("outside_active_hours")
            return ProactivityResponse(False, None, reason_trace, _next_active_hour(now, settings))

        temporal_event = _due_temporal_event(relation_state, now)
        if temporal_event is not None:
            reason_trace.extend(["temporal_memory", "event_reminder"])
            event_text = str(temporal_event.get("text", "un evento pendiente"))
            return ProactivityResponse(
                should_send=True,
                action={
                    "type": "external_message",
                    "payload": {
                        "text": f"Oye, acuérdate: {event_text}.",
                        "source": "relation_state.temporal_events",
                        "temporal_event_id": temporal_event.get("id"),
                        "due_at": temporal_event.get("next_due_at") or temporal_event.get("due_at"),
                        "recurrence": temporal_event.get("recurrence"),
                    },
                },
                reason_trace=reason_trace,
            )

        self.candidate_store.expire_stale(agent_id, now)
        ensure_checkin_candidate(self.candidate_store, agent_id, relation_state, now, checkin_prior=checkin_prior)
        allowed, candidate_reason = should_deliver_candidate(self.candidate_store, agent_id, now)
        if allowed:
            proactive_candidate = next(iter(self.candidate_store.pending(agent_id, now)), None)
            if proactive_candidate is not None:
                reason_trace.extend(["human_proactivity", proactive_candidate.get("kind", "candidate")])
                message = redact_proactive(proactive_candidate, self.llm_client)
                return ProactivityResponse(
                    should_send=True,
                    action={
                        "type": "external_message",
                        "payload": {
                            "text": message,
                            "source": "proactive_candidate",
                            "proactive_candidate_id": proactive_candidate.get("id"),
                            "kind": proactive_candidate.get("kind"),
                            "topic": proactive_candidate.get("topic"),
                        },
                    },
                    reason_trace=reason_trace,
                )
        else:
            reason_trace.append(candidate_reason)

        sent_at = _parse_sent_at(list(proactivity.get("sent_at", [])))
        recent_sent = [sent for sent in sent_at if sent >= now - timedelta(hours=24)]
        if len(recent_sent) >= settings.max_messages_per_day:
            next_allowed = min(recent_sent) + timedelta(hours=24) if recent_sent else None
            reason_trace.append("daily_frequency_cap")
            return ProactivityResponse(False, None, reason_trace, next_allowed)

        if sent_at:
            last_sent = max(sent_at)
            next_allowed = last_sent + timedelta(hours=settings.min_hours_between)
            if now < next_allowed:
                reason_trace.append("minimum_interval")
                return ProactivityResponse(False, None, reason_trace, next_allowed)

        if any(
            isinstance(event, dict) and event.get("reminder_status") == "offered"
            for event in relation_state.get("temporal_events", [])
        ):
            reason_trace.append("reminder_confirmation_pending")
            return ProactivityResponse(False, None, reason_trace)

        for event in relation_state.get("temporal_events", []):
            if not isinstance(event, dict) or event.get("reminder_status") != "confirmed":
                continue
            if event.get("status") != "pending":
                continue
            due_at = _parse_dt(event.get("next_due_at") or event.get("due_at"))
            if due_at is not None and now < due_at:
                reason_trace.append("temporal_alarm_scheduled")
                return ProactivityResponse(False, None, reason_trace)

        open_question = _latest_open_question(world_model)
        if open_question and not _contains_sensitive_term(str(open_question.get("text", ""))):
            reason_trace.extend(["goal_reduce_uncertainty", "open_question_follow_up"])
            return ProactivityResponse(
                should_send=True,
                action={
                    "type": "external_message",
                    "payload": {
                        "text": f"Me quedé pensando en esto: {open_question['text']} ¿Quieres que lo retomemos?",
                        "source": "world_model.open_questions",
                    },
                },
                reason_trace=reason_trace,
            )

        candidate = _latest_candidate(self.episode_store.list_for_agent(agent_id), now)
        if candidate is not None:
            if _contains_sensitive_term(candidate.text):
                reason_trace.append("sensitive_topic_blocked")
                return ProactivityResponse(False, None, reason_trace)

            reason_trace.append("salient_memory_follow_up")
            return ProactivityResponse(
                should_send=True,
                action={
                    "type": "external_message",
                    "payload": {
                        "text": "Estaba pensando en algo que compartiste. ¿Quieres retomarlo?",
                        "source_episode_id": candidate.episode_id,
                    },
                },
                reason_trace=reason_trace,
            )

        preference = _top_preference(relation_state)
        if preference and (
            "revisit_user_preferences" in active_goals
            or drive_vector.get("attachment", 0.0) >= 0.58
            or drive_vector.get("curiosity", 0.0) >= 0.58
        ):
            reason_trace.append("preference_continuity_follow_up")
            return ProactivityResponse(
                should_send=True,
                action={
                    "type": "external_message",
                    "payload": {
                        "text": f"He estado pensando en {preference}. ¿Quieres que sigamos explorándolo?",
                        "source": "relation_state.preferences",
                    },
                },
                reason_trace=reason_trace,
            )

        autobiographical = _latest_autobiographical_memory(self_model)
        if autobiographical and drive_vector.get("coherence", 0.0) >= 0.58:
            reason_trace.append("autobiographical_continuity_follow_up")
            return ProactivityResponse(
                should_send=True,
                action={
                    "type": "external_message",
                    "payload": {
                        "text": f"Estoy integrando algo de nuestra historia: {autobiographical.get('summary', '')}.",
                        "source": "self_model.autobiographical_timeline",
                    },
                },
                reason_trace=reason_trace,
            )

        global_concept = _top_global_concept(global_model)
        if global_concept and drive_vector.get("curiosity", 0.0) >= 0.5:
            reason_trace.extend(["anonymous_global_model", "general_pattern_suggestion"])
            return ProactivityResponse(
                should_send=True,
                action={
                    "type": "external_message",
                    "payload": {
                        "text": f"He detectado un patrón general anónimo alrededor de {global_concept}. ¿Quieres explorarlo desde tu propio contexto?",
                        "source": "operations.global_model",
                        "global_concept": global_concept,
                    },
                },
                reason_trace=reason_trace,
            )

        reason_trace.append("no_proactive_candidate")
        return ProactivityResponse(False, None, reason_trace)
