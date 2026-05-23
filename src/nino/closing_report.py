from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .completion_audit import build_completion_audit
from .product_status import build_product_status


def _git(command: list[str], root: Path) -> str | None:
    try:
        result = subprocess.run(["git", *command], cwd=root, check=False, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _metadata_value(root: Path, filename: str) -> str | None:
    path = root / filename
    if not path.exists():
        return None
    value = path.read_text(encoding="utf-8").strip()
    return value or None


def _run_json(command: list[str], root: Path) -> dict[str, Any]:
    result = subprocess.run(command, cwd=root, capture_output=True, text=True, check=False)
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        payload = {"ok": False, "stdout": result.stdout, "stderr": result.stderr}
    payload.setdefault("ok", result.returncode == 0)
    return payload


def build_closing_report(root: str | Path = ".") -> dict[str, Any]:
    root_path = Path(root).resolve()
    product_status = build_product_status(root_path)
    completion_audit = build_completion_audit(root_path)
    nino_profile = _run_json(
        [str(root_path / "scripts" / "nino-http-json"), "/agents/nino/profile"],
        root_path,
    )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "root": str(root_path),
        "git": {
            "branch": _git(["branch", "--show-current"], root_path) or _metadata_value(root_path, "BRANCH"),
            "head": _git(["rev-parse", "HEAD"], root_path) or _metadata_value(root_path, "REVISION"),
            "dirty": bool(_git(["status", "--short"], root_path)),
        },
        "summary": {
            "ok": bool(completion_audit.get("ok")),
            "product_status_ok": bool(product_status.get("ok")),
            "completion_audit_ok": bool(completion_audit.get("ok")),
            "blockers": [item.get("id") or item.get("name") for item in completion_audit.get("blockers", [])],
            "next_commands": completion_audit.get("next_commands", []),
        },
        "nino_profile": nino_profile,
        "product_status": product_status,
        "completion_audit": completion_audit,
    }


def write_closing_report(root: str | Path = ".", output: str | Path | None = None) -> Path:
    root_path = Path(root).resolve()
    report = build_closing_report(root_path)
    if output is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        output_path = root_path / "data" / "reports" / f"nino-closing-{stamp}.json"
    else:
        output_path = Path(output)
        if not output_path.is_absolute():
            output_path = root_path / output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return output_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write a NIÑO closing evidence report.")
    parser.add_argument("--root", default=".", help="Project root. Defaults to the current directory.")
    parser.add_argument("--output", default="", help="Output JSON path. Defaults to data/reports/nino-closing-*.json.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable summary.")
    args = parser.parse_args(argv)

    output = write_closing_report(args.root, args.output or None)
    payload = {"ok": True, "path": str(output)}
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"NIÑO closing report written: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
