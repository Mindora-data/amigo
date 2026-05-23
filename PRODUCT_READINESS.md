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
scripts/ninoctl product-status
scripts/ninoctl completion-audit
scripts/ninoctl closing-report
printf '%s' "$ANTHROPIC_API_KEY" | scripts/ninoctl finish --key-stdin
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
| Agente vivo persistente | `scripts/ninoctl completion-audit`, `GET /operations/completion-audit` verifican agente `nino` con estado y memoria/episodios persistidos | Cumplido |
| Claude opcional | `scripts/ninoctl configure-claude`, `scripts/ninoctl disable-claude`, `scripts/nino-configure-claude`, `scripts/nino-disable-claude`, `.env.local` o macOS Keychain, `NINO_LLM_PROVIDER=claude`, `/operations/claude`, `POST /operations/claude/configure`, `POST /operations/claude/disable`, panel LLM con `Guardar Claude`, `Cierre guiado`, `Desactivar Claude` y modo Keychain por defecto, `/llm/probe`, `config_errors` para valores invalidos | Implementado; prueba viva requiere key real |
| Local-first offline | `/operations/mode`, smoke `local_first_mode`, core sin red | Cumplido |
| Controles de seguridad | permisos por accion, bloqueo por defecto, export seguro, audit log | Cumplido |
| Tareas autonomas controladas | smoke `task_enqueue`, `task_run`, `proactive_inbox` | Cumplido |
| Proactividad limitada | consentimiento, intervalo minimo, maximo diario y ventanas horarias | Cumplido |
| Backups | `scripts/ninoctl backup`, `scripts/ninoctl backups`, `scripts/ninoctl restore`, `/operations/backup`, `/operations/backups`, lista UI con comando exacto de restore, smoke `sqlite_backup` y `sqlite_backup_list` | Cumplido |
| Operacion local | `scripts/ninoctl`, `scripts/nino-install-local`, `scripts/nino-launchd`, `scripts/nino-launchd doctor`, `scripts/ninoctl persistent-audit`, `GET /operations/logs`, `POST /operations/restart`, panel Logs y reinicio en UI, `nino-server`, `nino-smoke` | Cumplido |
| Empaquetado local | `pyproject.toml`, console scripts `nino-server`, `nino-smoke`, `nino-eval`, `nino-status` | Cumplido |
| Contrato API | `GET /openapi.json`, `openapi/README.md`, tests de alineacion con `GET /` | Cumplido |
| Regresion objetiva | `eval/memory_regression.json`, `nino.eval_runner`, `scripts/ninoctl eval`, `nino-eval`, `GET /operations/eval`, boton `Eval local` en `/app`, pytest | Cumplido |
| Auditoria final repetible | `GET /operations/audit`, `GET /operations/product-status`, `GET /operations/final-preflight`, `POST /operations/final-audit`, `scripts/nino-product-audit --json`, `scripts/ninoctl product-status`; `--require-launchd` para exigir servicio persistente; `--require-claude-config` para preflight sin llamada viva; `--require-claude-live` para cerrar Claude real | Cumplido local; Claude vivo requiere key real |
| Auditoria de terminacion | `scripts/ninoctl completion-audit`, `nino-completion-audit`, `GET /operations/completion-audit`, boton `Terminación`, requisito `closing_evidence` para informes de cierre | Cumplido local; marca Claude vivo como bloqueo hasta tener key real |
| Informe de cierre | `scripts/ninoctl closing-report`, `scripts/ninoctl reports`, `scripts/ninoctl report`, `scripts/ninoctl report latest`, `nino-closing-report`, `POST /operations/closing-report`, `GET /operations/reports`, `GET /operations/reports/latest`, `GET /operations/reports/{report_name}`, botones `Informe cierre`, `Ver informes`, `Último informe` y `Ver JSON`, smoke `closing_report`, `closing_report_list`, `closing_report_read`, `closing_report_latest` y `closing_report_name_guard`, JSON en `data/reports/` | Cumplido local |

La auditoria estricta de cierre es:

```bash
scripts/ninoctl final-preflight
scripts/ninoctl final-audit
```

