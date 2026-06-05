from __future__ import annotations

import argparse
import json
import os
import subprocess
from typing import Any
from urllib import error


CLAUDE_SETUP_COMMANDS = [
    "scripts/ninoctl finish --key-stdin",
    "scripts/ninoctl finish --key-env",
    "scripts/ninoctl finish --skip-configure",
    "scripts/ninoctl configure-claude --keychain-service nino-anthropic",
    "scripts/nino-launchd stop",
    "scripts/nino-launchd start",
    "scripts/ninoctl final-audit",
]


def claude_setup_commands(*, include_cd: bool = False) -> list[str]:
    commands = list(CLAUDE_SETUP_COMMANDS)
    if include_cd:
        return ["cd ~/Developer/amigo", *commands]
    return commands


def _safe_error_evidence(exc: Exception) -> dict[str, Any]:
    evidence: dict[str, Any] = {"error": exc.__class__.__name__}
    if isinstance(exc, error.HTTPError):
        evidence["http_status"] = exc.code
        try:
            body = exc.read(500).decode("utf-8", errors="replace")
        except Exception:
            body = ""
        if body:
            evidence["detail"] = body.replace("\n", " ")[:500]
    elif isinstance(exc, error.URLError):
        reason = getattr(exc, "reason", None)
        if reason:
            evidence["detail"] = str(reason)[:500]
    else:
        detail = str(exc)
        if detail:
            evidence["detail"] = detail[:500]
    return evidence


def _keychain_api_key(service: str) -> str | None:
    if not service:
        return None
    try:
        completed = subprocess.run(
            ["/usr/bin/security", "find-generic-password", "-w", "-s", service],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip() or None


def llm_config_status() -> dict[str, Any]:
    provider = os.environ.get("NINO_LLM_PROVIDER", "").strip().lower()
    normalized_provider = "claude" if provider == "anthropic" else provider
    supported_provider = provider in {"claude", "anthropic", "deepseek"}
    keychain_service = os.environ.get("NINO_KEYCHAIN_SERVICE", "").strip()
    anthropic_env_api_key_present = bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())
    keychain_api_key_present = bool(_keychain_api_key(keychain_service)) if keychain_service else False
    deepseek_api_key_present = bool(
        os.environ.get("DEEPSEEK_API_KEY", "").strip() or os.environ.get("NINO_DEEPSEEK_API_KEY", "").strip()
    )
    if normalized_provider == "deepseek":
        model = os.environ.get("NINO_DEEPSEEK_MODEL", "deepseek-chat").strip() or "deepseek-chat"
        base_url = os.environ.get("NINO_DEEPSEEK_BASE_URL", "https://api.deepseek.com/chat/completions").strip()
        api_key_present = deepseek_api_key_present
        api_key_source = "env" if api_key_present else None
    else:
        model = os.environ.get("NINO_CLAUDE_MODEL", "claude-sonnet-4-5").strip() or "claude-sonnet-4-5"
        base_url = "https://api.anthropic.com/v1/messages"
        api_key_present = anthropic_env_api_key_present or keychain_api_key_present
        api_key_source = "env" if anthropic_env_api_key_present else "keychain" if keychain_api_key_present else None
    missing: list[str] = []
    if not provider:
        missing.append("NINO_LLM_PROVIDER")
    elif not supported_provider:
        missing.append("supported_provider")
    if normalized_provider == "deepseek" and not api_key_present:
        missing.append("DEEPSEEK_API_KEY")
    elif supported_provider and not api_key_present:
        missing.append("ANTHROPIC_API_KEY")
    return {
        "enabled": supported_provider and api_key_present,
        "provider": normalized_provider or None,
        "supported_provider": supported_provider,
        "api_key_present": api_key_present,
        "api_key_source": api_key_source,
        "keychain_service": keychain_service or None,
        "model": model,
        "base_url": base_url,
        "max_tokens": 320,
        "timeout_seconds": 20.0,
        "missing": missing,
        "config_errors": [],
    }


def build_configured_llm() -> Any:
    from .llm import build_configured_llm as _build_configured_llm

    return _build_configured_llm()


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
            **_safe_error_evidence(exc),
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
