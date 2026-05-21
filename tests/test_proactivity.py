from __future__ import annotations

from datetime import datetime, timedelta, timezone

from nino.contracts import ProactivitySettings
from nino.memory import Episode
from nino.runtime import InMemoryStateStore, NinoRuntime


def test_proactivity_requires_explicit_consent() -> None:
    runtime = NinoRuntime(InMemoryStateStore())
    now = datetime(2026, 5, 21, 10, tzinfo=timezone.utc)
    runtime.episode_store.append(
        Episode("e1", "agent-p", now - timedelta(hours=1), "mañana tengo examen", "school", 0.9, 0.9)
    )

    out = runtime.evaluate_proactivity("agent-p", now=now)

    assert out.should_send is False
    assert out.action is None
    assert "proactivity_consent_required" in out.reason_trace


def test_allowed_proactivity_sends_from_salient_recent_memory_and_records_send() -> None:
    runtime = NinoRuntime(InMemoryStateStore())
    now = datetime(2026, 5, 21, 10, tzinfo=timezone.utc)
    runtime.configure_proactivity(
        "agent-p",
        ProactivitySettings(consent="allowed", max_messages_per_day=1, min_hours_between=24),
    )
    runtime.episode_store.append(
        Episode("e1", "agent-p", now - timedelta(hours=2), "mañana tengo examen", "school", 0.9, 0.9)
    )

    out = runtime.evaluate_proactivity("agent-p", now=now)
    state = runtime.load_or_init_state("agent-p")

    assert out.should_send is True
    assert out.action is not None
    assert out.action["payload"]["source_episode_id"] == "e1"
    assert "salient_memory_follow_up" in out.reason_trace
    assert state.relation_state["proactivity"]["sent_at"] == [now.isoformat()]


def test_proactivity_daily_cap_blocks_second_message() -> None:
    runtime = NinoRuntime(InMemoryStateStore())
    now = datetime(2026, 5, 21, 10, tzinfo=timezone.utc)
    runtime.configure_proactivity(
        "agent-p",
        ProactivitySettings(consent="allowed", max_messages_per_day=1, min_hours_between=0),
    )
    runtime.episode_store.append(
        Episode("e1", "agent-p", now - timedelta(hours=2), "mañana tengo examen", "school", 0.9, 0.9)
    )

    first = runtime.evaluate_proactivity("agent-p", now=now)
    second = runtime.evaluate_proactivity("agent-p", now=now + timedelta(hours=1))

    assert first.should_send is True
    assert second.should_send is False
    assert "daily_frequency_cap" in second.reason_trace
    assert second.next_allowed_at == now + timedelta(hours=24)


def test_proactivity_minimum_interval_blocks_until_next_allowed_time() -> None:
    runtime = NinoRuntime(InMemoryStateStore())
    now = datetime(2026, 5, 21, 10, tzinfo=timezone.utc)
    runtime.configure_proactivity(
        "agent-p",
        ProactivitySettings(consent="allowed", max_messages_per_day=3, min_hours_between=6),
    )
    runtime.episode_store.append(
        Episode("e1", "agent-p", now - timedelta(hours=2), "mañana tengo examen", "school", 0.9, 0.9)
    )

    first = runtime.evaluate_proactivity("agent-p", now=now)
    second = runtime.evaluate_proactivity("agent-p", now=now + timedelta(hours=3))

    assert first.should_send is True
    assert second.should_send is False
    assert "minimum_interval" in second.reason_trace
    assert second.next_allowed_at == now + timedelta(hours=6)


def test_proactivity_blocks_sensitive_topics_even_with_consent() -> None:
    runtime = NinoRuntime(InMemoryStateStore())
    now = datetime(2026, 5, 21, 10, tzinfo=timezone.utc)
    runtime.configure_proactivity("agent-p", ProactivitySettings(consent="allowed"))
    runtime.episode_store.append(
        Episode("e1", "agent-p", now - timedelta(hours=1), "mi contraseña es importante", "privacy", 0.9, 0.9)
    )

    out = runtime.evaluate_proactivity("agent-p", now=now)

    assert out.should_send is False
    assert "sensitive_topic_blocked" in out.reason_trace


def test_proactivity_follows_up_open_question_from_world_model() -> None:
    runtime = NinoRuntime(InMemoryStateStore())
    now = datetime(2026, 5, 21, 10, tzinfo=timezone.utc)
    runtime.configure_proactivity("agent-p", ProactivitySettings(consent="allowed"))
    runtime.tick(
        "agent-p",
        {"intent": "question", "text": "por qué la música me calma?", "salience": 0.6, "confidence": 0.9},
    )

    out = runtime.evaluate_proactivity("agent-p", now=now)

    assert out.should_send is True
    assert out.action is not None
    assert "música me calma" in out.action["payload"]["text"]
    assert "open_question_follow_up" in out.reason_trace


def test_proactivity_can_follow_relation_preference_without_salient_episode() -> None:
    runtime = NinoRuntime(InMemoryStateStore())
    now = datetime(2026, 5, 21, 10, tzinfo=timezone.utc)
    runtime.configure_proactivity("agent-p", ProactivitySettings(consent="allowed"))
    runtime.tick(
        "agent-p",
        {"intent": "chat", "text": "me gusta el piano", "salience": 0.4, "confidence": 0.9},
    )

    out = runtime.evaluate_proactivity("agent-p", now=now)

    assert out.should_send is True
    assert out.action is not None
    assert "piano" in out.action["payload"]["text"]
    assert "preference_continuity_follow_up" in out.reason_trace