`final-preflight` debe pasar cuando el servicio persistente esta cargado, la DB
auditada es la misma que usa el servidor y Claude esta configurado, sin gastar
una llamada viva. `final-audit` anade la respuesta real de Claude con una key
real.
`scripts/ninoctl product-status` resume ese preflight, la eval local, el ultimo
informe de cierre con head/bloqueos y los bloqueos restantes en formato legible,
o en JSON con `--json`. Tambien marca si ese informe corresponde a la revision
actual; si falta o esta obsoleto, `closing_evidence` queda como bloqueo e
incluye `recommended_next_action` con el proximo comando operativo.
`GET /operations/product-status` expone el mismo resumen a la UI con el boton
`Estado final`.
`scripts/ninoctl completion-audit` muestra una matriz requisito por requisito:
runtime persistente, UI, memoria, agente vivo `nino`, seguridad, backups,
eval, evidencia de cierre, Claude configurado, Claude vivo y ultimo informe.
Tambien marca si el ultimo informe corresponde a la revision actual e incluye
`recommended_next_action` para el siguiente paso de cierre. El requisito
`closing_evidence` exige que el ultimo informe apunte a la revision instalada.
`GET /operations/completion-audit` y el boton `Terminación` exponen esa matriz
desde el proceso servido.
`scripts/ninoctl closing-report` guarda una evidencia local versionable fuera de
Git con estado git, perfil de `nino`, estado final y auditoria de terminacion.
Cada JSON incluye `report_file` con su propio nombre/ruta local y
`recommended_next_action`. Las secciones internas `product_status` y
`completion_audit` apuntan al mismo informe para evitar referencias obsoletas.
`scripts/ninoctl reports`, `scripts/ninoctl report <name>` y `scripts/ninoctl
report latest` listan y leen esos informes desde terminal validando el nombre
local.
`POST /operations/closing-report` y el boton `Informe cierre` generan la misma
evidencia desde el proceso servido.
`GET /operations/reports` y el boton `Ver informes` listan la evidencia ya
generada.
`GET /operations/reports/latest` y el boton `Último informe` abren la evidencia
mas reciente sin copiar el nombre.
`GET /operations/reports/{report_name}` y `Ver JSON` abren un informe concreto
validando que el nombre sea un `nino-closing-*.json` local.
`scripts/ninoctl finish --key-stdin` ejecuta el cierre guiado completo:
configura Claude en Keychain por defecto, reinicia launchd y lanza el cierre
final. Tambien escribe un informe de cierre actualizado para dejar evidencia
del resultado y ejecuta `completion-audit --json` al final. Con
`--preflight-only` valida la configuracion sin gastar llamada viva. Si Claude
ya esta configurado desde la UI o el entorno local, `--skip-configure` ejecuta
el mismo cierre sin volver a pedir la key.
En JSON, esa ejecucion queda marcada como `audit_profile.strict_final: true` y
`audit_profile.required_checks` lista los checks exigidos, incluido `local_smoke`
cuando se ejecuta.
Si falta Claude, la evidencia `claude_configured` y `claude_live` incluye
comandos seguros de configuracion y vuelve a apuntar a
`scripts/ninoctl final-audit`. Esos comandos incluyen tanto
`scripts/ninoctl finish --key-stdin` para primera configuracion como
`scripts/ninoctl finish --skip-configure` cuando el secreto ya esta guardado.
Cuando Claude ya esta configurado y solo queda validar la llamada viva, la
accion recomendada pasa a `finish --skip-configure`. Ese modo falla antes de
reiniciar si Claude no esta ya configurado en `.env.local` o el entorno actual.
`GET /operations/audit` y la UI en `/app` exponen tambien `final_readiness`,
con estado de auditoria local, servicio persistente observado, bloqueos de
Claude y comandos siguientes para no interpretar el JSON completo de la
auditoria.
La UI tambien permite lanzar `Preflight final` sin llamada viva y `Cierre final`
con confirmacion, equivalente al perfil estricto con Claude vivo cuando la key
real ya esta configurada.
Desde el panel LLM, `Cierre guiado` configura Claude con la key pegada,
reinicia el servicio persistente, lanza `Cierre final` cuando `/health`
vuelve a responder y escribe un informe de cierre actualizado. El boton
`Cierre final` tambien genera ese informe despues de auditar. En ambos casos,
la UI refresca la matriz `Terminación` con la evidencia del informe recien
creado.

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
