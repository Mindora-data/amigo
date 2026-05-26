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

def test_proactivity_prioritizes_temporal_event_reminder() -> None:
    runtime = NinoRuntime(InMemoryStateStore())
    now = datetime(2026, 5, 21, 10, tzinfo=timezone.utc)
    runtime.configure_proactivity(
        "agent-p",
        ProactivitySettings(consent="allowed", max_messages_per_day=3, min_hours_between=0),
    )
    runtime.tick(
        "agent-p",
        {
            "intent": "chat",
            "text": "mañana tengo cita",
            "salience": 0.9,
            "confidence": 0.95,
            "now": now.isoformat(),
        },
    )
    runtime.tick("agent-p", {"intent": "chat", "text": "sí, recuérdamelo", "now": (now + timedelta(minutes=1)).isoformat()})

    out = runtime.evaluate_proactivity("agent-p", now=datetime(2026, 5, 22, 8, 30, tzinfo=timezone.utc))
    state = runtime.load_or_init_state("agent-p")

    assert out.should_send is True
    assert out.action is not None
    assert "mañana tengo cita" in out.action["payload"]["text"]
    assert "event_reminder" in out.reason_trace
    assert state.relation_state["temporal_events"][0]["status"] == "reminded"

def test_proactivity_reminds_weekday_event_with_exact_time() -> None:
    runtime = NinoRuntime(InMemoryStateStore())
    now = datetime(2026, 5, 25, 10, tzinfo=timezone.utc)
    runtime.configure_proactivity(
        "agent-p",
        ProactivitySettings(consent="allowed", max_messages_per_day=3, min_hours_between=0),
    )
    runtime.tick(
        "agent-p",
        {
            "intent": "chat",
            "text": "el jueves a las 17:30 tengo reunión",
            "salience": 0.9,
            "confidence": 0.95,
            "now": now.isoformat(),
        },
    )
    runtime.tick("agent-p", {"intent": "chat", "text": "vale", "now": (now + timedelta(minutes=1)).isoformat()})

    out = runtime.evaluate_proactivity("agent-p", now=datetime(2026, 5, 28, 17, tzinfo=timezone.utc))

    assert out.should_send is True
    assert out.action is not None
    assert out.action["payload"]["due_at"] == "2026-05-28T17:30:00+00:00"
    assert "jueves a las 17:30" in out.action["payload"]["text"]


def test_proactivity_reschedules_weekly_recurring_event_after_reminder() -> None:
    runtime = NinoRuntime(InMemoryStateStore())
    now = datetime(2026, 5, 25, 10, tzinfo=timezone.utc)
    runtime.configure_proactivity(
        "agent-p",
        ProactivitySettings(consent="allowed", max_messages_per_day=3, min_hours_between=0),
    )
    runtime.tick(
        "agent-p",
        {
            "intent": "chat",
            "text": "cada lunes a las 9 tengo llamada",
            "salience": 0.9,
            "confidence": 0.95,
            "now": now.isoformat(),
        },
    )
    runtime.tick("agent-p", {"intent": "chat", "text": "sí", "now": (now + timedelta(minutes=1)).isoformat()})

    first = runtime.evaluate_proactivity("agent-p", now=datetime(2026, 6, 1, 8, 30, tzinfo=timezone.utc))
    state = runtime.load_or_init_state("agent-p")
    event = state.relation_state["temporal_events"][0]

    assert first.should_send is True
    assert first.action is not None
    assert first.action["payload"]["recurrence"] == "weekly"
    assert event["status"] == "pending"
    assert event["next_due_at"] == "2026-06-08T09:00:00+00:00"


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


def test_proactivity_respects_active_hours_window() -> None:
    runtime = NinoRuntime(InMemoryStateStore())
    now = datetime(2026, 5, 21, 8, tzinfo=timezone.utc)
    runtime.configure_proactivity(
        "agent-p",
        ProactivitySettings(
            consent="allowed",
            max_messages_per_day=3,
            min_hours_between=0,
            active_hours_start=9,
            active_hours_end=18,
        ),
    )
    runtime.episode_store.append(
        Episode("e1", "agent-p", now - timedelta(hours=2), "mañana tengo examen", "school", 0.9, 0.9)
    )

    out = runtime.evaluate_proactivity("agent-p", now=now)

    assert out.should_send is False
    assert "outside_active_hours" in out.reason_trace
    assert out.next_allowed_at == now.replace(hour=9, minute=0, second=0, microsecond=0)


def test_proactivity_respects_overnight_active_hours_window() -> None:
    runtime = NinoRuntime(InMemoryStateStore())
    now = datetime(2026, 5, 21, 23, tzinfo=timezone.utc)
    runtime.configure_proactivity(
        "agent-p",
        ProactivitySettings(
            consent="allowed",
            max_messages_per_day=3,
            min_hours_between=0,
            active_hours_start=22,
            active_hours_end=7,
        ),
    )
    runtime.episode_store.append(
        Episode("e1", "agent-p", now - timedelta(hours=2), "mañana tengo examen", "school", 0.9, 0.9)
    )

    out = runtime.evaluate_proactivity("agent-p", now=now)

    assert out.should_send is True
    assert out.action is not None


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


def test_proactivity_can_suggest_anonymous_global_pattern_without_private_text() -> None:
    runtime = NinoRuntime(InMemoryStateStore())
    now = datetime(2026, 5, 21, 10, tzinfo=timezone.utc)
    runtime.tick("other-a", {"intent": "music", "text": "soy Ana y me gusta piano", "salience": 0.4})
    runtime.tick("other-b", {"intent": "music", "text": "mi email es bob@example.com y me gusta piano", "salience": 0.4})
    runtime.configure_proactivity("agent-p", ProactivitySettings(consent="allowed", max_messages_per_day=3, min_hours_between=0))

    out = runtime.evaluate_proactivity("agent-p", now=now)
    rendered = str(out.action).lower()

    assert out.should_send is True
    assert out.action is not None
    assert out.action["payload"]["global_concept"] == "piano"
    assert "anonymous_global_model" in out.reason_trace
    assert "ana" not in rendered
    assert "bob" not in rendered
    assert "example.com" not in rendered
