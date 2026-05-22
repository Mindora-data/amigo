from __future__ import annotations

import os
import subprocess
from pathlib import Path


def test_launchd_plist_does_not_embed_anthropic_key(tmp_path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    env_file = tmp_path / "nino.env"
    env_file.write_text(
        "\n".join(
            [
                "NINO_LLM_PROVIDER=claude",
                "NINO_CLAUDE_MODEL=claude-test",
                "ANTHROPIC_API_KEY=secret-launchd-test",
            ]
        ),
        encoding="utf-8",
    )
    launchctl = tmp_path / "launchctl"
    launchctl.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    launchctl.chmod(0o755)

    env = {
        **os.environ,
        "HOME": str(home),
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "NINO_ENV_FILE": str(env_file),
        "NINO_LAUNCHD_LABEL": "local.nino.test",
    }
    subprocess.run(["scripts/nino-launchd", "install"], check=True, env=env, capture_output=True, text=True)

    plist = home / "Library" / "LaunchAgents" / "local.nino.test.plist"
    content = plist.read_text(encoding="utf-8")

    assert "scripts/nino-launchd" in content
    assert "<string>run</string>" in content
    assert str(env_file) in content
    assert "ANTHROPIC_API_KEY" not in content
    assert "secret-launchd-test" not in content
