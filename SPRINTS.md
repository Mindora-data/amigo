# NIÑO Sprint Roadmap

Estado confirmado el 2026-05-28:

- GitHub se mantiene sincronizado desde los commits de sprint; confirmar hash exacto con `git rev-parse HEAD`.
- Rama activa: `main`.
- Suite actual: 253 tests pasando.
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
- Restauracion guiada de backups. Hecho inicial: la UI lista backups y muestra el comando exacto `scripts/ninoctl restore` para usarlo con el servidor parado.
- Logs operativos claros. Hecho inicial: `scripts/ninoctl logs`, `GET /operations/logs` y boton Logs en `/app`.
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
- Gestion individual de memoria desde UI. Hecho inicial: eliminar episodios y hechos frios con confirmacion.
- Decaimiento manual de memoria desde UI. Hecho inicial: aplicar factor decay con validacion y confirmacion.
- Acciones rapidas para consolidar, sonar, ciclo interno y scheduler. Hecho inicial: consolidar y operaciones internas via API/UI.
- Respuestas naturales con Claude usando memoria local como contexto. Hecho inicial: adaptador opcional `NINO_LLM_PROVIDER=claude`.
- Contexto mixto para Claude. Hecho inicial: ultimos turnos, memoria recuperada, hechos frios activos y redaccion de email/numeros largos.
- Cierre guiado desde UI. Hecho inicial: el panel LLM puede guardar Claude, reiniciar el servicio persistente, esperar `/health`, ejecutar `Cierre final` y generar informe de cierre.

Criterios de salida:

- El uso normal no requiere terminal.
- Las acciones peligrosas tienen confirmacion.
- La memoria se puede inspeccionar y buscar comodamente.
- El estado del agente se entiende en menos de un minuto.

## Sprint 7 - Robustez y evaluacion

Estado: hecho inicial.

Objetivo: medir si NIÑO mejora como sistema de memoria y continuidad.

Hecho:

- Evaluaciones repetibles de continuidad conversacional. Hecho inicial: `nino.eval_runner`, `scripts/ninoctl eval`, `nino-eval`, `GET /operations/eval` y boton `Eval local` en `/app`.
- Dataset local de conversaciones de prueba. Hecho inicial: `eval/memory_regression.json`.
- Pruebas de regresion para memoria realista. Hecho inicial: tests del eval runner.
- Smoke test local de producto. Hecho inicial: `scripts/nino-smoke` valida UI servida, memoria, permisos, tareas, inbox, export seguro, backup, informes de cierre y accion siguiente con SQLite temporal.
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
- Estado final resumido. Hecho inicial: `scripts/ninoctl product-status` y `nino-status` muestran preflight, eval local y bloqueos restantes.
- Accion siguiente directa. Hecho inicial: `scripts/ninoctl next-action` imprime solo `recommended_next_action`.
- Accion siguiente desde API. Hecho inicial: `GET /operations/next-action` devuelve accion recomendada y bloqueos actuales.
- Estado final desde API/UI. Hecho inicial: `GET /operations/product-status` y boton `Estado final` muestran el mismo resumen de preflight, eval y bloqueos.
- Estado final con evidencia de cierre. Hecho inicial: `product-status` y `/operations/product-status` incluyen `latest_report` con el ultimo informe disponible.
- Estado final legible con head y bloqueos. Hecho inicial: `product-status` y UI muestran head corto y bloqueos del ultimo informe.
- Estado final con evidencia actual. Hecho inicial: `product-status` y UI incluyen `latest_report_current` para comprobar que el ultimo informe coincide con la revision instalada.
- Estado final bloquea evidencia obsoleta. Hecho inicial: `product-status` incluye `closing_evidence` como bloqueo si falta informe actual.
- Siguiente accion de cierre. Hecho inicial: `product-status` y UI exponen `recommended_next_action` con el proximo comando operativo.
- Auditoria de terminacion. Hecho inicial: `scripts/ninoctl completion-audit` y `nino-completion-audit` listan requisitos, evidencias y bloqueos restantes, incluyendo agente vivo `nino`.
- Auditoria de terminacion desde API/UI. Hecho inicial: `GET /operations/completion-audit` y boton `Terminación`.
- Auditoria de terminacion con evidencia nombrada. Hecho inicial: `completion-audit` y UI muestran `latest_report` con head y bloqueos.
- Auditoria de terminacion con evidencia actual. Hecho inicial: `completion-audit` y UI incluyen `latest_report_current`.
- Siguiente accion en auditoria de terminacion. Hecho inicial: `completion-audit` y UI incluyen `recommended_next_action`.
- Requisito explicito de evidencia de cierre. Hecho inicial: `completion-audit` incluye `closing_evidence` cubierto por smoke y API.
- Evidencia de cierre actual obligatoria. Hecho inicial: `closing_evidence` exige `latest_report_current` para no cerrar con informes obsoletos.
- Evidencia de cierre accionable obligatoria. Hecho inicial: `closing_evidence` exige tambien `local_smoke.next_action`.
- Informe de cierre local. Hecho inicial: `scripts/ninoctl closing-report` y `nino-closing-report` escriben JSON de evidencias en `data/reports/`.
- Informe de cierre autocontenido. Hecho inicial: cada JSON incluye `report_file` con su nombre y ruta.
- Informe de cierre accionable. Hecho inicial: cada JSON incluye `summary.recommended_next_action`.
- Informe de cierre autoreferente. Hecho inicial: `product_status.latest_report` y `completion_audit.latest_report` apuntan al propio JSON generado.
- Lectura de informes desde CLI. Hecho inicial: `scripts/ninoctl reports` lista informes y `scripts/ninoctl report <name>` imprime un informe validando el nombre.
- Ultimo informe desde CLI. Hecho inicial: `scripts/ninoctl report latest` imprime el informe mas reciente.
- Informe de cierre desde API/UI. Hecho inicial: `POST /operations/closing-report` y boton `Informe cierre`.
- Listado de informes desde API/UI. Hecho inicial: `GET /operations/reports` y boton `Ver informes`.
- Ultimo informe desde API/UI. Hecho inicial: `GET /operations/reports/latest` y boton `Último informe`.
- Lectura segura de informes desde API/UI. Hecho inicial: `GET /operations/reports/{report_name}` y boton `Ver JSON`, limitado a nombres `nino-closing-*.json`.
- Smoke de informes de cierre. Hecho inicial: `scripts/nino-smoke` cubre crear, listar, leer, leer el ultimo y rechazar nombres invalidos de informes.
- Smoke de accion siguiente. Hecho inicial: `scripts/nino-smoke` cubre `GET /operations/next-action` y confirma que apunta al cierre con Claude cuando falta configurarlo.
- Cierre final guiado. Hecho inicial: `scripts/ninoctl finish --key-stdin` configura Claude en Keychain por defecto, reinicia launchd, ejecuta `final-audit`, genera informe de cierre y lanza `completion-audit`; `--preflight-only` evita la llamada viva.
- Cierre final sin reconfigurar secreto. Hecho inicial: `scripts/ninoctl finish --skip-configure` reinicia, audita, genera evidencia y lanza `completion-audit` cuando Claude ya esta configurado.
- Comandos de cierre con doble camino. Hecho inicial: la evidencia `setup_commands` incluye `finish --key-stdin` y `finish --skip-configure` para cubrir primera configuracion y cierre posterior.
- Recomendacion de cierre segun estado Claude. Hecho inicial: si solo bloquea `claude_live`, `recommended_next_action` cambia a `scripts/ninoctl finish --skip-configure`.
- Guardado de cierre sin reconfigurar. Hecho inicial: `finish --skip-configure` falla antes de reiniciar si Claude no esta ya configurado.
- Validacion CLI de configuracion Claude. Hecho inicial: `scripts/nino-configure-claude` rechaza modelo o servicio Keychain con `=` o saltos de linea antes de escribir `.env.local`.
- Matriz de terminacion tras cierre UI. Hecho inicial: `Cierre final` y `Cierre guiado` refrescan la auditoria de terminacion con la evidencia del informe recien generado.
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

