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


def test_agent_profile_compacts_operational_state(tmp_path) -> None:
    runtime = create_persistent_runtime(tmp_path / "nino.db")
    runtime.tick("agent-a", {"intent": "chat", "text": "soy Pablo", "salience": 0.8})
    runtime.tick("agent-a", {"intent": "music", "text": "me gusta piano", "salience": 0.9})
    runtime.enqueue_proactive_action("agent-a", {"type": "external_message", "payload": {"text": "hola"}})

    profile = runtime.agent_profile("agent-a")

    assert profile["known_user"] == "Pablo"
    assert "piano" in profile["preferences"]
    assert profile["episode_count"] == 2
    assert profile["pending_proactive_count"] == 1


def test_prune_agents_supports_dry_run_and_prefixes(tmp_path) -> None:
    runtime = create_persistent_runtime(tmp_path / "nino.db")
    runtime.tick("demo-a", {"intent": "chat", "text": "hola"})
    runtime.tick("check-b", {"intent": "chat", "text": "hola"})
    runtime.tick("real-c", {"intent": "chat", "text": "hola"})

    dry_run = runtime.prune_agents(prefixes=["demo-", "check-"], dry_run=True)
    pruned = runtime.prune_agents(prefixes=["demo-", "check-"], dry_run=False)

    assert dry_run["matched"] == ["check-b", "demo-a"]
    assert runtime.list_agents() == ["real-c"]
    assert [item["agent_id"] for item in pruned["deleted"]] == ["check-b", "demo-a"]
