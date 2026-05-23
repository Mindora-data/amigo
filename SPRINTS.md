# NIÑO Sprint Roadmap

Estado confirmado el 2026-05-23:

- GitHub se mantiene sincronizado desde los commits de sprint; confirmar hash exacto con `git rev-parse HEAD`.
- Rama activa: `main`.
- Suite actual: 125 tests pasando.
- La memoria viva local (`data/nino.db`) no se versiona en GitHub.
- El producto publicado es el motor, API, UI minima y pruebas; no incluye datos de uso real.

## Sprint 1 - Base del runtime

Estado: hecho.

Objetivo: crear el nucleo minimo de NIÑO como runtime persistente y testeable.

Hecho:

- Estructura inicial del proyecto Python.
- Contratos de datos principales.
- Runtime de agente con estado basico.
- Memoria episodica inicial.
- Demo ejecutable.
- Primeras pruebas automatizadas.
- Limpieza de cache y `.gitignore`.

Evidencia:

- Commits `041e332`, `0cb3dc8`, `618b148`, `fcb01df`.
- Tests de runtime, memoria y persistencia basica.

## Sprint 2 - Memoria persistente y API

Estado: hecho.

Objetivo: convertir el prototipo en un servicio persistente con API y memoria recuperable.

Hecho:

- Persistencia SQLite.
- API HTTP.
- UI minima en `/app`.
- Estado de relacion.
- Proactividad inicial.
- Consolidacion incremental.
- Manejo de contradicciones.
- Recuperacion hibrida hot+cold.

Evidencia:

- Commits `e2bef3f`, `a4742b6`.
- Tests de API, persistencia, consolidacion y recuperacion.

## Sprint 3 - Cognicion interna

Estado: hecho.

Objetivo: ampliar NIÑO con modelos internos y comportamiento cognitivo observable.

Hecho:

- Self-model.
- World-model.
- Maduracion de drives.
- Proactividad contextual.
- Ciclo de sueno/reflexion.
- Respuestas introspectivas.
- Scheduler interno.
- Modelo afectivo.
- Narrativa autobiografica.

Evidencia:

- Commits `e95b497`, `8b6579c`, `e68bdd8`, `3a690e5`, `7565baa`.
- Tests de modelos cognitivos, afecto, narrativa e internal loop.

## Sprint 4 - Operacion, privacidad y salud

Estado: hecho.

Objetivo: preparar el sistema para operar con mas seguridad y control.

Hecho:

- Gestion de memoria.
- Listado y limpieza de agentes.
- Scheduler autonomo.
- Recuperacion de memoria mas limpia.
- Export de agente.
- Export seguro con redaccion.
- Metricas.
- Politicas de privacidad.
- Inbox proactivo.
- Busqueda de memoria.
- Snapshots de desarrollo.
- Health profile operativo.
- Pruning de agentes.
- Mejora de continuidad conversacional.

Evidencia:

- Commits `b076939`, `2f9cc71`, `cff1a02`, `7e4d47e`, `79cb90d`, `ff2b176`.
- Tests de autonomia, scheduler, privacidad, inbox, busqueda, metricas, policy y servidor.

## Sprint 5 - Vida continua y experiencia real

Estado: hecho inicial.

Objetivo: pasar de prototipo funcional a uso vivo diario.

Hecho:

- Arranque persistente del servidor sin depender de una terminal abierta. Hecho inicial: `scripts/nino-launchd`; diagnostico de permisos macOS con `scripts/nino-launchd doctor`; instalacion en ruta segura con `scripts/nino-install-local`.
- Script o servicio local para iniciar/parar NIÑO de forma simple. Hecho: `scripts/ninoctl`.
- Crear un agente real inicial y poblar memoria con interacciones utiles. Hecho inicial: agente `nino`.
- Flujo de conversacion diaria desde la UI. Hecho inicial: consola `/app` centrada en agente vivo.
- Pantalla de estado entendible: energia, relacion, memoria, proactividad, madurez. Hecho inicial: metricas principales visibles.
- Historial conversacional legible. Hecho inicial: `/app` carga episodios persistidos al abrir o cambiar de agente.
- Backups locales de `data/nino.db`. Hecho inicial: `scripts/ninoctl backup`, `scripts/ninoctl backups`, `scripts/ninoctl restore`, `POST /operations/backup` y `GET /operations/backups` desde UI.
- Logs operativos claros. Hecho inicial: `scripts/ninoctl logs`.
- Comando de diagnostico: salud, modo local-first, agentes, episodios, memoria, inbox, ultimo ciclo, readiness y auditorias. Hecho inicial: `scripts/ninoctl doctor`, `scripts/ninoctl readiness`, `scripts/ninoctl audit`, `scripts/ninoctl server-audit`, `scripts/ninoctl persistent-audit`, `scripts/ninoctl live-audit`, `scripts/ninoctl final-audit`.
- Validar continuidad entre sesiones reales. Hecho inicial: `nino` conserva episodios y memoria tras reinicio.

