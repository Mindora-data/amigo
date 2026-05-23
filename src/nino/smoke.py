from __future__ import annotations

import argparse
from dataclasses import dataclass
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable, Iterable

from .api import create_app


WsgiApp = Callable[[dict[str, Any], Callable[[str, list[tuple[str, str]]], None]], Iterable[bytes]]


@dataclass(slots=True)
class SmokeResult:
    ok: bool
    checks: list[str]
    db_path: str


def _request(app: WsgiApp, method: str, path: str, payload: dict[str, Any] | None = None) -> tuple[str, Any]:
    data = b"" if payload is None else json.dumps(payload).encode("utf-8")
    captured: dict[str, str] = {}

    def start_response(status: str, headers: list[tuple[str, str]]) -> None:
        captured["status"] = status
        captured["content_type"] = dict(headers).get("Content-Type", "")

    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "CONTENT_LENGTH": str(len(data)),
        "wsgi.input": BytesIO(data),
    }
    body = b"".join(app(environ, start_response))
    status = captured["status"]
    if captured.get("content_type", "").startswith("text/html"):
        return status, body.decode("utf-8")
    return status, json.loads(body.decode("utf-8"))


def _require(condition: bool, checks: list[str], name: str) -> None:
    if not condition:
        raise AssertionError(name)
    checks.append(name)


def run_smoke(db_path: str | Path) -> SmokeResult:
    db_path = Path(db_path)
    app = create_app(db_path)
    checks: list[str] = []

    status, health = _request(app, "GET", "/health")
    _require(status.startswith("200") and health == {"ok": True, "service": "nino"}, checks, "health")

    status, mode = _request(app, "GET", "/operations/mode")
    _require(status.startswith("200") and mode["local_first"] is True, checks, "local_first_mode")
    _require(mode["storage"]["type"] == "sqlite", checks, "sqlite_storage")

    status, claude = _request(app, "GET", "/operations/claude")
    _require(status.startswith("200") and claude["api_key_present"] is False, checks, "claude_diagnostic")

    status, html = _request(app, "GET", "/app")
    _require(status.startswith("200") and "/operations/audit" in html and "/tasks/run-next" in html, checks, "browser_app")

    status, first = _request(
        app,
        "POST",
        "/agents/smoke/tick",
        {"intent": "chat", "text": "soy Smoke y prefiero piano", "salience": 0.9, "confidence": 0.9},
    )
    _require(status.startswith("200") and first["tick"] == 1, checks, "first_tick")

    status, second = _request(
        app,
        "POST",
        "/agents/smoke/tick",
        {"intent": "question", "text": "que recuerdas de mi?", "salience": 0.7, "confidence": 0.9},
    )
    _require(status.startswith("200") and "action" in second, checks, "second_tick_response")

    status, conversation = _request(app, "GET", "/agents/smoke/conversation")
    _require(status.startswith("200") and [turn["role"] for turn in conversation["turns"]] == ["user", "assistant", "user", "assistant"], checks, "conversation_history")

    status, search = _request(
        app,
        "POST",
        "/agents/smoke/memory/search",
        {"query_intent": "piano", "time_scope": "long"},
    )
    _require(status.startswith("200") and search["memory_candidates"], checks, "memory_search")

    status, permissions = _request(app, "GET", "/agents/smoke/permissions")
    _require(status.startswith("200") and permissions["permissions"]["tool_call"]["allowed"] is False, checks, "safe_permissions")

    status, queued = _request(app, "POST", "/agents/smoke/tasks", {"text": "recordar revisar piano"})
    _require(status.startswith("200") and queued["ok"] is True, checks, "task_enqueue")

    status, ran = _request(app, "POST", "/agents/smoke/tasks/run-next", {})
    _require(status.startswith("200") and ran["ok"] is True, checks, "task_run")

    status, inbox = _request(app, "GET", "/agents/smoke/proactivity/inbox")
    _require(status.startswith("200") and len(inbox["inbox"]) == 1, checks, "proactive_inbox")

    status, safe_export = _request(app, "GET", "/agents/smoke/export-safe")
    _require(status.startswith("200") and safe_export["export"]["agent_id"] == "smoke", checks, "safe_export")

    status, backup = _request(app, "POST", "/operations/backup", {})
    _require(status.startswith("200") and backup["ok"] is True and Path(backup["path"]).exists(), checks, "sqlite_backup")
    status, backups = _request(app, "GET", "/operations/backups")
    _require(status.startswith("200") and backups["backups"], checks, "sqlite_backup_list")

    return SmokeResult(ok=True, checks=checks, db_path=str(db_path))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a deterministic local NIÑO product smoke test.")
    parser.add_argument("--db", default="", help="SQLite database path. Defaults to a temporary smoke DB.")
    parser.add_argument("--json", action="store_true", help="Print JSON output.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.db:
            result = run_smoke(args.db)
        else:
            with TemporaryDirectory(prefix="nino-smoke-") as tmp:
                result = run_smoke(Path(tmp) / "nino-smoke.db")
    except Exception as exc:
        payload = {"ok": False, "error": exc.__class__.__name__, "detail": str(exc)}
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            print(f"NIÑO smoke failed: {payload['error']} {payload['detail']}")
        return 1

    payload = {"ok": result.ok, "checks": result.checks, "db_path": result.db_path}
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"NIÑO smoke ok: {len(result.checks)} checks")
        for check in result.checks:
            print(f"- {check}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