## Sprint 9 - Proactividad humana

Estado: hecho inicial.

Objetivo: ampliar la proactividad más allá de alarmas explícitas, con seguimientos y check-ins que respetan límites duros de intrusión.

Hecho:

- Candidatos proactivos persistentes en tabla `proactive_candidate`, con índice por usuario, estado y fecha.
- Seguimientos como candidatos separados de la entrega real: `followup` no implica mensaje hasta pasar por reglas.
- Extracción híbrida de seguimientos con LLM en JSON estricto y fallo seguro a silencio si el JSON no es válido.
- Filtro barato previo para no llamar al LLM extractor en mensajes sin señales temporales/futuras.
- Decisión de entrega en Python con cuatro cestas: horario 9-22, tope diario 1, cooldown 6 horas y receptividad.
- Caducidad de candidatos vencidos antes de evaluar entregas.
- Check-ins por inactividad con umbral alto de 7 días y retroceso exponencial si el usuario no reacciona.
- Redacción proactiva breve que hereda la ética de amigo y bloquea culpa/reproche/emoción humana fingida.
- Reacción del usuario: un mensaje posterior marca el último proactivo entregado como reaccionado.
- Aislamiento por usuario en candidatos proactivos.
- Reset de agente borra también candidatos proactivos.

Criterios de salida:

- Las alarmas temporales confirmadas mantienen prioridad sobre estos límites.
- La lógica decide cuándo hablar; el LLM solo extrae candidatos y redacta mensajes ya aprobados.
- Si hay duda, JSON inválido, candidato caducado, baja receptividad o fuera de horario, amigo calla.
- Tests cubren límites, caducidad, check-in, aislamiento, persistencia y entrega de candidato.

## Sprint 10 - Aprendizaje honesto

Estado: hecho inicial.

Objetivo: que amigo aprenda de patrones agregados anónimos sin inventar vida propia ni compartir contenido privado.

Hecho:

- Tabla SQLite `global_pattern_outcome` con agregados `(gesture, context, outcome)` y `UNIQUE`.
- Vocabulario cerrado para destilado: gestos, contextos y resultados validados estrictamente.
- `distill_to_global` rechaza cualquier etiqueta fuera de vocabulario; texto libre se trata como bug.
- `starting_prior` devuelve `0.5` bajo `MIN_SAMPLE=50` y solo mueve el prior con evidencia agregada suficiente.
- `GET /operations/global-model` incluye `pattern_outcomes` como agregado anónimo, sin `user_id`.
- Responder a un proactivo marca reacción local y destila un resultado positivo anónimo.
- El prior global de `checkin/dia_neutro` ajusta el umbral inicial de check-in para amigos nuevos.
- La relación real del usuario sigue sobreescribiendo el prior al registrar reacción local.
- Tests de no-fuga verifican que datos personales no aparecen en `global_pattern_outcome`.

Criterios de salida:

- No se guarda texto crudo, nombres, lugares, fechas concretas ni identificadores de usuario en patrones globales.
- Los patrones globales afectan solo priors iniciales, nunca crean recuerdos ni experiencias fingidas.
- Tests cubren vocabulario, conteo agregado, muestra mínima, no-fuga, persistencia y efecto de prior.

## Sprint 11 - Intención explícita para recordatorios

Estado: hecho inicial.

Objetivo: impedir que una hora mencionada de pasada se convierta en recordatorio o alarma.

Hecho:

- Los recordatorios requieren verbo explícito de intención: `recuérdame`, `avísame`, `ponme una alarma`, `pon un recordatorio` o `no me dejes olvidar`.
- Una hora suelta como `a las 18` o `sobre las 6` no crea evento temporal ni recordatorio.
- Las citas reales con hora, como `tengo dentista a las 11`, quedan como evento `offered` y amigo pregunta si quieres aviso media hora antes.
- El prompt del LLM prohíbe convertir respuestas breves con hora en recordatorios y limita la recuperación a una disculpa breve sin encadenar `perdona`.

Criterios de salida:

- Tests cubren hora suelta como respuesta, hora suelta aislada, recordatorio explícito, cita con confirmación pendiente y recuperación sin sumisión.

## Sprint 12 - Auth producción privada

Estado: hecho inicial.

Objetivo: endurecer amigo para exponerlo desde internet solo para uso propio, sin registro abierto ni consola interna pública.

Hecho:

- `NINO_ENV=prod` falla cerrado si `NINO_REQUIRE_SESSION=true` no está activo.
- Producción exige `NINO_PASSWORD_HASH`; la contraseña se configura por stdin con `scripts/ninoctl configure-password --password-stdin`.
- Hash de contraseña con `scrypt` de la librería estándar y comparación constante.
- Login con rate limit por IP en memoria para frenar fuerza bruta básica.
- Sesiones con TTL de 7 días, refresco en uso, logout invalidante y hash HMAC del token en memoria.
- `/user` usa cookie `HttpOnly`, `SameSite=Strict` y `Secure` en producción; no guarda el token en `localStorage`.
- `X-Nino-Session` se mantiene solo para clientes internos y tests.
- Producción rechaza tráfico sin `X-Forwarded-Proto: https`.
- Cabeceras de seguridad básicas: HSTS, `nosniff`, `DENY` y CSP mínima.
- Auditoría de acceso en memoria para `login_ok`, `login_failed`, `login_blocked` y `session_expired`, sin guardar contraseñas ni tokens.
- `/app` queda deshabilitada en producción para no exponer la consola interna.
- `DEPLOY_PROD.md` documenta variables, contraseña, sesiones, HTTPS, consola interna, secretos y reacción ante intentos sospechosos.

