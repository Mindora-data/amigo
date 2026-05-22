# NIÑO Product Readiness

Estado confirmado el 2026-05-22.

## Puerta local

Ejecutar:

```bash
scripts/nino-readiness
```

Esta puerta corre:

- `.venv/bin/python -m pytest`
- `scripts/nino-smoke --json`

La prueba viva de Claude queda separada porque requiere red y una key real:

```bash
scripts/nino-claude-live --require-key --json
```

## Requisitos del producto

| Requisito | Evidencia local | Estado |
| --- | --- | --- |
| Runtime persistente local | SQLite via `data/nino.db`; `/operations/mode` reporta `sqlite` | Cumplido |
| UI operativa | `/app` cubierta por tests y smoke `browser_app` | Cumplido |
| Memoria conversacional | `conversation_history`, `memory_search`, tests de retrieval y persistencia | Cumplido |
| Claude opcional | `NINO_LLM_PROVIDER=claude`, `ANTHROPIC_API_KEY`, `/operations/claude`, `/llm/probe` | Implementado; prueba viva requiere key real |
| Local-first offline | `/operations/mode`, smoke `local_first_mode`, core sin red | Cumplido |
| Controles de seguridad | permisos por accion, bloqueo por defecto, export seguro, audit log | Cumplido |
| Tareas autonomas controladas | smoke `task_enqueue`, `task_run`, `proactive_inbox` | Cumplido |
| Proactividad limitada | consentimiento, intervalo minimo, maximo diario y ventanas horarias | Cumplido |
| Backups | `scripts/ninoctl backup`, `/operations/backup`, smoke `sqlite_backup` | Cumplido |
| Operacion local | `scripts/ninoctl`, `scripts/nino-launchd`, `nino-server`, `nino-smoke` | Cumplido |
| Empaquetado local | `pyproject.toml`, console scripts `nino-server` y `nino-smoke` | Cumplido |
| Contrato API | `GET /openapi.json`, `openapi/README.md`, tests de alineacion con `GET /` | Cumplido |
| Regresion objetiva | `eval/memory_regression.json`, `nino.eval_runner`, pytest | Cumplido |

## Bloque externo

La unica comprobacion que no puede quedar demostrada sin estado externo es una
llamada viva a Claude. Para validarla:

```bash
export ANTHROPIC_API_KEY="..."
export NINO_LLM_PROVIDER=claude
scripts/ninoctl start
scripts/ninoctl claude
curl -X POST http://127.0.0.1:8000/agents/nino/llm/probe \
  -H 'Content-Type: application/json' \
  -d '{}'
```

Tambien se puede comprobar sin servidor:

```bash
scripts/nino-claude-live --require-key --json
```

No se debe commitear `ANTHROPIC_API_KEY`.

Para uso diario, guardar esos valores en `.env.local` a partir de `.env.example`.
`scripts/ninoctl` y `scripts/nino-launchd` cargan `.env.local`
automaticamente, y `.env.local` esta ignorado por Git.
El plist de launchd no incrusta `ANTHROPIC_API_KEY`; llama a
`scripts/nino-launchd run` y este carga el env local en runtime.
