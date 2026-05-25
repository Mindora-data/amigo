from __future__ import annotations

import os
import json
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


def test_scripts_default_to_macos_system_cert_bundle() -> None:
    ninoctl = Path("scripts/ninoctl").read_text(encoding="utf-8")
    launchd = Path("scripts/nino-launchd").read_text(encoding="utf-8")

    assert "SSL_CERT_FILE=/etc/ssl/cert.pem" in ninoctl
    assert "SSL_CERT_FILE=/etc/ssl/cert.pem" in launchd


def test_readme_documents_memory_search_cli_examples() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")

    assert 'scripts/ninoctl memory-search --agent nino --type cold "donde vivo"' in readme
    assert "Use `--json`" in readme


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


def test_ninoctl_can_list_and_read_closing_reports(tmp_path) -> None:
    data_dir = tmp_path / "data"
    report_dir = data_dir / "reports"
    report_dir.mkdir(parents=True)
    report_path = report_dir / "nino-closing-20260523-120000.json"
    report_path.write_text('{"ok": true}\n', encoding="utf-8")
    env = {
        **os.environ,
        "NINO_DATA_DIR": str(data_dir),
        "NINO_REPORT_DIR": str(report_dir),
    }

    listed = subprocess.run(
        ["scripts/ninoctl", "reports"],
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )
    read = subprocess.run(
        ["scripts/ninoctl", "report", report_path.name],
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )
    latest = subprocess.run(
        ["scripts/ninoctl", "report", "latest"],
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )
    invalid = subprocess.run(
        ["scripts/ninoctl", "report", "../nino.db"],
        check=False,
        env=env,
        capture_output=True,
        text=True,
    )

    assert str(report_path) in listed.stdout
    assert json.loads(read.stdout) == {"ok": True}
    assert json.loads(latest.stdout) == {"ok": True}
    assert invalid.returncode == 2
    assert "Invalid report name" in invalid.stderr


def test_ninoctl_status_reports_launchd_service_when_pid_file_is_absent(tmp_path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    launchd_log = data_dir / "nino-launchd.log"
    launchd_log.write_text("served by launchd\n", encoding="utf-8")
    launchctl = tmp_path / "launchctl"
    launchctl.write_text(
        "#!/usr/bin/env bash\n"
        "if [[ \"$1\" == \"print\" ]]; then\n"
        "  echo 'state = running'\n"
        "  echo 'pid = 4242'\n"
        f"  echo 'working directory = {Path.cwd()}'\n"
        "fi\n",
        encoding="utf-8",
    )
    launchctl.chmod(0o755)
    curl = tmp_path / "curl"
    curl.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    curl.chmod(0o755)
    pgrep = tmp_path / "pgrep"
    pgrep.write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
    pgrep.chmod(0o755)

    env = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "NINO_DATA_DIR": str(data_dir),
        "NINO_PID_FILE": str(data_dir / "missing.pid"),
        "NINO_LOG_FILE": str(data_dir / "nino-server.log"),
        "NINO_LAUNCHD_LOG_FILE": str(launchd_log),
        "NINO_LAUNCHD_LABEL": "local.nino.test",
        "NINO_PORT": "65532",
    }

    result = subprocess.run(
        ["scripts/ninoctl", "status"],
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )
    logs = subprocess.run(
        ["scripts/ninoctl", "logs"],
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )

    assert "process: running via launchd, pid 4242" in result.stdout
    assert "launchd: running (local.nino.test)" in result.stdout
    assert f"log: {launchd_log}" in result.stdout
    assert "served by launchd" in logs.stdout


