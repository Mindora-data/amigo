from __future__ import annotations

import argparse
import json
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


def _failed_checks(audit: dict[str, Any]) -> list[dict[str, Any]]:
    return [check for check in audit.get("checks", []) if not check.get("ok")]


def _setup_commands(audit: dict[str, Any]) -> list[str]:
    commands: list[str] = []
    for check in audit.get("checks", []):
        evidence = check.get("evidence", {})
        for command in evidence.get("setup_commands", []):
            if command not in commands:
                commands.append(command)
    return commands


def _recommended_next_action(ok: bool, blockers: list[dict[str, Any]], commands: list[str]) -> str:
    if ok:
        return "scripts/ninoctl final-audit"
    blocker_names = {str(blocker.get("name")) for blocker in blockers}
    if "claude_configured" in blocker_names:
        return "scripts/ninoctl finish --key-stdin"
    if "closing_evidence" in blocker_names:
        return "scripts/ninoctl closing-report"
    if "claude_live" in blocker_names:
        return "scripts/ninoctl finish --skip-configure"
    return commands[0] if commands else ""


def _metadata_value(root: Path, filename: str) -> str | None:
    path = root / filename
    if not path.exists():
        return None
    value = path.read_text(encoding="utf-8").strip()
    return value or None


def _current_revision(root: Path) -> str | None:
    revision = _metadata_value(root, "REVISION")
    if revision:
        return revision
    if not (root / ".git").exists():
        return None
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _latest_report(root: Path) -> dict[str, Any]:
    report_dir = root / "data" / "reports"
    reports = sorted(report_dir.glob("nino-closing-*.json"))
    if not reports:
        return {"ok": False, "error": "report_not_found", "report_dir": str(report_dir)}
    path = reports[-1]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"ok": False, "error": "invalid_report_json", "path": str(path), "detail": str(exc)}
    return {
        "ok": True,
        "path": str(path),
        "name": path.name,
        "generated_at": payload.get("generated_at"),
        "git_head": payload.get("git", {}).get("head"),
        "blockers": payload.get("summary", {}).get("blockers", []),
    }


def _latest_report_current(root: Path, latest_report: dict[str, Any]) -> dict[str, Any]:
    current_head = _current_revision(root)
    latest_head = latest_report.get("git_head") if latest_report.get("ok") else None
    return {
        "ok": bool(current_head and latest_head and current_head == latest_head),
        "current_head": current_head,
        "latest_report_head": latest_head,
        "report_name": latest_report.get("name") if latest_report.get("ok") else None,
        "reason": None
        if current_head and latest_head and current_head == latest_head
        else ("report_not_found" if not latest_report.get("ok") else "revision_mismatch"),
    }


def build_product_status(root: str | Path = ".") -> dict[str, Any]:
    root_path = Path(root).resolve()
    audit = _run_json(
        [str(root_path / "scripts" / "nino-product-audit"), "--require-launchd", "--require-claude-config", "--json"],
        root_path,
    )
    eval_result = _run_json([str(root_path / "scripts" / "nino-eval"), "--json"], root_path)
    blockers = []
    for check in _failed_checks(audit):
        evidence = check.get("evidence", {})
        blockers.append(
            {
                "name": check.get("name"),
                "missing": evidence.get("missing", []),
                "reason": evidence.get("reason"),
                "required": evidence.get("required", False),
            }
        )

    commands = _setup_commands(audit)
    latest_report = _latest_report(root_path)
    latest_report_current = _latest_report_current(root_path, latest_report)
    if latest_report_current.get("ok") is not True:
        blockers.append(
            {
                "name": "closing_evidence",
                "missing": ["latest_report_current"],
                "reason": latest_report_current.get("reason"),
                "required": True,
            }
        )
    ok = bool(audit.get("ok")) and bool(eval_result.get("ok")) and latest_report_current.get("ok") is True
    return {
        "ok": ok,
        "final_preflight_ok": bool(audit.get("ok")),
        "eval_ok": bool(eval_result.get("ok")),
        "eval_case_count": eval_result.get("case_count", 0),
        "blockers": blockers,
        "next_commands": commands,
        "recommended_next_action": _recommended_next_action(ok, blockers, commands),
        "latest_report": latest_report,
        "latest_report_current": latest_report_current,
        "audit": audit,
        "eval": eval_result,
    }


def format_product_status(status: dict[str, Any]) -> str:
    lines = [
        f"NIÑO product status: {'ready' if status['ok'] else 'blocked'}",
        f"final_preflight: {'ok' if status['final_preflight_ok'] else 'blocked'}",
        f"local_eval: {'ok' if status['eval_ok'] else 'failed'} ({status['eval_case_count']} cases)",
    ]
    blockers = status.get("blockers", [])
    if blockers:
        lines.append("blockers:")
        for blocker in blockers:
            missing = ", ".join(blocker.get("missing") or [])
            detail = f" missing={missing}" if missing else ""
            lines.append(f"- {blocker['name']}{detail}")
    latest_report = status.get("latest_report", {})
    if latest_report.get("ok"):
        head = str(latest_report.get("git_head") or "")[:8]
        blockers = ", ".join(latest_report.get("blockers") or [])
        detail_parts = [part for part in [f"head={head}" if head else "", f"blockers={blockers}" if blockers else ""] if part]
        detail = f" ({'; '.join(detail_parts)})" if detail_parts else ""
        lines.append(f"latest_report: {latest_report['name']}{detail}")
    report_current = status.get("latest_report_current", {})
    if report_current:
        current_head = str(report_current.get("current_head") or "")[:8]
        latest_head = str(report_current.get("latest_report_head") or "")[:8]
        if report_current.get("ok"):
            lines.append(f"latest_report_current: ok (head={current_head})")
        else:
            detail = f"current={current_head or 'unknown'} report={latest_head or 'missing'}"
            lines.append(f"latest_report_current: stale ({detail})")
    if status.get("recommended_next_action"):
        lines.append(f"recommended_next_action: {status['recommended_next_action']}")
    commands = status.get("next_commands", [])
    if commands:
        lines.append("next:")
        lines.extend(f"- {command}" for command in commands)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Summarize NIÑO product readiness.")
    parser.add_argument("--root", default=".", help="Project root. Defaults to the current directory.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args(argv)

    status = build_product_status(args.root)
    if args.json:
        print(json.dumps(status, indent=2))
    else:
        print(format_product_status(status))
    return 0 if status["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
