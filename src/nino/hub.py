"""Mindora Labs central superadmin dashboard.

A small, dependency-free WSGI app served at mindoralabs.net/dashboard. It lets
a superadmin log in and inspect every initiative (amigo, aliado, taskos, ...)
from one place: usage and learnings. Each initiative is read locally and
read-only from its own data on the VPS, so the hub never writes to a product.

Phase 1 wires up the amigo panel (users, usage, learnings); aliado and taskos
appear as "por conectar" until their data is available on the VPS.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from http.cookies import SimpleCookie
import json
import os
from pathlib import Path
import secrets
import sqlite3
import sys
import time
from typing import Any, Callable
from urllib.parse import urlparse
from wsgiref.simple_server import make_server

from .auth import hash_password, verify_password

SESSION_TTL_SECONDS = 7 * 24 * 3600

SECURITY_HEADERS = [
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "no-referrer"),
]


@dataclass
class Initiative:
    initiative_id: str
    name: str
    description: str
    db_path: str = ""


@dataclass
class HubConfig:
    username: str = "admin"
    password_hash: str = ""
    session_pepper: str = "hub-dev-pepper"
    require_https: bool = False
    amigo_db_path: str = ""


def default_initiatives(config: HubConfig) -> list[Initiative]:
    return [
        Initiative("amigo", "amigo", "Compañero con memoria en Telegram", config.amigo_db_path),
        Initiative("aliado", "aliado", "Iniciativa de Mindora Labs (por conectar)", ""),
        Initiative("taskos", "taskos", "Iniciativa de Mindora Labs (por conectar)", ""),
    ]


def _connect_ro(path: str) -> sqlite3.Connection:
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    except sqlite3.OperationalError:
        conn = sqlite3.connect(path, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def amigo_snapshot(db_path: str) -> dict[str, Any]:
    """Read amigo's data read-only and summarize users, usage and learnings."""
    if not db_path or not Path(db_path).exists():
        return {"connected": False, "reason": "sin datos todavía"}
    conn = _connect_ro(db_path)

    def q(sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        try:
            return conn.execute(sql, params).fetchall()
        except sqlite3.Error:
            return []

    try:
        links = [dict(r) for r in q(
            "SELECT user_id, chat_id, created_at FROM telegram_link ORDER BY created_at DESC"
        )]
        pending = q("SELECT COUNT(*) AS c FROM telegram_link_code WHERE used_at IS NULL")
        pending_count = int(pending[0]["c"]) if pending else 0

        groups = [dict(r) for r in q(
            "SELECT chat_id, title, last_seen_at FROM telegram_group ORDER BY last_seen_at DESC"
        )]
        replies = q(
            "SELECT COUNT(*) AS c FROM telegram_action_log WHERE action_type = 'group_reply' AND sent_ok = 1"
        )
        replies_count = int(replies[0]["c"]) if replies else 0
        decisions = {
            str(r["decision"]): int(r["c"])
            for r in q("SELECT decision, COUNT(*) AS c FROM telegram_social_decision GROUP BY decision")
        }
        reasons = [dict(r) for r in q(
            "SELECT reason, COUNT(*) AS c FROM telegram_social_decision GROUP BY reason ORDER BY c DESC LIMIT 12"
        )]

        groups_learning: list[dict[str, Any]] = []
        for row in q("SELECT agent_id, relation_state_json FROM agent_states"):
            try:
                relation = json.loads(row["relation_state_json"] or "{}")
            except (TypeError, ValueError):
                continue
            maturity = relation.get("group_maturity") or {}
            if not maturity:
                continue
            social = maturity.get("social_learning") or {}
            groups_learning.append({
                "agent_id": row["agent_id"],
                "participation_guidance": maturity.get("participation_guidance"),
                "observed_messages": maturity.get("observed_messages"),
                "questions_seen": maturity.get("questions_seen"),
                "bot_replies": maturity.get("bot_replies"),
                "signal_counts": social.get("signal_counts") or {},
                "topic_counts": maturity.get("topic_counts") or {},
                "last_observed_at": maturity.get("last_observed_at"),
            })

        journal_counts = {
            str(r["status"]): int(r["c"])
            for r in q("SELECT status, COUNT(*) AS c FROM learning_journal_entries GROUP BY status")
        }
        journal = [dict(r) for r in q(
            "SELECT title, lesson, status, tags_json, updated_at "
            "FROM learning_journal_entries ORDER BY updated_at DESC LIMIT 50"
        )]
        works = [dict(r) for r in q(
            "SELECT title, medium, source, updated_at FROM telegram_known_work ORDER BY updated_at DESC"
        )]
    finally:
        conn.close()

    return {
        "connected": True,
        "users": {
            "linked_count": len(links),
            "pending_codes": pending_count,
            "links": links,
        },
        "usage": {
            "group_replies": replies_count,
            "decisions": decisions,
            "reasons": reasons,
            "groups": groups,
        },
        "learnings": {
            "groups": groups_learning,
            "journal_counts": journal_counts,
            "journal": journal,
            "known_works": works,
        },
    }


def build_overview(config: HubConfig) -> dict[str, Any]:
    initiatives: list[dict[str, Any]] = []
    for initiative in default_initiatives(config):
        entry: dict[str, Any] = {
            "id": initiative.initiative_id,
            "name": initiative.name,
            "description": initiative.description,
        }
        if initiative.initiative_id == "amigo":
            snapshot = amigo_snapshot(initiative.db_path)
            entry["status"] = "connected" if snapshot.get("connected") else "empty"
            entry["snapshot"] = snapshot
        else:
            entry["status"] = "not_connected"
            entry["snapshot"] = {"connected": False, "reason": "por conectar"}
        initiatives.append(entry)
    return {"product": "Mindora Labs", "initiatives": initiatives}


class HubApp:
    def __init__(self, config: HubConfig) -> None:
        self.config = config
        self._sessions: dict[str, float] = {}

    # --- HTTP plumbing ---
    def __call__(self, environ: dict[str, Any], start_response: Callable[..., Any]) -> list[bytes]:
        method = environ.get("REQUEST_METHOD", "GET").upper()
        path = urlparse(environ.get("PATH_INFO", "")).path
        if self.config.require_https and environ.get("HTTP_X_FORWARDED_PROTO", "http").lower() != "https":
            return self._json(start_response, "403 Forbidden", {"error": "https_required"})
        if method == "GET" and path in ("/", "/dashboard"):
            return self._serve_html(start_response)
        if method == "GET" and path == "/healthz":
            return self._json(start_response, "200 OK", {"ok": True})
        if method == "POST" and path == "/dashboard/login":
            return self._login(environ, start_response)
        if method == "POST" and path == "/dashboard/logout":
            return self._logout(environ, start_response)
        if method == "GET" and path == "/dashboard/api/overview":
            if not self._authenticated(environ):
                return self._json(start_response, "401 Unauthorized", {"error": "login_required"})
            return self._json(start_response, "200 OK", build_overview(self.config))
        return self._json(start_response, "404 Not Found", {"error": "not_found"})

    def _serve_html(self, start_response: Callable[..., Any]) -> list[bytes]:
        body = HUB_HTML.encode("utf-8")
        start_response(
            "200 OK",
            [("Content-Type", "text/html; charset=utf-8"), ("Content-Length", str(len(body))), *SECURITY_HEADERS],
        )
        return [body]

    def _json(self, start_response: Callable[..., Any], status: str, payload: dict[str, Any], extra_headers: list[tuple[str, str]] | None = None) -> list[bytes]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = [
            ("Content-Type", "application/json; charset=utf-8"),
            ("Content-Length", str(len(body))),
            *SECURITY_HEADERS,
        ]
        if extra_headers:
            headers.extend(extra_headers)
        start_response(status, headers)
        return [body]

    def _read_json(self, environ: dict[str, Any]) -> dict[str, Any]:
        try:
            size = int(environ.get("CONTENT_LENGTH") or 0)
        except (TypeError, ValueError):
            size = 0
        if size <= 0:
            return {}
        raw = environ["wsgi.input"].read(size)
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except (ValueError, AttributeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _session_token(self, environ: dict[str, Any]) -> str:
        cookie = SimpleCookie()
        try:
            cookie.load(environ.get("HTTP_COOKIE", ""))
        except Exception:
            return ""
        morsel = cookie.get("hub_session")
        return morsel.value if morsel else ""

    def _authenticated(self, environ: dict[str, Any]) -> bool:
        token = self._session_token(environ)
        expiry = self._sessions.get(token)
        if not expiry:
            return False
        if expiry < time.time():
            self._sessions.pop(token, None)
            return False
        return True

    def _login(self, environ: dict[str, Any], start_response: Callable[..., Any]) -> list[bytes]:
        if not self.config.password_hash:
            return self._json(start_response, "503 Service Unavailable", {"error": "password_not_configured"})
        payload = self._read_json(environ)
        username = str(payload.get("user", "")).strip()
        password = str(payload.get("password", ""))
        if username != self.config.username or not verify_password(password, self.config.password_hash):
            return self._json(start_response, "401 Unauthorized", {"error": "invalid_credentials"})
        token = secrets.token_urlsafe(32)
        self._sessions[token] = time.time() + SESSION_TTL_SECONDS
        cookie = (
            f"hub_session={token}; HttpOnly; Path=/; SameSite=Strict; Max-Age={SESSION_TTL_SECONDS}"
        )
        if self.config.require_https:
            cookie += "; Secure"
        return self._json(start_response, "200 OK", {"ok": True}, [("Set-Cookie", cookie)])

    def _logout(self, environ: dict[str, Any], start_response: Callable[..., Any]) -> list[bytes]:
        token = self._session_token(environ)
        self._sessions.pop(token, None)
        cookie = "hub_session=; HttpOnly; Path=/; SameSite=Strict; Max-Age=0"
        return self._json(start_response, "200 OK", {"ok": True}, [("Set-Cookie", cookie)])


def create_hub_app(config: HubConfig) -> HubApp:
    return HubApp(config)


HUB_HTML = """<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Mindora Labs · Panel</title>
<style>
  :root { color-scheme: light; }
  * { box-sizing: border-box; }
  body { margin: 0; font-family: -apple-system, system-ui, Segoe UI, Roboto, sans-serif; background: #f4f6f8; color: #1d2730; }
  header { background: #10212e; color: #fff; padding: 16px 22px; display: flex; align-items: center; justify-content: space-between; }
  header h1 { font-size: 18px; margin: 0; }
  header button { background: #2c4356; color: #fff; border: 0; border-radius: 8px; padding: 8px 12px; cursor: pointer; }
  main { max-width: 1000px; margin: 0 auto; padding: 22px; }
  .login { max-width: 360px; margin: 12vh auto; background: #fff; padding: 24px; border-radius: 14px; box-shadow: 0 8px 30px rgba(0,0,0,.08); }
  .login h2 { margin-top: 0; }
  .login input { width: 100%; padding: 11px; margin: 6px 0; border: 1px solid #cdd6dd; border-radius: 9px; font-size: 15px; }
  .login button { width: 100%; padding: 12px; margin-top: 8px; background: #1d6fe0; color: #fff; border: 0; border-radius: 9px; font-size: 15px; cursor: pointer; }
  .error { color: #c0392b; font-size: 14px; min-height: 18px; }
  .cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 14px; }
  .card { background: #fff; border-radius: 14px; padding: 18px; box-shadow: 0 4px 16px rgba(0,0,0,.06); cursor: pointer; border: 2px solid transparent; }
  .card.active { border-color: #1d6fe0; }
  .card.off { opacity: .6; cursor: not-allowed; }
  .card h3 { margin: 0 0 6px; }
  .pill { display: inline-block; font-size: 12px; padding: 3px 9px; border-radius: 20px; }
  .pill.ok { background: #e3f4e8; color: #1f7a3d; }
  .pill.empty { background: #fff3df; color: #9a6b12; }
  .pill.off { background: #eceff1; color: #6b7884; }
  .tabs { display: flex; gap: 8px; margin: 20px 0 12px; flex-wrap: wrap; }
  .tabs button { background: #e7edf2; border: 0; border-radius: 9px; padding: 8px 14px; cursor: pointer; }
  .tabs button.active { background: #1d6fe0; color: #fff; }
  .panel { background: #fff; border-radius: 14px; padding: 18px; box-shadow: 0 4px 16px rgba(0,0,0,.06); margin-bottom: 14px; }
  .metric-row { display: flex; gap: 22px; flex-wrap: wrap; margin-bottom: 8px; }
  .metric { background: #f1f5f8; border-radius: 10px; padding: 12px 16px; }
  .metric strong { display: block; font-size: 22px; }
  table { width: 100%; border-collapse: collapse; font-size: 14px; }
  th, td { text-align: left; padding: 7px 8px; border-bottom: 1px solid #eef2f5; vertical-align: top; }
  .muted { color: #6b7884; }
  .hidden { display: none; }
  code { background: #f1f5f8; padding: 1px 5px; border-radius: 5px; }
</style>
</head>
<body>
<div id="loginView">
  <div class="login">
    <h2>Mindora Labs</h2>
    <p class="muted">Panel de superadmin</p>
    <input id="user" placeholder="Usuario" autocomplete="username" value="admin">
    <input id="password" type="password" placeholder="Contraseña" autocomplete="current-password">
    <button id="loginBtn">Entrar</button>
    <p id="loginError" class="error"></p>
  </div>
</div>

<div id="appView" class="hidden">
  <header>
    <h1>Mindora Labs · Panel</h1>
    <button id="logoutBtn">Salir</button>
  </header>
  <main>
    <div id="cards" class="cards"></div>
    <div id="detail"></div>
  </main>
</div>

<script>
const $ = (id) => document.getElementById(id);
let DATA = null;
let selected = null;
let tab = "users";

async function api(path, opts) {
  const res = await fetch(path, Object.assign({ headers: { "Content-Type": "application/json" } }, opts));
  return res;
}

async function load() {
  const res = await api("/dashboard/api/overview");
  if (res.status === 401) { showLogin(); return; }
  DATA = await res.json();
  showApp();
}

function showLogin() {
  $("loginView").classList.remove("hidden");
  $("appView").classList.add("hidden");
}

function showApp() {
  $("loginView").classList.add("hidden");
  $("appView").classList.remove("hidden");
  renderCards();
  if (!selected) selected = (DATA.initiatives.find(i => i.status === "connected") || DATA.initiatives[0]).id;
  renderDetail();
}

function renderCards() {
  const wrap = $("cards");
  wrap.innerHTML = "";
  for (const it of DATA.initiatives) {
    const card = document.createElement("div");
    const off = it.status === "not_connected";
    card.className = "card" + (it.id === selected ? " active" : "") + (off ? " off" : "");
    const pill = it.status === "connected" ? '<span class="pill ok">activo</span>'
      : it.status === "empty" ? '<span class="pill empty">sin datos</span>'
      : '<span class="pill off">por conectar</span>';
    card.innerHTML = `<h3>${it.name}</h3><div class="muted" style="font-size:13px;margin-bottom:8px">${it.description}</div>${pill}`;
    if (!off) card.onclick = () => { selected = it.id; renderCards(); renderDetail(); };
    wrap.appendChild(card);
  }
}

function esc(v) { return String(v == null ? "" : v).replace(/[&<>]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c])); }

function renderDetail() {
  const it = DATA.initiatives.find(i => i.id === selected);
  const d = $("detail");
  if (!it || it.status === "not_connected") { d.innerHTML = '<div class="panel muted">Esta iniciativa todavía no está conectada al panel.</div>'; return; }
  const s = it.snapshot || {};
  if (!s.connected) { d.innerHTML = '<div class="panel muted">Sin datos todavía. En cuanto haya actividad, aparecerá aquí.</div>'; return; }
  const tabs = [["users", "Usuarios"], ["usage", "Uso"], ["learnings", "Aprendizajes"]];
  let html = '<div class="tabs">' + tabs.map(([k, l]) => `<button class="${k === tab ? "active" : ""}" onclick="setTab('${k}')">${l}</button>`).join("") + "</div>";
  if (tab === "users") html += renderUsers(s.users);
  if (tab === "usage") html += renderUsage(s.usage);
  if (tab === "learnings") html += renderLearnings(s.learnings);
  d.innerHTML = html;
}

function setTab(k) { tab = k; renderDetail(); }

function renderUsers(u) {
  u = u || {};
  let h = `<div class="panel"><div class="metric-row">
    <div class="metric"><strong>${u.linked_count || 0}</strong>usuarios vinculados</div>
    <div class="metric"><strong>${u.pending_codes || 0}</strong>códigos pendientes</div></div></div>`;
  const links = u.links || [];
  h += '<div class="panel"><h3>Usuarios vinculados</h3>';
  if (!links.length) h += '<p class="muted">Nadie vinculado todavía.</p>';
  else h += '<table><tr><th>Usuario</th><th>Chat</th><th>Desde</th></tr>' +
    links.map(l => `<tr><td>${esc(l.user_id)}</td><td>${esc(l.chat_id)}</td><td>${esc(l.created_at)}</td></tr>`).join("") + "</table>";
  return h + "</div>";
}

function renderUsage(u) {
  u = u || {};
  const dec = u.decisions || {};
  let h = `<div class="panel"><div class="metric-row">
    <div class="metric"><strong>${u.group_replies || 0}</strong>respuestas en grupo</div>
    <div class="metric"><strong>${dec.reply || 0}</strong>decisiones: responder</div>
    <div class="metric"><strong>${dec.observe || 0}</strong>decisiones: observar</div></div></div>`;
  const groups = u.groups || [];
  h += '<div class="panel"><h3>Grupos</h3>';
  if (!groups.length) h += '<p class="muted">Sin grupos todavía.</p>';
  else h += '<table><tr><th>Grupo</th><th>Chat</th><th>Última actividad</th></tr>' +
    groups.map(g => `<tr><td>${esc(g.title)}</td><td>${esc(g.chat_id)}</td><td>${esc(g.last_seen_at)}</td></tr>`).join("") + "</table>";
  h += "</div>";
  const reasons = u.reasons || [];
  if (reasons.length) {
    h += '<div class="panel"><h3>Motivos de decisión</h3><table><tr><th>Motivo</th><th>Veces</th></tr>' +
      reasons.map(r => `<tr><td><code>${esc(r.reason)}</code></td><td>${esc(r.c)}</td></tr>`).join("") + "</table></div>";
  }
  return h;
}

function renderLearnings(l) {
  l = l || {};
  let h = "";
  const groups = l.groups || [];
  h += '<div class="panel"><h3>Aprendizaje social por grupo</h3>';
  if (!groups.length) h += '<p class="muted">Aún no ha aprendido nada en grupos. Déjalo observar un tiempo.</p>';
  else h += groups.map(g => `<div style="border-top:1px solid #eef2f5;padding-top:10px;margin-top:10px">
      <div><strong>${esc(g.agent_id)}</strong></div>
      <div class="muted" style="font-size:13px">Guía: ${esc(g.participation_guidance || "—")}</div>
      <div style="font-size:13px">Observados: ${esc(g.observed_messages || 0)} · Preguntas: ${esc(g.questions_seen || 0)} · Respuestas: ${esc(g.bot_replies || 0)}</div>
      <div style="font-size:13px">Señales: ${esc(JSON.stringify(g.signal_counts || {}))}</div>
      <div style="font-size:13px">Temas: ${esc(JSON.stringify(g.topic_counts || {}))}</div>
    </div>`).join("");
  h += "</div>";

  const jc = l.journal_counts || {};
  const journal = l.journal || [];
  h += `<div class="panel"><h3>Bitácora de aprendizajes</h3>
    <div class="metric-row"><div class="metric"><strong>${jc.active || 0}</strong>activos</div>
    <div class="metric"><strong>${jc.draft || 0}</strong>detectados</div></div>`;
  if (journal.length) h += '<table><tr><th>Título</th><th>Estado</th><th>Lección</th></tr>' +
    journal.map(e => `<tr><td>${esc(e.title)}</td><td>${esc(e.status)}</td><td>${esc(e.lesson)}</td></tr>`).join("") + "</table>";
  else h += '<p class="muted">Sin aprendizajes registrados todavía.</p>';
  h += "</div>";

  const works = l.known_works || [];
  h += '<div class="panel"><h3>Obras que ha leído o visto</h3>';
  if (!works.length) h += '<p class="muted">Ninguna obra registrada.</p>';
  else h += '<table><tr><th>Obra</th><th>Tipo</th><th>Origen</th></tr>' +
    works.map(w => `<tr><td>${esc(w.title)}</td><td>${esc(w.medium)}</td><td>${esc(w.source)}</td></tr>`).join("") + "</table>";
  return h + "</div>";
}

async function doLogin() {
  $("loginError").textContent = "";
  const res = await api("/dashboard/login", { method: "POST", body: JSON.stringify({ user: $("user").value, password: $("password").value }) });
  if (res.ok) { $("password").value = ""; load(); }
  else if (res.status === 503) $("loginError").textContent = "Falta configurar la contraseña en el servidor.";
  else $("loginError").textContent = "Usuario o contraseña incorrectos.";
}

async function doLogout() { await api("/dashboard/logout", { method: "POST" }); showLogin(); }

$("loginBtn").onclick = doLogin;
$("password").addEventListener("keydown", e => { if (e.key === "Enter") doLogin(); });
$("logoutBtn").onclick = doLogout;
load();
</script>
</body>
</html>"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Mindora Labs central dashboard.")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind.")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind.")
    parser.add_argument(
        "--amigo-db",
        default=os.environ.get("MINDORA_HUB_AMIGO_DB", "/srv/amigo/data/nino.db"),
        help="Path to amigo's SQLite database (read-only).",
    )
    parser.add_argument(
        "--hash-password",
        action="store_true",
        help="Read a password from stdin, print its hash, and exit.",
    )
    args = parser.parse_args(argv)

    if args.hash_password:
        password = sys.stdin.read().strip()
        if len(password) < 12:
            print("Password must be at least 12 characters.", file=sys.stderr)
            return 2
        print(hash_password(password))
        return 0

    config = HubConfig(
        username=os.environ.get("MINDORA_HUB_USER", "admin").strip() or "admin",
        password_hash=os.environ.get("MINDORA_HUB_PASSWORD_HASH", "").strip(),
        session_pepper=os.environ.get("MINDORA_HUB_SESSION_PEPPER", "hub-dev-pepper"),
        require_https=os.environ.get("MINDORA_HUB_ENV", "").lower() == "prod",
        amigo_db_path=args.amigo_db,
    )
    app = create_hub_app(config)
    print(f"Mindora hub on http://{args.host}:{args.port} (amigo db: {args.amigo_db})")
    with make_server(args.host, args.port, app) as httpd:
        httpd.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
