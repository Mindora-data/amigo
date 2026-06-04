from __future__ import annotations

import json
import subprocess
from urllib.error import URLError

from nino.claude_live import claude_setup_commands, run_live_claude_probe


def test_claude_setup_commands_can_include_runtime_directory() -> None:
    commands = claude_setup_commands(include_cd=True)

    assert commands[0] == "cd ~/Developer/amigo"
    assert "scripts/ninoctl finish --key-stdin" in commands
    assert "scripts/ninoctl finish --key-env" in commands
    assert "scripts/ninoctl finish --skip-configure" in commands
    assert "scripts/ninoctl configure-claude --keychain-service nino-anthropic" in commands
    assert commands[-1] == "scripts/ninoctl final-audit"


def test_live_claude_probe_skips_without_config(monkeypatch) -> None:
    monkeypatch.delenv("NINO_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    result = run_live_claude_probe()

    assert result["ok"] is True
    assert result["skipped"] is True
    assert result["reason"] == "claude_not_configured"
    assert "scripts/ninoctl finish --key-stdin" in result["setup_commands"]
    assert "scripts/ninoctl finish --key-env" in result["setup_commands"]
    assert "scripts/ninoctl finish --skip-configure" in result["setup_commands"]
    assert "scripts/ninoctl configure-claude --keychain-service nino-anthropic" in result["setup_commands"]
    assert "scripts/ninoctl final-audit" in result["setup_commands"]


def test_live_claude_probe_can_require_key(monkeypatch) -> None:
    monkeypatch.setenv("NINO_LLM_PROVIDER", "claude")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    result = run_live_claude_probe(require_key=True)

    assert result["ok"] is False
    assert result["skipped"] is True
    assert result["missing"] == ["ANTHROPIC_API_KEY"]
    assert "scripts/ninoctl final-audit" in result["setup_commands"]


def test_live_claude_probe_reports_safe_url_error_detail(monkeypatch) -> None:
    class FailingClient:
        def complete(self, _prompt):
            raise URLError("network unreachable")

    monkeypatch.setattr(
        "nino.claude_live.llm_config_status",
        lambda: {
            "enabled": True,
            "missing": [],
            "model": "claude-test",
        },
    )
    monkeypatch.setattr("nino.claude_live.build_configured_llm", lambda: FailingClient())

    result = run_live_claude_probe(require_key=True)

    assert result["ok"] is False
    assert result["error"] == "URLError"
    assert result["detail"] == "network unreachable"
    assert "setup_commands" not in result


def test_live_claude_probe_script_outputs_skip_json() -> None:
    completed = subprocess.run(
        ["scripts/nino-claude-live", "--json"],
        check=True,
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "NINO_ENV_FILE": "/tmp/does-not-exist"},
    )
    payload = json.loads(completed.stdout)

    assert payload["ok"] is True
    assert payload["skipped"] is True
    assert "scripts/ninoctl final-audit" in payload["setup_commands"]
