from __future__ import annotations

import json
import sqlite3

from nino.persistence import create_persistent_runtime
from nino.runtime import InMemoryStateStore, NinoRuntime


def test_tick_advances_cognitive_time_and_world_model() -> None:
    runtime = NinoRuntime(InMemoryStateStore())

    out1 = runtime.tick("agent-cog", {"intent": "chat", "text": "soy Pablo", "salience": 0.8})
    out2 = runtime.tick("agent-cog", {"intent": "music", "text": "me gusta el piano", "salience": 0.9})
    state = runtime.load_or_init_state("agent-cog")

    assert out1["maturity"] > 0
    assert out2["maturity"] > out1["maturity"]
    assert state.cognitive_time["age_ticks"] == 2
    assert state.world_model["intent_counts"]["chat"] == 1
    assert state.world_model["intent_counts"]["music"] == 1
    assert state.world_model["concept_counts"]["piano"] == 1
    assert len(state.self_model["autobiographical_timeline"]) == 2


def test_policy_answers_self_model_question() -> None:
    runtime = NinoRuntime(InMemoryStateStore())
    runtime.tick("agent-cog", {"intent": "music", "text": "me gusta el piano", "salience": 0.9})

    out = runtime.tick("agent-cog", {"intent": "question", "text": "quien eres?"})

    assert "Soy NIÑO" in out["action"]["payload"]["text"]
    assert "piano" in out["action"]["payload"]["text"]
    assert "self_model_query" in out["reason_trace"]


def test_persistent_runtime_restores_cognitive_models_after_restart(tmp_path) -> None:
    db_path = tmp_path / "nino.db"
    runtime = create_persistent_runtime(db_path)
    runtime.tick("agent-cog", {"intent": "music", "text": "me gusta el piano", "salience": 0.9})

    restarted = create_persistent_runtime(db_path)
    state = restarted.load_or_init_state("agent-cog")

    assert state.cognitive_time["age_ticks"] == 1
    assert state.self_model["interaction_count"] == 1
    assert state.world_model["concept_counts"]["piano"] == 1


def test_sqlite_state_store_migrates_existing_state_rows(tmp_path) -> None:
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE agent_states (
            agent_id TEXT PRIMARY KEY,
            tick INTEGER NOT NULL,
            drive_vector_json TEXT NOT NULL,
            active_goals_json TEXT NOT NULL,
            energy REAL NOT NULL,
            relation_state_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO agent_states (
            agent_id, tick, drive_vector_json, active_goals_json,
            energy, relation_state_json, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "legacy-agent",
            3,
            json.dumps({"curiosity": 0.5}),
            json.dumps([]),
            0.7,
            json.dumps({}),
            "2026-05-21T10:00:00+00:00",
        ),
    )
    conn.commit()
    conn.close()

    runtime = create_persistent_runtime(db_path)
    state = runtime.load_or_init_state("legacy-agent")
    out = runtime.tick("legacy-agent", {"intent": "chat", "text": "hola"})

    assert state.cognitive_time["age_ticks"] == 0.0
    assert state.self_model["identity_stage"] == "early_childhood"
    assert state.world_model["concept_counts"] == {}
    assert out["tick"] == 4
