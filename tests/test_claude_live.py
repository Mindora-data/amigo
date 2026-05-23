from __future__ import annotations

import json
import subprocess

from nino.claude_live import claude_setup_commands, run_live_claude_probe


def test_claude_setup_commands_can_include_runtime_directory() -> None:
    commands = claude_setup_commands(include_cd=True)

    assert commands[0] == "cd ~/Developer/bebe"
    assert "scripts/ninoctl configure-claude --keychain-service nino-anthropic" in commands
    assert commands[-1] == "scripts/ninoctl final-audit"


def test_live_claude_probe_skips_without_config(monkeypatch) -> None:
    monkeypatch.delenv("NINO_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    result = run_live_claude_probe()

    assert result["ok"] is True
    assert result["skipped"] is True
    assert result["reason"] == "claude_not_configured"
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
