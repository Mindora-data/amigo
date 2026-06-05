from __future__ import annotations

from datetime import datetime, timedelta, timezone

from nino.contracts import ConsolidationRequest, ProactivitySettings, RetrieveRequest
from nino.memory import Episode
from nino.persistence import create_persistent_runtime


def test_persistent_runtime_restores_state_and_episodes_after_restart(tmp_path) -> None:
    db_path = tmp_path / "nino.db"
    runtime = create_persistent_runtime(db_path)

    out = runtime.tick("agent-db", {"intent": "music", "text": "me gusta piano", "salience": 0.9})

    restarted = create_persistent_runtime(db_path)
    state = restarted.load_or_init_state("agent-db")
    episodes = restarted.episode_store.list_for_agent("agent-db")

    assert out["tick"] == 1
    assert state.tick == 1
    assert state.energy < 0.8
    assert len(episodes) == 1
    assert episodes[0].text == "me gusta piano"


def test_persistent_runtime_restores_relation_state_after_restart(tmp_path) -> None:
    db_path = tmp_path / "nino.db"
    runtime = create_persistent_runtime(db_path)
    runtime.tick("agent-db", {"intent": "chat", "text": "soy Pablo", "confidence": 0.9})
    runtime.tick("agent-db", {"intent": "chat", "text": "me gusta el piano", "salience": 0.9})

    restarted = create_persistent_runtime(db_path)
    state = restarted.load_or_init_state("agent-db")

    assert state.relation_state["user_name"] == "Pablo"
    assert state.relation_state["interaction_count"] == 2
    assert "piano" in state.relation_state["preferences"]


def test_persistent_runtime_restores_cold_memory_after_restart(tmp_path) -> None:
    db_path = tmp_path / "nino.db"
    now = datetime.now(timezone.utc)
    runtime = create_persistent_runtime(db_path)
    runtime.episode_store.append(
        Episode("e1", "agent-db", now - timedelta(minutes=5), "prefiero mate", "chat", 0.9, 0.9)
    )
    episodes = runtime.episode_store.list_for_agent("agent-db")

    consolidated = runtime.consolidate(ConsolidationRequest(agent_id="agent-db", episodes=episodes))

    restarted = create_persistent_runtime(db_path)
    req = RetrieveRequest(query_intent="preference mate", self_state={}, relation_state={}, time_scope="long")
    retrieved = restarted.retrieve_memory("agent-db", req)

    assert len(consolidated.cold_memory_updates) == 1
    assert any(candidate.fact_id == "cold::e1" for candidate in retrieved.memory_candidates)


def test_persistent_runtime_restores_proactivity_settings_and_send_history(tmp_path) -> None:
    db_path = tmp_path / "nino.db"
    now = datetime(2026, 5, 21, 10, tzinfo=timezone.utc)
    runtime = create_persistent_runtime(db_path)
    runtime.configure_proactivity(
        "agent-db",
        ProactivitySettings(consent="allowed", max_messages_per_day=1, min_hours_between=24),
    )
    runtime.episode_store.append(
        Episode("e1", "agent-db", now - timedelta(hours=26), "mañana tengo examen", "school", 0.9, 0.9)
    )

    first = runtime.evaluate_proactivity("agent-db", now=now)

    restarted = create_persistent_runtime(db_path)
    second = restarted.evaluate_proactivity("agent-db", now=now + timedelta(hours=1))

    assert first.should_send is True
    assert second.should_send is False
    assert "daily_frequency_cap" in second.reason_trace


def test_persistent_runtime_restores_proactive_candidates_after_restart(tmp_path) -> None:
    db_path = tmp_path / "nino.db"
    now = datetime(2026, 5, 21, 10, tzinfo=timezone.utc)
    runtime = create_persistent_runtime(db_path)
    runtime.configure_proactivity(
        "agent-db",
        ProactivitySettings(consent="allowed", max_messages_per_day=3, min_hours_between=0),
    )
    runtime.proactive_candidate_store.add(
        "agent-db",
        {
            "kind": "followup",
            "topic": "entrevista",
            "earliest_at": now.isoformat(),
            "expires_at": (now + timedelta(days=1)).isoformat(),
            "created_at": (now - timedelta(days=1)).isoformat(),
            "weight": 3,
        },
    )

    restarted = create_persistent_runtime(db_path)
    out = restarted.evaluate_proactivity("agent-db", now=now)
    pending = restarted.proactive_candidate_store.pending("agent-db", now)

    assert out.should_send is True
    assert out.action is not None
    assert out.action["payload"]["source"] == "proactive_candidate"
    assert pending == []


def test_persistent_runtime_reset_agent_deletes_state_episodes_and_cold_memory(tmp_path) -> None:
    db_path = tmp_path / "nino.db"
    runtime = create_persistent_runtime(db_path)
    runtime.tick("agent-db", {"intent": "chat", "text": "me gusta piano", "salience": 0.9})
    runtime.proactive_candidate_store.add(
        "agent-db",
        {
            "kind": "followup",
            "topic": "entrevista",
            "earliest_at": datetime.now(timezone.utc).isoformat(),
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    episodes = runtime.episode_store.list_for_agent("agent-db")
    runtime.consolidate(ConsolidationRequest(agent_id="agent-db", episodes=episodes))

    deleted = runtime.reset_agent("agent-db")
    restarted = create_persistent_runtime(db_path)
    state = restarted.load_or_init_state("agent-db")

    assert deleted["episodes"] == 1
    assert deleted["cold_memory"] == 1
    assert deleted["proactive_candidates"] == 1
    assert state.tick == 0
    assert restarted.episode_store.list_for_agent("agent-db") == []
    assert restarted.cold_store.list_for_agent("agent-db") == []
