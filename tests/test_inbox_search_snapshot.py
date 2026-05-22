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


def test_action_permissions_block_unknown_external_actions(tmp_path) -> None:
    runtime = create_persistent_runtime(tmp_path / "nino.db")

    item = runtime.enqueue_proactive_action(
        "agent-i",
        {"type": "tool_call", "payload": {"name": "danger"}},
    )

    assert item["blocked"] is True
    assert runtime.list_proactive_inbox("agent-i") == []
    assert runtime.audit_log("agent-i")[-1]["type"] == "action_blocked"


def test_action_permissions_can_be_configured(tmp_path) -> None:
    runtime = create_persistent_runtime(tmp_path / "nino.db")

    configured = runtime.configure_action_permission("agent-i", "tool_call", allowed=True)
    item = runtime.enqueue_proactive_action(
        "agent-i",
        {"type": "tool_call", "payload": {"name": "safe"}},
    )

    assert configured["permissions"]["tool_call"]["allowed"] is True
    assert "blocked" not in item
    assert runtime.list_proactive_inbox("agent-i")[0]["permission"]["allowed"] is True


def test_task_queue_runs_allowed_task_into_inbox(tmp_path) -> None:
    runtime = create_persistent_runtime(tmp_path / "nino.db")
    queued = runtime.enqueue_task(
        "agent-i",
        {"type": "external_message", "payload": {"text": "recordatorio"}},
        description="recordar",
    )

    ran = runtime.run_next_task("agent-i")

    assert queued["ok"] is True
    assert ran["ok"] is True
    assert runtime.list_tasks("agent-i")[0]["status"] == "completed"
    assert runtime.list_proactive_inbox("agent-i")[0]["action"]["payload"]["text"] == "recordatorio"


def test_task_queue_blocks_disallowed_task_and_enforces_limit(tmp_path) -> None:
    runtime = create_persistent_runtime(tmp_path / "nino.db")

    blocked = runtime.enqueue_task("agent-i", {"type": "tool_call", "payload": {"name": "x"}})
    first = runtime.enqueue_task("agent-i", {"type": "external_message", "payload": {"text": "a"}}, max_pending=1)
    limited = runtime.enqueue_task("agent-i", {"type": "external_message", "payload": {"text": "b"}}, max_pending=1)

    assert blocked["ok"] is False
    assert blocked["task"]["status"] == "blocked"
    assert first["ok"] is True
    assert limited["error"] == "task_queue_limit"


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
