from __future__ import annotations

from datetime import datetime, timedelta, timezone

from nino.consolidation import Consolidator, InMemoryColdStore
from nino.contracts import ConsolidationRequest
from nino.memory import Episode
from nino.runtime import InMemoryStateStore, NinoRuntime

def test_consolidation_detects_preference_contradiction() -> None:
    runtime = NinoRuntime(InMemoryStateStore())
    runtime.tick("agent-c", {"intent": "chat", "text": "prefiero mañanas"})
    runtime.tick("agent-c", {"intent": "chat", "text": "prefiero noches"})

    episodes = runtime.episode_store.list_for_agent("agent-c")
    out = runtime.consolidate(ConsolidationRequest(agent_id="agent-c", episodes=episodes))

    assert len(out.cold_memory_updates) >= 2
    assert len(out.contradictions) >= 1
    assert out.contradictions[0]["old_value"] == "mañanas"
    assert out.contradictions[0]["new_value"] == "noches"

def test_incremental_window_and_confidence_threshold() -> None:
    cold = InMemoryColdStore()
    consolidator = Consolidator(cold)
    now = datetime.now(timezone.utc)
    episodes = [
        Episode("e-old", "a1", now - timedelta(days=3), "prefiero café", "chat", 0.8, 0.9),
        Episode("e-low", "a1", now - timedelta(hours=1), "prefiero té", "chat", 0.8, 0.2),
        Episode("e-good", "a1", now - timedelta(minutes=30), "prefiero mate", "chat", 0.8, 0.9),
    ]

    out = consolidator.consolidate(
        "a1",
        episodes,
        since=now - timedelta(hours=24),
        until=now,
        min_confidence=0.55,
    )
    assert len(out["cold_memory_updates"]) == 1
    assert out["cold_memory_updates"][0]["source_episode_id"] == "e-good"

def test_consolidation_is_idempotent_per_episode() -> None:
    cold = InMemoryColdStore()
    consolidator = Consolidator(cold)
    now = datetime.now(timezone.utc)
    episodes = [Episode("e1", "a1", now - timedelta(minutes=5), "prefiero música", "chat", 0.9, 0.9)]

    out1 = consolidator.consolidate("a1", episodes, since=now - timedelta(hours=1), until=now)
    out2 = consolidator.consolidate("a1", episodes, since=now - timedelta(hours=1), until=now)

    assert len(out1["cold_memory_updates"]) == 1
    assert len(out2["cold_memory_updates"]) == 0
    assert len(cold.list_for_agent("a1")) == 1


def test_consolidation_keeps_distinct_preferences_active() -> None:
    cold = InMemoryColdStore()
    consolidator = Consolidator(cold)
    now = datetime.now(timezone.utc)
    episodes = [
        Episode("e1", "a1", now - timedelta(minutes=5), "prefiero trabajar con sprints claros", "chat", 0.9, 0.95),
        Episode(
            "e2",
            "a1",
            now - timedelta(minutes=4),
            "prefiero que cuando avancemos me des el resultado verificado y el siguiente paso",
            "chat",
            0.9,
            0.95,
        ),
    ]

    out = consolidator.consolidate("a1", episodes, since=now - timedelta(hours=1), until=now)

    active_values = {fact.value for fact in cold.list_for_agent("a1") if fact.valid_to is None}
    assert len(out["cold_memory_updates"]) == 2
    assert out["contradictions"] == []
    assert "trabajar con sprints claros" in active_values
    assert "que cuando avancemos me des el resultado verificado y el siguiente paso" in active_values


def test_consolidation_extracts_explicit_identity_facts() -> None:
    cold = InMemoryColdStore()
    consolidator = Consolidator(cold)
    now = datetime.now(timezone.utc)
    episodes = [
        Episode(
            "e1",
            "a1",
            now - timedelta(minutes=5),
            "me llamo Mindora y mi proyecto se llama NIÑO",
            "chat",
            0.9,
            0.95,
        )
    ]

    out = consolidator.consolidate("a1", episodes, since=now - timedelta(hours=1), until=now)

    facts = {(fact.key, fact.value) for fact in cold.list_for_agent("a1") if fact.valid_to is None}
    assert len(out["cold_memory_updates"]) == 2
    assert ("user_name", "mindora") in facts
    assert ("project_name", "niño") in facts


def test_consolidation_supersedes_changed_identity_fact() -> None:
    cold = InMemoryColdStore()
    consolidator = Consolidator(cold)
    now = datetime.now(timezone.utc)
    episodes = [
        Episode("e1", "a1", now - timedelta(minutes=5), "mi proyecto se llama alfa", "chat", 0.9, 0.95),
        Episode("e2", "a1", now - timedelta(minutes=4), "mi proyecto se llama beta", "chat", 0.9, 0.95),
    ]

    out = consolidator.consolidate("a1", episodes, since=now - timedelta(hours=1), until=now)

    active = [fact for fact in cold.list_for_agent("a1") if fact.key == "project_name" and fact.valid_to is None]
    assert [fact.value for fact in active] == ["beta"]
    assert len(out["contradictions"]) == 1
    assert out["contradictions"][0]["key"] == "project_name"


def test_tick_auto_consolidates_high_confidence_preference() -> None:
    runtime = NinoRuntime(InMemoryStateStore())

    out = runtime.tick(
        "agent-c",
        {"intent": "chat", "text": "prefiero trabajar por sprints claros", "salience": 0.8, "confidence": 0.95},
    )

    facts = runtime.cold_store.list_for_agent("agent-c")
    assert out["auto_consolidated_count"] == 1
    assert out["auto_consolidation"]["cold_memory_updates"][0]["value"] == "trabajar por sprints claros"
    assert facts[0].key == "preference"
    assert facts[0].value == "trabajar por sprints claros"
    assert "auto_memory_consolidation" in out["reason_trace"]


def test_tick_auto_consolidates_high_confidence_identity_fact() -> None:
    runtime = NinoRuntime(InMemoryStateStore())

    out = runtime.tick(
        "agent-c",
        {"intent": "chat", "text": "mi proyecto se llama NIÑO", "salience": 0.8, "confidence": 0.95},
    )

    facts = runtime.cold_store.list_for_agent("agent-c")
    assert out["auto_consolidated_count"] == 1
    assert out["auto_consolidation"]["cold_memory_updates"][0]["key"] == "project_name"
    assert facts[0].value == "niño"
    assert "auto_memory_consolidation" in out["reason_trace"]


def test_tick_does_not_auto_consolidate_low_confidence_preference() -> None:
    runtime = NinoRuntime(InMemoryStateStore())

    out = runtime.tick(
        "agent-c",
        {"intent": "chat", "text": "prefiero noches", "salience": 0.8, "confidence": 0.7},
    )

    assert out["auto_consolidated_count"] == 0
    assert out["auto_consolidation"]["cold_memory_updates"] == []
    assert runtime.cold_store.list_for_agent("agent-c") == []
