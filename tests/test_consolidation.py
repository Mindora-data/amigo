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
