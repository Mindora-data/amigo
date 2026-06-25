# Traspaso — Estado del VPS de Mindora Labs y plan de Aliado

> Documento de handoff para continuar en una sesión nueva (con los repos
> `mindora-data/amigo` **y** `mindora-data/aliado` añadidos). Resume todo lo
> montado hasta ahora y el plan pendiente para el dashboard/gestión de Aliado.

## 1. Infraestructura actual (VPS AlmaLinux 9)

- Host: `server1.mindoralabs.net` · IP `209.74.64.61` · 2 GB RAM + **4 GB swap** (`/swapfile`).
- Usuario: `mindora` (sudo). Acceso: `ssh mindora@209.74.64.61`.
- Proxy/HTTPS: **Caddy** (certificados Let's Encrypt automáticos). Config canónica
  versionada en el repo amigo: `deploy/Caddyfile` → se despliega con
  `sudo cp /srv/amigo/deploy/Caddyfile /etc/caddy/Caddyfile && sudo systemctl reload caddy`.
- Python 3.11 y Node 20 instalados.

### Servicios systemd y puertos (todos en 127.0.0.1, Caddy delante)
| Servicio | Puerto | Qué es | Ruta |
|---|---|---|---|
| `mindora-hub` | 8000 | Hub/dashboard central (`nino.hub`) | `/srv/amigo` (.venv) |
| (amigo nino-server) | 8001 | Amigo (NIÑO, Telegram) | `/srv/amigo` |
| `taskos-api` | 8002 | Taskos API (FastAPI, mindora-aios) | `/srv/mindora-aios` (.venv) |
| `taskos-web` | 3010 | Taskos web (Next.js) | `/srv/mindora-aios/apps/web` |
| `aliado` | 8003 | Aliado (NIÑO corporativo) | `/srv/aliado` (.venv) |

### Dominios (DNS A → 209.74.64.61) y rutas Caddy
- `mindoralabs.net` → landing estática (`/srv/landing`); `/dashboard*` → hub (8000)
- `amigo.mindoralabs.net` → `/` home estática (`/srv/amigo-landing`); resto → 8001
- `aliado.mindoralabs.net` → landing estática (`/srv/aliado-landing`)
- `app.aliado.mindoralabs.net` → app Aliado (8003)
- `taskos.mindoralabs.net` → landing estática (`/srv/taskos-landing`)
- `app.taskos.mindoralabs.net` → web Taskos (3010)
- `api.taskos.mindoralabs.net` → API Taskos (8002)

Las landings estáticas viven en el repo amigo bajo `web/<producto>/index.html` y se
copian a `/srv/<producto>-landing/`.

## 2. Productos

- **Amigo** = repo `mindora-data/amigo` (motor NIÑO, build Telegram). Es el repo
  "base" donde vive también el **hub** (`src/nino/hub.py`).
- **Aliado** = repo `mindora-data/aliado` (NIÑO corporativo; antes `bebe`). Clonado en
  `/srv/aliado` con deploy key `~/.ssh/id_ed25519_aliado` (alias SSH `github-aliado`,
  solo-lectura). Servicio `aliado` → `.venv/bin/nino-server --host 127.0.0.1 --port 8003
  --db /srv/aliado/data/nino.db --scheduler-interval 60`. Config en `/srv/aliado/.env.local`.
  - **LLM elegido: DeepSeek** (`NINO_LLM_PROVIDER=deepseek`, `NINO_DEEPSEEK_API_KEY` configurada).
  - Prod: `NINO_ENV=prod`, `NINO_REQUIRE_SESSION=true`, `NINO_REQUIRE_HTTPS=true`,
    `NINO_SESSION_PEPPER`, `NINO_PASSWORD_HASH` ya puestos.
- **Taskos** = repo `mindora-data/mindora-aios` (Next.js + FastAPI + worker, SQLite en
  `/srv/mindora-aios/var/taskos.db`, `EXECUTOR_MODE=mock`). Migrado fuera de Google Cloud.

## 3. Hub central (`src/nino/hub.py` en amigo) — estado

- WSGI propio en `:8000`, login superadmin (`MINDORA_HUB_PASSWORD_HASH`, etc.).
- **Solo lectura** por diseño. Lee la SQLite de cada producto y muestra secciones.
- Ya conectados los 3: `amigo` (Telegram), `aliado` (empresas/usuarios/documentos),
  `taskos` (usuarios/workspaces/tareas). Lectores: `amigo_snapshot`, `aliado_snapshot`,
  `taskos_snapshot`; render genérico por `sections` (métricas + tablas).
- Rutas de BD por defecto: `/srv/amigo/data/nino.db`, `/srv/aliado/data/nino.db`,
  `/srv/mindora-aios/var/taskos.db` (args `--aliado-db`/`--taskos-db` o env `MINDORA_HUB_*`).
- Desplegar cambios del hub: `cd /srv/amigo && git pull && sudo systemctl restart mindora-hub`.

## 4. Lo que pide el usuario (objetivo de la fase siguiente)

Gestión de Aliado, idealmente desde `mindoralabs.net/dashboard`:
1. **Superadmin crea admins/empresas**: nombre, apellidos, empresa, rol (admin).
2. Dentro de cada empresa, **ver todos los usuarios**.
3. El **admin da de alta usuarios** (nombre, apellidos, email, empresa).
4. El **admin sube documentación compartida** (la ven todos los usuarios y admins de su empresa).
5. **Admin y superadmin pueden eliminar usuarios.**
6. **Alta profesional con confirmación por email** (estilo Taskos: SIN Google, con verificación).
   - Decisión: usar **Resend** (el usuario tiene cuenta, la de Taskos) y verificar el
     dominio `mindoralabs.net` para enviar correos.

## 5. Auditoría de Aliado (lo que YA existe en su backend)

Endpoints presentes en `src/nino/api.py` (lista `API_ENDPOINTS` ~línea 2470+):
- **Superadmin**: `POST /superadmin/login`, `GET /superadmin/session`, `POST /superadmin/logout`,
  `GET /superadmin/companies`, `POST /superadmin/companies` (crear empresa),
  `GET/PATCH /superadmin/companies/{id}`, salud, leads, module-requests.
- **Empresa/usuarios**: `GET /companies/{id}/users`, `POST /companies/{id}/users/invite`
  (alta: name, email, role, team_id, send_onboarding → `invite_company_user` ~línea 5248),
  `PATCH /companies/{id}/users/{uid}/role`, `DELETE /companies/{id}/users/{uid}`.
- **Documentos compartidos**: `GET/POST /companies/{id}/documents`,
  `DELETE /companies/{id}/documents/{doc}`.
- Onboarding, CRM, módulos, HR, métricas: todo presente.
- Login solo email+contraseña (sin Google). UI superadmin (`/superadmin`) funciona en prod.
- **Consola de admin de empresa `/app`: DESHABILITADA en prod** (igual que amigo;
  gate `_is_prod()`), hay que habilitarla/ajustarla.

Esquema SQLite de Aliado (relevante): `company`, `company_user` (role admin/usuario,
password_hash, active, email único por empresa), `company_document` (con `restricted`
= privado/compartido, `category`, `folder`), `company_document_chunk`, `superadmins`,
CRM (`crm_*`), `user_onboarding_messages`, etc. NOTA: `company_user.name` es un único
campo (no hay "apellidos" separado).

**Lo que NO existe (a construir):**
- **Envío de email**: Aliado no tiene proveedor de correo (`.env.example` solo `AMIGO_EMAIL`
  para Let's Encrypt). El "invite" actual genera contraseña inicial / mensaje in-app
  (`user_onboarding_messages`, entregado por el chat), NO email. Hay que añadir Resend +
  dominio verificado + flujo de invitación con enlace de verificación y "establecer contraseña".
- Superadmin de Aliado: crearlo con `scripts/aliado-create-superadmin` (email superadmin
  por defecto en el login.html: `mindora.data@gmail.com`).

## 6. Plan por fases (acordado)

- **Fase 1 — Gestión desde el hub** (sin email): el hub (superadmin) crea empresas + admins
  y borra usuarios llamando a la **API de Aliado** (todos los endpoints existen). Requiere
  configurar credenciales de superadmin de Aliado para que el hub se autentique.
  - Alternativa más simple: usar/enlazar la consola `/superadmin` propia de Aliado.
- **Fase 2 — Consola de admin de empresa**: habilitar `/app` en prod en Aliado para que
  cada admin gestione sus usuarios y suba documentos compartidos.
- **Fase 3 — Alta profesional con email** (Resend + dominio `mindoralabs.net`): flujo de
  invitación con enlace de verificación + página "establecer contraseña" (sustituye la
  contraseña inicial). Reutiliza el enfoque de Taskos (verificación email, sin Google).

## 7. Cómo desplegar cambios (recordatorio)

- amigo/hub: editar repo `amigo` → push → en VPS `cd /srv/amigo && git pull` →
  `sudo systemctl restart mindora-hub` (o el servicio que toque).
- aliado: editar repo `aliado` → push → en VPS `cd /srv/aliado && git pull` →
  `.venv/bin/pip install -e . -q` (si cambian entrypoints/deps) →
  `sudo systemctl restart aliado`. Nota: la deploy key del VPS es **solo-lectura**;
  los push a `aliado` se hacen desde el Mac del usuario (o añadir write a la deploy key).
- Pegados largos en la terminal se corrompen: preferir ficheros versionados + `git pull`
  + `cp`, no heredocs gigantes.

## 8. Pendiente de verificar por el usuario

- Probar login + chat de Aliado en `https://app.aliado.mindoralabs.net/login` (DeepSeek).
- Apagar Google Cloud cuando confirme que Taskos va bien en el VPS (parar Cloud SQL = el
  coste; desactivar facturación del proyecto `mindora-server` es lo más limpio y reversible).
- Idea futura del usuario: instalar el plugin **superpowers** (obra/superpowers) y crear una
  skill propia para automatizar despliegues en el VPS.
