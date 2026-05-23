from __future__ import annotations

import json
import subprocess
from pathlib import Path

from nino.product_status import build_product_status, format_product_status, main


def test_product_status_summarizes_claude_blocker(monkeypatch, tmp_path, capsys) -> None:
    root = tmp_path
    (root / "REVISION").write_text("abc123", encoding="utf-8")
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
                    "setup_commands": [
                        "scripts/ninoctl finish --key-stdin",
                        "scripts/ninoctl finish --skip-configure",
                        "scripts/ninoctl configure-claude --keychain-service nino-anthropic",
                        "scripts/ninoctl final-audit",
                        "scripts/ninoctl finish --key-env",
                    ],
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
    assert status["next_commands"] == [
        "scripts/ninoctl finish --key-stdin",
        "scripts/ninoctl finish --key-env",
        "scripts/ninoctl finish --skip-configure",
        "scripts/ninoctl configure-claude --keychain-service nino-anthropic",
        "scripts/ninoctl final-audit",
    ]
    assert status["recommended_next_action"] == "scripts/ninoctl finish --key-stdin"
    assert status["latest_report"]["name"] == report_path.name
    assert status["latest_report"]["git_head"] == "abc123"
    assert status["latest_report_current"]["ok"] is True
    assert status["latest_report_current"]["current_head"] == "abc123"
    assert status["latest_report_current"]["latest_report_head"] == "abc123"
    assert "claude_configured missing=NINO_LLM_PROVIDER" in format_product_status(status)
    assert f"latest_report: {report_path.name}" in format_product_status(status)
    assert "head=abc123" in format_product_status(status)
    assert "latest_report_current: ok (head=abc123)" in format_product_status(status)
    assert "blockers=claude_configured" in format_product_status(status)
    assert "recommended_next_action: scripts/ninoctl finish --key-stdin" in format_product_status(status)

    assert main(["--root", str(root)]) == 1
    assert "NIÑO product status: blocked" in capsys.readouterr().out


def test_product_status_json_output(monkeypatch, tmp_path, capsys) -> None:
    def fake_run(command, **kwargs):
        payload = {"ok": True, "checks": []} if "nino-product-audit" in command[0] else {"ok": True, "case_count": 2}
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert main(["--root", str(Path(tmp_path)), "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["eval_case_count"] == 2
    assert any(blocker["name"] == "closing_evidence" for blocker in payload["blockers"])
    assert payload["latest_report_current"]["ok"] is False
    assert payload["latest_report_current"]["reason"] == "report_not_found"
    assert payload["recommended_next_action"] == "scripts/ninoctl closing-report"

    assert main(["--root", str(Path(tmp_path)), "--next-action"]) == 0
    assert capsys.readouterr().out.strip() == "scripts/ninoctl closing-report"


def test_product_status_recommends_skip_configure_when_only_claude_live_is_blocked(monkeypatch, tmp_path) -> None:
    root = tmp_path
    (root / "REVISION").write_text("abc123", encoding="utf-8")
    report_dir = root / "data" / "reports"
    report_dir.mkdir(parents=True)
    (report_dir / "nino-closing-20260523-191359.json").write_text(
        json.dumps({"generated_at": "2026-05-23T19:13:59+00:00", "git": {"head": "abc123"}, "summary": {"blockers": ["claude_live"]}}),
        encoding="utf-8",
    )
    audit = {
        "ok": False,
        "checks": [
            {"name": "claude_configured", "ok": True, "evidence": {}},
            {
                "name": "claude_live",
                "ok": False,
                "evidence": {"setup_commands": ["scripts/ninoctl finish --skip-configure"], "required": True},
            },
        ],
    }

    def fake_run(command, **kwargs):
        output = audit if "nino-product-audit" in command[0] else {"ok": True, "case_count": 1, "results": []}
        return subprocess.CompletedProcess(command, 1 if output is audit else 0, json.dumps(output), "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    status = build_product_status(root)
    assert status["recommended_next_action"] == "scripts/ninoctl finish --skip-configure"