Criterios de salida:

- Tests cubren login correcto/incorrecto, bloqueo por intentos, sesión obligatoria, expiración, logout, HTTPS, `/app` cerrada y auditoría.
- `nino-readiness` pasa y `product-status` queda `ready` tras informe de cierre actual.

## Sprint 13 - Despliegue privado controlado

Estado: hecho inicial.

Objetivo: tener una puerta práctica antes de exponer amigo: validar localmente el modo producción privado y dejar el despliegue externo reducido a configurar secretos/proxy.

Hecho:

- `scripts/nino-prod-smoke` ejecuta una simulación local de producción sin abrir el servidor a internet.
- `scripts/ninoctl prod-smoke` expone la misma puerta desde la CLI operativa.
- `nino-prod-smoke` valida arranque cerrado, HTTPS obligatorio, `/app` deshabilitada, rutas privadas con sesión, contraseña correcta/incorrecta, cookie segura y logout.
- `DEPLOY_PROD.md` incluye la validación `scripts/ninoctl prod-smoke` antes de exponer.

Criterios de salida:

- `scripts/ninoctl prod-smoke` debe pasar antes de activar un proxy público.
- La validación externa real queda pendiente hasta tener el proyecto/proxy conectado: abrir `/user` por HTTPS, confirmar login/logout y verificar que `/app` no responde.

## Sprint 14 - UI final minimalista robusta

Estado: hecho inicial.

Objetivo: dejar `/user` como superficie final simple y fiable: login y, después, chat/voz sin consola interna ni paneles técnicos.

Hecho:

- `/user` conserva una pantalla inicial mínima de usuario/contraseña y una pantalla posterior de chat/voz.
- La UI deja de intentar login automático con contraseña vacía cuando hay usuario guardado.
- La sesión se reanuda con `GET /session/status` usando la cookie `HttpOnly`; si no hay sesión válida, vuelve al login.
- El formulario de login exige usuario y contraseña antes de enviar.
- Errores de login se muestran en el estado mínimo sin abrir la conversación.
- Logout limpia la cookie en servidor, borra el usuario local y vacía la contraseña en la UI.
- Ajustes visuales discretos: altura `100dvh`, scroll solo en mensajes, foco accesible, texto largo sin romper layout y estado con `role=status`.

Criterios de salida:

- Tests verifican que `/user` contiene `resumeSession`, usa `/session/status`, no guarda token en `localStorage` y no llama `loginUser()` al cargar.
- La interfaz final sigue sin exponer controles operativos de `/app`.

## Sprint 15 - Proactividad no repetitiva

Estado: hecho inicial.

Objetivo: evitar que amigo insista con el mismo seguimiento o la misma pregunta abierta, especialmente al refrescar `/user` o al tener varios avisos pendientes iguales.

Hecho:

- Cada seguimiento no candidato registra una `proactive_source_key` para recordar que ya se propuso.
- Las preguntas abiertas del world-model solo se reabren una vez por tema en una ventana de 30 días.
- Los seguimientos genéricos de memoria saliente solo se envían una vez por episodio.
- Preferencias y patrones globales anónimos también quedan protegidos contra repetición por clave.
- El inbox proactivo deduplica mensajes pendientes con el mismo texto o la misma `proactive_source_key`.
- La UI `/user` deduplica avisos del inbox por texto y marca duplicados como entregados sin mostrarlos otra vez.
- La contraseña deja de ser obligatoria a nivel HTML en local si no se ha configurado password; producción sigue validando contraseña en backend.

Criterios de salida:

- Tests cubren que una misma pregunta abierta no se repite, un mismo episodio saliente no se repite y el inbox no duplica un mensaje pendiente.
- `nino-readiness` pasa con la suite completa.

## Sprint 16 - Cliente Telegram por long polling

Estado: hecho inicial.

Objetivo: añadir Telegram como ventana privada al backend local sin webhooks ni exponer la máquina a internet.

Hecho:

- Módulo `nino.telegram` con servicio long polling basado en `getUpdates`.
- Script `scripts/nino-telegram` y comando `scripts/ninoctl telegram`.
- Servicio launchd separado con `scripts/nino-telegram-launchd` y `scripts/ninoctl telegram-launchd`.
- Token del bot leído desde `NINO_TELEGRAM_BOT_TOKEN`, `TELEGRAM_BOT_TOKEN` o Keychain vía `NINO_TELEGRAM_KEYCHAIN_SERVICE`; si falta, no arranca.
- Tabla `telegram_link` para mapear `chat_id` a `user_id` interno aislado.
- Tabla `telegram_link_code` para códigos de vinculación de un solo uso, generados con `scripts/ninoctl telegram --create-link-code <user_id>`.
- Chat no vinculado recibe presentación explícita como bot/asistente con memoria y no accede a ninguna ruta privada.
- Mensajes vinculados llaman al backend por las rutas `/users/{user_id}/agents/nino/tick`, con sesión backend y fecha/hora del mensaje.
- Push proactivo por Telegram llama a `/proactivity/evaluate`; solo envía si la lógica del Sprint 9 ya decidió entregar.
- Respuestas del usuario por Telegram entran por `tick`, por lo que cuentan como reacción y actualizan receptividad.
- `TELEGRAM_SETUP.md` documenta BotFather, token, vinculación y launchd.

Criterios de salida:

- Tests cubren chat vinculado, chat no vinculado, aislamiento entre dos `chat_id`, push correcto, bloqueo por límites, reacción del usuario y falta de token.
- No hay webhooks ni puertos entrantes nuevos.

## Sprint 17 - Telegram grupos conservador

Estado: hecho inicial.

Objetivo: permitir usar amigo en grupos sin mezclar memorias privadas ni convertirlo en un bot pesado.

Hecho:

