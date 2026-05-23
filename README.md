# NIÑO

NIÑO is a persistent cognitive runtime prototype. It keeps agent state, episodes,
consolidated memory and safe proactivity configuration in SQLite.

## Run

```bash
PYTHONPATH=src python -m nino.server --db data/nino.db --host 127.0.0.1 --port 8000
PYTHONPATH=src python -m nino.server --db data/nino.db --scheduler-interval 60
```

For editable local installs:

```bash
.venv/bin/python -m pip install -e .
nino-server --db data/nino.db --host 127.0.0.1 --port 8000
```

Open the browser UI:

```text
http://127.0.0.1:8000/app
```

## Local Operations

Use `scripts/ninoctl` for day-to-day local operation:

```bash
scripts/ninoctl start
scripts/ninoctl status
scripts/ninoctl health
scripts/ninoctl mode
scripts/ninoctl claude
scripts/ninoctl configure-claude
scripts/ninoctl snapshot
scripts/ninoctl agents
scripts/ninoctl doctor
scripts/ninoctl readiness
scripts/ninoctl audit
scripts/ninoctl server-audit
scripts/ninoctl persistent-audit
scripts/ninoctl final-preflight
scripts/ninoctl final-audit
scripts/ninoctl finish
scripts/ninoctl completion-audit
scripts/ninoctl closing-report
scripts/ninoctl reports
scripts/ninoctl report nino-closing-YYYYMMDD-HHMMSS.json
scripts/ninoctl report latest
scripts/ninoctl logs
scripts/ninoctl backup
scripts/ninoctl backups
scripts/ninoctl stop
```

Follow logs:

```bash
scripts/ninoctl logs -f
```

Run the final setup as one flow, keeping the key in macOS Keychain by default:

```bash
printf '%s' "$ANTHROPIC_API_KEY" | scripts/ninoctl finish --key-stdin
```

Use `--preflight-only` to stop before the live Claude call, or `--no-keychain`
to store the key in `.env.local` instead.

Restart:

```bash
scripts/ninoctl restart
```

Restore a local SQLite backup while NIÑO is stopped:

```bash
scripts/ninoctl backups
scripts/ninoctl stop
scripts/ninoctl restore data/backups/nino-YYYYMMDD-HHMMSS.db
```

Restore writes a `pre-restore-*.db` backup of the current database first.

Run the local product smoke test:

```bash
scripts/nino-smoke
scripts/nino-smoke --json
```

The smoke test uses a temporary SQLite database and validates the local-first
flow: `/app`, memory, conversation history, Claude diagnostics, permissions,
task queue, proactive inbox, safe export, backup and closing-report evidence.

Run the full local readiness gate:

```bash
scripts/nino-readiness
```

See `PRODUCT_READINESS.md` for the requirement-by-requirement product checklist.

Install as a macOS user service:

```bash
scripts/nino-install-local install
cd ~/Developer/bebe
scripts/nino-launchd install
scripts/nino-launchd status
scripts/nino-launchd doctor
scripts/ninoctl persistent-audit
scripts/nino-launchd uninstall
```

The launchd plist calls `scripts/nino-launchd run` and points to `.env.local`;
it does not embed `ANTHROPIC_API_KEY` in the plist.
If the project lives under `Desktop`, `Documents` or `Downloads`, macOS privacy
controls can prevent launchd from reading the project and the service may exit
with `Operation not permitted`. Run `scripts/nino-launchd doctor` to confirm.
For unattended startup, keep the project in a non-protected folder such as
`~/Developer/bebe`; `scripts/nino-install-local install` copies the runtime
there while keeping an existing target `data/nino.db` and `.env.local`.

Defaults:

- URL: `http://127.0.0.1:8000`
- DB: `data/nino.db`
- PID file: `data/nino.pid`
- Log file: `data/nino-server.log`
- Backups: `data/backups/`
- Scheduler interval: `60` seconds

Optional environment overrides:

```bash
NINO_PORT=8010 scripts/ninoctl start
NINO_SCHEDULER_INTERVAL=300 scripts/ninoctl restart
NINO_DB_PATH=data/other.db scripts/ninoctl start
```

For persistent local configuration, copy `.env.example` to `.env.local`.
`scripts/ninoctl` and `scripts/nino-launchd` load `.env.local` automatically.
The `.env.local` file is ignored by Git.

## Claude Responses

NIÑO uses local rule-based responses by default. To enable Claude for natural
responses while keeping NIÑO memory and state local, set:

```bash
scripts/ninoctl configure-claude
```

For a non-interactive setup:

```bash
printf '%s' "$ANTHROPIC_API_KEY" | scripts/ninoctl configure-claude --key-stdin
printf '%s' "$ANTHROPIC_API_KEY" | scripts/ninoctl configure-claude --key-stdin --keychain-service nino-anthropic
```