def test_ninoctl_status_ignores_launchd_service_for_another_root(tmp_path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    other_root = tmp_path / "other-runtime"
    launchctl = tmp_path / "launchctl"
    launchctl.write_text(
        "#!/usr/bin/env bash\n"
        "if [[ \"$1\" == \"print\" ]]; then\n"
        "  echo 'state = running'\n"
        "  echo 'pid = 4242'\n"
        f"  echo 'working directory = {other_root}'\n"
        "fi\n",
        encoding="utf-8",
    )
    launchctl.chmod(0o755)
    curl = tmp_path / "curl"
    curl.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    curl.chmod(0o755)
    pgrep = tmp_path / "pgrep"
    pgrep.write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
    pgrep.chmod(0o755)

    env = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "NINO_DATA_DIR": str(data_dir),
        "NINO_PID_FILE": str(data_dir / "missing.pid"),
        "NINO_LOG_FILE": str(data_dir / "nino-server.log"),
        "NINO_LAUNCHD_LOG_FILE": str(data_dir / "nino-launchd.log"),
        "NINO_LAUNCHD_LABEL": "local.nino.test",
        "NINO_PORT": "65533",
    }

    result = subprocess.run(
        ["scripts/ninoctl", "status"],
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )

    assert "process: not running under ninoctl" in result.stdout
    assert "launchd: running" not in result.stdout
    assert f"log: {data_dir / 'nino-server.log'}" in result.stdout


def test_ninoctl_status_ignores_stale_pid_for_another_database(tmp_path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    pid_file = data_dir / "nino.pid"
    pid_file.write_text("4242\n", encoding="utf-8")
    kill = tmp_path / "kill"
    kill.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    kill.chmod(0o755)
    ps = tmp_path / "ps"
    ps.write_text(
        "#!/usr/bin/env bash\n"
        "echo '/usr/bin/python3 -m nino.server --db /tmp/other.db --host 127.0.0.1 --port 65534'\n",
        encoding="utf-8",
    )
    ps.chmod(0o755)
    curl = tmp_path / "curl"
    curl.write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
    curl.chmod(0o755)
    pgrep = tmp_path / "pgrep"
    pgrep.write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
    pgrep.chmod(0o755)

    env = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "NINO_DATA_DIR": str(data_dir),
        "NINO_PID_FILE": str(pid_file),
        "NINO_DB_PATH": str(data_dir / "nino.db"),
        "NINO_PORT": "65534",
    }

    result = subprocess.run(
        ["scripts/ninoctl", "status"],
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )

    assert "process: not running under ninoctl" in result.stdout
    assert "pid 4242" not in result.stdout


def test_ninoctl_wait_health_waits_for_server_response(tmp_path) -> None:
    calls = tmp_path / "curl-calls"
    curl = tmp_path / "curl"
    curl.write_text(
        "#!/usr/bin/env bash\n"
        "count=0\n"
        "if [[ -f \"$NINO_CURL_CALLS\" ]]; then count=$(cat \"$NINO_CURL_CALLS\"); fi\n"
        "count=$((count + 1))\n"
        "echo \"$count\" > \"$NINO_CURL_CALLS\"\n"
        "if [[ \"$count\" -lt 2 ]]; then exit 1; fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    curl.chmod(0o755)

    env = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "NINO_CURL_CALLS": str(calls),
        "NINO_PORT": "65535",
    }

    result = subprocess.run(
        ["scripts/ninoctl", "wait-health", "3"],
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )

    assert "health: ok at http://127.0.0.1:65535" in result.stdout
    assert calls.read_text(encoding="utf-8").strip() == "2"


def test_ninoctl_status_and_wait_health_report_curl_error_detail(tmp_path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    curl = tmp_path / "curl"
    curl.write_text(
        "#!/usr/bin/env bash\n"
        "echo 'curl: (7) Operation not permitted' >&2\n"
        "exit 7\n",
        encoding="utf-8",
    )
    curl.chmod(0o755)
    pgrep = tmp_path / "pgrep"
    pgrep.write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
    pgrep.chmod(0o755)
    launchctl = tmp_path / "launchctl"
    launchctl.write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
    launchctl.chmod(0o755)

    env = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "NINO_DATA_DIR": str(data_dir),
        "NINO_PORT": "65529",
    }

    status = subprocess.run(
        ["scripts/ninoctl", "status"],
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )
    waited = subprocess.run(
        ["scripts/ninoctl", "wait-health", "1"],
        check=False,
        env=env,
        capture_output=True,
        text=True,
    )

    assert "health: not responding at http://127.0.0.1:65529 (curl: (7) Operation not permitted)" in status.stdout
    assert waited.returncode == 1
    assert "health: not responding at http://127.0.0.1:65529 after 1s (curl: (7) Operation not permitted)" in waited.stderr