- Telegram detecta chats `group`/`supergroup`.
- En grupo, amigo ignora mensajes no dirigidos a él.
- Responde solo si lo mencionan por `@username`, usan comando o responden a un mensaje suyo.
- Los mensajes de grupo no vinculados usan memoria de grupo aislada `telegram-group-<chat_id>`.
- Si el remitente individual está vinculado como `user:<telegram_user_id>`, sus mensajes dirigidos al bot usan su memoria privada.
- La documentación explica las reglas de grupo y el límite de privacidad.

Criterios de salida:

- Tests cubren silencio en conversación de grupo no dirigida, mención al bot, respuesta a mensaje del bot y usuario vinculado dentro de grupo.
- No se comparte memoria privada con un grupo por defecto.

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
21. Hecho inicial: auditoria final repetible con `scripts/nino-product-audit` y `GET /operations/audit` visible en `/app`, incluyendo comando y requisitos de cierre estricto.
22. Hecho inicial: auditoria estricta del servicio persistente con `scripts/nino-product-audit --require-launchd --json` y `scripts/ninoctl persistent-audit`.
23. Hecho inicial: comando unico de cierre estricto `scripts/ninoctl final-audit`, que exige servicio persistente, misma DB auditada/servida y Claude vivo, y marca `audit_profile.strict_final`.
24. Hecho inicial: preflight final sin llamada externa con `scripts/ninoctl final-preflight`, que exige launchd, DB runtime alineada y Claude configurado antes de gastar una llamada viva.
25. Hecho inicial: resumen accionable de cierre en `/operations/audit` y `/app` con `final_readiness`, auditoria local, servicio persistente observado, bloqueos y siguientes comandos.
26. Hecho inicial: evaluacion local de regresion accesible desde `/operations/eval` y boton `Eval local` en `/app`.
27. Hecho inicial: preflight y cierre final accesibles desde API/UI con `GET /operations/final-preflight`, `POST /operations/final-audit`, botones `Preflight final` y `Cierre final`.
28. Hecho inicial: configuracion y desactivacion de Claude desde API/UI/CLI con `POST /operations/claude/configure`, `POST /operations/claude/disable`, `scripts/ninoctl configure-claude`, `scripts/ninoctl disable-claude`, botones `Guardar Claude`, `Cierre guiado` y `Desactivar Claude`, sin devolver la key, con Keychain por defecto o `.env.local` con modo `600`.
29. Hecho inicial: reinicio del servicio persistente desde API/UI con `POST /operations/restart` y boton `Reiniciar servicio`, usando `launchd KeepAlive`.
30. Hecho inicial: evaluacion local de regresion tambien disponible por CLI con `scripts/ninoctl eval` y console script `nino-eval`.
31. Hecho inicial: estado final resumido con `scripts/ninoctl product-status`, console script `nino-status`, `GET /operations/product-status` y boton `Estado final`.
31a2. Hecho inicial: `product-status` y `/operations/product-status` incluyen `latest_report` con nombre, ruta, head y bloqueos del ultimo informe.
31a3. Hecho inicial: salida legible y UI de `Estado final` muestran head corto y bloqueos de `latest_report`.
31a4. Hecho inicial: `product-status` y UI incluyen `recommended_next_action` para guiar el cierre.
31a5. Hecho inicial: `product-status` y UI incluyen `latest_report_current` para detectar si el informe mas reciente corresponde al commit/runtime actual.
31a6. Hecho inicial: `product-status` bloquea con `closing_evidence` cuando `latest_report_current` no esta listo.
31b. Hecho inicial: auditoria de terminacion requisito por requisito con `scripts/ninoctl completion-audit`, `nino-completion-audit`, `GET /operations/completion-audit` y boton `Terminación`, incluyendo agente vivo `nino`.
31b1. Hecho inicial: `completion-audit` incluye `latest_report` con nombre, head y bloqueos del ultimo informe.
31b1a. Hecho inicial: `completion-audit` incluye `recommended_next_action` para guiar el cierre desde la matriz de terminacion.
31b1b. Hecho inicial: `completion-audit` y UI incluyen `latest_report_current` para comprobar que la matriz apunta al informe de la revision actual.
31b2. Hecho inicial: requisito `closing_evidence` en auditoria de terminacion para comprobar informes de cierre.
31b3. Hecho inicial: `closing_evidence` solo queda listo si `latest_report_current` confirma informe de la revision instalada.
31c. Hecho inicial: informe de cierre local con `scripts/ninoctl closing-report` y `nino-closing-report`.
31c4. Hecho inicial: cada informe de cierre incluye `report_file` para identificarse sin depender del listado externo.
31c5. Hecho inicial: cada informe de cierre incluye `summary.recommended_next_action` para saber el siguiente paso desde el propio archivo.
31c6. Hecho inicial: cada informe de cierre actualiza sus secciones internas para que `latest_report_current` apunte al propio informe.
31c2. Hecho inicial: lectura de informes desde CLI con `scripts/ninoctl reports` y `scripts/ninoctl report <name>`.
31c3. Hecho inicial: lectura del ultimo informe desde CLI con `scripts/ninoctl report latest`.
31d. Hecho inicial: informe de cierre desde API/UI con `POST /operations/closing-report` y boton `Informe cierre`.
31e. Hecho inicial: listado de informes desde API/UI con `GET /operations/reports` y boton `Ver informes`.
31e2. Hecho inicial: lectura del ultimo informe desde API/UI con `GET /operations/reports/latest` y boton `Último informe`.
31f. Hecho inicial: lectura segura de informes desde API/UI con `GET /operations/reports/{report_name}` y boton `Ver JSON`.
31g. Hecho inicial: smoke de informes de cierre con checks `closing_report`, `closing_report_list`, `closing_report_read`, `closing_report_latest` y `closing_report_name_guard`.
32. Hecho inicial: cierre final guiado con `scripts/ninoctl finish --key-stdin`, Keychain por defecto, modo `--preflight-only`, informe de cierre y `completion-audit` al final del flujo.
33. Hecho inicial: cierre guiado desde UI con configuracion Claude, reinicio persistente, espera de salud, cierre final e informe de cierre.
34. Hecho inicial: tras `Cierre final` o `Cierre guiado`, la UI refresca la matriz `Terminación` usando el informe recien escrito.
35. Hecho inicial: `scripts/ninoctl finish --skip-configure` permite cerrar sin reintroducir la key si Claude ya esta guardado.
36. Hecho inicial: `setup_commands` de Claude expone tanto `finish --key-stdin` como `finish --skip-configure`.
37. Hecho inicial: `recommended_next_action` recomienda `finish --skip-configure` cuando Claude ya esta configurado y falta solo la prueba viva.
38. Hecho inicial: `finish --skip-configure` valida configuracion Claude antes de reiniciar launchd.
39. Hecho inicial: configurador CLI Claude rechaza valores que puedan corromper `.env.local`.
40. Hecho inicial: `scripts/ninoctl next-action` imprime el siguiente comando operativo sin leer el resumen completo.
41. Hecho inicial: `GET /operations/next-action` expone la accion recomendada para clientes ligeros.
42. Hecho inicial: `scripts/nino-smoke` cubre `GET /operations/next-action` dentro de la puerta local de producto.
43. Hecho inicial: `completion-audit` exige `local_smoke.next_action` dentro de `closing_evidence`.
44. Hecho inicial: `scripts/ninoctl status` y `logs` detectan el servicio launchd persistente aunque no exista PID manual de `ninoctl start`.
45. Hecho inicial: `scripts/ninoctl wait-health` espera `/health` y `finish` lo usa tras reiniciar launchd antes de auditar.
46. Hecho inicial: `scripts/ninoctl status` y `wait-health` muestran el ultimo error de `curl` para distinguir permisos/red de servicio caido.
47. Hecho inicial: `scripts/ninoctl finish --key-env` permite cerrar usando `ANTHROPIC_API_KEY` ya exportada, sin tuberia stdin.
48. Hecho inicial: `product-status` y `completion-audit` ordenan los siguientes comandos de cierre para mostrar juntos `finish --key-stdin`, `finish --key-env` y `finish --skip-configure` antes del reinicio/auditoria.
49. Hecho inicial: `scripts/nino-readiness` funciona en la copia runtime instalada aunque no incluya `tests/`, saltando solo la suite de desarrollo y manteniendo smoke/audit de producto.
50. Hecho inicial: la documentacion evita asignaciones inline de `ANTHROPIC_API_KEY` y guia hacia lectura silenciosa/export para no dejar secretos en comandos copiados.
51. Hecho inicial: llamada viva de Claude validada con Keychain, launchd y certificado TLS del sistema.
52. Hecho inicial: la UI muestra la respuesta del chat inmediatamente al volver `/tick`, antes de refrescar paneles secundarios.
53. Hecho inicial: cada respuesta `/tick` incluye `nino_context` con fuente, madurez, objetivos activos y memoria usada; la UI lo muestra como `contexto NIÑO` para diferenciar agente local de Claude directo.
54. Hecho inicial: el prompt activa `Modo continuidad` cuando el usuario pregunta por memoria, contexto, continuidad o diferencia con Claude directo; en ese modo NIÑO debe usar recuerdos/preferencias/objetivos concretos sin exponer mecanismos internos.
55. Hecho inicial: `tick` intenta consolidar automaticamente preferencias de alta confianza (`confidence >= 0.9`) y devuelve `auto_consolidated_count`/`auto_consolidation` para hacer visible cuándo NIÑO convirtió conversación en memoria fría.
56. Hecho inicial: la consolidacion conserva preferencias distintas como memoria activa y solo invalida preferencias cuando parecen alternativas del mismo tipo, evitando que una preferencia nueva borre otra no relacionada.
57. Hecho inicial: la consolidacion extrae hechos explicitos de identidad/contexto (`me llamo`, `trabajo como`, `mi proyecto se llama`) y los autoconsolida con confianza alta sin depender de que el usuario diga `prefiero`.
58. Hecho inicial: la consolidacion amplia hechos verificables con `vivo en`, `estudio` y `estoy/estamos trabajando en`, sustituyendo solo el dato activo del mismo tipo cuando aparece una version mas reciente de alta confianza.
59. Hecho inicial: la politica local recibe hechos frios activos y los usa al responder `que sabes de mi`/`quien soy`, haciendo visibles ubicacion, estudios, rol, proyecto y foco de trabajo sin depender solo del prompt Claude.
60. Hecho inicial: la recuperacion semantica de memoria fria expande claves tecnicas (`user_location`, `user_study`, `current_project_focus`, etc.) a terminos naturales para responder mejor a preguntas indirectas como `donde vivo` o `en que estamos trabajando`.
61. Hecho inicial: `nino_context.memory_candidates` expone `fact_id`, `source_episode_id` y `memory_type` (`hot`/`cold`) para distinguir memoria reciente de hechos persistidos en API/UI.
62. Hecho inicial: `/app` agrupa el contexto de NIÑO en `memoria fría` y `memoria reciente`, usando `memory_type` para separar hechos persistidos de episodios calientes.
63. Hecho inicial: la busqueda de memoria en `/app` muestra `memoria fria`/`memoria reciente`, score, confidence y origen corto para rastrear cada candidato recuperado.
64. Hecho inicial: la vista de memoria fria en `/app` muestra estado `activa`/`inactiva`, confidence y origen corto del episodio fuente para auditar recuerdos persistidos.
65. Hecho inicial: `/app` añade una entrada `memoria NIÑO` cuando una respuesta autoconsolida hechos, mostrando las claves/valores guardados en ese turno.
66. Hecho inicial: `/app` añade filtro `Todo`/`Fria`/`Reciente` en la busqueda de memoria, mostrando tambien `visible_candidates` y `memory_type_filter` en la salida cruda.
67. Hecho inicial: `POST /agents/{agent_id}/memory/search` acepta `memory_type_filter` (`all`/`cold`/`hot`) y devuelve `visible_candidates`, por lo que UI y clientes externos comparten el mismo filtro.
68. Hecho inicial: `POST /agents/{agent_id}/memory/search` devuelve `memory_type_counts` y `visible_memory_type_counts` con totales `cold`/`hot`/`total` antes y despues del filtro aplicado.
69. Hecho inicial: `/app` muestra una fila resumen de resultados de memoria con visibles/totales y desglose fria/reciente segun el filtro activo.
70. Hecho inicial: `memory/retrieve` y `memory/search` anotan cada candidato con `memory_type` (`cold`/`hot`) para que clientes externos no dependan de parsear `fact_id`.
71. Hecho inicial: `scripts/ninoctl memory-search` permite buscar memoria desde CLI con `--agent`, `--type all|cold|hot` y `--scope`, reutilizando el endpoint `/memory/search`.
72. Hecho inicial: `scripts/ninoctl memory-search` imprime por defecto un resumen legible con conteos, tipo, score, confidence y origen; `--json` conserva la salida cruda para automatizacion.
73. Hecho inicial: `README.md` documenta ejemplos de `scripts/ninoctl memory-search`, incluyendo filtros `--type`, `--scope` y salida `--json` para automatizacion.
74. Hecho inicial: `scripts/ninoctl memory-facts` lista memoria fria con `--agent`, `--status active|inactive|all` y `--json`, permitiendo inspeccion rapida de hechos persistidos sin busqueda semantica.
75. Hecho inicial: `README.md` documenta `memory-facts` junto a `memory-search` para operacion local de memoria fria activa.
76. Hecho inicial: `GET /agents/{agent_id}/memory/facts` devuelve `fact_counts` con totales active/inactive/total y desglose `active_by_key`/`inactive_by_key`.
77. Hecho inicial: `/app` muestra una fila resumen de memoria fria con total, activas e inactivas antes del listado de hechos.
78. Hecho inicial: `scripts/ninoctl memory-facts` imprime resumen con conteos active/inactive/total usando `fact_counts` del endpoint.
79. Hecho inicial: `scripts/ninoctl memory-facts` acepta `--key <clave>` para inspeccionar rapidamente preferencias, ubicacion, proyecto u otra familia de hechos frios.
80. Hecho inicial: `README.md` documenta `memory-facts --key` y explica filtros por estado y clave.
81. Hecho inicial: `GET /agents/{agent_id}/memory/facts` acepta filtros query `status=active|inactive|all` y `key=<clave>`, devolviendo `visible_facts`, `visible_fact_counts`, `status_filter` y `key_filter`.
82. Hecho inicial: `/app` carga memoria fria con `status=all` explicitamente para conservar la vista completa mientras el endpoint queda filtrable para clientes externos.
83. Hecho inicial: `scripts/ninoctl memory-facts` delega los filtros `--status` y `--key` al endpoint, evitando filtrar solo en cliente.
84. Hecho inicial: la memoria autonoma consolida acuerdos de trabajo e instrucciones operativas claras (`sprint tras sprint`, `no pares`, `no pidas permisos`) sin que el usuario tenga que decir `recuerda`.
85. Hecho inicial: acuerdos de trabajo y expectativas del usuario no se invalidan entre si como hechos singleton; pueden coexistir como contexto activo si son distintos.
86. Hecho inicial: recuperacion semantica y respuestas de memoria incluyen `working_agreement` y `user_expectation`, para que NIÑO pueda usar esos acuerdos al responder sobre continuidad/contexto.
87. Hecho inicial: `/app` permite filtrar memoria fria por estado (`Todas`/`Activas`/`Inactivas`) y por clave, reutilizando los filtros del endpoint y mostrando visibles/totales.
88. Hecho inicial: `/app` incorpora login local de usuario y usa rutas privadas `/users/{user_id}/agents/{agent_id}` para que varias personas puedan hablar con el mismo agente sin mezclar memoria privada.
89. Hecho inicial: el servidor traduce `usuario + agente` a un `agent_id` interno aislado (`user::<usuario>::agent::<agente>`) y lista solo agentes del usuario conectado.
90. Hecho inicial: la API queda probada contra fuga cruzada: dos usuarios con agente `nino` consolidan y recuperan memoria fria separada, sin ver hechos privados del otro.
91. Hecho inicial: cada tick acepta contexto temporal (`now`/`timestamp`), guarda episodios con esa fecha y expone `nino_context.current_time` para que NIÑO sepa cuándo ocurre la conversación.
92. Hecho inicial: la memoria caliente interpreta consultas temporales como `semana pasada`, `ayer` y `hoy`, recuperando episodios por ventana temporal aunque haya poco solape semántico.
93. Hecho inicial: NIÑO extrae eventos temporales simples (`mañana tengo cita`, `luego tengo reunión`, `hoy tengo examen`) a `relation_state.temporal_events`.
94. Hecho inicial: la proactividad prioriza recordatorios de eventos temporales próximos y los marca como `reminded` para evitar avisos repetidos.
95. Hecho inicial: el parser temporal entiende días de la semana y horas exactas (`el jueves a las 17:30 tengo reunión`) conservando la zona horaria del turno.
96. Hecho inicial: los eventos temporales guardan `lead_time_hours` y la proactividad avisa dentro de la ventana previa sin repetir tras marcar `reminded`.
97. Hecho inicial: NIÑO mantiene un modelo global anónimo con conteos agregados de intents, tags y conceptos, separado de la memoria privada por usuario.
98. Hecho inicial: el modelo global se persiste en SQLite y no guarda texto crudo, nombres, emails ni números sensibles.
99. Hecho inicial: `GET /operations/global-model` expone solo el agregado anónimo para inspección y auditoría.
100. Hecho inicial: la proactividad puede usar patrones del modelo global anónimo como última opción, sin incorporar texto privado ni identidad de otros usuarios.
101. Hecho inicial: `GET /operations/global-suggestions` expone sugerencias agregadas repetidas, con `privacy=anonymous_aggregate`.
102. Hecho inicial: `/app` añade controles `Global anónimo` y `Sugerencias` para inspeccionar aprendizaje agregado sin salir de la UI.
103. Hecho inicial: los eventos temporales soportan recurrencias `daily`/`weekly` desde frases como `todos los días`, `cada día`, `cada lunes` o `cada semana`.
104. Hecho inicial: la proactividad reprograma eventos recurrentes tras avisar actualizando `next_due_at`, evitando repetir el mismo aviso.
105. Hecho inicial: `GET/PATCH/DELETE /agents/{agent_id}/temporal-events` permite listar, editar estado/datos básicos y borrar eventos temporales.
106. Hecho inicial: `/app` permite ver eventos temporales y pausar/reactivar/eliminar recordatorios desde la consola operativa.
107. Requisito de producto final: el sitio para usuario debe ser extremadamente minimalista; primera pantalla de login y, tras entrar, solo experiencia de chat o voz.
108. Requisito de producto final: mantener `/app` como consola operativa/desarrollo, separada de la UI final de usuario para no exponer controles internos.
109. Decisión técnica pendiente: cambiar el proveedor LLM principal a DeepSeek por coste, manteniendo el diseño multi-proveedor para no acoplar NIÑO a un único modelo.
110. Validación pendiente: hacer una prueba en Vercel para confirmar que la UI final minimalista y la integración con el backend/API funcionan correctamente en despliegue externo.
111. Hecho inicial: `GET /user` y `GET /chat` sirven la UI final minimalista de usuario, separada de `/app`.
112. Hecho inicial: la UI final muestra primero login y, tras entrar, solo chat con NIÑO y control de voz si el navegador soporta Web Speech.
113. Hecho inicial: la UI final usa rutas privadas `/users/{user_id}/agents/nino/...`, conservando aislamiento de memoria por usuario.
114. Hecho inicial: el runtime LLM soporta proveedor `deepseek` mediante API compatible con chat completions (`DEEPSEEK_API_KEY`/`NINO_DEEPSEEK_API_KEY`).
115. Hecho inicial: las trazas, `llm_status`, `nino_context` y fuentes de respuesta reflejan el proveedor real (`llm_deepseek`, `llm_claude`) en lugar de asumir siempre Claude.
116. Hecho inicial: `POST /operations/deepseek/configure` guarda DeepSeek en `.env.local` con permisos 600, no devuelve la clave y cambia el runtime activo sin reinicio inmediato.
117. Hecho inicial: `/app` permite guardar DeepSeek desde la consola LLM manteniendo Claude disponible como configuración alternativa.
118. Hecho inicial: `api/index.py` expone la app WSGI de NIÑO para Vercel usando `NINO_DB_PATH` o `/tmp/nino-vercel.db`.
119. Hecho inicial: `vercel.json` reescribe todas las rutas a `/api/index.py`, permitiendo probar `/user`, `/chat` y API en despliegue serverless.
120. Hecho inicial: `README.md` documenta variables DeepSeek y comandos base para validar NIÑO en Vercel.
121. Validación pendiente: ejecutar una validación externa real en Vercel cuando haya proyecto Vercel conectado y credenciales disponibles.
122. Hecho inicial: la UI final `/user` puede leer en voz alta respuestas de NIÑO cuando la conversación se inicia desde el control de voz.
123. Hecho inicial: la UI final `/user` consulta discretamente el inbox proactivo, muestra avisos pendientes como mensajes de NIÑO y los marca entregados sin añadir paneles internos.
124. Hecho inicial: `/session/login` devuelve `session_token` y las UIs lo guardan/envián como `X-Nino-Session`.
125. Hecho inicial: si `NINO_REQUIRE_SESSION=true`, las rutas `/users/{user_id}/...` rechazan peticiones sin sesión o con sesión de otro usuario.
126. Hecho inicial: `GET /session/status` y `POST /session/logout` permiten comprobar y cerrar sesión activa; `/user` llama logout antes de limpiar almacenamiento local.
127. Hecho inicial: `/user` vuelve a pintar historial usando `turns` del endpoint de conversación, corrigiendo la carga de conversaciones previas.
128. Hecho inicial: la recuperación temporal entiende `antes de ayer`/`anteayer` y prioriza episodios de ese día.
129. Hecho inicial: la recuperación temporal entiende `el mes pasado` como ventana aproximada de 30 a 60 días.
130. Hecho inicial: la recuperación temporal entiende `hace N días` y `hace N semanas`, incluyendo números en texto como `dos`.
131. Hecho inicial: el prompt LLM activa modo continuidad ante preguntas temporales (`hace`, `ayer`, `mes pasado`, `semana pasada`) aunque el usuario no diga `recuerda`.
132. Hecho inicial: `RetrieveResponse` marca `temporal_query`, `temporal_window` y `temporal_miss` para distinguir ausencia de recuerdos en la fecha pedida.
133. Hecho inicial: `nino_context` y `/memory/search` exponen `temporal_miss`/`temporal_visible_miss`, de forma que UI y clientes pueden mostrar que no hubo memoria temporal.
134. Hecho inicial: el prompt LLM recibe una instrucción explícita para decir que no encontró recuerdos de esa ventana temporal y no inventar eventos.
135. Hecho inicial: la política local usa `temporal_miss` para responder “no encuentro recuerdos guardados de esa fecha” cuando no hay LLM o el proveedor falla.
136. Hecho inicial: `/user` muestra `No encuentro recuerdos guardados de esa fecha.` como mensaje normal si una respuesta no trae texto pero `nino_context.temporal_miss` está activo.
137. Estado final local: producto listo en `http://127.0.0.1:8000/user` para usuario y `http://127.0.0.1:8000/app` para operación interna; Vercel queda como validación externa pendiente.
138. Hecho inicial: `/user` activa proactividad para la sesión de usuario, evalúa continuidad al entrar y muestra avisos pendientes como conversación inicial.
139. Hecho inicial: si no hay historial ni aviso pendiente, `/user` inicia con `Estoy aquí. ¿Qué tal vas hoy?` para que amigo abra la conversación.
140. Hecho inicial: `/user` envía la fecha/hora local del navegador en cada turno y al evaluar proactividad, conservando zona horaria para expresiones como `hoy a las 11`.
141. Hecho inicial: el parser de eventos temporales reconoce citas implícitas de salud como `dentista`, `médico`, `doctor` y `consulta` aunque el usuario no escriba la palabra `cita`.
142. Hecho inicial: el prompt LLM recibe fecha/hora actual y eventos temporales activos, con instrucción de no preguntar si una cita ya pasó cuando puede inferirlo.
143. Hecho inicial: los avisos proactivos de eventos temporales se entregan como toque cercano (`Oye, acuérdate...`) en vez de volver a pedir confirmación.
144. Hecho inicial: frases declarativas con `hoy`/`ayer` ya no se tratan como consultas de memoria temporal si el usuario no está preguntando qué ocurrió.
145. Hecho inicial: las citas con hora se guardan como evento y amigo pregunta si el usuario quiere un toque media hora antes, sin activar el aviso automáticamente.
146. Hecho inicial: una respuesta afirmativa (`sí`, `vale`, `recuérdamelo`, `avísame`) confirma la alarma a 30 minutos; una negativa la deja recordada sin alarma.
147. Hecho inicial: la proactividad no envía avisos de eventos con alarma pendiente de confirmar y usa `lead_time_hours=0.5` para alarmas confirmadas.
148. Hecho inicial: si ya hay una alarma temporal confirmada y aún no toca, NIÑO no envía un mensaje proactivo genérico que pueda parecer un aviso anticipado.
149. Hecho inicial: las alarmas temporales confirmadas tienen prioridad sobre límites de frecuencia proactiva para no bloquear recordatorios reales.
150. Decisión de producto: la identidad pública pasa de `NIÑO` a `amigo`; internamente se conservan rutas/scripts `nino` para compatibilidad.
151. Hecho inicial: `/user` muestra `amigo` como nombre visible y el prompt LLM se orienta a un compañero cercano, poco invasivo, que recuerda y pregunta con tacto.
152. Hecho inicial: `AMIGO_ETHICS.md` define la ética de amigo: honestidad, confianza, privacidad, no manipulación, memoria responsable, límites claros y cuidado sin presión.
153. Hecho inicial: el prompt LLM carga explícitamente la ética de amigo en cada respuesta para que el tono y los límites no dependan solo de memoria conversacional.
154. Hecho inicial: `/user` evalúa proactividad en cada polling de inbox para que las alarmas vencidas se creen aunque el scheduler de fondo no haya corrido todavía.
155. Hecho inicial: amigo entiende recordatorios relativos como `recuérdame en 5 minutos que beba agua`, los confirma directamente y avisa al llegar la hora.
156. Hecho inicial: `/user` incorpora onboarding conversacional inicial con privacidad explícita y preguntas una a una para conocer al usuario sin formulario pesado.
157. Hecho inicial: las respuestas de onboarding se guardan en `relation_state.onboarding.answers` y se inyectan en el prompt como `Perfil inicial del usuario`.
158. Hecho inicial: amigo responde a `mi perfil` con el perfil inicial guardado y permite corregir campos por chat, por ejemplo `corrige mi lugar a Barcelona`.
159. Hecho inicial: `/user` sincroniza el onboarding con el estado persistente del backend para no repetir preguntas ya respondidas al cambiar de navegador o sesión.
160. Hecho inicial: amigo permite borrar datos del perfil por chat con frases como `olvida mi lugar` o `borra mi perfil`, limpiando el estado persistente asociado.

