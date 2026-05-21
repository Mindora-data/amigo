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


class ProactivityEngine:
    def __init__(self, episode_store: InMemoryEpisodeStore) -> None:
        self.episode_store = episode_store

    def evaluate(
        self,
        agent_id: str,
        relation_state: dict[str, Any],
        now: datetime | None = None,
    ) -> ProactivityResponse:
        now = now or datetime.now(timezone.utc)
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

        candidate = _latest_candidate(self.episode_store.list_for_agent(agent_id), now)
        if candidate is None:
            reason_trace.append("no_proactive_candidate")
            return ProactivityResponse(False, None, reason_trace)

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
