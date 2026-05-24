from __future__ import annotations

import json
import os
import subprocess

from nino.smoke import run_smoke


def test_product_smoke_runs_against_temp_sqlite(tmp_path) -> None:
    result = run_smoke(tmp_path / "smoke.db")

    assert result.ok is True
    assert "browser_app" in result.checks
    assert "conversation_history" in result.checks
    assert "safe_permissions" in result.checks
    assert "sqlite_backup" in result.checks
    assert "closing_report" in result.checks
    assert "closing_report_read" in result.checks
    assert "closing_report_latest" in result.checks
    assert "closing_report_name_guard" in result.checks
    assert "next_action" in result.checks


def test_product_smoke_stays_local_first_with_claude_env(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("NINO_LLM_PROVIDER", "claude")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret-key")
    monkeypatch.setenv("NINO_KEYCHAIN_SERVICE", "nino-test")

    result = run_smoke(tmp_path / "smoke.db")

    assert result.ok is True
    assert "claude_diagnostic" in result.checks
    assert os.environ["NINO_LLM_PROVIDER"] == "claude"
    assert os.environ["ANTHROPIC_API_KEY"] == "secret-key"
    assert os.environ["NINO_KEYCHAIN_SERVICE"] == "nino-test"


def test_product_smoke_cli_outputs_json() -> None:
    completed = subprocess.run(
        ["scripts/nino-smoke", "--json"],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["ok"] is True
    assert "claude_diagnostic" in payload["checks"]
    assert "closing_report_list" in payload["checks"]
    assert "closing_report_latest" in payload["checks"]
    assert "next_action" in payload["checks"]
