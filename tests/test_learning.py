from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from nino.contracts import ProactivitySettings
from nino.learning import distill_to_global, starting_prior
from nino.persistence import create_persistent_runtime
from nino.runtime import InMemoryGlobalModelStore, InMemoryStateStore, NinoRuntime


def test_distill_to_global_rejects_invalid_vocab() -> None:
    store = InMemoryGlobalModelStore()

    with pytest.raises(ValueError, match="invalid_gesture"):
        distill_to_global("texto libre Pablo entrevista", "dia_neutro", "positive", store)
    with pytest.raises(ValueError, match="invalid_context"):
        distill_to_global("checkin", "Madrid dentista 11", "positive", store)
    with pytest.raises(ValueError, match="invalid_outcome"):
        distill_to_global("checkin", "dia_neutro", "maybe", store)


def test_repeated_bump_increments_count_without_duplicate_rows() -> None:
    store = InMemoryGlobalModelStore()

    distill_to_global("followup", "evento_con_carga", "positive", store)
    distill_to_global("followup", "evento_con_carga", "positive", store)
    model = store.get()

    outcomes = model["pattern_outcomes"]
    assert len(outcomes) == 1
    assert outcomes["followup:evento_con_carga:positive"]["count"] == 2


def test_starting_prior_returns_neutral_below_min_sample() -> None:
    store = InMemoryGlobalModelStore()
    for _ in range(10):
        distill_to_global("checkin", "dia_neutro", "positive", store)

    assert starting_prior("checkin", "dia_neutro", store) == 0.5


def test_starting_prior_uses_aggregate_after_min_sample() -> None:
    store = InMemoryGlobalModelStore()
    for _ in range(40):
        distill_to_global("checkin", "dia_neutro", "ignored", store)
    for _ in range(10):
        distill_to_global("checkin", "dia_neutro", "positive", store)

    assert starting_prior("checkin", "dia_neutro", store) == 0.2


def test_global_pattern_outcome_does_not_store_private_text(tmp_path) -> None:
    runtime = create_persistent_runtime(tmp_path / "nino.db")
    runtime.tick(
        "agent-private",
        {
            "intent": "chat",
            "text": "soy Pablo y mañana tengo dentista en Madrid a las 11",
            "now": "2026-05-26T10:00:00+02:00",
        },
    )

    distill_to_global("followup", "evento_con_carga", "positive", runtime.global_model_store)
    rendered = str(runtime.global_model()["pattern_outcomes"]).lower()

    assert "pablo" not in rendered
    assert "madrid" not in rendered
    assert "dentista" not in rendered
    assert "11" not in rendered
    assert "user::" not in rendered


def test_persistent_global_pattern_table_is_aggregate_without_user_id(tmp_path) -> None:
    runtime = create_persistent_runtime(tmp_path / "nino.db")

    distill_to_global("checkin", "dia_neutro", "ignored", runtime.global_model_store)
    distill_to_global("checkin", "dia_neutro", "ignored", runtime.global_model_store)
    restarted = create_persistent_runtime(tmp_path / "nino.db")
    model = restarted.global_model()

    assert model["pattern_outcomes"]["checkin:dia_neutro:ignored"]["count"] == 2
    assert "user_id" not in str(model["pattern_outcomes"]).lower()
    assert restarted.global_model_store.global_pattern_stats("checkin", "dia_neutro") == {
        "positive": 0,
        "ignored": 2,
        "stop": 0,
        "total": 2,
    }


def test_global_prior_changes_new_friend_checkin_threshold_but_user_relation_overrides() -> None:
    now = datetime(2026, 5, 26, 10, 0, tzinfo=timezone.utc)
    low_prior_store = InMemoryGlobalModelStore()
    for _ in range(50):
        distill_to_global("checkin", "dia_neutro", "ignored", low_prior_store)
    neutral = NinoRuntime(InMemoryStateStore(), llm_client=None)
    low_prior = NinoRuntime(InMemoryStateStore(), global_model_store=low_prior_store, llm_client=None)
    for runtime in (neutral, low_prior):
        runtime.configure_proactivity("agent-a", ProactivitySettings(consent="allowed", max_messages_per_day=3, min_hours_between=0))
        state = runtime.load_or_init_state("agent-a")
        state.relation_state["last_interaction_at"] = (now - timedelta(days=8)).isoformat()
        runtime.state_store.put(state)

    neutral_out = neutral.evaluate_proactivity("agent-a", now=now)
    low_prior_out = low_prior.evaluate_proactivity("agent-a", now=now)

    assert neutral_out.should_send is True
    assert low_prior_out.should_send is False

    item = low_prior.proactive_candidate_store.add(
        "agent-a",
        {
            "kind": "checkin",
            "topic": "abrir una puerta tranquila tras varios días sin hablar",
            "earliest_at": (now - timedelta(hours=1)).isoformat(),
            "created_at": (now - timedelta(hours=1)).isoformat(),
        },
    )
    low_prior.proactive_candidate_store.mark_delivered(str(item["id"]), now - timedelta(hours=7))
    low_prior.tick("agent-a", {"intent": "chat", "text": "estoy aquí", "now": now.isoformat()})
    assert starting_prior("checkin", "dia_neutro", low_prior.global_model_store) < 0.5
    assert low_prior.proactive_candidate_store.recent_delivered("agent-a", 1)[0]["user_reacted"] == 1
