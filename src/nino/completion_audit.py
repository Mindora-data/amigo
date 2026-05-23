from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
from pathlib import Path
from typing import Any


def _run_json(command: list[str], root: Path) -> dict[str, Any]:
    result = subprocess.run(command, cwd=root, capture_output=True, text=True, check=False)
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        payload = {
            "ok": False,
            "parse_error": True,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
    payload.setdefault("ok", result.returncode == 0)
    payload["returncode"] = result.returncode
    if result.stderr.strip():
        payload["stderr"] = result.stderr.strip()
    return payload


def _check(audit: dict[str, Any], name: str) -> dict[str, Any]:
    for item in audit.get("checks", []):
        if item.get("name") == name:
            return item
    return {"name": name, "ok": False, "evidence": {"error": "missing_check"}}


def _smoke_has(audit: dict[str, Any], *names: str) -> bool:
    smoke = _check(audit, "local_smoke")
    checks = set(smoke.get("evidence", {}).get("checks", []))
    return smoke.get("ok") is True and all(name in checks for name in names)


def _requirement(requirement_id: str, label: str, ok: bool, evidence: list[str]) -> dict[str, Any]:
    return {
        "id": requirement_id,
        "label": label,
        "ok": ok,
        "evidence": evidence,
    }


def _living_agent_evidence(audit: dict[str, Any]) -> dict[str, Any]:
    db_path = audit.get("db_path")
    if not db_path:
        return {"ok": False, "agent_id": "nino", "error": "db_path_missing"}
    path = Path(str(db_path))
    if not path.exists():
        return {"ok": False, "agent_id": "nino", "path": str(path), "error": "db_missing"}
    try:
        with sqlite3.connect(path) as conn:
            state_count = conn.execute("SELECT COUNT(*) FROM agent_states WHERE agent_id = ?", ("nino",)).fetchone()[0]
            episode_count = conn.execute("SELECT COUNT(*) FROM episodes WHERE agent_id = ?", ("nino",)).fetchone()[0]
            fact_count = conn.execute("SELECT COUNT(*) FROM memory_facts WHERE agent_id = ?", ("nino",)).fetchone()[0]
    except sqlite3.Error as exc:
        return {"ok": False, "agent_id": "nino", "path": str(path), "error": exc.__class__.__name__}
    return {
        "ok": state_count > 0 and (episode_count > 0 or fact_count > 0),
        "agent_id": "nino",
        "path": str(path),
        "state_count": state_count,
        "episode_count": episode_count,
        "cold_memory_count": fact_count,
    }


def build_completion_audit(root: str | Path = ".") -> dict[str, Any]:
    root_path = Path(root).resolve()
    final_audit = _run_json(
        [
            str(root_path / "scripts" / "nino-product-audit"),
            "--require-launchd",
            "--require-claude-config",
            "--require-claude-live",
            "--json",
        ],
        root_path,
    )
    eval_result = _run_json([str(root_path / "scripts" / "nino-eval"), "--json"], root_path)

    sqlite_ok = _check(final_audit, "sqlite_database_exists").get("ok") is True
    launchd_ok = _check(final_audit, "launchd_service").get("ok") is True
    runtime_ok = _check(final_audit, "runtime_health").get("ok") is True
    local_first_ok = _check(final_audit, "local_first_mode").get("ok") is True
    db_match_ok = _check(final_audit, "runtime_database_matches").get("ok") is True
    claude_config_ok = _check(final_audit, "claude_configured").get("ok") is True
    claude_live = _check(final_audit, "claude_live")
    claude_live_ok = claude_live.get("ok") is True and claude_live.get("evidence", {}).get("skipped") is not True
    living_agent = _living_agent_evidence(final_audit)

    requirements = [
        _requirement(
            "runtime_persistent",
            "Runtime persistente local con launchd y SQLite alineado",
            sqlite_ok and launchd_ok and runtime_ok and local_first_ok and db_match_ok,
            ["sqlite_database_exists", "launchd_service", "runtime_health", "local_first_mode", "runtime_database_matches"],
        ),
        _requirement(
            "ui_operational",
            "UI local operativa",
            _smoke_has(final_audit, "browser_app"),
            ["local_smoke.browser_app"],
        ),
        _requirement(
            "memory_continuity",
            "Memoria y continuidad verificadas",
            _smoke_has(final_audit, "conversation_history", "memory_search") and eval_result.get("ok") is True,
            ["local_smoke.conversation_history", "local_smoke.memory_search", "nino-eval"],
        ),
        _requirement(
            "safety_controls",
            "Controles de seguridad, permisos y export seguro",
            _smoke_has(final_audit, "safe_permissions", "safe_export"),
            ["local_smoke.safe_permissions", "local_smoke.safe_export"],
        ),
        _requirement(
            "backups",
            "Backups locales verificados",
            _smoke_has(final_audit, "sqlite_backup", "sqlite_backup_list")
            and _check(final_audit, "backup_directory_available").get("ok") is True,
            ["local_smoke.sqlite_backup", "local_smoke.sqlite_backup_list", "backup_directory_available"],
        ),
        _requirement(
            "living_agent",
            "Agente vivo nino con continuidad persistida",
            living_agent["ok"] is True,
            ["agent_states.nino", "episodes.nino or memory_facts.nino"],
        ),
        _requirement(
            "regression_eval",
            "Evaluacion local de regresion",
            eval_result.get("ok") is True and eval_result.get("case_count", 0) >= 1,
            ["nino-eval"],
        ),
        _requirement(
            "closing_evidence",
            "Evidencia de cierre generable, listable y legible",
            _smoke_has(final_audit, "closing_report", "closing_report_list", "closing_report_read", "closing_report_name_guard"),
            [
                "local_smoke.closing_report",
                "local_smoke.closing_report_list",
                "local_smoke.closing_report_read",
                "local_smoke.closing_report_name_guard",
            ],
        ),
        _requirement(
            "claude_configured",
            "Claude configurado sin exponer la API key",
            claude_config_ok,
            ["claude_configured"],
        ),
        _requirement(
            "claude_live",
            "Respuesta viva de Claude validada",
            claude_live_ok,
            ["claude_live"],
        ),
    ]
    blockers = [item for item in requirements if not item["ok"]]
    return {
        "ok": not blockers,
        "requirements": requirements,
        "blockers": blockers,
        "next_commands": _next_commands(final_audit),
        "living_agent": living_agent,
        "final_audit": final_audit,
        "eval": eval_result,
    }


def _next_commands(audit: dict[str, Any]) -> list[str]:
    commands: list[str] = []
    for check in audit.get("checks", []):
        for command in check.get("evidence", {}).get("setup_commands", []):
            if command not in commands:
                commands.append(command)
    return commands


def format_completion_audit(result: dict[str, Any]) -> str:
    lines = [f"NIÑO completion audit: {'complete' if result['ok'] else 'incomplete'}"]
    for requirement in result["requirements"]:
        marker = "ok" if requirement["ok"] else "blocked"
        lines.append(f"{marker}: {requirement['id']} - {requirement['label']}")
    if result["next_commands"]:
        lines.append("next:")
        lines.extend(f"- {command}" for command in result["next_commands"])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit NIÑO completion against product requirements.")
    parser.add_argument("--root", default=".", help="Project root. Defaults to the current directory.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args(argv)

    result = build_completion_audit(args.root)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(format_completion_audit(result))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
