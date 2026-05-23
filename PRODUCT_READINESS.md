# NIÑO Product Readiness

Estado confirmado el 2026-05-23.

## Puerta local

Ejecutar:

```bash
scripts/nino-readiness
```

Esta puerta corre:

- `.venv/bin/python -m pytest`
- `scripts/nino-smoke --json`
- `scripts/nino-product-audit --skip-http --json` para evidencias locales sin depender de un servidor ya arrancado

La prueba viva de Claude queda separada porque requiere red y una key real:

```bash
scripts/nino-claude-live --require-key --json
scripts/nino-product-audit --require-claude-live --json
scripts/ninoctl live-audit
scripts/ninoctl final-preflight
scripts/ninoctl final-audit
```

Para auditar tambien el servidor persistente ya arrancado:

```bash
scripts/nino-product-audit --json
scripts/ninoctl audit
scripts/ninoctl server-audit
```

Para exigir que el arranque persistente de macOS este instalado y cargado:

```bash
scripts/nino-product-audit --require-launchd --json
scripts/ninoctl persistent-audit
```

## Requisitos del producto

| Requisito | Evidencia local | Estado |
| --- | --- | --- |
| Runtime persistente local | SQLite via `data/nino.db`; `/operations/mode` reporta `sqlite` | Cumplido |
| UI operativa | `/app` cubierta por tests y smoke `browser_app` | Cumplido |
| Memoria conversacional | `conversation_history`, `memory_search`, tests de retrieval y persistencia; borrado seguro de episodios y hechos desde API/UI; decay configurable desde API/UI | Cumplido |
| Claude opcional | `scripts/ninoctl configure-claude`, `scripts/nino-configure-claude`, `.env.local` o macOS Keychain, `NINO_LLM_PROVIDER=claude`, `/operations/claude`, `POST /operations/claude/configure`, `POST /operations/claude/disable`, panel LLM con `Guardar Claude`, `Desactivar Claude` y modo Keychain por defecto, `/llm/probe`, `config_errors` para valores invalidos | Implementado; prueba viva requiere key real |
| Local-first offline | `/operations/mode`, smoke `local_first_mode`, core sin red | Cumplido |
| Controles de seguridad | permisos por accion, bloqueo por defecto, export seguro, audit log | Cumplido |
| Tareas autonomas controladas | smoke `task_enqueue`, `task_run`, `proactive_inbox` | Cumplido |
| Proactividad limitada | consentimiento, intervalo minimo, maximo diario y ventanas horarias | Cumplido |
| Backups | `scripts/ninoctl backup`, `scripts/ninoctl backups`, `scripts/ninoctl restore`, `/operations/backup`, `/operations/backups`, lista UI con comando exacto de restore, smoke `sqlite_backup` y `sqlite_backup_list` | Cumplido |
| Operacion local | `scripts/ninoctl`, `scripts/nino-install-local`, `scripts/nino-launchd`, `scripts/nino-launchd doctor`, `scripts/ninoctl persistent-audit`, `GET /operations/logs`, `POST /operations/restart`, panel Logs y reinicio en UI, `nino-server`, `nino-smoke` | Cumplido |
| Empaquetado local | `pyproject.toml`, console scripts `nino-server` y `nino-smoke` | Cumplido |
| Contrato API | `GET /openapi.json`, `openapi/README.md`, tests de alineacion con `GET /` | Cumplido |
| Regresion objetiva | `eval/memory_regression.json`, `nino.eval_runner`, `GET /operations/eval`, boton `Eval local` en `/app`, pytest | Cumplido |
| Auditoria final repetible | `GET /operations/audit`, `GET /operations/final-preflight`, `POST /operations/final-audit`, `scripts/nino-product-audit --json`; `--require-launchd` para exigir servicio persistente; `--require-claude-config` para preflight sin llamada viva; `--require-claude-live` para cerrar Claude real | Cumplido local; Claude vivo requiere key real |

La auditoria estricta de cierre es:

```bash
scripts/ninoctl final-preflight
scripts/ninoctl final-audit
```

`final-preflight` debe pasar cuando el servicio persistente esta cargado, la DB
auditada es la misma que usa el servidor y Claude esta configurado, sin gastar
una llamada viva. `final-audit` anade la respuesta real de Claude con una key
real.
En JSON, esa ejecucion queda marcada como `audit_profile.strict_final: true` y
`audit_profile.required_checks` lista los checks exigidos, incluido `local_smoke`
cuando se ejecuta.
Si falta Claude, la evidencia `claude_configured` y `claude_live` incluye
comandos seguros de configuracion y vuelve a apuntar a
`scripts/ninoctl final-audit`.
`GET /operations/audit` y la UI en `/app` exponen tambien `final_readiness`,
con estado de auditoria local, servicio persistente observado, bloqueos de
Claude y comandos siguientes para no interpretar el JSON completo de la
auditoria.
La UI tambien permite lanzar `Preflight final` sin llamada viva y `Cierre final`
con confirmacion, equivalente al perfil estricto con Claude vivo cuando la key
real ya esta configurada.

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
`scripts/ninoctl configure-claude` y `scripts/nino-configure-claude` escriben `.env.local` con permisos `600` sin
imprimir la key. Con `--keychain-service`, guarda el secreto en macOS Keychain
y `.env.local` solo contiene `NINO_KEYCHAIN_SERVICE`. `scripts/ninoctl` y
`scripts/nino-launchd` cargan `.env.local` automaticamente, y `.env.local` esta ignorado por Git.
El plist de launchd no incrusta `ANTHROPIC_API_KEY`; llama a
`scripts/nino-launchd run` y este carga el env local en runtime.
Si el proyecto esta bajo `Desktop`, `Documents` o `Downloads`, macOS puede
bloquear a launchd con `Operation not permitted`; `scripts/nino-launchd doctor`
lo muestra junto con el stderr reciente. Para arranque desatendido estable,
`scripts/nino-install-local install` copia el runtime a una ruta no protegida
como `~/Developer/bebe` sin sobrescribir una base o `.env.local` existentes.
