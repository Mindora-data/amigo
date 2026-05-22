from __future__ import annotations

from nino.contracts import ConsolidationRequest
from nino.persistence import create_persistent_runtime


def test_export_import_agent_roundtrip(tmp_path) -> None:
    source = create_persistent_runtime(tmp_path / "source.db")
    source.tick("agent-x", {"intent": "chat", "text": "soy Pablo", "salience": 0.8})
    source.tick("agent-x", {"intent": "chat", "text": "me gusta piano", "salience": 0.9})
    source.consolidate(ConsolidationRequest(agent_id="agent-x", episodes=source.episode_store.list_for_agent("agent-x")))

    payload = source.export_agent("agent-x")
    target = create_persistent_runtime(tmp_path / "target.db")
    result = target.import_agent(payload, replace=True)
    state = target.load_or_init_state("agent-x")

    assert result["imported_episodes"] == 2
    assert result["imported_memory_facts"] == 1
    assert state.relation_state["user_name"] == "Pablo"
    assert len(target.episode_store.list_for_agent("agent-x")) == 2
    assert len(target.cold_store.list_for_agent("agent-x")) == 1


def test_metrics_summarize_agent_development(tmp_path) -> None:
    runtime = create_persistent_runtime(tmp_path / "nino.db")
    runtime.tick("agent-m", {"intent": "chat", "text": "soy Pablo", "salience": 0.8})
    runtime.tick("agent-m", {"intent": "chat", "text": "hoy estoy triste", "salience": 0.9})

    metrics = runtime.metrics("agent-m")

    assert metrics["tick"] == 2
    assert metrics["episode_count"] == 2
    assert metrics["emotional_signal_count"] == 1
    assert metrics["affect_mood"] == "concerned"
    assert metrics["maturity"] > 0