def test_ninoctl_memory_search_posts_type_filter(tmp_path) -> None:
    calls = tmp_path / "curl-calls.log"
    curl = tmp_path / "curl"
    curl.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" > \"$NINO_CURL_CALLS\"\n"
        "printf '{\"memory_candidates\": [], \"memory_type_filter\": \"cold\"}\\n'\n",
        encoding="utf-8",
    )
    curl.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "NINO_CURL_CALLS": str(calls),
        "NINO_PORT": "65528",
    }

    result = subprocess.run(
        ["scripts/ninoctl", "memory-search", "--agent", "api-agent", "--type", "cold", "--json", "donde vivo"],
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )

    call = calls.read_text(encoding="utf-8")
    assert "-X POST http://127.0.0.1:65528/agents/api-agent/memory/search" in call
    assert "-H Content-Type: application/json" in call
    assert '"query_intent":"donde vivo"' in call
    assert '"memory_type_filter":"cold"' in call
    assert json.loads(result.stdout)["memory_type_filter"] == "cold"


def test_ninoctl_memory_search_prints_readable_summary(tmp_path) -> None:
    curl = tmp_path / "curl"
    curl.write_text(
        "#!/usr/bin/env bash\n"
        "cat <<'JSON'\n"
        '{"memory_candidates":[{"memory_type":"cold","statement":"ubicacion madrid","score":0.8,"confidence":0.95,"source_episode_id":"abc123"}],"memory_type_counts":{"cold":1,"hot":1,"total":2},"visible_memory_type_counts":{"cold":1,"hot":0,"total":1},"visible_candidates":1,"memory_type_filter":"cold"}\n'
        "JSON\n",
        encoding="utf-8",
    )
    curl.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "NINO_PORT": "65528",
    }

    result = subprocess.run(
        ["scripts/ninoctl", "memory-search", "--type", "cold", "donde vivo"],
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )

    assert "memory: 1/2 (cold 1/1, hot 0/1, filter cold)" in result.stdout
    assert "- [cold] ubicacion madrid" in result.stdout
    assert "score=0.800 confidence=0.950 source=abc123" in result.stdout


def test_ninoctl_memory_facts_lists_active_cold_memory(tmp_path) -> None:
    curl = tmp_path / "curl"
    curl.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" > \"$NINO_CURL_CALLS\"\n"
        "cat <<'JSON'\n"
        '{"facts":[{"key":"preference","value":"sprints","confidence":0.95,"source_episode_id":"ep1","valid_to":null},{"key":"user_location","value":"madrid","confidence":0.9,"source_episode_id":"ep2","valid_to":"2026-01-01T00:00:00+00:00"}]}\n'
        "JSON\n",
        encoding="utf-8",
    )
    curl.chmod(0o755)
    calls = tmp_path / "curl-calls.log"
    env = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "NINO_CURL_CALLS": str(calls),
        "NINO_PORT": "65528",
    }

    result = subprocess.run(
        ["scripts/ninoctl", "memory-facts", "--agent", "api-agent"],
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )

    assert "http://127.0.0.1:65528/agents/api-agent/memory/facts" in calls.read_text(encoding="utf-8")
    assert "cold memory facts: 1 (active)" in result.stdout
    assert "- [active] preference: sprints" in result.stdout
    assert "user_location" not in result.stdout


