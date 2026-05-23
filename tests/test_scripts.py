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
    assert "NINO_PYTHON" in content
    assert "ANTHROPIC_API_KEY" not in content
    assert "secret-launchd-test" not in content


def test_launchd_doctor_reports_protected_desktop_path_and_recent_error(tmp_path) -> None:
    home = tmp_path / "home"
    repo = home / "Desktop" / "bebe"
    data = repo / "data"
    data.mkdir(parents=True)
    err_file = data / "nino-launchd.err.log"
    err_file.write_text("bash: scripts/nino-launchd: Operation not permitted\n", encoding="utf-8")
    launchctl = tmp_path / "launchctl"
    launchctl.write_text(
        "#!/usr/bin/env bash\n"
        "if [[ \"$1\" == \"list\" ]]; then echo '123 0 local.nino.test'; fi\n",
        encoding="utf-8",
    )
    launchctl.chmod(0o755)

    env = {
        **os.environ,
        "HOME": str(home),
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "NINO_ROOT_DIR": str(repo),
        "NINO_LAUNCHD_LABEL": "local.nino.test",
        "NINO_ERR_FILE": str(err_file),
        "NINO_LOG_FILE": str(data / "nino-launchd.log"),
    }
    result = subprocess.run(
        [str(Path.cwd() / "scripts" / "nino-launchd"), "doctor"],
        check=True,
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )

    assert "macOS privacy-protected folder" in result.stdout
    assert "Operation not permitted" in result.stdout
    assert "local.nino.test" in result.stdout


def test_launchd_status_falls_back_to_print_when_list_has_no_details(tmp_path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    launchctl = tmp_path / "launchctl"
    launchctl.write_text(
        "#!/usr/bin/env bash\n"
        "if [[ \"$1\" == \"list\" ]]; then exit 1; fi\n"
        "if [[ \"$1\" == \"print\" ]]; then echo 'state = running'; fi\n",
        encoding="utf-8",
    )
    launchctl.chmod(0o755)

    env = {
        **os.environ,
        "HOME": str(home),
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "NINO_LAUNCHD_LABEL": "local.nino.test",
    }
    result = subprocess.run(
        ["scripts/nino-launchd", "status"],
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )

    assert "trying launchctl print" in result.stdout
    assert "state = running" in result.stdout


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


def test_ninoctl_dispatches_readiness_and_audit_commands(tmp_path) -> None:
    root = tmp_path / "repo"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    ninoctl = Path("scripts/ninoctl").read_text(encoding="utf-8")
    (scripts_dir / "ninoctl").write_text(ninoctl, encoding="utf-8")
    (scripts_dir / "ninoctl").chmod(0o755)
    calls = tmp_path / "calls.log"
    for name in ("nino-readiness", "nino-product-audit", "nino-configure-claude", "nino-disable-claude"):
        path = scripts_dir / name
        path.write_text(
            "#!/usr/bin/env bash\n"
            "printf '%s %s\\n' \"$(basename \"$0\")\" \"$*\" >> \"$NINO_CALLS_LOG\"\n",
            encoding="utf-8",
        )
        path.chmod(0o755)
    env = {
        **os.environ,
        "NINO_CALLS_LOG": str(calls),
        "NINO_PORT": "65531",
    }

    subprocess.run([str(scripts_dir / "ninoctl"), "readiness"], check=True, env=env, capture_output=True, text=True)
    subprocess.run([str(scripts_dir / "ninoctl"), "audit"], check=True, env=env, capture_output=True, text=True)
    subprocess.run([str(scripts_dir / "ninoctl"), "server-audit"], check=True, env=env, capture_output=True, text=True)
    subprocess.run([str(scripts_dir / "ninoctl"), "persistent-audit"], check=True, env=env, capture_output=True, text=True)
    subprocess.run([str(scripts_dir / "ninoctl"), "live-audit"], check=True, env=env, capture_output=True, text=True)
    subprocess.run([str(scripts_dir / "ninoctl"), "final-preflight"], check=True, env=env, capture_output=True, text=True)
    subprocess.run([str(scripts_dir / "ninoctl"), "final-audit"], check=True, env=env, capture_output=True, text=True)
    subprocess.run(
        [str(scripts_dir / "ninoctl"), "configure-claude", "--key-stdin", "--model", "claude-test"],
        check=True,
        env=env,
        input="secret\n",
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [str(scripts_dir / "ninoctl"), "disable-claude", "--remove-keychain"],
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )

    assert calls.read_text(encoding="utf-8").splitlines() == [
        "nino-readiness ",
        "nino-product-audit --skip-http --json",
        "nino-product-audit --json",
        "nino-product-audit --require-launchd --json",
        "nino-product-audit --require-claude-live --json",
        "nino-product-audit --require-launchd --require-claude-config --json",
        "nino-product-audit --require-launchd --require-claude-config --require-claude-live --json",
        "nino-configure-claude --key-stdin --model claude-test",
        "nino-disable-claude --remove-keychain",
    ]


def test_install_local_copies_runtime_and_keeps_existing_data(tmp_path) -> None:
    install_dir = tmp_path / "installed"
    data_dir = install_dir / "data"
    data_dir.mkdir(parents=True)
    existing_db = data_dir / "nino.db"
    existing_db.write_text("keep-target-db", encoding="utf-8")

    env = {
        **os.environ,
        "NINO_INSTALL_DIR": str(install_dir),
    }
    result = subprocess.run(
        ["scripts/nino-install-local", "install"],
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )

    assert "Installed local runtime" in result.stdout
    assert (install_dir / "src" / "nino" / "server.py").exists()
    assert (install_dir / "scripts" / "nino-launchd").exists()
    assert (install_dir / "README.md").exists()
    assert existing_db.read_text(encoding="utf-8") == "keep-target-db"


def test_install_local_seeds_backups_when_target_has_none(tmp_path) -> None:
    install_dir = tmp_path / "installed"
    source_data = Path("data")
    source_backup_dir = source_data / "backups"
    source_backup_dir.mkdir(parents=True, exist_ok=True)
    source_backup = source_backup_dir / "nino-test-install.db"
    source_backup.write_text("backup", encoding="utf-8")

    env = {
        **os.environ,
        "NINO_INSTALL_DIR": str(install_dir),
    }
    try:
        subprocess.run(
            ["scripts/nino-install-local", "install"],
            check=True,
            env=env,
            capture_output=True,
            text=True,
        )
        assert (install_dir / "data" / "backups" / source_backup.name).read_text(encoding="utf-8") == "backup"
    finally:
        source_backup.unlink(missing_ok=True)


def test_configure_claude_writes_untracked_env_without_printing_key(tmp_path) -> None:
    env_file = tmp_path / ".env.local"
    env_file.write_text("NINO_PORT=8010\nANTHROPIC_API_KEY=old-key\n", encoding="utf-8")

    result = subprocess.run(
        [
            "scripts/nino-configure-claude",
            "--env-file",
            str(env_file),
            "--model",
            "claude-test",
            "--key-stdin",
        ],
        check=True,
        input="new-secret-key\n",
        capture_output=True,
        text=True,
    )
    content = env_file.read_text(encoding="utf-8")

    assert "new-secret-key" in content
    assert "old-key" not in content
    assert "NINO_PORT=8010" in content
    assert "NINO_LLM_PROVIDER=claude" in content
    assert "NINO_CLAUDE_MODEL=claude-test" in content
    assert "new-secret-key" not in result.stdout
    assert oct(env_file.stat().st_mode & 0o777) == "0o600"


def test_configure_claude_can_store_key_in_keychain_without_env_secret(tmp_path) -> None:
    env_file = tmp_path / ".env.local"
    security = tmp_path / "security"
    security.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$@\" > \"$NINO_SECURITY_ARGS_FILE\"\n",
        encoding="utf-8",
    )
    security.chmod(0o755)
    args_file = tmp_path / "security.args"
    env = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "NINO_SECURITY_ARGS_FILE": str(args_file),
    }

    result = subprocess.run(
        [
            "scripts/nino-configure-claude",
            "--env-file",
            str(env_file),
            "--key-stdin",
            "--keychain-service",
            "nino-test",
        ],
        check=True,
        env=env,
        input="keychain-secret\n",
        capture_output=True,
        text=True,
    )
    content = env_file.read_text(encoding="utf-8")

    assert "ANTHROPIC_API_KEY" not in content
    assert "keychain-secret" not in content
    assert "NINO_KEYCHAIN_SERVICE=nino-test" in content
    assert "keychain-secret" not in result.stdout
    assert "nino-test" in result.stdout
    assert "keychain-secret" in args_file.read_text(encoding="utf-8")