Esta tarea desbloquea el uso vivo sin depender de comandos sueltos.

## Sprint 18 - Aprendizaje relacional y dashboard operativo

Estado: hecho.

Objetivo: que amigo aprenda de la relacion, no solo del contenido. Cuando el usuario
senala que una respuesta ayudo, fallo, fue una correccion o cruzo un limite, amigo lo
guarda como senal agregada de relacion y ajusta su estilo: mas cautela y brevedad ante
fallos/limites, algo mas de iniciativa ante aciertos, siempre sin volverse pesado.

Hechos:

- Se detectan senales explicitas de acierto (`gracias`, `me ayuda`, `acertaste`),
  fallo (`te equivocas`, `no era eso`, `metiste la pata`) y limite (`no insistas`,
  `no me recuerdes`, `pesado`) en todos los clientes, incluido Telegram, porque viven
  en el runtime comun.
- `relation_state.relationship_learning` guarda solo conteos, ultima senal y estilo
  agregado (`brevity`, `caution`, `initiative`), sin texto crudo de conversaciones.
- El prompt LLM recibe ese aprendizaje agregado para responder mas breve, humilde y
  menos invasivo cuando hay fallos o limites recientes.
- `GET /agents/{agent_id}/relationship-dashboard` y la ruta privada equivalente
  `/users/{user_id}/agents/{agent_id}/relationship-dashboard` exponen madurez,
  aprendizaje relacional, memoria, proactividad y calidad conversacional sin secretos
  ni transcript completo.
