from __future__ import annotations

import json
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
    assert "closing_report_name_guard" in result.checks


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