Criterios de salida:

- NIÑO puede arrancar automaticamente o con un unico comando documentado.
- Arranque automatico macOS. Hecho inicial: `scripts/nino-launchd install`; si el repo vive en `Desktop/Documents/Downloads`, `scripts/nino-launchd doctor` detecta el bloqueo de privacidad de macOS y `scripts/nino-install-local` prepara una copia en `~/Developer/bebe`.
- Hay al menos un agente vivo con memoria persistente no vacia.
- La UI permite interactuar, revisar memoria y entender el estado sin usar `curl`.
- Existe backup local manual o automatico de la base.
- Una sesion cerrada y reabierta conserva continuidad verificable.

## Sprint 6 - Producto local usable

Estado: hecho inicial.

Objetivo: convertir la UI basica en una herramienta comoda para uso continuo.

Hecho:

- Redisenar `/app` como consola operativa real. Hecho inicial.
- Separar vistas: conversacion, memoria, estado, proactividad, export/backups. Hecho inicial: paneles operativos.
- Controles seguros para reset, pruning, export e import. Hecho inicial: confirmaciones para acciones destructivas o sensibles.
- Export desde UI. Hecho inicial: descarga segura y descarga completa con confirmacion.
- Import desde UI. Hecho inicial: restaurar agente desde JSON exportado.
- Pruning desde UI. Hecho inicial: previsualizar y limpiar agentes por prefijo con confirmacion.
- Indicadores visuales de salud y continuidad. Hecho inicial: metricas y estado.
- Mejor manejo de errores en UI. Hecho inicial: errores HTTP visibles por respuesta JSON.
- Configuracion visible de proactividad. Hecho inicial.
- Gestion de inbox proactivo desde UI. Hecho inicial: marcar entregado y limpiar entregados.
- Filtros y busqueda en episodios/memoria. Hecho inicial: busqueda de memoria.
- Acciones rapidas para consolidar, sonar, ciclo interno y scheduler. Hecho inicial: consolidar y operaciones internas via API/UI.
- Respuestas naturales con Claude usando memoria local como contexto. Hecho inicial: adaptador opcional `NINO_LLM_PROVIDER=claude`.
- Contexto mixto para Claude. Hecho inicial: ultimos turnos, memoria recuperada, hechos frios activos y redaccion de email/numeros largos.

Criterios de salida:

- El uso normal no requiere terminal.
- Las acciones peligrosas tienen confirmacion.
- La memoria se puede inspeccionar y buscar comodamente.
- El estado del agente se entiende en menos de un minuto.

## Sprint 7 - Robustez y evaluacion

Estado: hecho inicial.

Objetivo: medir si NIÑO mejora como sistema de memoria y continuidad.

Hecho:

- Evaluaciones repetibles de continuidad conversacional. Hecho inicial: `nino.eval_runner`.
- Dataset local de conversaciones de prueba. Hecho inicial: `eval/memory_regression.json`.
- Pruebas de regresion para memoria realista. Hecho inicial: tests del eval runner.
- Smoke test local de producto. Hecho inicial: `scripts/nino-smoke` valida UI servida, memoria, permisos, tareas, inbox, export seguro y backup con SQLite temporal.
- Puerta de readiness local. Hecho inicial: `scripts/nino-readiness` ejecuta tests y smoke end-to-end.
- Contrato API publicable. Hecho inicial: `GET /openapi.json` generado desde el catalogo de endpoints.
- Metricas de calidad historicas. Hecho inicial: guardar y consultar historial de evaluacion conversacional por agente.
- Comparativa antes/despues por sprint. Hecho inicial: runner determinista y dataset local.
- Deteccion de contradicciones mas explicable. Hecho inicial: contradicciones en consolidacion y razonamiento auditable.
- Decaimiento de memoria parametrizable por perfil. Hecho inicial: endpoint de decay y politicas de memoria.
- Auditoria de privacidad/export seguro. Hecho inicial: export seguro, redaccion y audit log.

Criterios de salida:

- Hay una forma objetiva de saber si una mejora ayuda o rompe continuidad.
- Las metricas sobreviven entre ejecuciones.
- Las regresiones de memoria se detectan antes de publicar.

## Sprint 8 - Integracion y autonomia avanzada

Estado: hecho inicial.