- `/app` incorpora el boton `Aprendizaje` para inspeccionar el dashboard operativo.

Tests:

- El dashboard cuenta aciertos, fallos y limites.
- Tras fallo/limite, suben cautela y brevedad y baja iniciativa.
- El dashboard no incluye texto crudo de la conversacion.
- La ruta queda publicada en root/openapi y accesible desde la UI interna.

## Sprint 19 - Privacidad Telegram grupo y dashboard directo

Estado: hecho.

Objetivo: corregir el riesgo de privacidad en grupos y hacer visible el aprendizaje
sin depender de navegar la consola interna.

Hechos:

- En Telegram, un grupo nunca usa memoria privada de un usuario aunque el remitente
  tenga chat privado vinculado. Todo mensaje dirigido al bot dentro de un grupo usa
  memoria aislada de grupo `telegram-group-<chat_id>`.
- Si alguien necesita hablar con su memoria privada, debe abrir conversación privada
  con el bot. En grupo, toda respuesta que el bot envía es visible para el grupo como
  cualquier mensaje de Telegram.
- Se añade `/dashboard` como pantalla directa local para ver aprendizaje relacional,
  madurez, señales, memoria y proactividad sin buscar el botón dentro de `/app`.
- `/dashboard` queda deshabilitado en `NINO_ENV=prod`, igual que `/app`, para no
  exponer consola operativa en internet.

