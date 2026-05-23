from __future__ import annotations

import json
import sqlite3
import subprocess

from nino.completion_audit import build_completion_audit, format_completion_audit, main


def _audit_payload() -> dict:
    return {
        "ok": False,
        "checks": [
            {"name": "sqlite_database_exists", "ok": True, "evidence": {}},
            {"name": "backup_directory_available", "ok": True, "evidence": {}},
            {"name": "launchd_service", "ok": True, "evidence": {}},
            {
                "name": "local_smoke",
                "ok": True,
                "evidence": {
                    "checks": [
                        "browser_app",
                        "conversation_history",
                        "memory_search",
                        "safe_permissions",
                        "safe_export",
                        "sqlite_backup",
                        "sqlite_backup_list",
                        "closing_report",
                        "closing_report_list",
                        "closing_report_read",
                        "closing_report_name_guard",
                    ]
                },
            },
            {"name": "runtime_health", "ok": True, "evidence": {}},
            {"name": "local_first_mode", "ok": True, "evidence": {}},
            {"name": "runtime_database_matches", "ok": True, "evidence": {}},
            {
                "name": "claude_configured",
                "ok": False,
                "evidence": {
                    "missing": ["NINO_LLM_PROVIDER"],
                    "setup_commands": ["scripts/ninoctl finish --key-stdin"],
                    "required": True,
                },
            },
            {
                "name": "claude_live",
                "ok": False,
                "evidence": {
                    "skipped": True,
                    "setup_commands": ["scripts/ninoctl finish --key-stdin"],
                },
            },
        ],
    }


def test_completion_audit_maps_requirements_and_blockers(monkeypatch, tmp_path, capsys) -> None:
    db_path = tmp_path / "nino.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE agent_states (agent_id TEXT PRIMARY KEY)")
        conn.execute("CREATE TABLE episodes (agent_id TEXT)")
        conn.execute("CREATE TABLE memory_facts (agent_id TEXT)")
        conn.execute("INSERT INTO agent_states (agent_id) VALUES ('nino')")
        conn.execute("INSERT INTO episodes (agent_id) VALUES ('nino')")
    audit_payload = {**_audit_payload(), "db_path": str(db_path)}

    def fake_run(command, **kwargs):
        payload = audit_payload if "nino-product-audit" in command[0] else {"ok": True, "case_count": 1}
        return subprocess.CompletedProcess(command, 1 if payload["ok"] is False else 0, json.dumps(payload), "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = build_completion_audit(tmp_path)
    assert result["ok"] is False
    blocked = {item["id"] for item in result["blockers"]}
    assert blocked == {"claude_configured", "claude_live"}
    assert any(item["id"] == "closing_evidence" and item["ok"] for item in result["requirements"])
    assert result["living_agent"]["episode_count"] == 1
    assert "scripts/ninoctl finish --key-stdin" in result["next_commands"]
    assert "blocked: claude_live" in format_completion_audit(result)

    assert main(["--root", str(tmp_path)]) == 1
    assert "NIÑO completion audit: incomplete" in capsys.readouterr().out


def test_completion_audit_json_output(monkeypatch, tmp_path, capsys) -> None:
    db_path = tmp_path / "nino.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE agent_states (agent_id TEXT PRIMARY KEY)")
        conn.execute("CREATE TABLE episodes (agent_id TEXT)")
        conn.execute("CREATE TABLE memory_facts (agent_id TEXT)")
        conn.execute("INSERT INTO agent_states (agent_id) VALUES ('nino')")
        conn.execute("INSERT INTO memory_facts (agent_id) VALUES ('nino')")
    audit = _audit_payload()
    audit["db_path"] = str(db_path)
    for check in audit["checks"]:
        if check["name"] in {"claude_configured", "claude_live"}:
            check["ok"] = True
            check["evidence"] = {"skipped": False}
    audit["ok"] = True

    def fake_run(command, **kwargs):
        payload = audit if "nino-product-audit" in command[0] else {"ok": True, "case_count": 1}
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert main(["--root", str(tmp_path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert all(item["ok"] for item in payload["requirements"])