def test_ninoctl_dispatches_readiness_and_audit_commands(tmp_path) -> None:
    root = tmp_path / "repo"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    ninoctl = Path("scripts/ninoctl").read_text(encoding="utf-8")
    (scripts_dir / "ninoctl").write_text(ninoctl, encoding="utf-8")
    (scripts_dir / "ninoctl").chmod(0o755)
    calls = tmp_path / "calls.log"
    for name in (
        "nino-readiness",
        "nino-product-audit",
        "nino-configure-claude",
        "nino-disable-claude",
        "nino-eval",
        "nino-status",
        "nino-completion-audit",
        "nino-closing-report",
        "nino-launchd",
        "sleep",
    ):
        path = scripts_dir / name
        path.write_text(
            "#!/usr/bin/env bash\n"
            "if [[ \"$(basename \"$0\")\" != \"sleep\" ]]; then printf '%s %s\\n' \"$(basename \"$0\")\" \"$*\" >> \"$NINO_CALLS_LOG\"; fi\n",
            encoding="utf-8",
        )
        path.chmod(0o755)
    curl = scripts_dir / "curl"
    curl.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s %s\\n' \"$(basename \"$0\")\" \"$*\" >> \"$NINO_CALLS_LOG\"\n"
        "exit 0\n",
        encoding="utf-8",
    )
    curl.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{scripts_dir}:{os.environ['PATH']}",
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
    subprocess.run(
        [str(scripts_dir / "ninoctl"), "eval", "--json"],
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [str(scripts_dir / "ninoctl"), "product-status", "--json"],
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [str(scripts_dir / "ninoctl"), "next-action"],
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [str(scripts_dir / "ninoctl"), "completion-audit", "--json"],
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [str(scripts_dir / "ninoctl"), "closing-report", "--json"],
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [str(scripts_dir / "ninoctl"), "finish", "--key-stdin", "--model", "claude-test", "--preflight-only"],
        check=True,
        env=env,
        input="secret\n",
        capture_output=True,
        text=True,
    )
    env_key_exported = {**env, "ANTHROPIC_API_KEY": "configured-secret"}
    subprocess.run(
        [str(scripts_dir / "ninoctl"), "finish", "--key-env", "--model", "claude-test", "--preflight-only"],
        check=True,
        env=env_key_exported,
        capture_output=True,
        text=True,
    )
    env_skip_configured = {
        **env,
        "NINO_LLM_PROVIDER": "claude",
        "ANTHROPIC_API_KEY": "configured-secret",
    }
    subprocess.run(
        [str(scripts_dir / "ninoctl"), "finish", "--skip-configure", "--preflight-only"],
        check=True,
        env=env_skip_configured,
        capture_output=True,
        text=True,
    )
    invalid_finish = subprocess.run(
        [str(scripts_dir / "ninoctl"), "finish", "--skip-configure", "--model", "claude-test"],
        check=False,
        env=env,
        capture_output=True,
        text=True,
    )
    missing_key_env_finish = subprocess.run(
        [str(scripts_dir / "ninoctl"), "finish", "--key-env"],
        check=False,
        env=env,
        capture_output=True,
        text=True,
    )
    missing_config_finish = subprocess.run(
        [str(scripts_dir / "ninoctl"), "finish", "--skip-configure"],
        check=False,
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
        "nino-eval --json",
        "nino-status --json",
        "nino-status --next-action",
        "nino-completion-audit --json",
        "nino-closing-report --json",
        "nino-configure-claude --key-stdin --model claude-test --keychain-service nino-anthropic",
        "nino-launchd stop",
        "nino-launchd start",
        "curl -fsS http://127.0.0.1:65531/health",
        "nino-status ",
        "nino-product-audit --require-launchd --require-claude-config --json",
        "nino-closing-report --json",
        "nino-completion-audit --json",
        "nino-configure-claude --model claude-test --keychain-service nino-anthropic",
        "nino-launchd stop",
        "nino-launchd start",
        "curl -fsS http://127.0.0.1:65531/health",
        "nino-status ",
        "nino-product-audit --require-launchd --require-claude-config --json",
        "nino-closing-report --json",
        "nino-completion-audit --json",
        "nino-launchd stop",
        "nino-launchd start",
        "curl -fsS http://127.0.0.1:65531/health",
        "nino-status ",
        "nino-product-audit --require-launchd --require-claude-config --json",
        "nino-closing-report --json",
        "nino-completion-audit --json",
    ]
    assert invalid_finish.returncode == 2
    assert "--skip-configure cannot be combined" in invalid_finish.stderr
    assert missing_key_env_finish.returncode == 2
    assert "--key-env requires ANTHROPIC_API_KEY" in missing_key_env_finish.stderr
    assert missing_config_finish.returncode == 2
    assert "--skip-configure requires Claude to be configured" in missing_config_finish.stderr


