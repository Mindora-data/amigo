from __future__ import annotations

from pathlib import Path

from nino.eval_runner import run_eval_case, run_eval_dir
from nino.eval_runner import main


def test_eval_runner_passes_memory_regression_case() -> None:
    result = run_eval_case(Path("eval/memory_regression.json"))

    assert result["ok"] is True
    assert result["metrics"]["episode_count"] >= 3
    assert result["metrics"]["cold_memory_count"] >= 1


def test_eval_runner_runs_directory() -> None:
    result = run_eval_dir("eval")

    assert result["ok"] is True
    assert result["case_count"] >= 1


def test_eval_runner_cli_outputs_json(capsys) -> None:
    code = main(["eval", "--json"])
    out = capsys.readouterr().out

    assert code == 0
    assert '"ok": true' in out
    assert '"case_count":' in out
