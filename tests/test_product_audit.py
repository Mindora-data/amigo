from __future__ import annotations

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
            return {"local_first": True, "storage": {"type": "sqlite"}}
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
            return {"local_first": True, "storage": {"type": "sqlite"}}
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
