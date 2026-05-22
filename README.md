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
scripts/ninoctl snapshot
scripts/ninoctl agents
scripts/ninoctl doctor
scripts/ninoctl logs
scripts/ninoctl backup
scripts/ninoctl stop
```

Follow logs:

```bash
scripts/ninoctl logs -f
```

Restart:

```bash
scripts/ninoctl restart
```

Run the local product smoke test:

```bash
scripts/nino-smoke
scripts/nino-smoke --json
```

The smoke test uses a temporary SQLite database and validates the local-first
flow: `/app`, memory, conversation history, Claude diagnostics, permissions,
task queue, proactive inbox, safe export and backup.

Run the full local readiness gate:

```bash
scripts/nino-readiness
```

See `PRODUCT_READINESS.md` for the requirement-by-requirement product checklist.

Install as a macOS user service:

```bash
scripts/nino-launchd install
scripts/nino-launchd status
scripts/nino-launchd uninstall
```

The launchd plist calls `scripts/nino-launchd run` and points to `.env.local`;
it does not embed `ANTHROPIC_API_KEY` in the plist.

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
cp .env.example .env.local
# edit .env.local and set ANTHROPIC_API_KEY
```

Then start the server:

```bash
PYTHONPATH=src .venv/bin/python -m nino.server --db data/nino.db --host 127.0.0.1 --port 8000 --scheduler-interval 60
```

Or with `ninoctl`:

```bash
scripts/ninoctl start
```

Check configuration without sending a Claude prompt:

```bash
scripts/ninoctl claude
```

Run a live probe only after the key is configured:

```bash
scripts/nino-claude-live --require-key --json

curl -X POST http://127.0.0.1:8000/agents/nino/llm/probe \
  -H 'Content-Type: application/json' \
  -d '{}'
```

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
GET  /autonomy/status
POST /autonomy/run-once
GET  /development/snapshot
GET  /operations/mode
GET  /operations/claude
POST /operations/backup
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
PYTHONPATH=src .venv/bin/python - <<'PY'
from nino.eval_runner import run_eval_dir
print(run_eval_dir("eval"))
PY
```

Run the broader local product gate:

```bash
scripts/nino-smoke --json
```

Proactivity is closed by default. Enable it explicitly:

```bash
curl -X POST http://127.0.0.1:8000/agents/demo/proactivity/configure \
  -H 'Content-Type: application/json' \
  -d '{"consent":"allowed","max_messages_per_day":1,"min_hours_between":24,"active_hours_start":9,"active_hours_end":18}'
```

`active_hours_start` and `active_hours_end` define the local active window by hour
of day. The default `0` to `24` allows evaluation all day.
