from __future__ import annotations

import argparse
import json
import os
import platform
from pathlib import Path
import subprocess
import tempfile
from typing import Any
from urllib import error, request

from .claude_live import run_live_claude_probe


def _http_json(base_url: str, path: str, timeout: float = 2.0) -> dict[str, Any]:
    with request.urlopen(f"{base_url.rstrip('/')}{path}", timeout=timeout) as res:
        return json.loads(res.read().decode("utf-8"))


def _check(name: str, ok: bool, evidence: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"name": name, "ok": ok, "evidence": evidence or {}}


def _summarize_launchctl_output(output: str) -> str:
    safe_prefixes = ("state =", "path =", "program =", "working directory =")
    safe_lines = []
    for line in output.splitlines():
        stripped = line.strip()
        if any(stripped.startswith(prefix) for prefix in safe_prefixes):
            safe_lines.append(stripped)
    return "\n".join(safe_lines)[:500]


def _launchd_check(*, require_launchd: bool, label: str) -> dict[str, Any]:
    if platform.system() != "Darwin":
        return _check(
            "launchd_service",
            not require_launchd,
            {"skipped": True, "reason": "not_macos", "required": require_launchd, "label": label},
        )

    plist = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
    evidence: dict[str, Any] = {
        "label": label,
        "plist": str(plist),
        "plist_exists": plist.exists(),
        "required": require_launchd,
    }
    if not plist.exists():
        evidence["skipped"] = not require_launchd
        evidence["reason"] = "plist_missing"
        return _check("launchd_service", not require_launchd, evidence)

    try:
        result = subprocess.run(
            ["launchctl", "print", f"gui/{os.getuid()}/{label}"],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
        evidence["launchctl_returncode"] = result.returncode
        evidence["launchctl_output"] = _summarize_launchctl_output(result.stdout or result.stderr)
        running = result.returncode == 0
    except (OSError, subprocess.TimeoutExpired) as exc:
        evidence["error"] = exc.__class__.__name__
        running = False

    return _check("launchd_service", running if require_launchd else True, evidence)


def _storage_path_from_mode(mode: dict[str, Any]) -> str | None:
    path = mode.get("storage", {}).get("path")
    return path if isinstance(path, str) and path else None


def _same_database_path(expected: Path, actual: str | None) -> dict[str, Any]:
    evidence = {
        "expected": str(expected),
        "expected_resolved": str(expected.resolve()),
        "actual": actual,
        "actual_resolved": str(Path(actual).resolve()) if actual else None,
    }
    return _check(
        "runtime_database_matches",
        bool(actual) and Path(actual).resolve() == expected.resolve(),
        evidence,
    )


def _audit_profile(
    *,
    require_launchd: bool,
    require_claude_config: bool,
    require_claude_live: bool,
    http_checks: bool,
    run_local_smoke: bool,
) -> dict[str, Any]:
    strict_final = require_launchd and require_claude_live and http_checks
    requirements = ["sqlite_database_exists", "backup_directory_available", "claude_live"]
    if require_launchd:
        requirements.append("launchd_service")
    if run_local_smoke:
        requirements.append("local_smoke")
    if http_checks:
        requirements.extend(["runtime_health", "local_first_mode", "runtime_database_matches", "claude_config_endpoint"])
    if require_claude_config or require_claude_live:
        requirements.append("claude_configured")
    return {
        "name": "final" if strict_final else "local",
        "strict_final": strict_final,
        "required_checks": requirements,
    }


def _claude_configured_check(claude: dict[str, Any], *, required: bool) -> dict[str, Any]:
    configured = claude.get("configured") is True and not claude.get("config_errors")
    evidence = {
        "configured": claude.get("configured"),
        "runtime_enabled": claude.get("runtime_enabled"),
        "provider": claude.get("provider"),
        "model": claude.get("model"),
        "api_key_present": claude.get("api_key_present"),
        "api_key_source": claude.get("api_key_source"),
        "keychain_service": claude.get("keychain_service"),
        "missing": claude.get("missing", []),
        "config_errors": claude.get("config_errors", []),
        "setup_commands": claude.get("setup_commands", []),
        "required": required,
    }
    return _check("claude_configured", configured if required else True, evidence)


def audit_product(
    *,
    db_path: str | Path,
    base_url: str = "http://127.0.0.1:8000",
    require_claude_config: bool = False,
    require_claude_live: bool = False,
    require_launchd: bool = False,
    launchd_label: str = "local.nino.server",
    run_local_smoke: bool = True,
    http_checks: bool = True,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    db = Path(db_path)

    checks.append(_check("sqlite_database_exists", db.exists(), {"path": str(db)}))

    backup_dir = db.parent / "backups"
    claude_config_required = require_claude_config or require_claude_live
    checks.append(
        _check(
            "backup_directory_available",
            backup_dir.exists(),
            {"path": str(backup_dir), "count": len(list(backup_dir.glob("*.db"))) if backup_dir.exists() else 0},
        )
    )
    checks.append(_launchd_check(require_launchd=require_launchd, label=launchd_label))

    if run_local_smoke:
        from .smoke import run_smoke

        with tempfile.TemporaryDirectory(prefix="nino-audit-smoke-") as tmp:
            smoke = run_smoke(Path(tmp) / "audit-smoke.db")
        checks.append(_check("local_smoke", smoke.ok, {"checks": smoke.checks}))

    if http_checks:
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
            checks.append(_same_database_path(db, _storage_path_from_mode(mode)))
        except (OSError, error.URLError, TimeoutError) as exc:
            checks.append(_check("local_first_mode", False, {"error": exc.__class__.__name__}))
            checks.append(_check("runtime_database_matches", False, {"error": exc.__class__.__name__}))

        try:
            claude = _http_json(base_url, "/operations/claude")
            checks.append(_check("claude_config_endpoint", "api_key_present" in claude and "missing" in claude, claude))
            if claude_config_required:
                checks.append(_claude_configured_check(claude, required=True))
        except (OSError, error.URLError, TimeoutError) as exc:
            checks.append(_check("claude_config_endpoint", False, {"error": exc.__class__.__name__}))
            if claude_config_required:
                checks.append(_check("claude_configured", False, {"error": exc.__class__.__name__, "required": True}))
    elif claude_config_required:
        checks.append(_check("claude_configured", False, {"reason": "http_checks_disabled", "required": True}))

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
        "require_claude_config": require_claude_config,
        "require_claude_live": require_claude_live,
        "require_launchd": require_launchd,
        "audit_profile": _audit_profile(
            require_launchd=require_launchd,
            require_claude_config=require_claude_config,
            require_claude_live=require_claude_live,
            http_checks=http_checks,
            run_local_smoke=run_local_smoke,
        ),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit NIÑO local product readiness with concrete evidence.")
    parser.add_argument("--db", default=os.environ.get("NINO_DB_PATH", "data/nino.db"))
    parser.add_argument("--base-url", default=os.environ.get("NINO_BASE_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--require-claude-config", action="store_true")
    parser.add_argument("--require-claude-live", action="store_true")
    parser.add_argument("--require-launchd", action="store_true")
    parser.add_argument("--launchd-label", default=os.environ.get("NINO_LAUNCHD_LABEL", "local.nino.server"))
    parser.add_argument("--skip-smoke", action="store_true")
    parser.add_argument("--skip-http", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = audit_product(
        db_path=args.db,
        base_url=args.base_url,
        require_claude_config=args.require_claude_config,
        require_claude_live=args.require_claude_live,
        require_launchd=args.require_launchd,
        launchd_label=args.launchd_label,
        run_local_smoke=not args.skip_smoke,
        http_checks=not args.skip_http,
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
