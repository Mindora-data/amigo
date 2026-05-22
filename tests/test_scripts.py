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


def test_ninoctl_can_list_and_restore_backups(tmp_path) -> None:
    data_dir = tmp_path / "data"
    backup_dir = data_dir / "backups"
    db_path = data_dir / "nino.db"
    backup_dir.mkdir(parents=True)
    db_path.write_text("current", encoding="utf-8")
    backup_path = backup_dir / "nino-backup.db"
    backup_path.write_text("restored", encoding="utf-8")
    env = {
        **os.environ,
        "NINO_DATA_DIR": str(data_dir),
        "NINO_DB_PATH": str(db_path),
        "NINO_BACKUP_DIR": str(backup_dir),
        "NINO_PORT": "65530",
    }

    listed = subprocess.run(
        ["scripts/ninoctl", "backups"],
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )
    restored = subprocess.run(
        ["scripts/ninoctl", "restore", str(backup_path)],
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )

    assert str(backup_path) in listed.stdout
    assert "Database restored from" in restored.stdout
    assert db_path.read_text(encoding="utf-8") == "restored"
    assert list(backup_dir.glob("pre-restore-*.db"))