def test_disable_claude_removes_env_settings_without_printing_key(tmp_path) -> None:
    env_file = tmp_path / ".env.local"
    env_file.write_text(
        "NINO_PORT=8010\n"
        "NINO_LLM_PROVIDER=claude\n"
        "NINO_CLAUDE_MODEL=claude-test\n"
        "ANTHROPIC_API_KEY=secret-key\n"
        "NINO_KEYCHAIN_SERVICE=nino-test\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        ["scripts/nino-disable-claude", "--env-file", str(env_file)],
        check=True,
        capture_output=True,
        text=True,
    )
    content = env_file.read_text(encoding="utf-8")

    assert "NINO_PORT=8010" in content
    assert "NINO_LLM_PROVIDER" not in content
    assert "NINO_CLAUDE_MODEL" not in content
    assert "ANTHROPIC_API_KEY" not in content
    assert "NINO_KEYCHAIN_SERVICE" not in content
    assert "secret-key" not in result.stdout
    assert oct(env_file.stat().st_mode & 0o777) == "0o600"


def test_disable_claude_can_remove_keychain_service(tmp_path) -> None:
    env_file = tmp_path / ".env.local"
    env_file.write_text("NINO_KEYCHAIN_SERVICE=nino-test\n", encoding="utf-8")
    security = tmp_path / "security"
    security.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$@\" > \"$NINO_SECURITY_ARGS_FILE\"\n",
        encoding="utf-8",
    )
    security.chmod(0o755)
    args_file = tmp_path / "security.args"
    env = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "NINO_SECURITY_ARGS_FILE": str(args_file),
    }

    result = subprocess.run(
        ["scripts/nino-disable-claude", "--env-file", str(env_file), "--remove-keychain"],
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )

    assert "NINO_KEYCHAIN_SERVICE" not in env_file.read_text(encoding="utf-8")
    assert "delete-generic-password" in args_file.read_text(encoding="utf-8")
    assert "nino-test" in args_file.read_text(encoding="utf-8")
    assert "nino-test" in result.stdout