Tests:

- Un remitente vinculado que habla en grupo no enruta a su memoria privada.
- `/dashboard` sirve HTML local y queda documentado.
- En producción `/dashboard` devuelve 404.

## Sprint 20 - Continuidad de hilo conversacional

Estado: hecho.

Objetivo: evitar que amigo trate frases consecutivas como mensajes aislados. Si el
usuario desarrolla una idea en dos o mas frases, amigo debe mantener el hilo activo y
usar ese contexto antes de responder.

Hechos:

- `relation_state.active_conversation_thread` guarda el resumen privado del hilo
  actual, terminos principales, ultimos mensajes del usuario, turnos enlazados y fecha
  de actualizacion.
- La recuperacion de memoria amplia `query_intent` con el hilo activo cuando el mensaje
  parece continuacion corta o pronominal.
- La politica local tiene una ruta `active_thread_continuity` para conectar mensajes
  cortos con la idea previa en vez de responder como si no hubiera contexto.
- El prompt LLM recibe `Hilo activo de conversacion` e instruccion explicita de no
  tratar mensajes cortos o continuaciones como aislados.
- Si el usuario corrige que amigo se pierde, no conecta o no relaciona contexto, se
  registra `continuity_miss` como senal de aprendizaje relacional y sube la cautela.
- El dashboard muestra si hay hilo activo, turnos enlazados y terminos del hilo sin
  exponer el texto crudo.

Tests:

- Dos frases consecutivas se unen en el mismo hilo activo.
- Una continuacion corta usa `active_thread_continuity`.
- Una correccion de continuidad incrementa `continuity_miss`.
- Las rutas explicitas de tema/preferencia mantienen prioridad sobre el hilo activo.

## Sprint 21 - Dashboard completo en `/dashboard`

Estado: hecho.

Objetivo: que `/dashboard` sea el lugar unico y directo para ver todos los datos
operativos del agente, sin tener que buscar botones dentro de `/app`.

Hechos:

- `/dashboard` carga desde `/dashboard-data` y muestra resumen, aprendizaje,
  hilo activo, perfil, self-model, world-model, conversacion, memoria, eventos,
  proactividad, LLM y calidad.
- `GET /dashboard-data?user_id=mindora&agent_id=nino` devuelve un paquete completo
  de datos locales del agente seleccionado.
- En produccion, `/dashboard-data` queda deshabilitado igual que `/dashboard` y `/app`.

Tests:

- `/dashboard` contiene la pantalla de datos completos.
- `/dashboard-data` devuelve el paquete operativo completo.
- `/dashboard-data` no se expone en `NINO_ENV=prod`.
