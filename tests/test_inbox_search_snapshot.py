from __future__ import annotations

from datetime import datetime, timezone

from nino.contracts import RetrieveRequest
from nino.persistence import create_persistent_runtime


def test_proactive_inbox_mark_delivered_and_clear(tmp_path) -> None:
    runtime = create_persistent_runtime(tmp_path / "nino.db")
    item = runtime.enqueue_proactive_action(
        "agent-i",
        {"type": "external_message", "payload": {"text": "hola"}},
        now=datetime(2026, 5, 22, 10, tzinfo=timezone.utc),
    )

    marked = runtime.mark_proactive_item_delivered("agent-i", item["id"])
    cleared = runtime.clear_delivered_proactive_items("agent-i")

    assert marked["updated"] is True
    assert cleared["cleared"] == 1
    assert runtime.list_proactive_inbox("agent-i") == []


def test_memory_search_uses_retriever(tmp_path) -> None:
    runtime = create_persistent_runtime(tmp_path / "nino.db")
    runtime.tick("agent-i", {"intent": "music", "text": "me gusta el piano", "salience": 0.9})

    out = runtime.retrieve_memory(
        "agent-i",
        RetrieveRequest(query_intent="piano", self_state={}, relation_state={}, time_scope="long"),
    )

    assert out.memory_candidates
    assert out.memory_candidates[0].statement == "me gusta el piano"


def test_development_snapshot_aggregates_agents(tmp_path) -> None:
    runtime = create_persistent_runtime(tmp_path / "nino.db")
    runtime.tick("agent-a", {"intent": "chat", "text": "hola"})
    runtime.tick("agent-b", {"intent": "music", "text": "me gusta piano"})

    snapshot = runtime.development_snapshot()

    assert snapshot["agent_count"] == 2
    assert snapshot["total_episodes"] == 2
    assert snapshot["average_maturity"] > 0
