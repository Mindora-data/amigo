from __future__ import annotations

from datetime import datetime, timedelta, timezone

from nino.contracts import ProactivitySettings, RetrieveRequest
from nino.internal_loop import InternalLoop
from nino.memory import Episode
from nino.persistence import create_persistent_runtime


def test_internal_cycle_consolidates_and_emits_allowed_proactive_action(tmp_path) -> None:
    now = datetime.now(timezone.utc)
    runtime = create_persistent_runtime(tmp_path / "nino.db")
    runtime.configure_proactivity(
        "agent-loop",
        ProactivitySettings(consent="allowed", max_messages_per_day=1, min_hours_between=24),
    )
    runtime.episode_store.append(
        Episode("e1", "agent-loop", now - timedelta(minutes=5), "prefiero piano", "music", 0.9, 0.9)
    )

    out = InternalLoop(runtime).cycle_once("agent-loop", now=now)
    retrieved = runtime.retrieve_memory(
        "agent-loop",
        RetrieveRequest(
            query_intent="preference piano",
            self_state={},
            relation_state={},
            time_scope="long",
        ),
    )

    assert out.consolidated_count == 1
    assert out.proactive_action is not None
    assert any(candidate.fact_id == "cold::e1" for candidate in retrieved.memory_candidates)


def test_internal_cycle_does_not_emit_proactive_action_without_consent(tmp_path) -> None:
    now = datetime.now(timezone.utc)
    runtime = create_persistent_runtime(tmp_path / "nino.db")
    runtime.episode_store.append(
        Episode("e1", "agent-loop", now - timedelta(minutes=5), "prefiero piano", "music", 0.9, 0.9)
    )

    out = InternalLoop(runtime).cycle_once("agent-loop", now=now)

    assert out.consolidated_count == 1
    assert out.proactive_action is None
    assert "proactivity_consent_required" in out.reason_trace


def test_dream_cycle_consolidates_and_creates_internal_reflection(tmp_path) -> None:
    now = datetime.now(timezone.utc)
    runtime = create_persistent_runtime(tmp_path / "nino.db")
    runtime.tick("agent-loop", {"intent": "music", "text": "me gusta el piano", "salience": 0.9})
    before = runtime.load_or_init_state("agent-loop")
    before_maturity = before.cognitive_time["maturity"]
    before_energy = before.energy

    out = InternalLoop(runtime).dream_cycle("agent-loop", now=now)
    state = runtime.load_or_init_state("agent-loop")

    assert out.consolidated_count == 1
    assert out.reflection_count == 1
    assert out.maturity > before_maturity
    assert state.energy > before_energy
    assert state.drive_vector["coherence"] > before.drive_vector["coherence"]
    assert state.self_model["dream_reflections"]
    assert "piano" in state.self_model["dream_reflections"][-1]["summary"]


def test_dream_cycle_does_not_emit_external_proactive_action(tmp_path) -> None:
    runtime = create_persistent_runtime(tmp_path / "nino.db")
    runtime.tick("agent-loop", {"intent": "question", "text": "por qué la música me calma?", "salience": 0.9})

    out = InternalLoop(runtime).dream_cycle("agent-loop")

    assert out.reason_trace == ["dream_cycle", "memory_consolidation", "self_reflection"]
