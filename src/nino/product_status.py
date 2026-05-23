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

    return {
        "ok": bool(audit.get("ok")) and bool(eval_result.get("ok")),
        "final_preflight_ok": bool(audit.get("ok")),
        "eval_ok": bool(eval_result.get("ok")),
        "eval_case_count": eval_result.get("case_count", 0),
        "blockers": blockers,
        "next_commands": _setup_commands(audit),
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
