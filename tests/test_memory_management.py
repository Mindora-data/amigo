from __future__ import annotations

from nino.contracts import ConsolidationRequest, ProactivitySettings
from nino.persistence import create_persistent_runtime


def test_runtime_deletes_individual_episode_and_memory_fact(tmp_path) -> None:
    runtime = create_persistent_runtime(tmp_path / "nino.db")
    runtime.tick("agent-m", {"intent": "chat", "text": "me gusta piano", "salience": 0.9})
    episode = runtime.episode_store.list_for_agent("agent-m")[0]
    runtime.consolidate(
        ConsolidationRequest(agent_id="agent-m", episodes=runtime.episode_store.list_for_agent("agent-m"))
    )

    fact = runtime.cold_store.list_for_agent("agent-m")[0]
    episode_deleted = runtime.delete_episode("agent-m", episode.episode_id)
    fact_deleted = runtime.delete_memory_fact("agent-m", fact.fact_id)

    assert episode_deleted["deleted"] is True
    assert fact_deleted["deleted"] is True
    assert runtime.episode_store.list_for_agent("agent-m") == []
    assert runtime.cold_store.list_for_agent("agent-m") == []


def test_runtime_lists_agents_across_state_episode_and_cold_stores(tmp_path) -> None:
    runtime = create_persistent_runtime(tmp_path / "nino.db")
    runtime.tick("agent-a", {"intent": "chat", "text": "hola"})
    runtime.configure_proactivity("agent-b", ProactivitySettings(consent="allowed"))

    assert runtime.list_agents() == ["agent-a", "agent-b"]
