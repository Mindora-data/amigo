from __future__ import annotations

from nino.internal_loop import InternalLoop
from nino.persistence import create_persistent_runtime
from nino.runtime import InMemoryStateStore, NinoRuntime


def test_emotional_signal_updates_relation_and_self_model() -> None:
    runtime = NinoRuntime(InMemoryStateStore())

    out = runtime.tick("agent-a", {"intent": "chat", "text": "hoy estoy triste", "salience": 0.9})
    state = runtime.load_or_init_state("agent-a")

    assert "Te noto triste" in out["action"]["payload"]["text"]
    assert "emotional_signal" in out["reason_trace"]
    assert state.relation_state["last_emotional_tone"] == "sad"
    assert state.self_model["affect_state"]["mood"] == "concerned"


def test_emotional_state_persists_after_restart(tmp_path) -> None:
    db_path = tmp_path / "nino.db"
    runtime = create_persistent_runtime(db_path)
    runtime.tick("agent-a", {"intent": "chat", "text": "estoy agobiado", "salience": 0.8})

    restarted = create_persistent_runtime(db_path)
    state = restarted.load_or_init_state("agent-a")

    assert state.relation_state["last_emotional_tone"] == "stressed"
    assert state.self_model["affect_state"]["mood"] == "attentive"


def test_build_narrative_summarizes_identity_relation_and_dreams(tmp_path) -> None:
    runtime = create_persistent_runtime(tmp_path / "nino.db")
    runtime.tick("agent-a", {"intent": "chat", "text": "soy Pablo", "salience": 0.8})
    runtime.tick("agent-a", {"intent": "music", "text": "me gusta el piano", "salience": 0.9})
    InternalLoop(runtime).dream_cycle("agent-a")

    narrative = runtime.build_narrative("agent-a")

    assert narrative["known_user"] == "Pablo"
    assert "piano" in narrative["preferences"]
    assert "piano" in narrative["dominant_concepts"]
    assert narrative["dream_reflection_count"] == 1
    assert "Soy NIÑO" in narrative["summary"]
