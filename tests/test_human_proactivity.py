from __future__ import annotations

from datetime import datetime, timedelta, timezone

from nino.proactivity import (
    InMemoryProactiveCandidateStore,
    ensure_checkin_candidate,
    extract_followups,
    should_deliver_candidate,
)
from nino.runtime import InMemoryStateStore, NinoRuntime


class FakeLLM:
    provider = "fake"

    def __init__(self, response: str) -> None:
        self.response = response

    def complete(self, prompt: dict[str, object]) -> str:
        return self.response


def test_should_deliver_false_outside_hours() -> None:
    store = InMemoryProactiveCandidateStore()
    now = datetime(2026, 5, 26, 8, 0, tzinfo=timezone.utc)

    allowed, reason = should_deliver_candidate(store, "user-a", now)

    assert allowed is False
    assert reason == "outside_human_proactivity_hours"


def test_should_deliver_false_at_daily_cap() -> None:
    store = InMemoryProactiveCandidateStore()
    now = datetime(2026, 5, 26, 10, 0, tzinfo=timezone.utc)
    item = store.add(
        "user-a",
        {"kind": "followup", "topic": "entrevista", "earliest_at": now.isoformat(), "created_at": now.isoformat()},
    )
    store.mark_delivered(str(item["id"]), now)

    allowed, reason = should_deliver_candidate(store, "user-a", now + timedelta(hours=1))

    assert allowed is False
    assert reason == "human_proactivity_daily_cap"


def test_should_deliver_false_inside_cooldown() -> None:
    store = InMemoryProactiveCandidateStore()
    now = datetime(2026, 5, 26, 10, 0, tzinfo=timezone.utc)
    item = store.add(
        "user-a",
        {"kind": "followup", "topic": "viaje", "earliest_at": now.isoformat(), "created_at": now.isoformat()},
    )
    store.mark_delivered(str(item["id"]), now)

    allowed, reason = should_deliver_candidate(
        store,
        "user-a",
        now + timedelta(hours=2),
        daily_cap=3,
    )

    assert allowed is False
    assert reason == "human_proactivity_cooldown"


def test_should_deliver_false_after_two_ignored_proactives() -> None:
    store = InMemoryProactiveCandidateStore()
    now = datetime(2026, 5, 26, 10, 0, tzinfo=timezone.utc)
    for offset in (14, 7):
        item = store.add(
            "user-a",
            {
                "kind": "followup",
                "topic": f"tema {offset}",
                "earliest_at": (now - timedelta(hours=offset)).isoformat(),
                "created_at": (now - timedelta(hours=offset)).isoformat(),
            },
        )
        store.mark_delivered(str(item["id"]), now - timedelta(hours=offset))

    allowed, reason = should_deliver_candidate(store, "user-a", now, daily_cap=3)

    assert allowed is False
    assert reason == "human_proactivity_low_receptivity"


def test_should_deliver_true_when_limits_pass() -> None:
    store = InMemoryProactiveCandidateStore()
    now = datetime(2026, 5, 26, 10, 0, tzinfo=timezone.utc)

    allowed, reason = should_deliver_candidate(store, "user-a", now)

    assert allowed is True
    assert reason == "human_proactivity_allowed"


def test_extract_followups_returns_empty_on_malformed_json() -> None:
    now = datetime(2026, 5, 26, 10, 0, tzinfo=timezone.utc)

    assert extract_followups("mañana tengo entrevista", now, FakeLLM("no json")) == []


def test_expired_candidate_is_not_delivered() -> None:
    store = InMemoryProactiveCandidateStore()
    now = datetime(2026, 5, 26, 10, 0, tzinfo=timezone.utc)
    store.add(
        "user-a",
        {
            "kind": "followup",
            "topic": "entrevista",
            "earliest_at": (now - timedelta(days=4)).isoformat(),
            "expires_at": (now - timedelta(minutes=1)).isoformat(),
            "created_at": (now - timedelta(days=4)).isoformat(),
        },
    )

    assert store.expire_stale("user-a", now) == 1
    assert store.pending("user-a", now) == []


def test_checkin_respects_high_threshold_and_backs_off_after_ignored_checkin() -> None:
    store = InMemoryProactiveCandidateStore()
    now = datetime(2026, 5, 26, 10, 0, tzinfo=timezone.utc)
    relation = {"last_interaction_at": (now - timedelta(days=6)).isoformat()}

    assert ensure_checkin_candidate(store, "user-a", relation, now) is None

    relation["last_interaction_at"] = (now - timedelta(days=8)).isoformat()
    first = ensure_checkin_candidate(store, "user-a", relation, now)
    assert first is not None
    store.mark_delivered(str(first["id"]), now)

    later = now + timedelta(days=8)
    relation["last_interaction_at"] = now.isoformat()
    assert ensure_checkin_candidate(store, "user-a", relation, later) is None


def test_candidates_are_isolated_by_user() -> None:
    store = InMemoryProactiveCandidateStore()
    now = datetime(2026, 5, 26, 10, 0, tzinfo=timezone.utc)
    store.add(
        "user-a",
        {"kind": "followup", "topic": "entrevista", "earliest_at": now.isoformat(), "created_at": now.isoformat()},
    )

    assert len(store.pending("user-a", now)) == 1
    assert store.pending("user-b", now) == []


def test_runtime_delivers_due_human_followup_candidate_once() -> None:
    now = datetime(2026, 5, 26, 10, 0, tzinfo=timezone.utc)
    store = InMemoryProactiveCandidateStore()
    runtime = NinoRuntime(InMemoryStateStore(), proactive_candidate_store=store, llm_client=None)
    runtime.configure_proactivity("agent-a", runtime_proactivity_settings())
    store.add(
        "agent-a",
        {
            "kind": "followup",
            "topic": "entrevista",
            "earliest_at": now.isoformat(),
            "expires_at": (now + timedelta(days=1)).isoformat(),
            "created_at": (now - timedelta(days=1)).isoformat(),
            "weight": 3,
        },
    )

    out = runtime.evaluate_proactivity("agent-a", now=now)

    assert out.should_send is True
    assert out.action is not None
    assert out.action["payload"]["source"] == "proactive_candidate"
    assert "entrevista" in out.action["payload"]["text"]
    assert "human_proactivity" in out.reason_trace
    assert store.pending("agent-a", now) == []


def runtime_proactivity_settings():
    from nino.contracts import ProactivitySettings

    return ProactivitySettings(consent="allowed", max_messages_per_day=3, min_hours_between=0)