The command writes `.env.local` with file mode `600`, preserves unrelated local
overrides, and never prints the key. With `--keychain-service`, the secret is
stored in macOS Keychain and `.env.local` stores only `NINO_KEYCHAIN_SERVICE`.
The local UI also includes `Guardar Claude`, backed by
`POST /operations/claude/configure`, for configuring Claude without a terminal.
By default it stores the secret in macOS Keychain and writes only
`NINO_KEYCHAIN_SERVICE` to `.env.local`; `.env.local` storage is still available
as an explicit option. The API never returns the key and the response recommends
restarting launchd so the persistent service reloads the file from a clean
process.
`Cierre guiado` uses the same panel to save Claude, restart the persistent
service, wait for `/health` and run the final audit.
`Estado final` calls `GET /operations/product-status` and shows the same
preflight/eval/blocker summary as the CLI status command, including the newest
closing report name, git head and blockers when one exists.
`scripts/ninoctl completion-audit` prints the requirement-by-requirement
completion matrix, including the remaining Claude live blocker when no real key
has been configured. It also verifies that the live `nino` agent has persisted
state plus episodes or cold memory.
The same matrix is available in the UI with `Terminación`, backed by
`GET /operations/completion-audit`.
`scripts/ninoctl closing-report` writes a timestamped JSON evidence report under
`data/reports/` with git state, product status, completion audit and the live
`nino` profile. Each report also includes its own `report_file` name and path.
`scripts/ninoctl reports` lists those reports and `scripts/ninoctl report
<name>` prints a single timestamped report after validating the local filename.
Use `scripts/ninoctl report latest` to print the newest report.
The same evidence can be generated from `/app` with `Informe cierre`, backed by
`POST /operations/closing-report`.
Existing reports can be listed with `Ver informes`, backed by
`GET /operations/reports`.
Each listed report can be opened and downloaded from the UI with `Ver JSON`,
backed by `GET /operations/reports/{report_name}`. The API only accepts
timestamped `nino-closing-*.json` names from the local reports directory.
`Último informe` opens the newest report directly with
`GET /operations/reports/latest`.
The same panel includes `Desactivar Claude`, backed by
`POST /operations/claude/disable`, to remove Claude settings from `.env.local`,
disable the runtime client immediately, and optionally delete the configured
Keychain item.
The same cleanup is available from terminal with `scripts/ninoctl disable-claude`
or `scripts/ninoctl disable-claude --remove-keychain`.
The `Reiniciar servicio` button calls `POST /operations/restart`; when NIÑO is
running under launchd, the process exits after responding and launchd `KeepAlive`
starts it again.
Then start the server:

```bash
PYTHONPATH=src .venv/bin/python -m nino.server --db data/nino.db --host 127.0.0.1 --port 8000 --scheduler-interval 60
```

Or with `ninoctl`:

```bash
scripts/ninoctl start
```

If using the macOS service copy, configure Claude in that installed folder and
restart launchd:

```bash
cd ~/Developer/bebe
scripts/ninoctl configure-claude
scripts/nino-launchd stop
scripts/nino-launchd start
```

Check configuration without sending a Claude prompt:

```bash
scripts/ninoctl claude
```

If `NINO_LLM_MAX_TOKENS` or `NINO_LLM_TIMEOUT` is invalid, the Claude
diagnostic reports `config_errors` and keeps Claude disabled until the value is
fixed.

The same safe setup commands are exposed in `/operations/claude` and the LLM
panel in `/app`; the API reports whether a key is present, but never returns
the key value.

Run a live probe only after the key is configured:

```bash
scripts/nino-claude-live --require-key --json
scripts/nino-product-audit --require-claude-live --json
scripts/ninoctl live-audit
scripts/ninoctl final-preflight
scripts/ninoctl final-audit

curl -X POST http://127.0.0.1:8000/agents/nino/llm/probe \
  -H 'Content-Type: application/json' \
  -d '{}'
```

Run `scripts/ninoctl final-preflight` before spending a live Claude call. It
requires launchd, the same audited SQLite database served over HTTP, and a valid
Claude configuration, but it does not send a prompt.
Run `scripts/ninoctl final-audit` from the installed runtime folder used by
launchd, normally `~/Developer/bebe`; it adds the live Claude response check.
The JSON result is marked with `audit_profile.strict_final: true` only for this
strict final profile, and `audit_profile.required_checks` lists every check
required for the selected profile.
If Claude is not configured, the `claude_configured` and `claude_live` evidence
include safe setup commands and point back to `scripts/ninoctl final-audit`.
The `/operations/audit` endpoint and the `/app` audit button also return this
strict final command, the required checks, and `final_readiness` with current
local audit state, observed launchd state, Claude configuration blockers, and
the next safe commands.
`GET /operations/final-preflight` runs the strict preflight profile from the API
without a live Claude call. `POST /operations/final-audit` runs the strict final
profile and may call Claude when a real key is configured; the UI exposes it as
`Cierre final` with confirmation.

