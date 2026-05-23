from __future__ import annotations

import json
import subprocess
from pathlib import Path

from nino.product_status import build_product_status, format_product_status, main


def test_product_status_summarizes_claude_blocker(monkeypatch, tmp_path, capsys) -> None:
    root = tmp_path
    report_dir = root / "data" / "reports"
    report_dir.mkdir(parents=True)
    report_path = report_dir / "nino-closing-20260523-191359.json"
    report_path.write_text(
        json.dumps(
            {
                "generated_at": "2026-05-23T19:13:59+00:00",
                "git": {"head": "abc123"},
                "summary": {"blockers": ["claude_configured"]},
            }
        ),
        encoding="utf-8",
    )
    audit = {
        "ok": False,
        "checks": [
            {"name": "launchd_service", "ok": True, "evidence": {}},
            {
                "name": "claude_configured",
                "ok": False,
                "evidence": {
                    "missing": ["NINO_LLM_PROVIDER"],
                    "setup_commands": ["scripts/ninoctl configure-claude --keychain-service nino-anthropic"],
                    "required": True,
                },
            },
        ],
    }
    eval_result = {"ok": True, "case_count": 1, "results": []}

    def fake_run(command, **kwargs):
        output = audit if "nino-product-audit" in command[0] else eval_result
        return subprocess.CompletedProcess(command, 1 if output is audit else 0, json.dumps(output), "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    status = build_product_status(root)
    assert status["ok"] is False
    assert status["final_preflight_ok"] is False
    assert status["eval_ok"] is True
    assert status["blockers"][0]["name"] == "claude_configured"
    assert status["next_commands"] == ["scripts/ninoctl configure-claude --keychain-service nino-anthropic"]
    assert status["latest_report"]["name"] == report_path.name
    assert status["latest_report"]["git_head"] == "abc123"
    assert "claude_configured missing=NINO_LLM_PROVIDER" in format_product_status(status)
    assert f"latest_report: {report_path.name}" in format_product_status(status)

    assert main(["--root", str(root)]) == 1
    assert "NIÑO product status: blocked" in capsys.readouterr().out


def test_product_status_json_output(monkeypatch, tmp_path, capsys) -> None:
    def fake_run(command, **kwargs):
        payload = {"ok": True, "checks": []} if "nino-product-audit" in command[0] else {"ok": True, "case_count": 2}
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert main(["--root", str(Path(tmp_path)), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["eval_case_count"] == 2
