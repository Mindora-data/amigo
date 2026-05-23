from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile
from typing import Any
from urllib import error, request

from .claude_live import run_live_claude_probe
from .smoke import run_smoke


def _http_json(base_url: str, path: str, timeout: float = 2.0) -> dict[str, Any]:
    with request.urlopen(f"{base_url.rstrip('/')}{path}", timeout=timeout) as res:
        return json.loads(res.read().decode("utf-8"))


def _check(name: str, ok: bool, evidence: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"name": name, "ok": ok, "evidence": evidence or {}}


def audit_product(
    *,
    db_path: str | Path,
    base_url: str = "http://127.0.0.1:8000",
    require_claude_live: bool = False,
    run_local_smoke: bool = True,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    db = Path(db_path)

    checks.append(_check("sqlite_database_exists", db.exists(), {"path": str(db)}))

    backup_dir = db.parent / "backups"
    checks.append(
        _check(
            "backup_directory_available",
            backup_dir.exists(),
            {"path": str(backup_dir), "count": len(list(backup_dir.glob("*.db"))) if backup_dir.exists() else 0},
        )
    )

    if run_local_smoke:
        with tempfile.TemporaryDirectory(prefix="nino-audit-smoke-") as tmp:
            smoke = run_smoke(Path(tmp) / "audit-smoke.db")
        checks.append(_check("local_smoke", smoke.ok, {"checks": smoke.checks}))

    try:
        health = _http_json(base_url, "/health")
        checks.append(_check("runtime_health", health.get("ok") is True, health))
    except (OSError, error.URLError, TimeoutError) as exc:
        checks.append(_check("runtime_health", False, {"error": exc.__class__.__name__}))

    try:
        mode = _http_json(base_url, "/operations/mode")
        checks.append(
            _check(
                "local_first_mode",
                mode.get("local_first") is True and mode.get("storage", {}).get("type") == "sqlite",
                mode,
            )
        )
    except (OSError, error.URLError, TimeoutError) as exc:
        checks.append(_check("local_first_mode", False, {"error": exc.__class__.__name__}))

    try:
        claude = _http_json(base_url, "/operations/claude")
        checks.append(_check("claude_config_endpoint", "api_key_present" in claude and "missing" in claude, claude))
    except (OSError, error.URLError, TimeoutError) as exc:
        checks.append(_check("claude_config_endpoint", False, {"error": exc.__class__.__name__}))

    live = run_live_claude_probe(require_key=require_claude_live)
    checks.append(
        _check(
            "claude_live",
            live["ok"],
            {key: value for key, value in live.items() if key != "text"},
        )
    )

    return {
        "ok": all(check["ok"] for check in checks),
        "checks": checks,
        "base_url": base_url,
        "db_path": str(db),
        "require_claude_live": require_claude_live,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit NIÑO local product readiness with concrete evidence.")
    parser.add_argument("--db", default=os.environ.get("NINO_DB_PATH", "data/nino.db"))
    parser.add_argument("--base-url", default=os.environ.get("NINO_BASE_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--require-claude-live", action="store_true")
    parser.add_argument("--skip-smoke", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = audit_product(
        db_path=args.db,
        base_url=args.base_url,
        require_claude_live=args.require_claude_live,
        run_local_smoke=not args.skip_smoke,
    )
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        for check in result["checks"]:
            marker = "ok" if check["ok"] else "blocked"
            print(f"{marker}: {check['name']}")
        print(f"product_audit: {'ok' if result['ok'] else 'blocked'}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
