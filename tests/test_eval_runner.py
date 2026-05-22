from __future__ import annotations

from pathlib import Path

from nino.eval_runner import run_eval_case, run_eval_dir


def test_eval_runner_passes_memory_regression_case() -> None:
    result = run_eval_case(Path("eval/memory_regression.json"))

    assert result["ok"] is True
    assert result["metrics"]["episode_count"] >= 3
    assert result["metrics"]["cold_memory_count"] >= 1


def test_eval_runner_runs_directory() -> None:
    result = run_eval_dir("eval")

    assert result["ok"] is True
    assert result["case_count"] >= 1
