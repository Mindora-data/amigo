from __future__ import annotations

import json
from pathlib import Path

from nino.closing_report import build_closing_report, main, write_closing_report


def test_closing_report_writes_summary(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.setattr(
        "nino.closing_report.build_product_status",
        lambda root: {"ok": False, "blockers": [{"name": "claude_configured"}]},
    )
    monkeypatch.setattr(
        "nino.closing_report.build_completion_audit",
        lambda root: {
            "ok": False,
            "blockers": [{"id": "claude_live"}],
            "next_commands": ["scripts/ninoctl finish --key-stdin"],
        },
    )
    monkeypatch.setattr("nino.closing_report._run_json", lambda command, root: {"ok": True, "profile": {"agent_id": "nino"}})
    monkeypatch.setattr("nino.closing_report._git", lambda command, root: "main" if command[0] == "branch" else "abc123")

    path = write_closing_report(tmp_path, "report.json")
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["summary"]["ok"] is False
    assert payload["summary"]["blockers"] == ["claude_live"]
    assert payload["nino_profile"]["profile"]["agent_id"] == "nino"
    assert payload["report_file"]["path"] == str(path)
    assert payload["report_file"]["name"] == "report.json"

    assert main(["--root", str(tmp_path), "--output", "report-2.json", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["path"].endswith("report-2.json")


def test_closing_report_default_path(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("nino.closing_report.build_closing_report", lambda root, report_path=None: {"ok": True})

    path = write_closing_report(tmp_path)

    assert path.parent == tmp_path / "data" / "reports"
    assert path.name.startswith("nino-closing-")
    assert path.suffix == ".json"


def test_closing_report_reads_installed_revision(monkeypatch, tmp_path) -> None:
    (tmp_path / "REVISION").write_text("installed-head\n", encoding="utf-8")
    (tmp_path / "BRANCH").write_text("main\n", encoding="utf-8")
    monkeypatch.setattr("nino.closing_report.build_product_status", lambda root: {"ok": True})
    monkeypatch.setattr("nino.closing_report.build_completion_audit", lambda root: {"ok": True, "blockers": [], "next_commands": []})
    monkeypatch.setattr("nino.closing_report._run_json", lambda command, root: {"ok": True})
    monkeypatch.setattr("nino.closing_report._git", lambda command, root: None)

    report = build_closing_report(tmp_path, tmp_path / "data" / "reports" / "nino-closing-20260523-191902.json")

    assert report["git"]["head"] == "installed-head"
    assert report["git"]["branch"] == "main"
    assert report["report_file"]["name"] == "nino-closing-20260523-191902.json"
