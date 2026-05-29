from __future__ import annotations

from nino.synthetic_companion import SCRIPT, ScriptedSyntheticUser, run_synthetic_relationship_eval


class BrokenLLM:
    def complete(self, prompt: dict) -> str:
        return "me invento una boda el martes"


def test_synthetic_user_falls_back_if_llm_changes_fixed_facts() -> None:
    user = ScriptedSyntheticUser(BrokenLLM())
    step = next(item for item in SCRIPT if item.label == "future_appointment_offer")

    assert user.say(step) == step.text


def test_synthetic_relationship_eval_covers_temporal_honesty_and_reminders() -> None:
    report = run_synthetic_relationship_eval()

    assert report["ok"] is True
    assert report["privacy"] == "synthetic_user_no_real_data"
    assert report["runtime_memory"]["writes_live_database"] is False
    assert report["metrics"]["temporal_miss_count"] >= 1
    assert report["metrics"]["invented_event_failures"] == 0
    offer = next(turn for turn in report["turns"] if turn["label"] == "future_appointment_offer")
    assert "reminder_offer" in offer["reason_trace"]
    relative = next(turn for turn in report["turns"] if turn["label"] == "relative_reminder")
    assert "direct_reminder_created" in relative["reason_trace"]
    miss = next(turn for turn in report["turns"] if turn["label"] == "empty_temporal_question")
    assert miss["temporal_miss"] is True


def test_synthetic_relationship_eval_does_not_use_deepseek_by_default() -> None:
    report = run_synthetic_relationship_eval()

    assert report["deepseek_user"] is False
    assert report["agent_id"] == "synthetic-amigo"