def test_nino_readiness_skips_pytest_when_tests_are_not_installed(tmp_path) -> None:
    root = tmp_path / "runtime"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    readiness = Path("scripts/nino-readiness").read_text(encoding="utf-8")
    (scripts_dir / "nino-readiness").write_text(readiness, encoding="utf-8")
    (scripts_dir / "nino-readiness").chmod(0o755)
    calls = tmp_path / "calls.log"
    for name in ("nino-smoke", "nino-product-audit"):
        path = scripts_dir / name
        path.write_text(
            "#!/usr/bin/env bash\n"
            "printf '%s %s\\n' \"$(basename \"$0\")\" \"$*\" >> \"$NINO_CALLS_LOG\"\n",
            encoding="utf-8",
        )
        path.chmod(0o755)
    env = {**os.environ, "NINO_CALLS_LOG": str(calls)}

    result = subprocess.run(
        [str(scripts_dir / "nino-readiness")],
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )

    assert "pytest: skipped (tests directory not installed in this runtime copy)" in result.stdout
    assert calls.read_text(encoding="utf-8").splitlines() == [
        "nino-smoke --json",
        "nino-product-audit --skip-http --json",
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
    assert (install_dir / "REVISION").exists()
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


def test_configure_claude_rejects_env_injection_values(tmp_path) -> None:
    env_file = tmp_path / ".env.local"

    bad_model = subprocess.run(
        [
            "scripts/nino-configure-claude",
            "--env-file",
            str(env_file),
            "--model",
            "claude-test=bad",
            "--key-stdin",
        ],
        check=False,
        input="secret\n",
        capture_output=True,
        text=True,
    )
    bad_service = subprocess.run(
        [
            "scripts/nino-configure-claude",
            "--env-file",
            str(env_file),
            "--key-stdin",
            "--keychain-service",
            "nino-test=bad",
        ],
        check=False,
        input="secret\n",
        capture_output=True,
        text=True,
    )
    bad_model_newline = subprocess.run(
        [
            "scripts/nino-configure-claude",
            "--env-file",
            str(env_file),
            "--model",
            "claude-test\nNINO_PORT=9999",
            "--key-stdin",
        ],
        check=False,
        input="secret\n",
        capture_output=True,
        text=True,
    )
    bad_service_newline = subprocess.run(
        [
            "scripts/nino-configure-claude",
            "--env-file",
            str(env_file),
            "--key-stdin",
            "--keychain-service",
            "nino-test\nANTHROPIC_API_KEY=leak",
        ],
        check=False,
        input="secret\n",
        capture_output=True,
        text=True,
    )

    assert bad_model.returncode == 2
    assert "Model contains invalid characters" in bad_model.stderr
    assert bad_service.returncode == 2
    assert "Keychain service contains invalid characters" in bad_service.stderr
    assert bad_model_newline.returncode == 2
    assert "Model contains invalid characters" in bad_model_newline.stderr
    assert bad_service_newline.returncode == 2
    assert "Keychain service contains invalid characters" in bad_service_newline.stderr
    assert not env_file.exists()


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


def test_runtime_scripts_fall_back_to_system_python() -> None:
    for script in (
        "scripts/nino-smoke",
        "scripts/nino-eval",
        "scripts/nino-status",
        "scripts/nino-completion-audit",
        "scripts/nino-closing-report",
    ):
        content = Path(script).read_text(encoding="utf-8")
        assert 'DEFAULT_PYTHON="$ROOT_DIR/.venv/bin/python"' in content
        assert "command -v python3" in content
