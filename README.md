# NIÑO

NIÑO is a persistent cognitive runtime prototype. It keeps agent state, episodes,
consolidated memory and safe proactivity configuration in SQLite.

## Run

```bash
PYTHONPATH=src python -m nino.server --db data/nino.db --host 127.0.0.1 --port 8000
```

Open the browser UI:

```text
http://127.0.0.1:8000/app
```

## Core Endpoints

```text
GET  /health
GET  /app
POST /agents/{agent_id}/tick
GET  /agents/{agent_id}/state
GET  /agents/{agent_id}/episodes
GET  /agents/{agent_id}/relation
GET  /agents/{agent_id}/self-model
GET  /agents/{agent_id}/world-model
POST /agents/{agent_id}/reset
POST /agents/{agent_id}/memory/retrieve
POST /agents/{agent_id}/consolidate
POST /agents/{agent_id}/internal/cycle
POST /agents/{agent_id}/internal/dream
POST /agents/{agent_id}/internal/scheduled
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

Proactivity is closed by default. Enable it explicitly:

```bash
curl -X POST http://127.0.0.1:8000/agents/demo/proactivity/configure \
  -H 'Content-Type: application/json' \
  -d '{"consent":"allowed","max_messages_per_day":1,"min_hours_between":24}'
```
