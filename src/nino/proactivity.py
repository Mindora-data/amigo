from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Any

from .contracts import ProactivityResponse, ProactivitySettings
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
    def __init__(self, episode_store: InMemoryEpisodeStore) -> None:
        self.episode_store = episode_store

    def evaluate(
        self,
        agent_id: str,
        relation_state: dict[str, Any],
        now: datetime | None = None,
        self_model: dict[str, Any] | None = None,
        world_model: dict[str, Any] | None = None,
        global_model: dict[str, Any] | None = None,
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