Do not commit API keys. Keep `ANTHROPIC_API_KEY` in your shell environment or a
local untracked secret manager.

When Claude is enabled, NIÑO sends a compact context bundle:

- the current user message,
- recent conversation turns,
- retrieved memory candidates,
- active cold memory facts,
- relation preferences,
- active goals and dominant world-model concepts.

Emails and long numeric sequences are redacted before context is sent to Claude.

## Core Endpoints

```text
GET  /health
GET  /app
GET  /openapi.json
GET  /autonomy/status
POST /autonomy/run-once
GET  /development/snapshot
GET  /operations/mode
GET  /operations/claude
POST /operations/claude/configure
POST /operations/claude/disable
GET  /operations/audit
GET  /operations/product-status
GET  /operations/completion-audit
POST /operations/closing-report
GET  /operations/reports
GET  /operations/reports/latest
GET  /operations/reports/{report_name}
GET  /operations/eval
GET  /operations/final-preflight
POST /operations/final-audit
GET  /operations/backups
GET  /operations/logs
POST /operations/backup
POST /operations/restart
GET  /agents
POST /agents/{agent_id}/tick
GET  /agents/{agent_id}/state
GET  /agents/{agent_id}/conversation
GET  /agents/{agent_id}/episodes
GET  /agents/{agent_id}/llm/status
POST /agents/{agent_id}/llm/probe
DELETE /agents/{agent_id}/episodes/{episode_id}
GET  /agents/{agent_id}/memory/facts
DELETE /agents/{agent_id}/memory/facts/{fact_id}
GET  /agents/{agent_id}/relation
GET  /agents/{agent_id}/self-model
GET  /agents/{agent_id}/world-model
GET  /agents/{agent_id}/narrative
GET  /agents/{agent_id}/metrics
GET  /agents/{agent_id}/export
GET  /agents/{agent_id}/export-safe
GET  /agents/{agent_id}/proactivity/inbox
POST /agents/{agent_id}/proactivity/inbox/{item_id}/delivered
POST /agents/{agent_id}/proactivity/inbox/clear-delivered
POST /agents/{agent_id}/memory/decay
POST /agents/{agent_id}/memory/search
GET  /agents/{agent_id}/eval/conversation
POST /agents/{agent_id}/eval/conversation/record
GET  /agents/{agent_id}/eval/conversation/history
GET  /agents/{agent_id}/audit
GET  /agents/{agent_id}/permissions
POST /agents/{agent_id}/permissions/configure
GET  /agents/{agent_id}/tasks
POST /agents/{agent_id}/tasks
POST /agents/{agent_id}/tasks/run-next
POST /agents/import
POST /agents/{agent_id}/reset
POST /agents/{agent_id}/memory/retrieve
POST /agents/{agent_id}/consolidate
POST /agents/{agent_id}/internal/cycle
POST /agents/{agent_id}/internal/dream
POST /agents/{agent_id}/internal/scheduled
POST /internal/scheduled
POST /agents/{agent_id}/proactivity/configure
POST /agents/{agent_id}/proactivity/evaluate
```

## Example

```bash
curl -X POST http://127.0.0.1:8000/agents/demo/tick \
  -H 'Content-Type: application/json' \
  -d '{"intent":"music","text":"prefiero piano","salience":0.9,"confidence":0.9}'

curl -X POST http://127.0.0.1:8000/agents/demo/internal/cycle \
  -H 'Content-Type: application/json' \
  -d '{}'
```

## Evaluations

Run deterministic local regression evaluations:

```bash
scripts/ninoctl eval --json
curl http://127.0.0.1:8000/operations/eval
```

The same check is available from the `/app` operation panel as `Eval local`.
For direct module use:

```bash
PYTHONPATH=src .venv/bin/python - <<'PY'
from nino.eval_runner import run_eval_dir
print(run_eval_dir("eval"))
PY
```

Run the broader local product gate:

```bash
scripts/nino-smoke --json
scripts/ninoctl product-status
```

Proactivity is closed by default. Enable it explicitly:

```bash
curl -X POST http://127.0.0.1:8000/agents/demo/proactivity/configure \
  -H 'Content-Type: application/json' \
  -d '{"consent":"allowed","max_messages_per_day":1,"min_hours_between":24,"active_hours_start":9,"active_hours_end":18}'
```

`active_hours_start` and `active_hours_end` define the local active window by hour
of day. The default `0` to `24` allows evaluation all day.
