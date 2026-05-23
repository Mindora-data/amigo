from __future__ import annotations

import argparse
import json
from typing import Any

from .llm import build_configured_llm, llm_config_status


CLAUDE_SETUP_COMMANDS = [
    "scripts/ninoctl configure-claude --keychain-service nino-anthropic",
    "scripts/nino-launchd stop",
    "scripts/nino-launchd start",
    "scripts/ninoctl final-audit",
]


def claude_setup_commands(*, include_cd: bool = False) -> list[str]:
    commands = list(CLAUDE_SETUP_COMMANDS)
    if include_cd:
        return ["cd ~/Developer/bebe", *commands]
    return commands


def run_live_claude_probe(*, require_key: bool = False) -> dict[str, Any]:
    config = llm_config_status()
    if not config["enabled"]:
        return {
            "ok": not require_key,
            "skipped": True,
            "reason": "claude_not_configured",
            "missing": config["missing"],
            "configured": False,
            "setup_commands": claude_setup_commands(),
        }

    client = build_configured_llm()
    if client is None:
        return {
            "ok": False,
            "skipped": False,
            "reason": "client_not_available",
            "missing": config["missing"],
            "configured": config["enabled"],
            "setup_commands": claude_setup_commands(),
        }

    prompt = {
        "system": "Responde solo con una frase breve en español.",
        "user": "Di que Claude esta conectado a NIÑO para una prueba de producto.",
    }
    try:
        text = client.complete(prompt)
    except Exception as exc:
        return {
            "ok": False,
            "skipped": False,
            "configured": True,
            "provider": "claude",
            "model": config["model"],
            "error": exc.__class__.__name__,
        }

    return {
        "ok": bool(text),
        "skipped": False,
        "configured": True,
        "provider": "claude",
        "model": config["model"],
        "text": text,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run an optional live Claude connectivity probe.")
    parser.add_argument("--require-key", action="store_true", help="Fail when Claude env vars are missing.")
    parser.add_argument("--json", action="store_true", help="Print JSON output.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = run_live_claude_probe(require_key=args.require_key)
    if args.json:
        print(json.dumps(result, indent=2))
    elif result["ok"]:
        if result.get("skipped"):
            print(f"Claude live probe skipped: {result['reason']} ({', '.join(result.get('missing', []))})")
        else:
            print(f"Claude live probe ok: {result.get('model')}")
    else:
        print(f"Claude live probe failed: {result}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