Objetivo: preparar integraciones externas sin perder control ni seguridad.

Hecho:

- Adaptador LLM configurable. Hecho inicial: `NINO_LLM_PROVIDER`.
- Adaptador Claude via Anthropic Messages API. Hecho inicial en Sprint 6 por necesidad de conversacion real; diagnostico local con `GET /operations/claude`, panel LLM, `scripts/ninoctl claude`, errores de configuracion numerica y configuracion persistente segura con `scripts/ninoctl configure-claude`, `scripts/nino-configure-claude`, `.env.local` o macOS Keychain.
- Prueba viva opcional de Claude. Hecho inicial: `scripts/nino-claude-live --require-key --json`.
- Politicas de herramienta/accion externa. Hecho inicial: permisos por tipo de accion, bloqueo por defecto y auditoria.
- Cola de tareas autonomas con limites. Hecho inicial: cola persistente por agente con limite de pendientes, bloqueo por permisos, ejecucion manual y panel en `/app`.
- Agenda proactiva con ventanas horarias. Hecho inicial: `active_hours_start`/`active_hours_end` bloquean envios fuera de ventana y se configuran desde `/app`.
- Sistema de permisos por accion. Hecho inicial: `GET /permissions` y `POST /permissions/configure`.
- Registro auditable de decisiones. Hecho inicial: audit log por agente para cada tick.
- Modo offline/local-first claramente definido. Hecho inicial: `GET /operations/mode` y boton Modo en `/app` muestran almacenamiento local, Claude opcional y capacidades offline.
- Preparacion para empaquetado local. Hecho inicial: `pyproject.toml` con paquete `nino-local` y comando instalable `nino-server`.

Criterios de salida:

- NIÑO puede usar capacidades externas bajo consentimiento explicito.
- Cada accion autonoma queda registrada y explicada.
- El usuario puede apagar o limitar cualquier capacidad.

## Proxima tarea recomendada

Auditoria final de producto 100% local:

1. Hecho: crear `scripts/ninoctl` para `start`, `stop`, `status`, `health`, `logs` y `backup`.
2. Hecho: documentarlo en `README.md`.
3. Hecho: probar que el servidor arranca y responde `/health`.
4. Hecho: crear, listar y restaurar backups de `data/nino.db`.
5. Hecho inicial: crear el agente vivo `nino` y validar continuidad entre reinicios.
6. Hecho inicial: mejorar la UI para conversacion diaria y estado operativo.
7. Hecho inicial: mejorar historial conversacional persistente.
8. Hecho inicial: acciones de entrega del inbox y mejor control de proactividad.
9. Hecho inicial: cola de tareas autonomas con permisos y ejecucion manual.
10. Hecho inicial: agenda proactiva con ventanas horarias y limites configurables desde UI.
11. Hecho inicial: modo offline/local-first claramente definido y visible.
12. Hecho inicial: preparacion para empaquetado local.
13. Hecho inicial: validado con servidor real local: `/health`, `/operations/mode`, `/tasks`, `/permissions` y `/app`.
14. Hecho inicial: diagnostico de Claude sin llamada externa mediante `/operations/claude`, incluyendo `config_errors` para valores invalidos.
15. Hecho inicial: puerta local de producto con `scripts/nino-smoke`.
16. Hecho inicial: checklist de producto en `PRODUCT_READINESS.md` y puerta `scripts/nino-readiness` accesible tambien con `scripts/ninoctl readiness`.
17. Hecho inicial: plantilla `.env.example`, configuradores `scripts/ninoctl configure-claude` y `scripts/nino-configure-claude`, y carga automatica de `.env.local` para Claude local.
18. Hecho inicial: launchd carga `.env.local` en runtime sin incrustar `ANTHROPIC_API_KEY` en el plist, diagnostica bloqueos de permisos macOS y puede instalarse desde una copia local no protegida.
19. Hecho inicial: prueba viva opcional de Claude con `scripts/nino-claude-live`.
20. Hecho inicial: contrato OpenAPI local con `/openapi.json`.
21. Hecho inicial: auditoria final repetible con `scripts/nino-product-audit` y `GET /operations/audit` visible en `/app`.
22. Hecho inicial: auditoria estricta del servicio persistente con `scripts/nino-product-audit --require-launchd --json` y `scripts/ninoctl persistent-audit`.
23. Hecho inicial: comando unico de cierre estricto `scripts/ninoctl final-audit`, que exige servicio persistente y Claude vivo.
24. Siguiente: ejecutar `scripts/ninoctl final-audit` con una `ANTHROPIC_API_KEY` real.

Esta tarea desbloquea el uso vivo sin depender de comandos sueltos.
