from __future__ import annotations

import subprocess
from pathlib import Path

from nino.product_audit import audit_product


def test_product_audit_reports_local_evidence_without_requiring_live_claude(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "nino.db"
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    db_path.write_text("db", encoding="utf-8")
    backup_dir.joinpath("nino-test.db").write_text("backup", encoding="utf-8")
    app_url = "http://nino.test"

    def fake_http_json(base_url: str, path: str, timeout: float = 2.0) -> dict:
        assert base_url == app_url
        if path == "/health":
            return {"ok": True, "service": "nino"}
        if path == "/operations/mode":
            return {"local_first": True, "storage": {"type": "sqlite", "path": str(db_path)}}
        if path == "/operations/claude":
            return {"api_key_present": False, "missing": ["NINO_LLM_PROVIDER"]}
        raise AssertionError(path)

    monkeypatch.setattr("nino.product_audit._http_json", fake_http_json)
    monkeypatch.setattr(
        "nino.product_audit.run_live_claude_probe",
        lambda require_key=False: {"ok": True, "skipped": True, "reason": "claude_not_configured"},
    )
    result = audit_product(db_path=db_path, base_url=app_url, run_local_smoke=False)

    assert result["ok"] is True
    assert {check["name"] for check in result["checks"]} >= {
        "sqlite_database_exists",
        "backup_directory_available",
        "runtime_health",
        "local_first_mode",
        "runtime_database_matches",
        "claude_config_endpoint",
        "claude_live",
    }


def test_product_audit_blocks_when_live_claude_is_required(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "nino.db"
    db_path.write_text("db", encoding="utf-8")

    def fake_http_json(_base_url: str, path: str, timeout: float = 2.0) -> dict:
        if path == "/health":
            return {"ok": True}
        if path == "/operations/mode":
            return {"local_first": True, "storage": {"type": "sqlite", "path": str(db_path)}}
        if path == "/operations/claude":
            return {"api_key_present": False, "missing": ["ANTHROPIC_API_KEY"]}
        raise AssertionError(path)

    monkeypatch.setattr("nino.product_audit._http_json", fake_http_json)
    monkeypatch.setattr(
        "nino.product_audit.run_live_claude_probe",
        lambda require_key=False: {
            "ok": False,
            "skipped": True,
            "reason": "claude_not_configured",
            "missing": ["ANTHROPIC_API_KEY"],
        },
    )

    result = audit_product(
        db_path=db_path,
        require_claude_live=True,
        run_local_smoke=False,
    )

    assert result["ok"] is False
    assert [check for check in result["checks"] if check["name"] == "claude_live"][0]["ok"] is False


def test_product_audit_blocks_when_runtime_uses_a_different_database(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "nino.db"
    other_db = tmp_path / "other.db"
    db_path.write_text("db", encoding="utf-8")
    other_db.write_text("other", encoding="utf-8")

    def fake_http_json(_base_url: str, path: str, timeout: float = 2.0) -> dict:
        if path == "/health":
            return {"ok": True}
        if path == "/operations/mode":
            return {"local_first": True, "storage": {"type": "sqlite", "path": str(other_db)}}
        if path == "/operations/claude":
            return {"api_key_present": False, "missing": []}
        raise AssertionError(path)

    monkeypatch.setattr("nino.product_audit._http_json", fake_http_json)
    monkeypatch.setattr(
        "nino.product_audit.run_live_claude_probe",
        lambda require_key=False: {"ok": True, "skipped": True},
    )

    result = audit_product(db_path=db_path, run_local_smoke=False)
    match = [check for check in result["checks"] if check["name"] == "runtime_database_matches"][0]

    assert result["ok"] is False
    assert match["ok"] is False
    assert match["evidence"]["actual_resolved"] == str(other_db.resolve())


def test_product_audit_smoke_uses_fresh_database_each_run(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "nino.db"
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    db_path.write_text("db", encoding="utf-8")
    backup_dir.joinpath("nino-test.db").write_text("backup", encoding="utf-8")

    def fake_http_json(_base_url: str, path: str, timeout: float = 2.0) -> dict:
        if path == "/health":
            return {"ok": True}
        if path == "/operations/mode":
            return {"local_first": True, "storage": {"type": "sqlite", "path": str(db_path)}}
        if path == "/operations/claude":
            return {"api_key_present": False, "missing": []}
        raise AssertionError(path)

    monkeypatch.setattr("nino.product_audit._http_json", fake_http_json)
    monkeypatch.setattr(
        "nino.product_audit.run_live_claude_probe",
        lambda require_key=False: {"ok": True, "skipped": True},
    )

    first = audit_product(db_path=db_path)
    second = audit_product(db_path=db_path)

    assert first["ok"] is True
    assert second["ok"] is True


def test_product_audit_blocks_when_launchd_is_required_but_missing(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "nino.db"
    db_path.write_text("db", encoding="utf-8")
    monkeypatch.setattr("nino.product_audit.platform.system", lambda: "Darwin")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(
        "nino.product_audit.run_live_claude_probe",
        lambda require_key=False: {"ok": True, "skipped": True},
    )

    result = audit_product(
        db_path=db_path,
        require_launchd=True,
        run_local_smoke=False,
        http_checks=False,
        launchd_label="local.nino.test",
    )
    launchd = [check for check in result["checks"] if check["name"] == "launchd_service"][0]

    assert result["ok"] is False
    assert launchd["ok"] is False
    assert launchd["evidence"]["reason"] == "plist_missing"


def test_product_audit_accepts_running_launchd_service_when_required(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "nino.db"
    db_path.write_text("db", encoding="utf-8")
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    backup_dir.joinpath("nino-test.db").write_text("backup", encoding="utf-8")
    plist = tmp_path / "Library" / "LaunchAgents" / "local.nino.test.plist"
    plist.parent.mkdir(parents=True)
    plist.write_text("<plist/>", encoding="utf-8")
    monkeypatch.setattr("nino.product_audit.platform.system", lambda: "Darwin")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(
        "nino.product_audit.run_live_claude_probe",
        lambda require_key=False: {"ok": True, "skipped": True},
    )

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 0, stdout="state = running\n", stderr="")

    monkeypatch.setattr("nino.product_audit.subprocess.run", fake_run)

    result = audit_product(
        db_path=db_path,
        require_launchd=True,
        run_local_smoke=False,
        http_checks=False,
        launchd_label="local.nino.test",
    )
    launchd = [check for check in result["checks"] if check["name"] == "launchd_service"][0]

    assert result["ok"] is True
    assert launchd["ok"] is True
    assert launchd["evidence"]["plist_exists"] is True
    assert launchd["evidence"]["launchctl_returncode"] == 0
