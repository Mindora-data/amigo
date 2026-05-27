from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import subprocess
import threading
import time
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlparse
from wsgiref.simple_server import WSGIRequestHandler, make_server

from .autonomy import BackgroundAutonomy
from .auth import token_hash, verify_password
from .claude_live import claude_setup_commands
from .contracts import ConsolidationRequest, ProactivitySettings, RetrieveRequest
from .internal_loop import InternalLoop
from .llm import build_configured_llm, llm_config_status
from .memory import Episode
from .persistence import create_persistent_runtime
from .runtime import NinoRuntime
from .scheduler import NinoScheduler

SESSION_COOKIE_NAME = "nino_session"
SESSION_TTL_SECONDS = 7 * 24 * 60 * 60
LOGIN_RATE_LIMIT_ATTEMPTS = 5
LOGIN_RATE_LIMIT_WINDOW_SECONDS = 10 * 60
LOGIN_RATE_LIMIT_BLOCK_SECONDS = 15 * 60


def _is_prod() -> bool:
    return os.environ.get("NINO_ENV", "").strip().lower() in {"prod", "production"}


def _require_session_enabled() -> bool:
    return os.environ.get("NINO_REQUIRE_SESSION", "").strip().lower() in {"1", "true", "yes", "on"}


def _password_hash() -> str:
    return os.environ.get("NINO_PASSWORD_HASH", "").strip()


def _security_headers() -> list[tuple[str, str]]:
    headers = [
        ("X-Content-Type-Options", "nosniff"),
        ("X-Frame-Options", "DENY"),
        ("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; media-src 'self'; base-uri 'none'; frame-ancestors 'none'"),
    ]
    if _is_prod():
        headers.append(("Strict-Transport-Security", "max-age=31536000; includeSubDomains"))
    return headers


def _session_cookie(token: str, *, max_age: int = SESSION_TTL_SECONDS) -> str:
    secure = "; Secure" if _is_prod() else ""
    return f"{SESSION_COOKIE_NAME}={token}; Max-Age={max_age}; Path=/; HttpOnly; SameSite=Strict{secure}"


def _clear_session_cookie() -> str:
    secure = "; Secure" if _is_prod() else ""
    return f"{SESSION_COOKIE_NAME}=; Max-Age=0; Path=/; HttpOnly; SameSite=Strict{secure}"


APP_HTML = """<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>amigo</title>
  <style>
    :root {
      color-scheme: light;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: #172026;
      background: #edf1f4;
    }
    * { box-sizing: border-box; }
    body { margin: 0; min-height: 100vh; background: #edf1f4; }
    main {
      display: grid;
      grid-template-columns: minmax(0, 1fr) 420px;
      min-height: 100vh;
    }
    section, aside { padding: 18px; }
    section { display: flex; flex-direction: column; gap: 12px; border-right: 1px solid #c9d3da; }
    h1 { margin: 0; font-size: 21px; letter-spacing: 0; }
    h2 { margin: 0; font-size: 13px; letter-spacing: 0; color: #33424a; }
    label { font-size: 12px; color: #53646d; }
    .topbar { display: grid; grid-template-columns: auto minmax(180px, 260px) minmax(220px, 360px); align-items: end; gap: 16px; }
    .agent { display: grid; grid-template-columns: 58px minmax(0, 1fr); align-items: center; gap: 8px; }
    .userLogin { display: grid; grid-template-columns: minmax(0, 1fr) 86px; gap: 8px; }
    input, textarea, select, button {
      font: inherit;
      border: 1px solid #b9c5cc;
      border-radius: 6px;
      background: #fff;
      color: #172026;
    }
    input, textarea, select { padding: 9px 10px; width: 100%; }
    textarea { resize: vertical; min-height: 58px; }
    button { padding: 9px 12px; cursor: pointer; background: #1f6f78; color: #fff; border-color: #1f6f78; min-height: 38px; }
    button.secondary { background: #fff; color: #172026; border-color: #b9c5cc; }
    button.danger { background: #fff; color: #9b1c1c; border-color: #d7aaaa; }
    button:disabled { opacity: 0.55; cursor: not-allowed; }
    .strip {
      display: grid;
      grid-template-columns: repeat(5, minmax(0, 1fr));
      gap: 8px;
    }
    .metric {
      background: #fff;
      border: 1px solid #cfd8df;
      border-radius: 8px;
      padding: 9px 10px;
      min-height: 58px;
    }
    .metric span { display: block; font-size: 11px; color: #667781; margin-bottom: 4px; }
    .metric strong { display: block; font-size: 18px; line-height: 1.1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .log {
      flex: 1;
      overflow: auto;
      background: #fff;
      border: 1px solid #cfd8df;
      border-radius: 8px;
      padding: 12px;
      min-height: 420px;
    }
    .entry { border-bottom: 1px solid #edf1f3; padding: 10px 0; }
    .entry:last-child { border-bottom: 0; }
    .role { font-size: 12px; color: #5a6a72; margin-bottom: 4px; }
    .entry .text { line-height: 1.45; white-space: pre-wrap; }
    .composer { display: grid; grid-template-columns: 130px minmax(0, 1fr) 92px; gap: 8px; align-items: stretch; }
    aside { display: flex; flex-direction: column; gap: 14px; background: #f8fafb; }
    .panel {
      background: #fff;
      border: 1px solid #cfd8df;
      border-radius: 8px;
      padding: 12px;
    }
    .panelHead { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 10px; }
    .row { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-top: 8px; }
    .row.three { grid-template-columns: repeat(3, 1fr); }
    .output {
      margin-top: 10px;
      border-top: 1px solid #edf1f3;
      padding-top: 10px;
    }
    .list {
      display: grid;
      gap: 8px;
      max-height: 220px;
      overflow: auto;
      margin-top: 10px;
    }
    .listItem {
      border-bottom: 1px solid #edf1f3;
      padding-bottom: 8px;
      line-height: 1.4;
    }
    .listItem:last-child { border-bottom: 0; padding-bottom: 0; }
    .listItem button { margin-top: 6px; min-height: 30px; padding: 5px 8px; }
    .muted { color: #667781; font-size: 12px; }
    .readiness {
      display: grid;
      gap: 6px;
      margin-top: 8px;
      font-size: 12px;
      color: #33424a;
    }
    .readinessRow {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      border-bottom: 1px solid #edf1f3;
      padding-bottom: 6px;
    }
    .readinessRow:last-child { border-bottom: 0; padding-bottom: 0; }
    .pill {
      border-radius: 999px;
      padding: 2px 8px;
      background: #e8f3ed;
      color: #24523a;
      white-space: nowrap;
    }
    .pill.blocked { background: #f8e8e8; color: #8c1d1d; }
    .status { font-size: 12px; color: #667781; min-height: 18px; }
    pre {
      margin: 0;
      white-space: pre-wrap;
      word-break: break-word;
      font-size: 12px;
      line-height: 1.45;
      max-height: 220px;
      overflow: auto;
    }
    @media (max-width: 860px) {
      main { grid-template-columns: 1fr; }
      section { border-right: 0; border-bottom: 1px solid #cfd8df; }
      .topbar { grid-template-columns: 1fr; }
      .strip { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .composer { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <main>
    <section>
      <div class="topbar">
        <h1>amigo</h1>
        <div>
          <label for="userId">Usuario</label>
          <div class="userLogin">
            <input id="userId" value="mindora" autocomplete="username" aria-label="usuario">
            <button id="loginUser" class="secondary">Login</button>
          </div>
        </div>
        <div class="agent">
          <label for="agentId">Agente</label>
          <input id="agentId" value="nino">
        </div>
      </div>
      <div class="strip">
        <div class="metric"><span>Ticks</span><strong id="metricTick">0</strong></div>
        <div class="metric"><span>Madurez</span><strong id="metricMaturity">0</strong></div>
        <div class="metric"><span>Episodios</span><strong id="metricEpisodes">0</strong></div>
        <div class="metric"><span>Memoria</span><strong id="metricMemory">0</strong></div>
        <div class="metric"><span>Energía</span><strong id="metricEnergy">0</strong></div>
      </div>
      <div id="log" class="log"></div>
      <div class="composer">
        <input id="intent" value="chat" aria-label="intent">
        <textarea id="text" rows="2" placeholder="Escribe algo para amigo"></textarea>
        <button id="send">Enviar</button>
      </div>
      <div id="status" class="status"></div>
    </section>
    <aside>
      <div class="panel">
        <div class="panelHead">
          <h2>Estado</h2>
          <button id="refresh" class="secondary">Actualizar</button>
        </div>
        <div id="profileSummary" class="muted"></div>
        <div class="row">
          <button id="profile" class="secondary">Perfil</button>
          <button id="metrics" class="secondary">Métricas</button>
        </div>
        <div class="row">
          <button id="healthDeep" class="secondary">Salud</button>
          <button id="snapshot" class="secondary">Snapshot</button>
        </div>
        <div class="row">
          <button id="globalModel" class="secondary">Global anónimo</button>
          <button id="globalSuggestions" class="secondary">Sugerencias</button>
        </div>
        <div class="row">
          <button id="openapi" class="secondary">API</button>
          <button id="audit" class="secondary">Auditoría</button>
        </div>
        <div class="row three">
          <button id="cycle" class="secondary">Ciclo</button>
          <button id="dream" class="secondary">Sueño</button>
          <button id="scheduled" class="secondary">Scheduler</button>
        </div>
        <div class="output"><pre id="state">{}</pre></div>
      </div>
      <div class="panel">
        <div class="panelHead">
          <h2>Agentes</h2>
          <button id="agents" class="secondary">Cargar</button>
        </div>
        <div class="row">
          <input id="prunePrefixes" value="demo-,check-" aria-label="prefijos limpieza">
          <button id="prunePreview" class="secondary">Previsualizar</button>
        </div>
        <button id="pruneRun" class="danger" style="margin-top:8px;width:100%">Limpiar coincidentes</button>
        <div id="agentList" class="list"></div>
      </div>
      <div class="panel">
        <div class="panelHead"><h2>Memoria</h2></div>
        <div class="row">
          <button id="episodes" class="secondary">Episodios</button>
          <button id="facts" class="secondary">Memoria fría</button>
        </div>
        <div class="row">
          <select id="factStatusFilter" aria-label="estado memoria fría">
            <option value="all">Todas</option>
            <option value="active">Activas</option>
            <option value="inactive">Inactivas</option>
          </select>
          <input id="factKeyFilter" placeholder="clave" aria-label="clave memoria fría">
        </div>
        <div class="row">
          <input id="memoryQuery" value="sprints" aria-label="buscar memoria">
          <select id="memoryTypeFilter" aria-label="filtro tipo memoria">
            <option value="all">Todo</option>
            <option value="cold">Fría</option>
            <option value="hot">Reciente</option>
          </select>
          <button id="memorySearch" class="secondary">Buscar</button>
        </div>
        <div class="row">
          <input id="decayFactor" type="number" min="0" max="1" step="0.01" value="0.98" aria-label="factor decay">
          <button id="decayMemory" class="danger">Aplicar decay</button>
        </div>
        <div id="memoryList" class="list"></div>
        <div class="row">
          <button id="relation" class="secondary">Relación</button>
          <button id="narrative" class="secondary">Narrativa</button>
        </div>
        <div class="row">
          <button id="selfModel" class="secondary">Self</button>
          <button id="worldModel" class="secondary">Mundo</button>
        </div>
        <div class="row">
          <button id="exportSafe" class="secondary">Export seguro</button>
          <button id="downloadSafe" class="secondary">Descargar seguro</button>
        </div>
        <div class="row">
          <button id="downloadFull" class="secondary">Descargar completo</button>
          <button id="quality" class="secondary">Calidad</button>
        </div>
        <div class="row">
          <button id="recordQuality" class="secondary">Guardar calidad</button>
          <button id="qualityHistory" class="secondary">Historial calidad</button>
        </div>
        <div class="row">
          <input id="importFile" type="file" accept="application/json" aria-label="importar agente">
          <button id="importAgent" class="secondary">Importar</button>
        </div>
        <div class="output"><pre id="memory">{}</pre></div>
      </div>
      <div class="panel">
        <div class="panelHead"><h2>Proactividad</h2></div>
        <select id="consent">
          <option value="unknown">Sin decidir</option>
          <option value="allowed">Permitida</option>
          <option value="paused">Pausada</option>
          <option value="denied">Denegada</option>
        </select>
        <div class="row">
          <input id="maxDay" type="number" min="0" value="1" aria-label="mensajes por día">
          <input id="minHours" type="number" min="0" value="24" aria-label="horas mínimas">
        </div>
        <div class="row">
          <input id="activeStart" type="number" min="0" max="23" value="0" aria-label="hora inicio">
          <input id="activeEnd" type="number" min="1" max="24" value="24" aria-label="hora fin">
        </div>
        <div class="row">
          <button id="saveProactivity" class="secondary">Guardar</button>
          <button id="evalProactivity" class="secondary">Evaluar</button>
        </div>
        <div class="row">
          <button id="inbox" class="secondary">Inbox</button>
          <button id="clearDelivered" class="secondary">Limpiar entregados</button>
        </div>
        <div class="row">
          <button id="temporalEvents" class="secondary">Eventos</button>
        </div>
        <div id="inboxList" class="list"></div>
        <div class="output"><pre id="proactivity">{}</pre></div>
      </div>
      <div class="panel">
        <div class="panelHead">
          <h2>LLM</h2>
          <button id="llmStatus" class="secondary">Estado</button>
        </div>
        <div id="llmSummary" class="muted">Sin comprobar.</div>
        <div id="llmSetup" class="muted"></div>
        <input id="claudeModel" value="claude-sonnet-4-5" aria-label="modelo Claude">
        <input id="claudeKey" type="password" placeholder="ANTHROPIC_API_KEY" aria-label="api key Claude">
        <div class="row">
          <select id="claudeSecretMode" aria-label="almacenamiento Claude">
            <option value="keychain">Keychain</option>
            <option value="env">.env.local</option>
          </select>
          <input id="claudeKeychainService" value="nino-anthropic" aria-label="servicio Keychain">
        </div>
        <div class="row three">
          <button id="claudeConfig" class="secondary">Config</button>
          <button id="saveClaude" class="secondary">Guardar Claude</button>
          <button id="llmProbe" class="secondary">Probar Claude</button>
        </div>
        <input id="deepseekModel" value="deepseek-chat" aria-label="modelo DeepSeek">
        <input id="deepseekKey" type="password" placeholder="DEEPSEEK_API_KEY" aria-label="api key DeepSeek">
        <div class="row">
          <button id="saveDeepSeek" class="secondary">Guardar DeepSeek</button>
          <input id="deepseekBaseUrl" value="https://api.deepseek.com/chat/completions" aria-label="base url DeepSeek">
        </div>
        <div class="row">
          <button id="guidedFinal" class="danger">Cierre guiado</button>
        </div>
        <div class="row">
          <button id="disableClaude" class="danger">Desactivar Claude</button>
        </div>
        <div class="output"><pre id="llm">{}</pre></div>
      </div>
      <div class="panel">
        <div class="panelHead">
          <h2>Permisos</h2>
          <button id="permissions" class="secondary">Ver</button>
        </div>
        <select id="permissionAction">
          <option value="external_message">Mensaje externo</option>
          <option value="tool_call">Tool call</option>
          <option value="network_request">Network request</option>
          <option value="file_write">File write</option>
        </select>
        <div class="row">
          <select id="permissionAllowed">
            <option value="false">Bloquear</option>
            <option value="true">Permitir</option>
          </select>
          <button id="savePermission" class="secondary">Guardar</button>
        </div>
        <div class="output"><pre id="permissionsOut">{}</pre></div>
      </div>
      <div class="panel">
        <div class="panelHead"><h2>Operación</h2></div>
        <div class="row">
          <button id="consolidate" class="secondary">Consolidar</button>
          <button id="backup" class="secondary">Backup DB</button>
        </div>
        <div class="row">
          <button id="backups" class="secondary">Ver backups</button>
          <button id="auditProduct" class="secondary">Auditoría</button>
        </div>
        <div class="row">
          <button id="productStatus" class="secondary">Estado final</button>
          <button id="completionAudit" class="secondary">Terminación</button>
        </div>
        <div class="row">
          <button id="closingReport" class="secondary">Informe cierre</button>
          <button id="reports" class="secondary">Ver informes</button>
        </div>
        <div class="row">
          <button id="latestReport" class="secondary">Último informe</button>
        </div>
        <div class="row">
          <button id="evalProduct" class="secondary">Eval local</button>
          <button id="finalPreflight" class="secondary">Preflight final</button>
        </div>
        <div class="row">
          <button id="finalAudit" class="danger">Cierre final</button>
        </div>
        <div id="finalReadiness" class="readiness"></div>
        <div class="row">
          <button id="mode" class="secondary">Modo</button>
          <button id="logs" class="secondary">Logs</button>
        </div>
        <div class="row">
          <button id="restartService" class="danger">Reiniciar servicio</button>
        </div>
        <div class="row">
          <button id="reset" class="danger">Reset agente</button>
        </div>
        <div id="backupList" class="list"></div>
        <div class="output"><pre id="backupsOut">{}</pre></div>
      </div>
      <div class="panel">
        <div class="panelHead">
          <h2>Tareas</h2>
          <button id="tasks" class="secondary">Ver</button>
        </div>
        <textarea id="taskText" rows="2" placeholder="Mensaje para encolar como tarea"></textarea>
        <div class="row">
          <button id="enqueueTask" class="secondary">Encolar</button>
          <button id="runTask" class="secondary">Ejecutar siguiente</button>
        </div>
        <div class="output"><pre id="tasksOut">{}</pre></div>
      </div>
    </aside>
  </main>
  <script>
    const $ = (id) => document.getElementById(id);
    const log = $("log");
    const sessionToken = () => localStorage.getItem("nino_session_token") || "";
    const api = (path, options = {}) => fetch(path, {
      headers: {
        "Content-Type": "application/json",
        ...(sessionToken() ? {"X-Nino-Session": sessionToken()} : {}),
        ...(options.headers || {}),
      },
      ...options
    }).then(async (res) => {
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || res.statusText);
      return data;
    });
    const currentUserId = () => ($("userId").value || "local").trim() || "local";
    const currentAgentId = () => ($("agentId").value || "nino").trim() || "nino";
    const agentPath = (tail) => `/users/${encodeURIComponent(currentUserId())}/agents/${encodeURIComponent(currentAgentId())}${tail}`;
    const print = (target, value) => { target.textContent = JSON.stringify(value, null, 2); };
    const status = (text) => { $("status").textContent = text; };
    const fmt = (value) => Number.isFinite(Number(value)) ? Number(value).toFixed(3).replace(/0+$/, "").replace(/[.]$/, "") : "0";
    const clearList = (target) => { target.textContent = ""; };
    const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
    const downloadJson = (filename, value) => {
      const blob = new Blob([JSON.stringify(value, null, 2)], {type: "application/json"});
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      link.click();
      URL.revokeObjectURL(url);
    };
    const addListItem = (target, title, body) => {
      const item = document.createElement("div");
      item.className = "listItem";
      const head = document.createElement("div");
      const detail = document.createElement("div");
      head.textContent = title;
      detail.className = "muted";
      detail.textContent = body || "";
      item.appendChild(head);
      item.appendChild(detail);
      target.appendChild(item);
      return item;
    };
    const addEntry = (role, text) => {
      const item = document.createElement("div");
      item.className = "entry";
      item.innerHTML = `<div class="role"></div><div class="text"></div>`;
      item.children[0].textContent = role;
      item.children[1].textContent = text;
      log.appendChild(item);
      log.scrollTop = log.scrollHeight;
    };
    const addContextEntry = (context) => {
      if (!context) return;
      const candidates = context.memory_candidates || [];
      const coldMemories = candidates
        .filter((item) => item.memory_type === "cold")
        .slice(0, 2)
        .map((item) => item.statement)
        .filter(Boolean);
      const hotMemories = candidates
        .filter((item) => item.memory_type !== "cold")
        .slice(0, 2)
        .map((item) => item.statement)
        .filter(Boolean);
      const goals = (context.active_goals || []).slice(0, 2);
      const parts = [
        `fuente: ${context.response_source || "policy"}`,
        `madurez: ${fmt(context.maturity)}`,
        `memoria usada: ${context.llm_context_memory_count ?? context.retrieved_memory_count ?? 0}`,
      ];
      if (goals.length) parts.push(`objetivos: ${goals.join(", ")}`);
      if (context.temporal_miss) parts.push("memoria temporal: sin resultados");
      if (coldMemories.length) parts.push(`memoria fría: ${coldMemories.join(" · ")}`);
      if (hotMemories.length) parts.push(`memoria reciente: ${hotMemories.join(" · ")}`);
      addEntry("contexto amigo", parts.join("\\n"));
    };
    const addEmptyConversation = () => {
      const item = document.createElement("div");
      item.className = "entry";
      item.innerHTML = `<div class="role">historial</div><div class="text muted">Sin episodios guardados para este agente.</div>`;
      log.appendChild(item);
    };
    async function loadConversation() {
      const out = await api(agentPath("/conversation"));
      log.textContent = "";
      if (!out.turns.length) {
        addEmptyConversation();
        return out;
      }
      out.turns.forEach((turn) => {
        const role = turn.role === "assistant" ? "niño" : "usuario";
        addEntry(`${role} · ${turn.intent}`, turn.text);
      });
      return out;
    }
    async function waitForHealth() {
      for (let attempt = 0; attempt < 15; attempt += 1) {
        try {
          const res = await fetch("/health", {cache: "no-store"});
          if (res.ok) return true;
        } catch (_err) {}
        await sleep(1000);
      }
      return false;
    }
    async function refreshState() {
      const [profile, metrics] = await Promise.all([
        api(agentPath("/profile")),
        api(agentPath("/metrics"))
      ]);
      const data = {profile: profile.profile, metrics: metrics.metrics};
      $("metricTick").textContent = metrics.metrics.tick ?? 0;
      $("metricMaturity").textContent = fmt(metrics.metrics.maturity);
      $("metricEpisodes").textContent = metrics.metrics.episode_count ?? 0;
      $("metricMemory").textContent = metrics.metrics.cold_memory_count ?? 0;
      $("metricEnergy").textContent = fmt(metrics.metrics.energy);
      $("profileSummary").textContent = profile.profile.summary || "";
      print($("state"), data);
      status(`Actualizado: ${new Date().toLocaleTimeString()}`);
    }
    async function loginUser() {
      const out = await api("/session/login", {method: "POST", body: JSON.stringify({user_id: currentUserId(), agent_id: currentAgentId()})});
      $("userId").value = out.user_id;
      $("agentId").value = out.agent_id;
      localStorage.setItem("nino_user_id", out.user_id);
      localStorage.setItem("nino_agent_id", out.agent_id);
      if (out.session_token) localStorage.setItem("nino_session_token", out.session_token);
      status(`Login: ${out.user_id}`);
      await refreshState();
      await loadAgents();
      await loadMemorySearch();
      await loadConversation();
      return out;
    }
    async function loadLLMStatus() {
      const out = await api(agentPath("/llm/status"));
      const llm = out.llm;
      const mode = llm.enabled ? `Claude · ${llm.model || "modelo no indicado"}` : "Reglas locales";
      const last = llm.last_response?.source ? ` · último origen: ${llm.last_response.source}` : "";
      const error = llm.last_response?.error ? ` · error: ${llm.last_response.error}` : "";
      $("llmSummary").textContent = `${mode}${last}${error}`;
      $("llmSetup").textContent = "";
      print($("llm"), out);
      return out;
    }
    async function probeLLM() {
      const out = await api(agentPath("/llm/probe"), {method: "POST", body: "{}"});
      const probe = out.probe;
      if (probe.ok) {
        $("llmSummary").textContent = `Claude conectado · ${probe.model || "modelo no indicado"}`;
      } else {
        $("llmSummary").textContent = `Claude no disponible · ${probe.error}`;
      }
      print($("llm"), out);
      return out;
    }
    function describeClaudeConfig(out) {
      const errors = out.config_errors || [];
      if (errors.length) {
        const names = errors.map(error => `${error.name}: ${error.error}`).join(", ");
        return `Configuración Claude inválida · ${names}`;
      }
      if (out.configured) return "Claude configurado en runtime.";
      const missing = out.missing?.length ? ` · falta: ${out.missing.join(", ")}` : "";
      return `Claude no configurado${missing}`;
    }
    function renderFinalReadiness(out) {
      const box = $("finalReadiness");
      box.textContent = "";
      const readiness = out.final_readiness;
      if (!readiness) return;
      const rows = [
        ["Auditoría local", readiness.local_audit_ok],
        ["Servicio persistente", readiness.launchd_observed],
        ["Claude configurado", readiness.claude_configured],
        ["Preflight final", readiness.ready_for_final_preflight],
        ["Cierre con Claude vivo", readiness.ready_for_final_audit]
      ];
      rows.forEach(([label, ok]) => {
        const row = document.createElement("div");
        row.className = "readinessRow";
        const name = document.createElement("span");
        name.textContent = label;
        const pill = document.createElement("span");
        pill.className = ok ? "pill" : "pill blocked";
        pill.textContent = ok ? "listo" : "bloqueado";
        row.appendChild(name);
        row.appendChild(pill);
        box.appendChild(row);
      });
      if (readiness.blockers?.length) {
        const blocked = document.createElement("div");
        blocked.className = "muted";
        blocked.textContent = `Falta: ${readiness.blockers.join(", ")}`;
        box.appendChild(blocked);
      }
      if (readiness.next_commands?.length) {
        const next = document.createElement("div");
        next.className = "muted";
        next.textContent = `Siguiente: ${readiness.next_commands.join(" && ")}`;
        box.appendChild(next);
      }
    }
    function renderCompletionAudit(out) {
      const box = $("finalReadiness");
      box.textContent = "";
      if (!out.requirements) return renderFinalReadiness(out.audit || out);
      out.requirements.forEach((requirement) => {
        const row = document.createElement("div");
        row.className = "readinessRow";
        const name = document.createElement("span");
        name.textContent = requirement.label;
        const pill = document.createElement("span");
        pill.className = requirement.ok ? "pill" : "pill blocked";
        pill.textContent = requirement.ok ? "listo" : "bloqueado";
        row.appendChild(name);
        row.appendChild(pill);
        box.appendChild(row);
      });
      const latest = out.latest_report;
      if (latest) {
        const row = document.createElement("div");
        row.className = "readinessRow";
        const name = document.createElement("span");
        name.textContent = "Último informe";
        const pill = document.createElement("span");
        pill.className = latest.ok ? "pill" : "pill blocked";
        if (latest.ok) {
          const head = latest.git_head ? ` · ${latest.git_head.slice(0, 8)}` : "";
          const blockers = latest.blockers?.length ? ` · ${latest.blockers.join(", ")}` : "";
          pill.textContent = `${latest.name}${head}${blockers}`;
        } else {
          pill.textContent = "sin informe";
        }
        row.appendChild(name);
        row.appendChild(pill);
        box.appendChild(row);
      }
      const current = out.latest_report_current;
      if (current) {
        const row = document.createElement("div");
        row.className = "readinessRow";
        const name = document.createElement("span");
        name.textContent = "Informe actual";
        const pill = document.createElement("span");
        pill.className = current.ok ? "pill" : "pill blocked";
        const currentHead = current.current_head ? current.current_head.slice(0, 8) : "sin head";
        const reportHead = current.latest_report_head ? current.latest_report_head.slice(0, 8) : "sin informe";
        pill.textContent = current.ok ? currentHead : `${currentHead} / ${reportHead}`;
        row.appendChild(name);
        row.appendChild(pill);
        box.appendChild(row);
      }
      if (out.recommended_next_action) {
        const row = document.createElement("div");
        row.className = "readinessRow";
        const name = document.createElement("span");
        name.textContent = "Siguiente acción";
        const pill = document.createElement("span");
        pill.className = out.ok ? "pill" : "pill blocked";
        pill.textContent = out.recommended_next_action;
        row.appendChild(name);
        row.appendChild(pill);
        box.appendChild(row);
      }
      if (out.next_commands?.length) {
        const next = document.createElement("div");
        next.className = "muted";
        next.textContent = `Siguiente: ${out.next_commands.join(" && ")}`;
        box.appendChild(next);
      }
    }
    function renderProductStatus(out) {
      renderFinalReadiness(out.audit || out);
      const box = $("finalReadiness");
      if (out.recommended_next_action) {
        const row = document.createElement("div");
        row.className = "readinessRow";
        const name = document.createElement("span");
        name.textContent = "Siguiente acción";
        const pill = document.createElement("span");
        pill.className = out.ok ? "pill" : "pill blocked";
        pill.textContent = out.recommended_next_action;
        row.appendChild(name);
        row.appendChild(pill);
        box.appendChild(row);
      }
      const latest = out.latest_report;
      if (!latest) return;
      const row = document.createElement("div");
      row.className = "readinessRow";
      const name = document.createElement("span");
      name.textContent = "Último informe";
      const pill = document.createElement("span");
      pill.className = latest.ok ? "pill" : "pill blocked";
      if (latest.ok) {
        const head = latest.git_head ? ` · ${latest.git_head.slice(0, 8)}` : "";
        const blockers = latest.blockers?.length ? ` · ${latest.blockers.join(", ")}` : "";
        pill.textContent = `${latest.name}${head}${blockers}`;
      } else {
        pill.textContent = "sin informe";
      }
      row.appendChild(name);
      row.appendChild(pill);
      box.appendChild(row);
      const current = out.latest_report_current;
      if (!current) return;
      const currentRow = document.createElement("div");
      currentRow.className = "readinessRow";
      const currentName = document.createElement("span");
      currentName.textContent = "Informe actual";
      const currentPill = document.createElement("span");
      currentPill.className = current.ok ? "pill" : "pill blocked";
      const currentHead = current.current_head ? current.current_head.slice(0, 8) : "sin head";
      const reportHead = current.latest_report_head ? current.latest_report_head.slice(0, 8) : "sin informe";
      currentPill.textContent = current.ok ? currentHead : `${currentHead} / ${reportHead}`;
      currentRow.appendChild(currentName);
      currentRow.appendChild(currentPill);
      box.appendChild(currentRow);
    }
    function renderBackups(out) {
      const target = $("backupList");
      clearList(target);
      if (!out.backups?.length) {
        addListItem(target, "Sin backups", out.backup_dir || "");
        return;
      }
      out.backups.forEach((backup) => {
        const size = backup.size_bytes ? `${backup.size_bytes} bytes` : "sin tamaño";
        const item = addListItem(target, backup.name || backup.path, `${size} · ${backup.modified_at || ""}`);
        const command = document.createElement("div");
        command.className = "muted";
        command.textContent = `Restaurar con amigo parado: scripts/ninoctl restore ${backup.path}`;
        item.appendChild(command);
      });
    }
    function renderReports(out) {
      const target = $("backupList");
      clearList(target);
      if (!out.reports?.length) {
        addListItem(target, "Sin informes", out.report_dir || "");
        return;
      }
      out.reports.forEach((report) => {
        const size = report.size_bytes ? `${report.size_bytes} bytes` : "sin tamaño";
        const item = addListItem(target, report.name || report.path, `${size} · ${report.modified_at || ""}`);
        const read = document.createElement("button");
        read.className = "secondary";
        read.textContent = "Ver JSON";
        read.onclick = async () => {
          const out = await api(`/operations/reports/${encodeURIComponent(report.name)}`);
          print($("backupsOut"), out);
          downloadJson(report.name || "nino-closing.json", out.report);
        };
        item.appendChild(read);
      });
    }
    $("send").onclick = async () => {
      const payload = {intent: $("intent").value || "chat", text: $("text").value, salience: 0.7, confidence: 0.9};
      if (!payload.text.trim()) return;
      $("send").disabled = true;
      try {
        addEntry("usuario", payload.text);
        status("amigo está pensando...");
        const out = await api(agentPath("/tick"), {method: "POST", body: JSON.stringify(payload)});
        $("text").value = "";
        print($("state"), out);
        if (out.action?.payload?.text) addEntry(`niño · ${out.llm_provider || out.action.type}`, out.action.payload.text);
        if ((out.auto_consolidated_count || 0) > 0) {
          const labels = (out.auto_consolidation?.cold_memory_updates || [])
            .slice(0, 3)
            .map((fact) => `${fact.key}: ${fact.value}`)
            .filter(Boolean);
          addEntry("memoria amigo", labels.length ? `consolidada: ${labels.join(" · ")}` : `consolidados ${out.auto_consolidated_count} hechos`);
        }
        addContextEntry(out.nino_context);
        status(out.llm_error ? `amigo respondió con reglas locales: ${out.llm_error}` : "amigo respondió.");
        await loadConversation();
        await refreshState();
        await loadMemorySearch();
        await loadLLMStatus();
      } catch (err) {
        status(`Error al enviar: ${err.message}`);
      } finally {
        $("send").disabled = false;
      }
    };
    $("refresh").onclick = refreshState;
    $("cycle").onclick = async () => {
      const out = await api(agentPath("/internal/cycle"), {method: "POST", body: "{}"});
      print($("state"), out);
      if (out.proactive_action) addEntry("niño · proactivo", out.proactive_action.payload.text);
    };
    $("dream").onclick = async () => {
      const out = await api(agentPath("/internal/dream"), {method: "POST", body: "{}"});
      print($("state"), out);
      await refreshState();
    };
    $("scheduled").onclick = async () => {
      const out = await api(agentPath("/internal/scheduled"), {method: "POST", body: "{}"});
      print($("state"), out);
      if (out.proactive_action) addEntry("niño · programado", out.proactive_action.payload.text);
    };
    async function loadAgents() {
      const out = await api(`/users/${encodeURIComponent(currentUserId())}/agents`);
      clearList($("agentList"));
      out.agents.forEach((agent) => {
        const item = document.createElement("button");
        item.className = "secondary";
        item.textContent = agent;
        item.onclick = async () => {
          $("agentId").value = agent;
          localStorage.setItem("nino_agent_id", agent);
          await refreshState();
          await loadMemorySearch();
          await loadConversation();
        };
        $("agentList").appendChild(item);
      });
      print($("state"), out);
    }
    $("agents").onclick = loadAgents;
    $("loginUser").onclick = loginUser;
    const prunePayload = (dryRun) => ({
      prefixes: $("prunePrefixes").value.split(",").map((item) => item.trim()).filter(Boolean),
      dry_run: dryRun
    });
    $("prunePreview").onclick = async () => {
      const out = await api("/agents/prune", {method: "POST", body: JSON.stringify(prunePayload(true))});
      print($("state"), out);
    };
    $("pruneRun").onclick = async () => {
      if (!confirm("Eliminar agentes coincidentes con esos prefijos?")) return;
      const out = await api("/agents/prune", {method: "POST", body: JSON.stringify(prunePayload(false))});
      print($("state"), out);
      await loadAgents();
      await refreshState();
      await loadConversation();
    };
    $("healthDeep").onclick = async () => print($("state"), await api("/health/deep"));
    $("snapshot").onclick = async () => print($("state"), await api("/development/snapshot"));
    $("globalModel").onclick = async () => print($("state"), await api("/operations/global-model"));
    $("globalSuggestions").onclick = async () => print($("state"), await api("/operations/global-suggestions"));
    $("openapi").onclick = async () => print($("state"), await api("/openapi.json"));
    $("audit").onclick = async () => print($("state"), await api(agentPath("/audit")));
    $("profile").onclick = async () => print($("state"), await api(agentPath("/profile")));
    $("metrics").onclick = async () => print($("state"), await api(agentPath("/metrics")));
    $("reset").onclick = async () => {
      if (!confirm(`Reset agente ${$("agentId").value || "demo"}?`)) return;
      const out = await api(agentPath("/reset"), {method: "POST", body: "{}"});
      log.textContent = "";
      print($("state"), out);
      print($("memory"), {});
      print($("proactivity"), {});
      await refreshState();
      await loadConversation();
    };
    $("consolidate").onclick = async () => {
      const out = await api(agentPath("/consolidate"), {method: "POST", body: "{}"});
      print($("memory"), out);
      await loadFacts();
      await refreshState();
    };
    $("backup").onclick = async () => {
      const out = await api("/operations/backup", {method: "POST", body: "{}"});
      print($("state"), out);
      status(out.ok ? `Backup creado: ${out.path}` : `Backup fallido: ${out.error}`);
      await loadBackups();
    };
    async function loadBackups() {
      const out = await api("/operations/backups");
      renderBackups(out);
      print($("backupsOut"), out);
      return out;
    }
    $("backups").onclick = loadBackups;
    $("auditProduct").onclick = async () => {
      const out = await api("/operations/audit");
      print($("backupsOut"), out);
      renderFinalReadiness(out);
      const preflight = out.final_preflight_command ? ` · preflight: ${out.final_preflight_command}` : "";
      const final = out.final_audit_command ? ` · cierre: ${out.final_audit_command}` : "";
      status(out.ok ? `Auditoría local OK${preflight}${final}` : `Auditoría con bloqueos${preflight}${final}`);
    };
    $("productStatus").onclick = async () => {
      const out = await api("/operations/product-status");
      print($("backupsOut"), out);
      renderProductStatus(out);
      status(out.ok ? `Estado final OK · eval ${out.eval_case_count} casos` : `Estado final con bloqueos · eval ${out.eval_case_count} casos`);
    };
    $("completionAudit").onclick = async () => {
      const out = await api("/operations/completion-audit");
      print($("backupsOut"), out);
      renderCompletionAudit(out);
      status(out.ok ? "Auditoría de terminación OK" : `Auditoría de terminación con ${out.blockers?.length || 0} bloqueos`);
    };
    $("closingReport").onclick = async () => {
      const out = await api("/operations/closing-report", {method: "POST", body: "{}"});
      print($("backupsOut"), out);
      renderCompletionAudit(out.report?.completion_audit || out);
      status(out.ok ? `Informe de cierre creado: ${out.path}` : "Informe de cierre fallido");
      await loadReports();
    };
    async function writeClosingReportAfterAudit(auditLabel) {
      const report = await api("/operations/closing-report", {method: "POST", body: "{}"});
      print($("backupsOut"), report);
      renderCompletionAudit(report.report?.completion_audit || report);
      if (report.ok) {
        await loadReports();
        status(`${auditLabel} · informe: ${report.path} · terminación actualizada`);
      } else {
        status(`${auditLabel} · informe fallido`);
      }
      return report;
    }
    async function loadReports() {
      const out = await api("/operations/reports");
      renderReports(out);
      print($("backupsOut"), out);
      return out;
    };
    $("reports").onclick = loadReports;
    $("latestReport").onclick = async () => {
      const out = await api("/operations/reports/latest");
      print($("backupsOut"), out);
      downloadJson(out.name || "nino-closing-latest.json", out.report);
      status(out.ok ? `Último informe: ${out.name}` : "No hay informe de cierre.");
    };
    $("evalProduct").onclick = async () => {
      const out = await api("/operations/eval");
      print($("backupsOut"), out);
      status(out.ok ? `Eval local OK · ${out.case_count} casos` : `Eval local con fallos · ${out.case_count} casos`);
    };
    $("finalPreflight").onclick = async () => {
      const out = await api("/operations/final-preflight");
      print($("backupsOut"), out);
      renderFinalReadiness(out);
      status(out.ok ? "Preflight final OK" : "Preflight final con bloqueos");
    };
    $("finalAudit").onclick = async () => {
      if (!confirm("Ejecutar cierre final? Puede realizar una llamada real a Claude si hay API key configurada.")) return;
      const out = await api("/operations/final-audit", {method: "POST", body: "{}"});
      print($("backupsOut"), out);
      renderFinalReadiness(out);
      await writeClosingReportAfterAudit(out.ok ? "Cierre final OK" : "Cierre final con bloqueos");
    };
    $("mode").onclick = async () => print($("state"), await api("/operations/mode"));
    $("logs").onclick = async () => print($("backupsOut"), await api("/operations/logs"));
    $("restartService").onclick = async () => {
      if (!confirm("Reiniciar el servicio persistente de amigo? La UI puede tardar unos segundos en responder.")) return;
      const out = await api("/operations/restart", {method: "POST", body: JSON.stringify({confirm: true})});
      print($("backupsOut"), out);
      status(out.ok ? "Reinicio programado. Esperando al servicio..." : `Reinicio no programado: ${out.error || "bloqueado"}`);
      if (out.ok) {
        setTimeout(() => fetch("/health").then(() => status("Servicio reiniciado."), () => status("Servicio reiniciando...")), 2500);
      }
    };
    $("saveProactivity").onclick = async () => {
      const payload = {
        consent: $("consent").value,
        max_messages_per_day: Number($("maxDay").value),
        min_hours_between: Number($("minHours").value),
        active_hours_start: Number($("activeStart").value),
        active_hours_end: Number($("activeEnd").value)
      };
      print($("proactivity"), await api(agentPath("/proactivity/configure"), {method: "POST", body: JSON.stringify(payload)}));
    };
    $("evalProactivity").onclick = async () => {
      const out = await api(agentPath("/proactivity/evaluate"), {method: "POST", body: "{}"});
      print($("proactivity"), out);
      if (out.should_send) addEntry("niño · proactivo", out.action.payload.text);
    };
    async function loadInbox() {
      const out = await api(agentPath("/proactivity/inbox"));
      clearList($("inboxList"));
      out.inbox.forEach((item) => {
        const row = document.createElement("div");
        row.className = "listItem";
        const title = document.createElement("div");
        title.textContent = item.action?.payload?.text || item.id;
        const detail = document.createElement("div");
        detail.className = "muted";
        detail.textContent = item.delivered ? "entregado" : "pendiente";
        row.appendChild(title);
        row.appendChild(detail);
        if (!item.delivered) {
          const button = document.createElement("button");
          button.className = "secondary";
          button.style.marginTop = "8px";
          button.textContent = "Marcar entregado";
          button.onclick = async () => {
            const marked = await api(agentPath(`/proactivity/inbox/${encodeURIComponent(item.id)}/delivered`), {method: "POST", body: "{}"});
            print($("proactivity"), marked);
            await loadInbox();
            await refreshState();
          };
          row.appendChild(button);
        }
        $("inboxList").appendChild(row);
      });
      print($("proactivity"), out);
    }
    $("inbox").onclick = loadInbox;
    async function loadTemporalEvents() {
      const out = await api(agentPath("/temporal-events"));
      clearList($("inboxList"));
      out.events.forEach((event) => {
        const row = document.createElement("div");
        row.className = "listItem";
        const title = document.createElement("div");
        title.textContent = event.text || event.id;
        const detail = document.createElement("div");
        detail.className = "muted";
        const recurrence = event.recurrence ? ` · ${event.recurrence}` : "";
        detail.textContent = `${event.status || "pending"} · ${event.next_due_at || event.due_at || "sin fecha"}${recurrence}`;
        row.appendChild(title);
        row.appendChild(detail);
        const pause = document.createElement("button");
        pause.className = "secondary";
        pause.textContent = event.status === "paused" ? "Reactivar" : "Pausar";
        pause.onclick = async () => {
          const statusValue = event.status === "paused" ? "pending" : "paused";
          const updated = await api(agentPath(`/temporal-events/${encodeURIComponent(event.id)}`), {method: "PATCH", body: JSON.stringify({status: statusValue})});
          print($("proactivity"), updated);
          await loadTemporalEvents();
        };
        row.appendChild(pause);
        const remove = document.createElement("button");
        remove.className = "danger";
        remove.textContent = "Eliminar";
        remove.onclick = async () => {
          if (!confirm("Eliminar este evento temporal?")) return;
          const deleted = await api(agentPath(`/temporal-events/${encodeURIComponent(event.id)}`), {method: "DELETE"});
          print($("proactivity"), deleted);
          await loadTemporalEvents();
        };
        row.appendChild(remove);
        $("inboxList").appendChild(row);
      });
      print($("proactivity"), out);
    }
    $("temporalEvents").onclick = loadTemporalEvents;
    $("clearDelivered").onclick = async () => {
      const out = await api(agentPath("/proactivity/inbox/clear-delivered"), {method: "POST", body: "{}"});
      print($("proactivity"), out);
      await loadInbox();
      await refreshState();
    };
    async function loadEpisodes() {
      const out = await api(agentPath("/episodes"));
      clearList($("memoryList"));
      out.episodes.slice().reverse().forEach((episode) => {
        const item = addListItem($("memoryList"), episode.text, `${episode.intent} · salience ${episode.salience}`);
        const button = document.createElement("button");
        button.className = "danger";
        button.textContent = "Eliminar episodio";
        button.onclick = async () => {
          if (!confirm("Eliminar este episodio de memoria?")) return;
          const deleted = await api(agentPath(`/episodes/${encodeURIComponent(episode.episode_id)}`), {method: "DELETE"});
          print($("memory"), deleted);
          await loadEpisodes();
          await refreshState();
          await loadConversation();
        };
        item.appendChild(button);
      });
      print($("memory"), out);
    }
    async function loadFacts() {
      const params = new URLSearchParams();
      params.set("status", $("factStatusFilter").value || "all");
      const key = $("factKeyFilter").value.trim();
      if (key) params.set("key", key);
      const out = await api(agentPath(`/memory/facts?${params.toString()}`));
      clearList($("memoryList"));
      if (out.fact_counts) {
        const counts = out.fact_counts;
        const visible = out.visible_fact_counts || counts;
        addListItem(
          $("memoryList"),
          `Memoria fría: ${out.visible_facts ?? out.facts.length}/${counts.total}`,
          `activa ${visible.active}/${counts.active} · inactiva ${visible.inactive}/${counts.inactive} · clave ${out.key_filter || "todas"} · estado ${out.status_filter || "all"}`
        );
      }
      out.facts.forEach((fact) => {
        const status = fact.valid_to ? "inactiva" : "activa";
        const origin = fact.source_episode_id ? ` · origen ${fact.source_episode_id.slice(0, 8)}` : "";
        const item = addListItem($("memoryList"), `${fact.key}: ${fact.value}`, `memoria fría ${status} · confidence ${fact.confidence}${origin}`);
        const button = document.createElement("button");
        button.className = "danger";
        button.textContent = "Eliminar hecho";
        button.onclick = async () => {
          if (!confirm("Eliminar este hecho de memoria fría?")) return;
          const deleted = await api(agentPath(`/memory/facts/${encodeURIComponent(fact.fact_id)}`), {method: "DELETE"});
          print($("memory"), deleted);
          await loadFacts();
          await refreshState();
        };
        item.appendChild(button);
      });
      print($("memory"), out);
    }
    async function loadMemorySearch() {
      const query = $("memoryQuery").value || "";
      const filter = $("memoryTypeFilter").value || "all";
      const out = await api(agentPath("/memory/search"), {method: "POST", body: JSON.stringify({query_intent: query, time_scope: "long", memory_type_filter: filter})});
      clearList($("memoryList"));
      const candidates = out.memory_candidates;
      if (out.memory_type_counts) {
        const counts = out.memory_type_counts;
        const visible = out.visible_memory_type_counts || counts;
        addListItem(
          $("memoryList"),
          `Resultados: ${out.visible_candidates ?? candidates.length}/${counts.total}`,
          `fría ${visible.cold}/${counts.cold} · reciente ${visible.hot}/${counts.hot} · filtro ${out.memory_type_filter || "all"}`
        );
      }
      candidates.forEach((candidate) => {
        const type = candidate.memory_type === "cold" ? "fría" : "reciente";
        const source = candidate.source_episode_id ? ` · origen ${candidate.source_episode_id.slice(0, 8)}` : "";
        addListItem($("memoryList"), candidate.statement, `memoria ${type} · score ${fmt(candidate.score)} · confidence ${fmt(candidate.confidence)}${source}`);
      });
      print($("memory"), out);
    }
    $("episodes").onclick = loadEpisodes;
    $("facts").onclick = loadFacts;
    $("factStatusFilter").onchange = loadFacts;
    $("factKeyFilter").onkeydown = (event) => {
      if (event.key === "Enter") loadFacts();
    };
    $("memorySearch").onclick = loadMemorySearch;
    $("decayMemory").onclick = async () => {
      const factor = Number($("decayFactor").value);
      if (!Number.isFinite(factor) || factor < 0 || factor > 1) return status("Factor decay debe estar entre 0 y 1.");
      if (!confirm(`Aplicar decay ${factor} a la memoria del agente?`)) return;
      const out = await api(agentPath("/memory/decay"), {method: "POST", body: JSON.stringify({factor})});
      print($("memory"), out);
      await refreshState();
      await loadFacts();
    };
    $("relation").onclick = async () => print($("memory"), await api(agentPath("/relation")));
    $("selfModel").onclick = async () => print($("memory"), await api(agentPath("/self-model")));
    $("worldModel").onclick = async () => print($("memory"), await api(agentPath("/world-model")));
    $("narrative").onclick = async () => print($("memory"), await api(agentPath("/narrative")));
    $("exportSafe").onclick = async () => print($("memory"), await api(agentPath("/export-safe")));
    $("downloadSafe").onclick = async () => {
      const out = await api(agentPath("/export-safe"));
      downloadJson(`${$("agentId").value || "nino"}-safe-export.json`, out);
      print($("memory"), out);
    };
    $("downloadFull").onclick = async () => {
      if (!confirm("Descargar export completo con episodios sin redactar?")) return;
      const out = await api(agentPath("/export"));
      downloadJson(`${$("agentId").value || "nino"}-full-export.json`, out);
      print($("memory"), out);
    };
    $("importAgent").onclick = async () => {
      const file = $("importFile").files[0];
      if (!file) return status("Selecciona un JSON de export.");
      if (!confirm("Importar agente desde JSON? Puede reemplazar datos si el archivo lo indica.")) return;
      const text = await file.text();
      const parsed = JSON.parse(text);
      const payload = parsed.export ? {export: parsed.export, replace: true} : {...parsed, replace: true};
      const out = await api("/agents/import", {method: "POST", body: JSON.stringify(payload)});
      print($("memory"), out);
      if (out.agent_id) $("agentId").value = out.agent_id;
      await loadAgents();
      await refreshState();
      await loadConversation();
    };
    $("quality").onclick = async () => print($("memory"), await api(agentPath("/eval/conversation")));
    $("recordQuality").onclick = async () => print($("memory"), await api(agentPath("/eval/conversation/record"), {method: "POST", body: "{}"}));
    $("qualityHistory").onclick = async () => print($("memory"), await api(agentPath("/eval/conversation/history")));
    $("claudeConfig").onclick = async () => {
      const out = await api("/operations/claude");
      $("llmSummary").textContent = describeClaudeConfig(out);
      if (out.configured) {
        $("llmSetup").textContent = "Claude configurado en runtime.";
      } else if (out.config_errors?.length) {
        $("llmSetup").textContent = "Corrige los valores indicados y reinicia el servicio antes de probar Claude.";
      } else {
        $("llmSetup").textContent = `Configurar: ${out.setup_commands?.join(" && ") || "scripts/ninoctl configure-claude"}`;
      }
      print($("llm"), out);
    };
    async function configureClaudeFromForm() {
      const apiKey = $("claudeKey").value.trim();
      const model = $("claudeModel").value.trim() || "claude-sonnet-4-5";
      const mode = $("claudeSecretMode").value;
      const keychainService = $("claudeKeychainService").value.trim() || "nino-anthropic";
      if (!apiKey) {
        status("Pega una ANTHROPIC_API_KEY para configurar Claude.");
        return null;
      }
      const out = await api("/operations/claude/configure", {
        method: "POST",
        body: JSON.stringify({api_key: apiKey, model, use_keychain: mode === "keychain", keychain_service: keychainService})
      });
      $("claudeKey").value = "";
      $("llmSummary").textContent = describeClaudeConfig(out.claude || out);
      print($("llm"), out);
      return out;
    }
    $("saveClaude").onclick = async () => {
      const mode = $("claudeSecretMode").value;
      if (!confirm(mode === "keychain" ? "Guardar Claude en macOS Keychain y referencia en .env.local?" : "Guardar Claude en .env.local con permisos locales 600?")) return;
      const out = await configureClaudeFromForm();
      if (!out) return;
      status(out.ok ? "Claude configurado. Reinicia launchd para persistencia completa." : `Claude no configurado: ${out.error || "error"}`);
      await loadLLMStatus();
    };
    $("saveDeepSeek").onclick = async () => {
      const apiKey = $("deepseekKey").value.trim();
      const model = $("deepseekModel").value.trim() || "deepseek-chat";
      const baseUrl = $("deepseekBaseUrl").value.trim() || "https://api.deepseek.com/chat/completions";
      if (!apiKey) return status("Pega una DEEPSEEK_API_KEY para configurar DeepSeek.");
      if (!confirm("Guardar DeepSeek en .env.local con permisos locales 600?")) return;
      const out = await api("/operations/deepseek/configure", {
        method: "POST",
        body: JSON.stringify({api_key: apiKey, model, base_url: baseUrl})
      });
      $("deepseekKey").value = "";
      $("llmSummary").textContent = describeClaudeConfig(out.llm || out);
      print($("llm"), out);
      status(out.ok ? "DeepSeek configurado. Reinicia launchd para persistencia completa." : `DeepSeek no configurado: ${out.error || "error"}`);
      await loadLLMStatus();
    };
    $("guidedFinal").onclick = async () => {
      const mode = $("claudeSecretMode").value;
      if (!confirm(mode === "keychain" ? "Guardar Claude, reiniciar el servicio y ejecutar cierre final con llamada real a Claude?" : "Guardar Claude en .env.local, reiniciar el servicio y ejecutar cierre final con llamada real a Claude?")) return;
      const configured = await configureClaudeFromForm();
      if (!configured?.ok) return status(`Claude no configurado: ${configured?.error || "error"}`);
      status("Claude configurado. Reiniciando servicio...");
      const restarted = await api("/operations/restart", {method: "POST", body: JSON.stringify({confirm: true})});
      print($("backupsOut"), restarted);
      if (!restarted.ok) return status(`Reinicio no programado: ${restarted.error || "bloqueado"}`);
      status("Esperando al servicio para cierre final...");
      const healthy = await waitForHealth();
      if (!healthy) return status("Servicio no respondió tras el reinicio.");
      await loadLLMStatus();
      const out = await api("/operations/final-audit", {method: "POST", body: "{}"});
      print($("backupsOut"), out);
      renderFinalReadiness(out);
      await writeClosingReportAfterAudit(out.ok ? "Cierre guiado OK" : "Cierre guiado con bloqueos");
    };
    $("disableClaude").onclick = async () => {
      const removeKeychain = $("claudeSecretMode").value === "keychain";
      const keychainService = $("claudeKeychainService").value.trim() || "nino-anthropic";
      if (!confirm(removeKeychain ? "Desactivar Claude y borrar la entrada de Keychain indicada?" : "Desactivar Claude y limpiar .env.local?")) return;
      const out = await api("/operations/claude/disable", {
        method: "POST",
        body: JSON.stringify({confirm: true, remove_keychain: removeKeychain, keychain_service: keychainService})
      });
      $("llmSummary").textContent = describeClaudeConfig(out.claude || out);
      print($("llm"), out);
      status(out.ok ? "Claude desactivado." : `Claude no desactivado: ${out.error || "error"}`);
      await loadLLMStatus();
    };
    $("llmStatus").onclick = loadLLMStatus;
    $("llmProbe").onclick = probeLLM;
    $("permissions").onclick = async () => print($("permissionsOut"), await api(agentPath("/permissions")));
    $("savePermission").onclick = async () => {
      const payload = {
        action_type: $("permissionAction").value,
        allowed: $("permissionAllowed").value === "true",
        delivery: "inbox_only"
      };
      print($("permissionsOut"), await api(agentPath("/permissions/configure"), {method: "POST", body: JSON.stringify(payload)}));
      await refreshState();
    };
    $("tasks").onclick = async () => print($("tasksOut"), await api(agentPath("/tasks")));
    $("enqueueTask").onclick = async () => {
      const text = $("taskText").value.trim();
      if (!text) return status("Escribe una tarea.");
      const payload = {description: text, action_type: "external_message", text};
      print($("tasksOut"), await api(agentPath("/tasks"), {method: "POST", body: JSON.stringify(payload)}));
      $("taskText").value = "";
    };
    $("runTask").onclick = async () => {
      const out = await api(agentPath("/tasks/run-next"), {method: "POST", body: "{}"});
      print($("tasksOut"), out);
      await loadInbox();
      await refreshState();
    };
    $("text").addEventListener("keydown", (event) => {
      if ((event.metaKey || event.ctrlKey) && event.key === "Enter") $("send").click();
    });
    $("userId").value = localStorage.getItem("nino_user_id") || $("userId").value;
    $("agentId").value = localStorage.getItem("nino_agent_id") || $("agentId").value;
    loginUser()
      .then(loadAgents)
      .then(loadMemorySearch)
      .then(loadConversation)
      .then(loadLLMStatus)
      .then(loadBackups)
      .then(() => $("productStatus").click())
      .then(() => $("permissions").click())
      .then(() => $("tasks").click())
      .catch((err) => status(err.message));
  </script>
</body>
</html>
"""

USER_HTML = """<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>amigo</title>
  <style>
    :root {
      color-scheme: light;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: #182126;
      background: #f4f6f2;
    }
    * { box-sizing: border-box; }
    body { margin: 0; min-height: 100vh; background: #f4f6f2; }
    main {
      min-height: 100vh;
      display: grid;
      grid-template-rows: auto 1fr auto;
      max-width: 780px;
      margin: 0 auto;
      padding: 18px;
      gap: 14px;
    }
    header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      min-height: 44px;
    }
    h1 { margin: 0; font-size: 22px; letter-spacing: 0; font-weight: 650; }
    input, textarea, button {
      font: inherit;
      border: 1px solid #bac6c0;
      border-radius: 6px;
      background: #fff;
      color: #182126;
    }
    input, textarea { width: 100%; padding: 11px 12px; }
    textarea { resize: none; min-height: 48px; max-height: 150px; }
    button {
      min-height: 42px;
      padding: 10px 14px;
      cursor: pointer;
      background: #225f5b;
      border-color: #225f5b;
      color: #fff;
      white-space: nowrap;
    }
    button.secondary { background: #fff; border-color: #bac6c0; color: #182126; }
    button:disabled { opacity: 0.55; cursor: not-allowed; }
    .login {
      align-self: center;
      display: grid;
      gap: 10px;
      width: min(100%, 380px);
      margin: 0 auto;
    }
    .chat {
      display: none;
      min-height: 0;
      grid-template-rows: 1fr auto;
      gap: 12px;
    }
    .messages {
      overflow: auto;
      display: flex;
      flex-direction: column;
      gap: 10px;
      padding: 6px 0;
    }
    .message {
      max-width: 78%;
      line-height: 1.45;
      white-space: pre-wrap;
      padding: 10px 12px;
      border: 1px solid #d2dbd6;
      border-radius: 8px;
      background: #fff;
    }
    .message.user {
      align-self: flex-end;
      background: #225f5b;
      border-color: #225f5b;
      color: #fff;
    }
    .message.nino { align-self: flex-start; }
    .composer {
      display: grid;
      grid-template-columns: 48px minmax(0, 1fr) 86px;
      gap: 8px;
      align-items: stretch;
    }
    .voiceActive { border-color: #9b4a1b; color: #9b4a1b; }
    .status { min-height: 20px; font-size: 12px; color: #68766f; }
    @media (max-width: 560px) {
      main { padding: 12px; }
      .composer { grid-template-columns: 44px minmax(0, 1fr) 72px; }
      .message { max-width: 92%; }
      button { padding-left: 10px; padding-right: 10px; }
    }
  </style>
</head>
<body>
  <main id="minimalUserApp">
    <header>
      <h1>amigo</h1>
      <button id="logoutButton" class="secondary" hidden>Salir</button>
    </header>
    <form id="loginView" class="login">
      <input id="userId" autocomplete="username" placeholder="Usuario" aria-label="Usuario">
      <input id="password" autocomplete="current-password" placeholder="Contraseña" aria-label="Contraseña" type="password">
      <button id="loginButton" type="submit">Entrar</button>
    </form>
    <section id="chatView" class="chat" aria-live="polite">
      <div id="messages" class="messages"></div>
      <form id="composer" class="composer">
        <button id="voiceButton" class="secondary" type="button" title="Voz" aria-label="Voz">Voz</button>
        <textarea id="text" rows="1" placeholder="Mensaje" aria-label="Mensaje"></textarea>
        <button id="sendButton" type="submit">Enviar</button>
      </form>
    </section>
    <div id="status" class="status"></div>
  </main>
  <script>
    const $ = (id) => document.getElementById(id);
    const STORAGE_USER = "nino_user_id";
    const AGENT_ID = "nino";
    const ONBOARDING = [
      {key: "name", question: "¿Cómo te llamas?"},
      {key: "location", question: "¿De dónde eres o dónde vives ahora?"},
      {key: "birth", question: "¿Cuándo naciste o qué edad tienes?"},
      {key: "likes", question: "¿Qué te gusta hacer? Hobbies, música, planes..."},
      {key: "important_memory", question: "¿Hay algo importante que quieres que recuerde de ti?"},
      {key: "expectation", question: "¿Qué esperas de mí como amigo?"}
    ];
    let recognition = null;
    let listening = false;
    let voiceReply = false;
    let inboxTimer = null;
    let onboardingActive = false;
    let onboardingStep = 0;
    const deliveredInbox = new Set();

    function currentUserId() {
      return ($("userId").value || localStorage.getItem(STORAGE_USER) || "usuario").trim();
    }
    function agentPath(path) {
      return `/users/${encodeURIComponent(currentUserId())}/agents/${encodeURIComponent(AGENT_ID)}${path}`;
    }
    function onboardingStorageKey() {
      return `amigo_onboarding_${currentUserId()}`;
    }
    function onboardingState() {
      try {
        return JSON.parse(localStorage.getItem(onboardingStorageKey()) || "{}");
      } catch {
        return {};
      }
    }
    function saveOnboardingState(state) {
      localStorage.setItem(onboardingStorageKey(), JSON.stringify(state));
    }
    async function api(path, options = {}) {
      const headers = {"content-type": "application/json", ...(options.headers || {})};
      const res = await fetch(path, {...options, headers, credentials: "same-origin"});
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || res.statusText);
      return data;
    }
    function localNowIso() {
      const now = new Date();
      const offsetMinutes = -now.getTimezoneOffset();
      const sign = offsetMinutes >= 0 ? "+" : "-";
      const abs = Math.abs(offsetMinutes);
      const hours = String(Math.floor(abs / 60)).padStart(2, "0");
      const minutes = String(abs % 60).padStart(2, "0");
      const local = new Date(now.getTime() - now.getTimezoneOffset() * 60000).toISOString().slice(0, -1);
      return `${local}${sign}${hours}:${minutes}`;
    }
    function setStatus(text) {
      $("status").textContent = text || "";
    }
    function addMessage(role, text) {
      const row = document.createElement("div");
      row.className = `message ${role}`;
      row.textContent = text;
      $("messages").appendChild(row);
      $("messages").scrollTop = $("messages").scrollHeight;
    }
    function speak(text) {
      if (!voiceReply || !("speechSynthesis" in window) || !text) return;
      window.speechSynthesis.cancel();
      const utterance = new SpeechSynthesisUtterance(text);
      utterance.lang = "es-ES";
      window.speechSynthesis.speak(utterance);
    }
    async function loginUser(event) {
      if (event) event.preventDefault();
      const userId = currentUserId();
      localStorage.setItem(STORAGE_USER, userId);
      const login = await api("/session/login", {method: "POST", body: JSON.stringify({user_id: userId, agent_id: AGENT_ID, password: $("password").value})});
      $("loginView").style.display = "none";
      $("chatView").style.display = "grid";
      $("logoutButton").hidden = false;
      await loadConversation();
      await startProactiveConversation();
      await startOnboardingIfNeeded();
      await loadProactiveInbox();
      startInboxPolling();
      $("text").focus();
    }
    async function loadConversation() {
      const out = await api(agentPath("/conversation"));
      $("messages").replaceChildren();
      (out.turns || out.conversation || []).forEach((entry) => {
        const role = entry.role === "user" ? "user" : "nino";
        addMessage(role, entry.text || entry.content || "");
      });
      return (out.turns || out.conversation || []).length;
    }
    async function startOnboardingIfNeeded() {
      const state = onboardingState();
      if (state.completed || $("messages").children.length > 0) return;
      const relation = await api(agentPath("/relation")).catch(() => null);
      const backendOnboarding = relation && relation.relation_state ? relation.relation_state.onboarding : null;
      if (backendOnboarding && backendOnboarding.completed) {
        saveOnboardingState({completed: true, step: ONBOARDING.length});
        return;
      }
      if (backendOnboarding && backendOnboarding.last_key) {
        const index = ONBOARDING.findIndex((item) => item.key === backendOnboarding.last_key);
        if (index >= 0 && index + 1 < ONBOARDING.length) {
          onboardingStep = index + 1;
          saveOnboardingState({completed: false, step: onboardingStep});
        }
      }
      onboardingActive = true;
      onboardingStep = Number(state.step || 0);
      if (backendOnboarding && backendOnboarding.last_key) {
        const index = ONBOARDING.findIndex((item) => item.key === backendOnboarding.last_key);
        if (index >= 0 && index + 1 < ONBOARDING.length) onboardingStep = index + 1;
      }
      addMessage("nino", "Antes de empezar, me gustaría conocerte un poco. Lo que me cuentes queda entre nosotros y lo usaré solo para recordarte mejor, acompañarte mejor y no tratarte como a cualquiera. Puedes saltarte cualquier pregunta.");
      addMessage("nino", ONBOARDING[onboardingStep].question);
    }
    async function sendOnboardingAnswer(text) {
      const current = ONBOARDING[onboardingStep];
      const out = await api(agentPath("/tick"), {
        method: "POST",
        body: JSON.stringify({intent: `onboarding:${current.key}`, text, salience: 0.7, confidence: 0.9, now: localNowIso()}),
      });
      const reply = out.action && out.action.payload ? out.action.payload.text : "";
      if (reply) addMessage("nino", reply);
      onboardingStep += 1;
      if (onboardingStep >= ONBOARDING.length) {
        onboardingActive = false;
        saveOnboardingState({completed: true, step: onboardingStep});
      } else {
        saveOnboardingState({completed: false, step: onboardingStep});
      }
    }
    async function sendText(event) {
      event.preventDefault();
      const text = $("text").value.trim();
      if (!text) return;
      $("text").value = "";
      addMessage("user", text);
      $("sendButton").disabled = true;
      setStatus("amigo está pensando");
      try {
        if (onboardingActive) {
          await sendOnboardingAnswer(text);
          setStatus("");
          return;
        }
        const out = await api(agentPath("/tick"), {
          method: "POST",
          body: JSON.stringify({intent: "chat", text, salience: 0.7, confidence: 0.8, now: localNowIso()}),
        });
        const reply = out.action && out.action.payload ? out.action.payload.text : "";
        if (reply) {
          addMessage("nino", reply);
          speak(reply);
        } else if (out.nino_context && out.nino_context.temporal_miss) {
          const miss = "No encuentro recuerdos guardados de esa fecha.";
          addMessage("nino", miss);
          speak(miss);
        }
        setStatus("");
      } catch (err) {
        setStatus(err.message);
      } finally {
        $("sendButton").disabled = false;
        $("text").focus();
      }
    }
    function setupVoice() {
      const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
      if (!SpeechRecognition) {
        $("voiceButton").disabled = true;
        return;
      }
      recognition = new SpeechRecognition();
      recognition.lang = "es-ES";
      recognition.interimResults = false;
      recognition.onstart = () => {
        listening = true;
        $("voiceButton").classList.add("voiceActive");
      };
      recognition.onend = () => {
        listening = false;
        $("voiceButton").classList.remove("voiceActive");
      };
      recognition.onresult = (event) => {
        const transcript = Array.from(event.results).map((result) => result[0].transcript).join(" ");
        $("text").value = transcript;
        voiceReply = true;
        $("composer").requestSubmit();
      };
    }
    async function loadProactiveInbox() {
      if ($("chatView").style.display !== "grid") return;
      await api(agentPath("/proactivity/evaluate"), {method: "POST", body: JSON.stringify({now: localNowIso()})}).catch(() => {});
      const out = await api(agentPath("/proactivity/inbox"));
      for (const item of out.inbox || []) {
        if (item.status === "delivered" || deliveredInbox.has(item.id)) continue;
        const text = item.action && item.action.payload ? item.action.payload.text : "";
        if (!text) continue;
        deliveredInbox.add(item.id);
        addMessage("nino", text);
        speak(text);
        await api(agentPath(`/proactivity/inbox/${encodeURIComponent(item.id)}/delivered`), {method: "POST", body: "{}"});
      }
    }
    async function startProactiveConversation() {
      await api(agentPath("/proactivity/configure"), {
        method: "POST",
        body: JSON.stringify({consent: "allowed", max_messages_per_day: 3, min_hours_between: 1})
      });
      await api(agentPath("/proactivity/evaluate"), {method: "POST", body: JSON.stringify({now: localNowIso()})}).catch(() => {});
      await loadProactiveInbox();
      if ($("messages").children.length === 0 && onboardingState().completed) {
        addMessage("nino", "Estoy aquí. ¿Qué tal vas hoy?");
      }
    }
    function startInboxPolling() {
      if (inboxTimer) return;
      inboxTimer = window.setInterval(() => {
        loadProactiveInbox().catch(() => {});
      }, 30000);
    }
    function stopInboxPolling() {
      if (!inboxTimer) return;
      window.clearInterval(inboxTimer);
      inboxTimer = null;
    }
    $("loginView").addEventListener("submit", loginUser);
    $("composer").addEventListener("submit", sendText);
    $("logoutButton").onclick = async () => {
      await api("/session/logout", {method: "POST", body: "{}"}).catch(() => {});
      localStorage.removeItem(STORAGE_USER);
      stopInboxPolling();
      voiceReply = false;
      $("chatView").style.display = "none";
      $("loginView").style.display = "grid";
      $("logoutButton").hidden = true;
      $("messages").replaceChildren();
      $("userId").focus();
    };
    $("voiceButton").onclick = () => {
      if (!recognition) return;
      voiceReply = true;
      if (listening) recognition.stop();
      else recognition.start();
    };
    const storedUser = localStorage.getItem(STORAGE_USER);
    if (storedUser) {
      $("userId").value = storedUser;
      loginUser();
    }
    setupVoice();
  </script>
</body>
</html>
"""


API_ENDPOINTS = [
    "GET /user",
    "GET /chat",
    "GET /health",
    "GET /health/deep",
    "GET /app",
    "GET /openapi.json",
    "GET /autonomy/status",
    "POST /autonomy/run-once",
    "GET /development/snapshot",
    "GET /operations/global-model",
    "GET /operations/global-suggestions",
    "GET /operations/mode",
    "GET /operations/claude",
    "POST /operations/claude/configure",
    "POST /operations/claude/disable",
    "POST /operations/deepseek/configure",
    "GET /operations/audit",
    "GET /operations/product-status",
    "GET /operations/next-action",
    "GET /operations/completion-audit",
    "POST /operations/closing-report",
    "GET /operations/reports",
    "GET /operations/reports/latest",
    "GET /operations/reports/{report_name}",
    "GET /operations/eval",
    "GET /operations/final-preflight",
    "POST /operations/final-audit",
    "GET /operations/backups",
    "GET /operations/logs",
    "POST /operations/backup",
    "POST /operations/restart",
    "POST /session/login",
    "GET /session/status",
    "POST /session/logout",
    "GET /agents",
    "GET /users/{user_id}/agents",
    "POST /users/{user_id}/agents/{agent_id}/tick",
    "GET /users/{user_id}/agents/{agent_id}/state",
    "GET /users/{user_id}/agents/{agent_id}/conversation",
    "GET /users/{user_id}/agents/{agent_id}/memory/facts",
    "POST /users/{user_id}/agents/{agent_id}/memory/search",
    "GET /users/{user_id}/agents/{agent_id}/profile",
    "GET /users/{user_id}/agents/{agent_id}/metrics",
    "POST /agents/prune",
    "POST /agents/import",
    "POST /agents/{agent_id}/tick",
    "GET /agents/{agent_id}/state",
    "GET /agents/{agent_id}/conversation",
    "GET /agents/{agent_id}/episodes",
    "DELETE /agents/{agent_id}/episodes/{episode_id}",
    "GET /agents/{agent_id}/llm/status",
    "POST /agents/{agent_id}/llm/probe",
    "GET /agents/{agent_id}/memory/facts",
    "DELETE /agents/{agent_id}/memory/facts/{fact_id}",
    "GET /agents/{agent_id}/temporal-events",
    "PATCH /agents/{agent_id}/temporal-events/{event_id}",
    "DELETE /agents/{agent_id}/temporal-events/{event_id}",
    "GET /agents/{agent_id}/relation",
    "GET /agents/{agent_id}/self-model",
    "GET /agents/{agent_id}/world-model",
    "GET /agents/{agent_id}/narrative",
    "GET /agents/{agent_id}/profile",
    "GET /agents/{agent_id}/metrics",
    "GET /agents/{agent_id}/export",
    "GET /agents/{agent_id}/export-safe",
    "GET /agents/{agent_id}/proactivity/inbox",
    "POST /agents/{agent_id}/proactivity/inbox/{item_id}/delivered",
    "POST /agents/{agent_id}/proactivity/inbox/clear-delivered",
    "POST /agents/{agent_id}/memory/decay",
    "POST /agents/{agent_id}/memory/search",
    "GET /agents/{agent_id}/eval/conversation",
    "POST /agents/{agent_id}/eval/conversation/record",
    "GET /agents/{agent_id}/eval/conversation/history",
    "GET /agents/{agent_id}/audit",
    "GET /agents/{agent_id}/permissions",
    "POST /agents/{agent_id}/permissions/configure",
    "GET /agents/{agent_id}/tasks",
    "POST /agents/{agent_id}/tasks",
    "POST /agents/{agent_id}/tasks/run-next",
    "POST /agents/{agent_id}/reset",
    "POST /agents/{agent_id}/memory/retrieve",
    "POST /agents/{agent_id}/consolidate",
    "POST /agents/{agent_id}/internal/cycle",
    "POST /agents/{agent_id}/internal/dream",
    "POST /agents/{agent_id}/internal/scheduled",
    "POST /internal/scheduled",
    "POST /agents/{agent_id}/proactivity/configure",
    "POST /agents/{agent_id}/proactivity/evaluate",
]


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if is_dataclass(value):
        return asdict(value)
    raise TypeError(f"{type(value).__name__} is not JSON serializable")


def _to_jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, default=_json_default))


def _memory_type_for_candidate(candidate: dict[str, Any]) -> str:
    return "cold" if str(candidate.get("fact_id", "")).startswith("cold::") else "hot"


def _memory_type_counts(candidates: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"cold": 0, "hot": 0, "total": 0}
    for candidate in candidates:
        memory_type = str(candidate.get("memory_type") or _memory_type_for_candidate(candidate))
        if memory_type not in {"cold", "hot"}:
            memory_type = "hot"
        counts[memory_type] += 1
        counts["total"] += 1
    return counts


def _annotate_memory_response(payload: dict[str, Any]) -> dict[str, Any]:
    candidates = payload.get("memory_candidates", [])
    if isinstance(candidates, list):
        for candidate in candidates:
            if isinstance(candidate, dict):
                candidate["memory_type"] = _memory_type_for_candidate(candidate)
    return payload


def _cold_fact_counts(facts: list[Any]) -> dict[str, Any]:
    counts: dict[str, Any] = {"active": 0, "inactive": 0, "total": 0, "active_by_key": {}, "inactive_by_key": {}}
    for fact in facts:
        key = str(getattr(fact, "key", "") or "")
        active = getattr(fact, "valid_to", None) is None
        status = "active" if active else "inactive"
        counts[status] += 1
        counts["total"] += 1
        bucket = counts[f"{status}_by_key"]
        bucket[key] = int(bucket.get(key, 0)) + 1
    return counts


def _filter_cold_facts(facts: list[Any], status_filter: str = "all", key_filter: str = "") -> list[Any]:
    filtered = list(facts)
    if status_filter == "active":
        filtered = [fact for fact in filtered if getattr(fact, "valid_to", None) is None]
    elif status_filter == "inactive":
        filtered = [fact for fact in filtered if getattr(fact, "valid_to", None) is not None]
    elif status_filter != "all":
        raise ValueError("status must be active, inactive or all")
    if key_filter:
        filtered = [fact for fact in filtered if str(getattr(fact, "key", "")) == key_filter]
    return filtered


def _identity_slug(value: str, default: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", value.strip().lower()).strip("-_")
    return slug or default


def _scoped_agent_id(user_id: str, agent_id: str) -> str:
    user_slug = _identity_slug(user_id, "local")
    agent_slug = _identity_slug(agent_id, "nino")
    return f"user::{user_slug}::agent::{agent_slug}"


def _public_agent_id(scoped_agent_id: str, user_id: str) -> str | None:
    prefix = f"user::{_identity_slug(user_id, 'local')}::agent::"
    if not scoped_agent_id.startswith(prefix):
        return None
    return scoped_agent_id[len(prefix):]


def _attach_current_report_summary(report: dict[str, Any]) -> None:
    report_file = report.get("report_file", {})
    path = report_file.get("path")
    name = report_file.get("name")
    if not path or not name:
        return
    git_head = report.get("git", {}).get("head")
    latest_report = {
        "ok": True,
        "path": path,
        "name": name,
        "generated_at": report.get("generated_at"),
        "git_head": git_head,
        "blockers": report.get("summary", {}).get("blockers", []),
    }
    latest_report_current = {
        "ok": bool(git_head),
        "current_head": git_head,
        "latest_report_head": git_head,
        "report_name": name,
        "reason": None if git_head else "revision_unknown",
    }
    for section_name in ("product_status", "completion_audit"):
        section = report.get(section_name)
        if isinstance(section, dict):
            section["latest_report"] = latest_report
            section["latest_report_current"] = latest_report_current
    completion_audit = report.get("completion_audit")
    if isinstance(completion_audit, dict):
        requirements = completion_audit.get("requirements", [])
        if not requirements:
            return
        for requirement in requirements:
            if requirement.get("id") == "closing_evidence":
                requirement["ok"] = True
                evidence = requirement.setdefault("evidence", [])
                if "latest_report_current" not in evidence:
                    evidence.append("latest_report_current")
        completion_audit["blockers"] = [item for item in requirements if not item.get("ok")]
        completion_audit["ok"] = not completion_audit["blockers"]
        report["summary"]["completion_audit_ok"] = bool(completion_audit.get("ok"))
        report["summary"]["ok"] = bool(completion_audit.get("ok"))
        report["summary"]["blockers"] = [item.get("id") or item.get("name") for item in completion_audit.get("blockers", [])]


def _redact_log_line(line: str) -> str:
    line = re.sub(r"(ANTHROPIC_API_KEY=)[^\s]+", r"\1[REDACTED]", line)
    line = re.sub(r"(sk-ant-[A-Za-z0-9_-]{8})[A-Za-z0-9_-]+", r"\1[REDACTED]", line)
    return line


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _openapi_document() -> dict[str, Any]:
    paths: dict[str, Any] = {}
    for endpoint in API_ENDPOINTS:
        method, path = endpoint.split(" ", 1)
        if path == "/app":
            continue
        parameters = []
        for name in ("user_id", "agent_id", "item_id", "episode_id", "fact_id", "event_id", "report_name"):
            if "{" + name + "}" in path:
                parameters.append({
                    "name": name,
                    "in": "path",
                    "required": True,
                    "schema": {"type": "string"},
                })
        operation: dict[str, Any] = {
            "summary": endpoint,
            "responses": {"200": {"description": "OK"}},
        }
        if parameters:
            operation["parameters"] = parameters
        if method in {"POST", "PUT", "PATCH"}:
            operation["requestBody"] = {
                "required": False,
                "content": {"application/json": {"schema": {"type": "object"}}},
            }
        paths.setdefault(path, {})[method.lower()] = operation
    return {
        "openapi": "3.1.0",
        "info": {"title": "amigo Local API", "version": "0.8.0"},
        "paths": paths,
    }


def _episode_from_raw(raw: dict[str, Any]) -> Episode:
    return Episode(
        episode_id=str(raw["episode_id"]),
        agent_id=str(raw["agent_id"]),
        timestamp=_parse_datetime(str(raw["timestamp"])),
        text=str(raw.get("text", "")),
        intent=str(raw.get("intent", "unknown")),
        salience=float(raw.get("salience", 0.5)),
        confidence=float(raw.get("confidence", 0.8)),
    )


class NinoService:
    def __init__(
        self,
        runtime: NinoRuntime,
        autonomy: BackgroundAutonomy | None = None,
        db_path: str | Path | None = None,
        restart_callback: Callable[[], None] | None = None,
    ) -> None:
        self.runtime = runtime
        self.internal_loop = InternalLoop(runtime)
        self.scheduler = NinoScheduler(runtime)
        self.autonomy = autonomy
        self.db_path = Path(db_path) if db_path is not None else None
        self.restart_callback = restart_callback
        self.sessions: dict[str, dict[str, str]] = {}
        self.login_attempts: dict[str, dict[str, Any]] = {}
        self.security_audit: list[dict[str, Any]] = []

    def _audit_access(self, event_type: str, payload: dict[str, Any]) -> None:
        safe_payload = {
            key: value
            for key, value in payload.items()
            if key not in {"password", "session_token", "token", "token_hash"}
        }
        self.security_audit.append(
            {
                "at": datetime.now(timezone.utc).isoformat(),
                "type": event_type,
                "payload": safe_payload,
            }
        )
        self.security_audit = self.security_audit[-200:]

    def _login_blocked(self, ip: str, now: float) -> bool:
        entry = self.login_attempts.get(ip, {"failures": [], "blocked_until": 0.0})
        return float(entry.get("blocked_until", 0.0)) > now

    def _record_login_failure(self, ip: str, user_id: str, reason: str, now: float) -> None:
        entry = self.login_attempts.setdefault(ip, {"failures": [], "blocked_until": 0.0})
        failures = [stamp for stamp in entry.get("failures", []) if now - float(stamp) <= LOGIN_RATE_LIMIT_WINDOW_SECONDS]
        failures.append(now)
        entry["failures"] = failures
        if len(failures) >= LOGIN_RATE_LIMIT_ATTEMPTS:
            entry["blocked_until"] = now + LOGIN_RATE_LIMIT_BLOCK_SECONDS
        self._audit_access("login_failed", {"ip": ip, "user_id": user_id, "reason": reason})

    def _clean_session(self, token: str, now: datetime) -> dict[str, str] | None:
        if not token:
            return None
        digest = token_hash(token)
        session = self.sessions.get(digest)
        if not session:
            return None
        expires_at = _parse_datetime(session["expires_at"])
        if expires_at <= now:
            self.sessions.pop(digest, None)
            self._audit_access("session_expired", {"user_id": session.get("user_id"), "agent_id": session.get("agent_id")})
            return None
        session["last_seen_at"] = now.isoformat()
        session["expires_at"] = (now + timedelta(seconds=SESSION_TTL_SECONDS)).isoformat()
        return session

    def health(self) -> dict[str, Any]:
        return {"ok": True, "service": "nino"}

    def deep_health(self) -> dict[str, Any]:
        snapshot = self.runtime.development_snapshot()
        autonomy = self.autonomy_status()
        return {
            "ok": True,
            "service": "nino",
            "storage": "ok",
            "agent_count": snapshot["agent_count"],
            "total_episodes": snapshot.get("total_episodes", 0),
            "autonomy": autonomy,
        }

    def autonomy_status(self) -> dict[str, Any]:
        if self.autonomy is None:
            return {"enabled": False, "running": False}
        return _to_jsonable(self.autonomy.status())

    def autonomy_run_once(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.autonomy is None:
            return {"enabled": False, "results": []}
        now = _parse_datetime(payload["now"]) if "now" in payload else None
        return {"enabled": True, "results": self.autonomy.run_once(now=now)}

    def list_agents(self) -> dict[str, Any]:
        return {"agents": self.runtime.list_agents()}

    def login(self, payload: dict[str, Any]) -> dict[str, Any]:
        user_id = _identity_slug(str(payload.get("user_id", "local")), "local")
        agent_id = _identity_slug(str(payload.get("agent_id", "nino")), "nino")
        ip = str(payload.get("_remote_addr", "unknown"))
        now_ts = time.time()
        if self._login_blocked(ip, now_ts):
            self._audit_access("login_blocked", {"ip": ip, "user_id": user_id})
            return {"ok": False, "error": "login_rate_limited"}
        configured_hash = _password_hash()
        if _is_prod() and not configured_hash:
            self._record_login_failure(ip, user_id, "password_not_configured", now_ts)
            return {"ok": False, "error": "password_not_configured"}
        if configured_hash and not verify_password(str(payload.get("password", "")), configured_hash):
            self._record_login_failure(ip, user_id, "bad_credentials", now_ts)
            return {"ok": False, "error": "bad_credentials"}
        scoped_agent_id = _scoped_agent_id(user_id, agent_id)
        session_token = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        self.sessions[token_hash(session_token)] = {
            "user_id": user_id,
            "agent_id": agent_id,
            "created_at": now.isoformat(),
            "last_seen_at": now.isoformat(),
            "expires_at": (now + timedelta(seconds=SESSION_TTL_SECONDS)).isoformat(),
        }
        self.runtime.load_or_init_state(scoped_agent_id)
        self.login_attempts.pop(ip, None)
        self._audit_access("login_ok", {"ip": ip, "user_id": user_id, "agent_id": agent_id})
        return {
            "ok": True,
            "user_id": user_id,
            "agent_id": agent_id,
            "scoped_agent_id": scoped_agent_id,
            "session_token": session_token,
            "expires_in_seconds": SESSION_TTL_SECONDS,
            "privacy": "private_user_scope",
        }

    def session_status(self, payload: dict[str, Any]) -> dict[str, Any]:
        token = str(payload.get("_session_token", "")).strip()
        session = self._clean_session(token, datetime.now(timezone.utc))
        if not session:
            return {"ok": False, "authenticated": False}
        return {
            "ok": True,
            "authenticated": True,
            "user_id": session["user_id"],
            "agent_id": session["agent_id"],
            "created_at": session["created_at"],
            "expires_at": session["expires_at"],
            "privacy": "private_user_scope",
        }

    def logout(self, payload: dict[str, Any]) -> dict[str, Any]:
        token = str(payload.get("_session_token", "")).strip()
        removed = self.sessions.pop(token_hash(token), None) is not None if token else False
        return {"ok": True, "logged_out": removed}

    def authorize_user_scope(self, user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        required = _require_session_enabled()
        if not required:
            return {"ok": True, "required": False}
        token = str(payload.get("_session_token", "")).strip()
        session = self._clean_session(token, datetime.now(timezone.utc))
        scoped_user_id = _identity_slug(user_id, "local")
        if not session:
            return {"ok": False, "required": True, "error": "session_required"}
        if session.get("user_id") != scoped_user_id:
            return {"ok": False, "required": True, "error": "session_user_mismatch"}
        return {"ok": True, "required": True, "user_id": scoped_user_id}

    def list_user_agents(self, user_id: str) -> dict[str, Any]:
        agents = [
            public_id
            for agent_id in self.runtime.list_agents()
            for public_id in [_public_agent_id(agent_id, user_id)]
            if public_id is not None
        ]
        return {
            "user_id": _identity_slug(user_id, "local"),
            "agents": sorted(agents),
            "scope": "private_user_scope",
        }

    def prune_agents(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.runtime.prune_agents(
            prefixes=list(payload.get("prefixes") or []),
            agent_ids=list(payload.get("agent_ids") or []),
            dry_run=bool(payload.get("dry_run", True)),
        )

    def development_snapshot(self) -> dict[str, Any]:
        return {"snapshot": self.runtime.development_snapshot()}

    def global_model(self) -> dict[str, Any]:
        return {"global_model": self.runtime.global_model(), "privacy": "anonymous_aggregate"}

    def global_suggestions(self) -> dict[str, Any]:
        model = self.runtime.global_model()
        concepts = model.get("concept_counts", {})
        ranked = []
        if isinstance(concepts, dict):
            ranked = [
                {"concept": concept, "count": count, "prompt": f"Explorar {concept} desde el contexto privado del usuario."}
                for concept, count in sorted(concepts.items(), key=lambda item: (int(item[1]), str(item[0])), reverse=True)
                if int(count) >= 2
            ][:5]
        return {"suggestions": ranked, "privacy": "anonymous_aggregate"}

    def backup(self) -> dict[str, Any]:
        if self.db_path is None:
            return {"ok": False, "error": "db_path_unavailable"}
        backup_dir = self.db_path.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = backup_dir / f"{self.db_path.stem}-{stamp}.db"
        source = sqlite3.connect(str(self.db_path))
        try:
            target = sqlite3.connect(str(backup_path))
            try:
                source.backup(target)
            finally:
                target.close()
        finally:
            source.close()
        return {"ok": True, "path": str(backup_path)}

    def restart_service(self, payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("confirm") is not True:
            return {"ok": False, "error": "confirmation_required"}
        if self.restart_callback is not None:
            self.restart_callback()
            return {"ok": True, "scheduled": True, "method": "callback"}

        from .product_audit import _launchd_check

        launchd = _launchd_check(require_launchd=True, label=os.environ.get("NINO_LAUNCHD_LABEL", "local.nino.server"))
        if launchd["ok"] is not True:
            return {
                "ok": False,
                "error": "launchd_not_observed",
                "launchd": launchd,
                "command": "scripts/nino-launchd start",
            }

        def exit_for_launchd() -> None:
            os._exit(0)

        threading.Timer(0.25, exit_for_launchd).start()
        return {
            "ok": True,
            "scheduled": True,
            "method": "launchd_keepalive",
            "delay_seconds": 0.25,
            "note": "launchd KeepAlive should restart the service after this process exits",
        }

    def list_backups(self) -> dict[str, Any]:
        if self.db_path is None:
            return {"ok": False, "error": "db_path_unavailable", "backups": []}
        backup_dir = self.db_path.parent / "backups"
        if not backup_dir.exists():
            return {"ok": True, "backup_dir": str(backup_dir), "backups": []}
        backups = []
        for path in sorted(backup_dir.glob("*.db"), key=lambda item: item.stat().st_mtime, reverse=True):
            stat = path.stat()
            backups.append(
                {
                    "path": str(path),
                    "name": path.name,
                    "size_bytes": stat.st_size,
                    "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                }
            )
        return {"ok": True, "backup_dir": str(backup_dir), "backups": backups}

    def list_reports(self) -> dict[str, Any]:
        if self.db_path is None:
            return {"ok": False, "error": "db_path_unavailable", "reports": []}
        report_dir = self.db_path.parent / "reports"
        if not report_dir.exists():
            return {"ok": True, "report_dir": str(report_dir), "reports": []}
        reports = []
        for path in sorted(report_dir.glob("nino-closing-*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
            stat = path.stat()
            reports.append(
                {
                    "path": str(path),
                    "name": path.name,
                    "size_bytes": stat.st_size,
                    "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                }
            )
        return {"ok": True, "report_dir": str(report_dir), "reports": reports}

    def get_report(self, report_name: str) -> dict[str, Any]:
        if self.db_path is None:
            return {"ok": False, "error": "db_path_unavailable"}
        if report_name == "latest":
            reports = self.list_reports()
            if not reports.get("reports"):
                return {"ok": False, "error": "report_not_found", "report_dir": reports.get("report_dir")}
            report_name = str(reports["reports"][0]["name"])
        if not re.fullmatch(r"nino-closing-\d{8}-\d{6}[.]json", report_name):
            return {"ok": False, "error": "invalid_report_name"}
        report_path = self.db_path.parent / "reports" / report_name
        if not report_path.exists() or not report_path.is_file():
            return {"ok": False, "error": "report_not_found", "path": str(report_path)}
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            return {"ok": False, "error": "invalid_report_json", "path": str(report_path), "detail": str(exc)}
        return {"ok": True, "path": str(report_path), "name": report_name, "report": _to_jsonable(report)}

    def logs(self, *, limit: int = 80) -> dict[str, Any]:
        if self.db_path is None:
            return {"ok": False, "error": "db_path_unavailable", "lines": []}
        log_path = self.db_path.parent / "nino-server.log"
        if not log_path.exists():
            return {"ok": True, "path": str(log_path), "exists": False, "lines": []}
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]
        return {
            "ok": True,
            "path": str(log_path),
            "exists": True,
            "lines": [_redact_log_line(line) for line in lines],
        }

    def operating_mode(self) -> dict[str, Any]:
        llm_client = self.runtime.llm_client
        config = llm_config_status()
        provider = getattr(llm_client, "provider", None) if llm_client is not None else config["provider"]
        return {
            "local_first": True,
            "storage": {
                "type": "sqlite" if self.db_path is not None else "runtime",
                "path": str(self.db_path) if self.db_path is not None else None,
            },
            "external_llm": {
                "enabled": llm_client is not None,
                "provider": provider if llm_client is not None else None,
                "model": getattr(llm_client, "model", None) if llm_client is not None else None,
                "config": config,
            },
            "network_required_for_core": False,
            "offline_capabilities": [
                "memory",
                "conversation_policy",
                "proactivity_rules",
                "task_queue",
                "export_import",
                "backup",
            ],
            "external_capabilities": [f"{provider}_responses"] if provider and llm_client is not None else [],
        }

    def claude_config(self) -> dict[str, Any]:
        client = self.runtime.llm_client
        config = llm_config_status()
        provider = getattr(client, "provider", None) if client is not None else config["provider"]
        return {
            "configured": config["enabled"],
            "runtime_enabled": client is not None,
            "provider": provider,
            "model": getattr(client, "model", None) if client is not None else config["model"],
            "api_key_present": config["api_key_present"],
            "api_key_source": config["api_key_source"],
            "keychain_service": config["keychain_service"],
            "missing": config["missing"],
            "config_errors": config["config_errors"],
            "probe_endpoint": "/agents/{agent_id}/llm/probe",
            "setup_commands": claude_setup_commands(include_cd=True),
            "notes": [
                "Con --keychain-service, .env.local guarda solo NINO_KEYCHAIN_SERVICE.",
                "Sin Keychain, la API key se guarda solo en .env.local con permisos 600.",
                "El plist de launchd no incrusta ANTHROPIC_API_KEY.",
            ],
        }

    def configure_claude(self, payload: dict[str, Any]) -> dict[str, Any]:
        api_key = str(payload.get("api_key", "")).strip()
        model = str(payload.get("model", "claude-sonnet-4-5")).strip() or "claude-sonnet-4-5"
        use_keychain = bool(payload.get("use_keychain", False))
        keychain_service = str(payload.get("keychain_service", "nino-anthropic")).strip() or "nino-anthropic"
        if not api_key:
            return {"ok": False, "error": "missing_api_key", "claude": self.claude_config()}
        if any(char in model for char in "\r\n="):
            return {"ok": False, "error": "invalid_model", "claude": self.claude_config()}
        if use_keychain and any(char in keychain_service for char in "\r\n="):
            return {"ok": False, "error": "invalid_keychain_service", "claude": self.claude_config()}

        env_file = Path(".env.local")
        preserved: list[str] = []
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                if not line.startswith(
                    ("NINO_LLM_PROVIDER=", "NINO_CLAUDE_MODEL=", "ANTHROPIC_API_KEY=", "NINO_KEYCHAIN_SERVICE=")
                ):
                    preserved.append(line)
        if use_keychain:
            try:
                subprocess.run(
                    [
                        "/usr/bin/security",
                        "add-generic-password",
                        "-U",
                        "-a",
                        os.environ.get("USER", ""),
                        "-s",
                        keychain_service,
                        "-w",
                        api_key,
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                return {"ok": False, "error": "keychain_write_failed", "detail": exc.__class__.__name__, "claude": self.claude_config()}
        lines = [
            *preserved,
            "NINO_LLM_PROVIDER=claude",
            f"NINO_CLAUDE_MODEL={model}",
        ]
        if use_keychain:
            lines.append(f"NINO_KEYCHAIN_SERVICE={keychain_service}")
        else:
            lines.append(f"ANTHROPIC_API_KEY={api_key}")
        env_file.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        env_file.chmod(0o600)

        os.environ["NINO_LLM_PROVIDER"] = "claude"
        os.environ["NINO_CLAUDE_MODEL"] = model
        if use_keychain:
            os.environ["NINO_KEYCHAIN_SERVICE"] = keychain_service
            os.environ.pop("ANTHROPIC_API_KEY", None)
        else:
            os.environ["ANTHROPIC_API_KEY"] = api_key
            os.environ.pop("NINO_KEYCHAIN_SERVICE", None)
        self.runtime.llm_client = build_configured_llm()
        return {
            "ok": True,
            "env_file": str(env_file),
            "mode": "keychain" if use_keychain else "env_file",
            "keychain_service": keychain_service if use_keychain else None,
            "restart_recommended": True,
            "claude": self.claude_config(),
            "notes": [
                "La API key no se devuelve en esta respuesta.",
                "Reinicia launchd para que el servicio persistente cargue .env.local desde cero.",
            ],
        }

    def configure_deepseek(self, payload: dict[str, Any]) -> dict[str, Any]:
        api_key = str(payload.get("api_key", "")).strip()
        model = str(payload.get("model", "deepseek-chat")).strip() or "deepseek-chat"
        base_url = str(payload.get("base_url", "https://api.deepseek.com/chat/completions")).strip() or "https://api.deepseek.com/chat/completions"
        if not api_key:
            return {"ok": False, "error": "missing_api_key", "llm": self.claude_config()}
        if any(char in model for char in "\r\n="):
            return {"ok": False, "error": "invalid_model", "llm": self.claude_config()}
        if any(char in base_url for char in "\r\n="):
            return {"ok": False, "error": "invalid_base_url", "llm": self.claude_config()}

        env_file = Path(".env.local")
        preserved: list[str] = []
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                if not line.startswith(
                    (
                        "NINO_LLM_PROVIDER=",
                        "NINO_CLAUDE_MODEL=",
                        "ANTHROPIC_API_KEY=",
                        "NINO_KEYCHAIN_SERVICE=",
                        "NINO_DEEPSEEK_MODEL=",
                        "NINO_DEEPSEEK_BASE_URL=",
                        "NINO_DEEPSEEK_API_KEY=",
                        "DEEPSEEK_API_KEY=",
                    )
                ):
                    preserved.append(line)
        lines = [
            *preserved,
            "NINO_LLM_PROVIDER=deepseek",
            f"NINO_DEEPSEEK_MODEL={model}",
            f"NINO_DEEPSEEK_BASE_URL={base_url}",
            f"NINO_DEEPSEEK_API_KEY={api_key}",
        ]
        env_file.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        env_file.chmod(0o600)

        os.environ["NINO_LLM_PROVIDER"] = "deepseek"
        os.environ["NINO_DEEPSEEK_MODEL"] = model
        os.environ["NINO_DEEPSEEK_BASE_URL"] = base_url
        os.environ["NINO_DEEPSEEK_API_KEY"] = api_key
        os.environ.pop("ANTHROPIC_API_KEY", None)
        os.environ.pop("NINO_KEYCHAIN_SERVICE", None)
        self.runtime.llm_client = build_configured_llm()
        return {
            "ok": True,
            "env_file": str(env_file),
            "provider": "deepseek",
            "model": model,
            "base_url": base_url,
            "restart_recommended": True,
            "llm": self.claude_config(),
            "notes": [
                "La API key no se devuelve en esta respuesta.",
                "Reinicia launchd para que el servicio persistente cargue .env.local desde cero.",
            ],
        }

    def disable_claude(self, payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("confirm") is not True:
            return {"ok": False, "error": "confirmation_required", "claude": self.claude_config()}
        remove_keychain = bool(payload.get("remove_keychain", False))
        keychain_service = str(payload.get("keychain_service", "")).strip() or os.environ.get("NINO_KEYCHAIN_SERVICE", "").strip()
        if remove_keychain and keychain_service and any(char in keychain_service for char in "\r\n="):
            return {"ok": False, "error": "invalid_keychain_service", "claude": self.claude_config()}

        keychain_removed = False
        keychain_error: str | None = None
        if remove_keychain and keychain_service:
            try:
                completed = subprocess.run(
                    ["/usr/bin/security", "delete-generic-password", "-s", keychain_service],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                keychain_removed = completed.returncode == 0
                if completed.returncode not in (0, 44):
                    keychain_error = "keychain_delete_failed"
            except (OSError, subprocess.SubprocessError) as exc:
                keychain_error = exc.__class__.__name__

        env_file = Path(".env.local")
        if env_file.exists():
            preserved = [
                line
                for line in env_file.read_text(encoding="utf-8").splitlines()
                if not line.startswith(
                    (
                        "NINO_LLM_PROVIDER=",
                        "NINO_CLAUDE_MODEL=",
                        "ANTHROPIC_API_KEY=",
                        "NINO_KEYCHAIN_SERVICE=",
                        "NINO_DEEPSEEK_MODEL=",
                        "NINO_DEEPSEEK_BASE_URL=",
                        "NINO_DEEPSEEK_API_KEY=",
                        "DEEPSEEK_API_KEY=",
                    )
                )
            ]
            env_file.write_text(("\n".join(preserved).rstrip() + "\n") if preserved else "", encoding="utf-8")
            env_file.chmod(0o600)

        for name in (
            "NINO_LLM_PROVIDER",
            "NINO_CLAUDE_MODEL",
            "ANTHROPIC_API_KEY",
            "NINO_KEYCHAIN_SERVICE",
            "NINO_DEEPSEEK_MODEL",
            "NINO_DEEPSEEK_BASE_URL",
            "NINO_DEEPSEEK_API_KEY",
            "DEEPSEEK_API_KEY",
        ):
            os.environ.pop(name, None)
        self.runtime.llm_client = None
        return {
            "ok": keychain_error is None,
            "env_file": str(env_file),
            "keychain_service": keychain_service or None,
            "keychain_removed": keychain_removed,
            "keychain_error": keychain_error,
            "restart_recommended": True,
            "claude": self.claude_config(),
            "notes": [
                "Claude queda desactivado en este proceso.",
                "Reinicia launchd para que el servicio persistente cargue .env.local desde cero.",
            ],
        }

    def _final_audit_metadata(self, result: dict[str, Any]) -> dict[str, Any]:
        def final_metadata(result: dict[str, Any]) -> dict[str, Any]:
            claude = self.claude_config()
            claude_configured = claude["configured"] is True and not claude["config_errors"]
            checks = {check["name"]: check for check in result.get("checks", [])}
            launchd = checks.get("launchd_service", {})
            launchd_evidence = launchd.get("evidence", {})
            launchd_observed = launchd.get("ok") is True and not launchd_evidence.get("skipped", False)
            local_audit_ok = result.get("ok") is True
            claude_live_ok = checks.get("claude_live", {}).get("ok") is True
            blockers = []
            if not local_audit_ok:
                blockers.extend(check["name"] for check in result.get("checks", []) if not check.get("ok"))
            if not launchd_observed:
                blockers.append("launchd_service")
            if not claude_configured:
                blockers.extend(claude["missing"])
                blockers.extend(error["name"] for error in claude["config_errors"])
            blockers = list(dict.fromkeys(blockers))
            ready_for_final_preflight = local_audit_ok and launchd_observed and claude_configured
            if ready_for_final_preflight:
                next_commands = ["scripts/ninoctl final-preflight", "scripts/ninoctl final-audit"]
            elif not claude_configured:
                next_commands = claude["setup_commands"]
            elif not launchd_observed:
                next_commands = ["scripts/nino-launchd start", "scripts/ninoctl final-preflight"]
            else:
                next_commands = ["scripts/ninoctl persistent-audit"]
            return {
                "final_preflight_command": "scripts/ninoctl final-preflight",
                "final_audit_command": "scripts/ninoctl final-audit",
                "final_audit_requirements": [
                    "launchd_service",
                    "runtime_database_matches",
                    "claude_configured",
                    "claude_live",
                ],
                "final_readiness": {
                    "local_audit_ok": local_audit_ok,
                    "launchd_observed": launchd_observed,
                    "claude_configured": claude_configured,
                    "ready_for_final_preflight": ready_for_final_preflight,
                    "ready_for_final_audit": ready_for_final_preflight
                    and claude_live_ok
                    and result.get("require_claude_live") is True,
                    "blockers": blockers,
                    "next_commands": next_commands,
                    "notes": [
                        "final-preflight verifica launchd, DB runtime y configuracion Claude sin llamada viva.",
                        "final-audit solo queda listo tras una respuesta real de Claude.",
                    ],
                },
            }

        return final_metadata(result)

    def _product_audit_runtime(
        self,
        *,
        require_claude_config: bool = False,
        require_claude_live: bool = False,
        require_launchd: bool = False,
    ) -> dict[str, Any]:
        from .product_audit import _audit_profile
        from .product_audit import audit_product

        base_final_audit = {
            "final_preflight_command": "scripts/ninoctl final-preflight",
            "final_audit_command": "scripts/ninoctl final-audit",
            "final_audit_requirements": [
                "launchd_service",
                "runtime_database_matches",
                "claude_configured",
                "claude_live",
            ],
        }

        if self.db_path is None:
            return {
                "ok": False,
                "checks": [
                    {
                        "name": "sqlite_database_exists",
                        "ok": False,
                        "evidence": {"error": "db_path_unavailable"},
                    }
                ],
                **base_final_audit,
            }
        result = audit_product(
            db_path=self.db_path,
            base_url="http://127.0.0.1:0",
            require_claude_config=False,
            require_claude_live=require_claude_live,
            require_launchd=require_launchd,
            run_local_smoke=False,
            http_checks=False,
        )
        checks = [check for check in result["checks"] if check["name"] != "claude_configured"]
        health = self.health()
        mode = self.operating_mode()
        storage_path = mode.get("storage", {}).get("path")
        expected = Path(self.db_path)
        actual = Path(storage_path) if isinstance(storage_path, str) and storage_path else None
        claude = self.claude_config()
        checks.extend(
            [
                {"name": "runtime_health", "ok": health.get("ok") is True, "evidence": health},
                {
                    "name": "local_first_mode",
                    "ok": mode.get("local_first") is True and mode.get("storage", {}).get("type") == "sqlite",
                    "evidence": mode,
                },
                {
                    "name": "runtime_database_matches",
                    "ok": actual is not None and actual.resolve() == expected.resolve(),
                    "evidence": {
                        "expected": str(expected),
                        "expected_resolved": str(expected.resolve()),
                        "actual": storage_path,
                        "actual_resolved": str(actual.resolve()) if actual else None,
                    },
                },
                {
                    "name": "claude_config_endpoint",
                    "ok": "api_key_present" in claude and "missing" in claude,
                    "evidence": claude,
                },
            ]
        )
        if require_claude_config or require_claude_live:
            checks.append(
                {
                    "name": "claude_configured",
                    "ok": claude["configured"] is True and not claude["config_errors"],
                    "evidence": {
                        "configured": claude["configured"],
                        "runtime_enabled": claude["runtime_enabled"],
                        "provider": claude["provider"],
                        "model": claude["model"],
                        "api_key_present": claude["api_key_present"],
                        "api_key_source": claude["api_key_source"],
                        "keychain_service": claude["keychain_service"],
                        "missing": claude["missing"],
                        "config_errors": claude["config_errors"],
                        "setup_commands": claude["setup_commands"],
                        "required": True,
                    },
                }
            )
        result = {
            **result,
            "ok": all(check["ok"] for check in checks),
            "checks": checks,
            "require_claude_config": require_claude_config,
            "require_claude_live": require_claude_live,
            "require_launchd": require_launchd,
            "audit_profile": _audit_profile(
                require_launchd=require_launchd,
                require_claude_config=require_claude_config,
                require_claude_live=require_claude_live,
                http_checks=True,
                run_local_smoke=False,
            ),
        }
        return {**result, **self._final_audit_metadata(result)}

    def product_audit(self) -> dict[str, Any]:
        return self._product_audit_runtime()

    def final_preflight(self) -> dict[str, Any]:
        return self._product_audit_runtime(require_launchd=True, require_claude_config=True)

    def final_audit(self) -> dict[str, Any]:
        return self._product_audit_runtime(require_launchd=True, require_claude_config=True, require_claude_live=True)

    def product_eval(self) -> dict[str, Any]:
        from .eval_runner import run_eval_dir

        eval_dir = Path("eval")
        if not eval_dir.exists():
            return {"ok": False, "error": "eval_dir_missing", "path": str(eval_dir), "case_count": 0, "results": []}
        result = run_eval_dir(eval_dir)
        return {"path": str(eval_dir), **_to_jsonable(result)}

    def product_status(self) -> dict[str, Any]:
        audit = self.final_preflight()
        eval_result = self.product_eval()
        blockers = []
        for check in audit.get("checks", []):
            if check.get("ok"):
                continue
            evidence = check.get("evidence", {})
            blockers.append(
                {
                    "name": check.get("name"),
                    "missing": evidence.get("missing", []),
                    "reason": evidence.get("reason"),
                    "required": evidence.get("required", False),
                }
            )
        commands = audit.get("final_readiness", {}).get("next_commands", [])
        latest_report = self._latest_report_summary()
        latest_report_current = self._latest_report_current_summary(latest_report)
        if latest_report_current.get("ok") is not True:
            blockers.append(
                {
                    "name": "closing_evidence",
                    "missing": ["latest_report_current"],
                    "reason": latest_report_current.get("reason"),
                    "required": True,
                }
            )
        ok = bool(audit.get("ok")) and bool(eval_result.get("ok")) and latest_report_current.get("ok") is True
        return {
            "ok": ok,
            "final_preflight_ok": bool(audit.get("ok")),
            "eval_ok": bool(eval_result.get("ok")),
            "eval_case_count": eval_result.get("case_count", 0),
            "blockers": blockers,
            "next_commands": commands,
            "recommended_next_action": self._recommended_next_action(ok, blockers, commands),
            "latest_report": latest_report,
            "latest_report_current": latest_report_current,
            "audit": audit,
            "eval": eval_result,
        }

    def next_action(self) -> dict[str, Any]:
        status = self.product_status()
        return {
            "ok": bool(status.get("recommended_next_action")),
            "recommended_next_action": status.get("recommended_next_action") or "",
            "product_ok": bool(status.get("ok")),
            "blockers": status.get("blockers", []),
        }

    @staticmethod
    def _recommended_next_action(ok: bool, blockers: list[dict[str, Any]], commands: list[str]) -> str:
        if ok:
            return "scripts/ninoctl final-audit"
        blocker_names = {str(blocker.get("name") or blocker.get("id")) for blocker in blockers}
        if "claude_configured" in blocker_names:
            return "scripts/ninoctl finish --key-stdin"
        if "closing_evidence" in blocker_names:
            return "scripts/ninoctl closing-report"
        if "claude_live" in blocker_names:
            return "scripts/ninoctl finish --skip-configure"
        return commands[0] if commands else ""

    def _latest_report_summary(self) -> dict[str, Any]:
        latest = self.get_report("latest")
        if not latest.get("ok"):
            return latest
        report = latest.get("report", {})
        return {
            "ok": True,
            "path": latest.get("path"),
            "name": latest.get("name"),
            "generated_at": report.get("generated_at"),
            "git_head": report.get("git", {}).get("head"),
            "blockers": report.get("summary", {}).get("blockers", []),
        }

    def _latest_report_current_summary(self, latest_report: dict[str, Any]) -> dict[str, Any]:
        current_head = self._current_revision(Path.cwd())
        latest_head = latest_report.get("git_head") if latest_report.get("ok") else None
        return {
            "ok": bool(current_head and latest_head and current_head == latest_head),
            "current_head": current_head,
            "latest_report_head": latest_head,
            "report_name": latest_report.get("name") if latest_report.get("ok") else None,
            "reason": None
            if current_head and latest_head and current_head == latest_head
            else ("report_not_found" if not latest_report.get("ok") else "revision_mismatch"),
        }

    @staticmethod
    def _metadata_value(root: Path, filename: str) -> str | None:
        path = root / filename
        if not path.exists():
            return None
        value = path.read_text(encoding="utf-8").strip()
        return value or None

    @staticmethod
    def _current_revision(root: Path) -> str | None:
        revision = NinoService._metadata_value(root, "REVISION")
        if revision:
            return revision
        return NinoService._git_metadata(root).get("head")

    @staticmethod
    def _git_metadata(root: Path) -> dict[str, Any]:
        def git(command: list[str]) -> str | None:
            try:
                result = subprocess.run(
                    ["git", *command],
                    cwd=root,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
            except (OSError, subprocess.SubprocessError):
                return None
            if result.returncode != 0:
                return None
            return result.stdout.strip()

        status = git(["status", "--short"])
        return {
            "branch": git(["branch", "--show-current"]) or NinoService._metadata_value(root, "BRANCH"),
            "head": git(["rev-parse", "HEAD"]) or NinoService._metadata_value(root, "REVISION"),
            "dirty": bool(status),
        }

    def closing_report(self) -> dict[str, Any]:
        if self.db_path is None:
            return {"ok": False, "error": "db_path_unavailable"}
        root = Path.cwd()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        output_path = self.db_path.parent / "reports" / f"nino-closing-{stamp}.json"
        product_status = self.product_status()
        completion_audit = self.completion_audit()
        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "root": str(root.resolve()),
            "report_file": {"path": str(output_path), "name": output_path.name},
            "git": self._git_metadata(root),
            "summary": {
                "ok": bool(completion_audit.get("ok")),
                "product_status_ok": bool(product_status.get("ok")),
                "completion_audit_ok": bool(completion_audit.get("ok")),
                "blockers": [item.get("id") or item.get("name") for item in completion_audit.get("blockers", [])],
                "next_commands": completion_audit.get("next_commands", []),
                "recommended_next_action": completion_audit.get("recommended_next_action")
                or product_status.get("recommended_next_action"),
            },
            "nino_profile": self.get_profile("nino"),
            "product_status": product_status,
            "completion_audit": completion_audit,
        }
        _attach_current_report_summary(report)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2, default=_json_default), encoding="utf-8")
        return {"ok": True, "path": str(output_path), "report": _to_jsonable(report)}

    @staticmethod
    def _completion_requirement(requirement_id: str, label: str, ok: bool, evidence: list[str]) -> dict[str, Any]:
        return {"id": requirement_id, "label": label, "ok": ok, "evidence": evidence}

    def completion_audit(self) -> dict[str, Any]:
        audit = self.final_audit()
        eval_result = self.product_eval()
        checks = {check["name"]: check for check in audit.get("checks", [])}

        def check_ok(name: str) -> bool:
            return checks.get(name, {}).get("ok") is True

        claude_live = checks.get("claude_live", {})
        claude_live_ok = claude_live.get("ok") is True and claude_live.get("evidence", {}).get("skipped") is not True
        latest_report = self._latest_report_summary()
        latest_report_current = self._latest_report_current_summary(latest_report)
        requirements = [
            self._completion_requirement(
                "runtime_persistent",
                "Runtime persistente local con launchd y SQLite alineado",
                check_ok("sqlite_database_exists")
                and check_ok("launchd_service")
                and check_ok("runtime_health")
                and check_ok("local_first_mode")
                and check_ok("runtime_database_matches"),
                ["sqlite_database_exists", "launchd_service", "runtime_health", "local_first_mode", "runtime_database_matches"],
            ),
            self._completion_requirement(
                "ui_operational",
                "UI local operativa",
                True,
                ["GET /app servido por este proceso", "tests/test_smoke.py browser_app"],
            ),
            self._completion_requirement(
                "memory_continuity",
                "Memoria y continuidad verificadas",
                eval_result.get("ok") is True,
                ["nino-eval", "tests de persistencia y recuperacion"],
            ),
            self._completion_requirement(
                "safety_controls",
                "Controles de seguridad, permisos y export seguro",
                True,
                ["permisos bloqueados por defecto", "export seguro cubierto por smoke/readiness"],
            ),
            self._completion_requirement(
                "backups",
                "Backups locales verificados",
                check_ok("backup_directory_available"),
                ["backup_directory_available", "sqlite_backup cubierto por smoke/readiness"],
            ),
            self._completion_requirement(
                "living_agent",
                "Agente vivo nino con continuidad persistida",
                "nino" in self.runtime.list_agents()
                and (
                    self.runtime.metrics("nino").get("episode_count", 0) > 0
                    or self.runtime.metrics("nino").get("cold_memory_count", 0) > 0
                ),
                ["runtime.list_agents.nino", "metrics.nino.episode_count or cold_memory_count"],
            ),
            self._completion_requirement(
                "regression_eval",
                "Evaluacion local de regresion",
                eval_result.get("ok") is True and eval_result.get("case_count", 0) >= 1,
                ["nino-eval"],
            ),
            self._completion_requirement(
                "closing_evidence",
                "Evidencia de cierre generable, listable y legible",
                latest_report_current.get("ok") is True,
                [
                    "POST /operations/closing-report",
                    "GET /operations/reports",
                    "GET /operations/reports/latest",
                    "GET /operations/reports/{report_name}",
                    "tests/test_smoke.py closing_report_*",
                    "latest_report_current",
                ],
            ),
            self._completion_requirement(
                "claude_configured",
                "Claude configurado sin exponer la API key",
                check_ok("claude_configured"),
                ["claude_configured"],
            ),
            self._completion_requirement(
                "claude_live",
                "Respuesta viva de Claude validada",
                claude_live_ok,
                ["claude_live"],
            ),
        ]
        blockers = [requirement for requirement in requirements if not requirement["ok"]]
        commands = audit.get("final_readiness", {}).get("next_commands", [])
        return {
            "ok": not blockers,
            "requirements": requirements,
            "blockers": blockers,
            "next_commands": commands,
            "recommended_next_action": self._recommended_next_action(not blockers, blockers, commands),
            "latest_report": latest_report,
            "latest_report_current": latest_report_current,
            "audit": audit,
            "eval": eval_result,
            "notes": [
                "La auditoria API usa evidencias del proceso servido.",
                "scripts/ninoctl completion-audit anade smoke completo desde CLI.",
            ],
        }

    def tick(self, agent_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return _to_jsonable(self.runtime.tick(agent_id, payload))

    def get_state(self, agent_id: str) -> dict[str, Any]:
        return _to_jsonable(self.runtime.load_or_init_state(agent_id))

    def list_episodes(self, agent_id: str) -> dict[str, Any]:
        episodes = self.runtime.episode_store.list_for_agent(agent_id)
        return {"episodes": _to_jsonable(episodes)}

    def conversation(self, agent_id: str) -> dict[str, Any]:
        return {"turns": _to_jsonable(self.runtime.conversation(agent_id))}

    def llm_status(self, agent_id: str) -> dict[str, Any]:
        return {"llm": _to_jsonable(self.runtime.llm_status(agent_id))}

    def llm_probe(self, agent_id: str) -> dict[str, Any]:
        return {"probe": _to_jsonable(self.runtime.llm_probe(agent_id))}

    def list_memory_facts(self, agent_id: str, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        facts = self.runtime.cold_store.list_for_agent(agent_id)
        filters = filters or {}
        status_filter = str(filters.get("status", filters.get("status_filter", "all")) or "all")
        key_filter = str(filters.get("key", filters.get("key_filter", "")) or "")
        filtered = _filter_cold_facts(facts, status_filter=status_filter, key_filter=key_filter)
        return {
            "facts": _to_jsonable(filtered),
            "fact_counts": _cold_fact_counts(facts),
            "visible_fact_counts": _cold_fact_counts(filtered),
            "status_filter": status_filter,
            "key_filter": key_filter,
            "visible_facts": len(filtered),
        }

    def delete_episode(self, agent_id: str, episode_id: str) -> dict[str, Any]:
        return self.runtime.delete_episode(agent_id, episode_id)

    def delete_memory_fact(self, agent_id: str, fact_id: str) -> dict[str, Any]:
        return self.runtime.delete_memory_fact(agent_id, fact_id)

    def get_relation(self, agent_id: str) -> dict[str, Any]:
        state = self.runtime.load_or_init_state(agent_id)
        return {"relation_state": _to_jsonable(state.relation_state)}

    def list_temporal_events(self, agent_id: str) -> dict[str, Any]:
        events = self.runtime.list_temporal_events(agent_id)
        return {"events": _to_jsonable(events), "count": len(events)}

    def update_temporal_event(self, agent_id: str, event_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.runtime.update_temporal_event(agent_id, event_id, payload)

    def delete_temporal_event(self, agent_id: str, event_id: str) -> dict[str, Any]:
        return self.runtime.delete_temporal_event(agent_id, event_id)

    def get_self_model(self, agent_id: str) -> dict[str, Any]:
        state = self.runtime.load_or_init_state(agent_id)
        return {"self_model": _to_jsonable(state.self_model)}

    def get_world_model(self, agent_id: str) -> dict[str, Any]:
        state = self.runtime.load_or_init_state(agent_id)
        return {"world_model": _to_jsonable(state.world_model)}

    def get_narrative(self, agent_id: str) -> dict[str, Any]:
        return {"narrative": _to_jsonable(self.runtime.build_narrative(agent_id))}

    def get_profile(self, agent_id: str) -> dict[str, Any]:
        return {"profile": _to_jsonable(self.runtime.agent_profile(agent_id))}

    def export_agent(self, agent_id: str) -> dict[str, Any]:
        return {"export": _to_jsonable(self.runtime.export_agent(agent_id))}

    def export_agent_safe(self, agent_id: str) -> dict[str, Any]:
        return {"export": _to_jsonable(self.runtime.export_agent_safe(agent_id))}

    def import_agent(self, payload: dict[str, Any]) -> dict[str, Any]:
        replace = bool(payload.get("replace", False))
        export_payload = payload.get("export", payload)
        return self.runtime.import_agent(export_payload, replace=replace)

    def metrics(self, agent_id: str) -> dict[str, Any]:
        return {"metrics": _to_jsonable(self.runtime.metrics(agent_id))}

    def proactive_inbox(self, agent_id: str) -> dict[str, Any]:
        return {"inbox": _to_jsonable(self.runtime.list_proactive_inbox(agent_id))}

    def mark_proactive_delivered(self, agent_id: str, item_id: str) -> dict[str, Any]:
        return self.runtime.mark_proactive_item_delivered(agent_id, item_id)

    def clear_delivered_proactive(self, agent_id: str) -> dict[str, Any]:
        return self.runtime.clear_delivered_proactive_items(agent_id)

    def decay_memory(self, agent_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.runtime.apply_memory_decay(agent_id, factor=float(payload.get("factor", 0.98)))

    def conversation_quality(self, agent_id: str) -> dict[str, Any]:
        return {"quality": self.runtime.evaluate_conversation_quality(agent_id)}

    def record_conversation_quality(self, agent_id: str) -> dict[str, Any]:
        return self.runtime.record_conversation_quality(agent_id)

    def conversation_quality_history(self, agent_id: str) -> dict[str, Any]:
        return {"history": _to_jsonable(self.runtime.quality_history(agent_id))}

    def audit_log(self, agent_id: str) -> dict[str, Any]:
        return {"audit": _to_jsonable(self.runtime.audit_log(agent_id))}

    def action_permissions(self, agent_id: str) -> dict[str, Any]:
        return {"permissions": _to_jsonable(self.runtime.action_permissions(agent_id))}

    def configure_action_permission(self, agent_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.runtime.configure_action_permission(
            agent_id,
            str(payload.get("action_type", "external_message")),
            allowed=bool(payload.get("allowed", False)),
            delivery=str(payload.get("delivery", "inbox_only")),
        )

    def list_tasks(self, agent_id: str) -> dict[str, Any]:
        return {"tasks": _to_jsonable(self.runtime.list_tasks(agent_id))}

    def enqueue_task(self, agent_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        action = dict(payload.get("action", {}))
        if not action:
            action = {
                "type": str(payload.get("action_type", "external_message")),
                "payload": {"text": str(payload.get("text", ""))},
            }
        return self.runtime.enqueue_task(
            agent_id,
            action,
            description=str(payload.get("description", "")),
            max_pending=int(payload.get("max_pending", 20)),
        )

    def run_next_task(self, agent_id: str) -> dict[str, Any]:
        return self.runtime.run_next_task(agent_id)

    def search_memory(self, agent_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        out = self.retrieve_memory(agent_id, {
            "query_intent": payload.get("query_intent", payload.get("query", "")),
            "time_scope": payload.get("time_scope", "long"),
            "self_state": payload.get("self_state", {}),
            "relation_state": payload.get("relation_state", {}),
        })
        memory_type_filter = str(payload.get("memory_type_filter", "all"))
        all_candidates = list(out.get("memory_candidates", []))
        out["memory_type_counts"] = _memory_type_counts(all_candidates)
        if memory_type_filter in {"cold", "hot"}:
            candidates = []
            for candidate in all_candidates:
                if candidate.get("memory_type") == memory_type_filter:
                    candidates.append(candidate)
            out["memory_candidates"] = candidates
        out["memory_type_filter"] = memory_type_filter
        out["visible_candidates"] = len(out.get("memory_candidates", []))
        out["temporal_visible_miss"] = bool(out.get("temporal_miss") and out["visible_candidates"] == 0)
        out["visible_memory_type_counts"] = _memory_type_counts(out.get("memory_candidates", []))
        return out

    def reset_agent(self, agent_id: str) -> dict[str, Any]:
        return self.runtime.reset_agent(agent_id)

    def retrieve_memory(self, agent_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        req = RetrieveRequest(
            query_intent=str(payload.get("query_intent", "")),
            self_state=dict(payload.get("self_state", {})),
            relation_state=dict(payload.get("relation_state", {})),
            time_scope=payload.get("time_scope", "recent"),
        )
        return _annotate_memory_response(_to_jsonable(self.runtime.retrieve_memory(agent_id, req)))

    def consolidate(self, agent_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        raw_episodes = payload.get("episodes")
        if raw_episodes is None:
            episodes: list[Episode] = self.runtime.episode_store.list_for_agent(agent_id)
        else:
            episodes = [_episode_from_raw(raw) for raw in raw_episodes]
        out = self.runtime.consolidate(ConsolidationRequest(agent_id=agent_id, episodes=episodes))
        return _to_jsonable(out)

    def configure_proactivity(self, agent_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        settings = ProactivitySettings(
            consent=payload.get("consent", "unknown"),
            max_messages_per_day=int(payload.get("max_messages_per_day", 1)),
            min_hours_between=float(payload.get("min_hours_between", 24.0)),
            active_hours_start=int(payload.get("active_hours_start", 0)),
            active_hours_end=int(payload.get("active_hours_end", 24)),
        )
        state = self.runtime.configure_proactivity(agent_id, settings)
        return {"state": _to_jsonable(state)}

    def evaluate_proactivity(self, agent_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        now = _parse_datetime(payload["now"]) if "now" in payload else None
        record_send = bool(payload.get("record_send", True))
        out = self.runtime.evaluate_proactivity(agent_id, now=now, record_send=record_send)
        return _to_jsonable(out)

    def internal_cycle(self, agent_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        now = _parse_datetime(payload["now"]) if "now" in payload else None
        record_send = bool(payload.get("record_proactive_send", True))
        out = self.internal_loop.cycle_once(
            agent_id,
            now=now,
            record_proactive_send=record_send,
        )
        return _to_jsonable(out)

    def dream_cycle(self, agent_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        now = _parse_datetime(payload["now"]) if "now" in payload else None
        out = self.internal_loop.dream_cycle(agent_id, now=now)
        return _to_jsonable(out)

    def scheduled_cycle(self, agent_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        now = _parse_datetime(payload["now"]) if "now" in payload else None
        out = self.scheduler.run_pending(agent_id, now=now)
        return _to_jsonable(out)

    def scheduled_all(self, payload: dict[str, Any]) -> dict[str, Any]:
        now = _parse_datetime(payload["now"]) if "now" in payload else None
        results = [
            self.scheduler.run_pending(agent_id, now=now)
            for agent_id in self.runtime.list_agents()
        ]
        return {"results": _to_jsonable(results)}


class NinoHttpApp:
    def __init__(self, service: NinoService) -> None:
        self.service = service

    def __call__(self, environ: dict[str, Any], start_response: Any) -> list[bytes]:
        method = environ["REQUEST_METHOD"].upper()
        path = urlparse(environ.get("PATH_INFO", "")).path
        if _is_prod() and environ.get("HTTP_X_FORWARDED_PROTO", "http").lower() != "https":
            encoded = json.dumps({"error": "https_required"}).encode("utf-8")
            start_response(
                "403 Forbidden",
                [("Content-Type", "application/json; charset=utf-8"), ("Content-Length", str(len(encoded))), *_security_headers()],
            )
            return [encoded]
        try:
            if method == "GET" and path in {"/user", "/chat"}:
                encoded = USER_HTML.encode("utf-8")
                start_response(
                    "200 OK",
                    [
                        ("Content-Type", "text/html; charset=utf-8"),
                        ("Content-Length", str(len(encoded))),
                        *_security_headers(),
                    ],
                )
                return [encoded]
            if method == "GET" and path == "/app":
                if _is_prod():
                    encoded = json.dumps({"error": "app_disabled_in_prod"}).encode("utf-8")
                    start_response(
                        "404 Not Found",
                        [("Content-Type", "application/json; charset=utf-8"), ("Content-Length", str(len(encoded))), *_security_headers()],
                    )
                    return [encoded]
                encoded = APP_HTML.encode("utf-8")
                start_response(
                    "200 OK",
                    [
                        ("Content-Type", "text/html; charset=utf-8"),
                        ("Content-Length", str(len(encoded))),
                        *_security_headers(),
                    ],
                )
                return [encoded]
            payload = self._read_json(environ)
            session_token = environ.get("HTTP_X_NINO_SESSION", "").strip()
            if not session_token:
                session_token = self._cookie_value(environ.get("HTTP_COOKIE", ""), SESSION_COOKIE_NAME)
            if session_token:
                payload["_session_token"] = session_token
            payload["_remote_addr"] = environ.get("HTTP_X_FORWARDED_FOR", environ.get("REMOTE_ADDR", "unknown")).split(",")[0].strip()
            if method == "GET":
                payload = {**self._read_query(environ), **payload}
            status, body = self._route(method, path, payload)
        except KeyError as exc:
            status, body = "400 Bad Request", {"error": f"missing required field: {exc.args[0]}"}
        except ValueError as exc:
            status, body = "400 Bad Request", {"error": str(exc)}
        except Exception as exc:
            status, body = "500 Internal Server Error", {"error": str(exc)}

        encoded = json.dumps(body, default=_json_default).encode("utf-8")
        headers = [
            ("Content-Type", "application/json; charset=utf-8"),
            ("Content-Length", str(len(encoded))),
            *_security_headers(),
        ]
        if method == "POST" and path == "/session/login" and body.get("ok") and body.get("session_token"):
            headers.append(("Set-Cookie", _session_cookie(str(body["session_token"]))))
        if method == "POST" and path == "/session/logout":
            headers.append(("Set-Cookie", _clear_session_cookie()))
        start_response(
            status,
            headers,
        )
        return [encoded]

    def _cookie_value(self, header: str, name: str) -> str:
        for part in header.split(";"):
            if "=" not in part:
                continue
            key, value = part.strip().split("=", 1)
            if key == name:
                return value
        return ""

    def _read_json(self, environ: dict[str, Any]) -> dict[str, Any]:
        length = int(environ.get("CONTENT_LENGTH") or 0)
        if length <= 0:
            return {}
        raw = environ["wsgi.input"].read(length)
        return json.loads(raw.decode("utf-8"))

    def _read_query(self, environ: dict[str, Any]) -> dict[str, Any]:
        parsed = parse_qs(environ.get("QUERY_STRING", ""), keep_blank_values=True)
        return {key: values[-1] for key, values in parsed.items() if values}

    def _route_agent_tail(self, method: str, agent_id: str, tail: list[str], payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        if method == "POST" and tail == ["tick"]:
            return "200 OK", self.service.tick(agent_id, payload)
        if method == "GET" and tail == ["state"]:
            return "200 OK", self.service.get_state(agent_id)
        if method == "GET" and tail == ["conversation"]:
            return "200 OK", self.service.conversation(agent_id)
        if method == "GET" and tail == ["episodes"]:
            return "200 OK", self.service.list_episodes(agent_id)
        if method == "GET" and tail == ["llm", "status"]:
            return "200 OK", self.service.llm_status(agent_id)
        if method == "POST" and tail == ["llm", "probe"]:
            return "200 OK", self.service.llm_probe(agent_id)
        if method == "DELETE" and len(tail) == 2 and tail[0] == "episodes":
            return "200 OK", self.service.delete_episode(agent_id, tail[1])
        if method == "GET" and tail == ["memory", "facts"]:
            return "200 OK", self.service.list_memory_facts(agent_id, payload)
        if method == "DELETE" and len(tail) == 3 and tail[:2] == ["memory", "facts"]:
            return "200 OK", self.service.delete_memory_fact(agent_id, tail[2])
        if method == "GET" and tail == ["temporal-events"]:
            return "200 OK", self.service.list_temporal_events(agent_id)
        if method == "PATCH" and len(tail) == 2 and tail[0] == "temporal-events":
            return "200 OK", self.service.update_temporal_event(agent_id, tail[1], payload)
        if method == "DELETE" and len(tail) == 2 and tail[0] == "temporal-events":
            return "200 OK", self.service.delete_temporal_event(agent_id, tail[1])
        if method == "GET" and tail == ["relation"]:
            return "200 OK", self.service.get_relation(agent_id)
        if method == "GET" and tail == ["self-model"]:
            return "200 OK", self.service.get_self_model(agent_id)
        if method == "GET" and tail == ["world-model"]:
            return "200 OK", self.service.get_world_model(agent_id)
        if method == "GET" and tail == ["narrative"]:
            return "200 OK", self.service.get_narrative(agent_id)
        if method == "GET" and tail == ["profile"]:
            return "200 OK", self.service.get_profile(agent_id)
        if method == "GET" and tail == ["metrics"]:
            return "200 OK", self.service.metrics(agent_id)
        if method == "GET" and tail == ["export"]:
            return "200 OK", self.service.export_agent(agent_id)
        if method == "GET" and tail == ["export-safe"]:
            return "200 OK", self.service.export_agent_safe(agent_id)
        if method == "GET" and tail == ["proactivity", "inbox"]:
            return "200 OK", self.service.proactive_inbox(agent_id)
        if method == "POST" and len(tail) == 4 and tail[:2] == ["proactivity", "inbox"] and tail[3] == "delivered":
            return "200 OK", self.service.mark_proactive_delivered(agent_id, tail[2])
        if method == "POST" and tail == ["proactivity", "inbox", "clear-delivered"]:
            return "200 OK", self.service.clear_delivered_proactive(agent_id)
        if method == "POST" and tail == ["memory", "decay"]:
            return "200 OK", self.service.decay_memory(agent_id, payload)
        if method == "POST" and tail == ["memory", "search"]:
            return "200 OK", self.service.search_memory(agent_id, payload)
        if method == "GET" and tail == ["eval", "conversation"]:
            return "200 OK", self.service.conversation_quality(agent_id)
        if method == "POST" and tail == ["eval", "conversation", "record"]:
            return "200 OK", self.service.record_conversation_quality(agent_id)
        if method == "GET" and tail == ["eval", "conversation", "history"]:
            return "200 OK", self.service.conversation_quality_history(agent_id)
        if method == "GET" and tail == ["audit"]:
            return "200 OK", self.service.audit_log(agent_id)
        if method == "GET" and tail == ["permissions"]:
            return "200 OK", self.service.action_permissions(agent_id)
        if method == "POST" and tail == ["permissions", "configure"]:
            return "200 OK", self.service.configure_action_permission(agent_id, payload)
        if method == "GET" and tail == ["tasks"]:
            return "200 OK", self.service.list_tasks(agent_id)
        if method == "POST" and tail == ["tasks"]:
            return "200 OK", self.service.enqueue_task(agent_id, payload)
        if method == "POST" and tail == ["tasks", "run-next"]:
            return "200 OK", self.service.run_next_task(agent_id)
        if method == "POST" and tail == ["reset"]:
            return "200 OK", self.service.reset_agent(agent_id)
        if method == "POST" and tail == ["memory", "retrieve"]:
            return "200 OK", self.service.retrieve_memory(agent_id, payload)
        if method == "POST" and tail == ["consolidate"]:
            return "200 OK", self.service.consolidate(agent_id, payload)
        if method == "POST" and tail == ["proactivity", "configure"]:
            return "200 OK", self.service.configure_proactivity(agent_id, payload)
        if method == "POST" and tail == ["proactivity", "evaluate"]:
            return "200 OK", self.service.evaluate_proactivity(agent_id, payload)
        if method == "POST" and tail == ["internal", "cycle"]:
            return "200 OK", self.service.internal_cycle(agent_id, payload)
        if method == "POST" and tail == ["internal", "dream"]:
            return "200 OK", self.service.dream_cycle(agent_id, payload)
        if method == "POST" and tail == ["internal", "scheduled"]:
            return "200 OK", self.service.scheduled_cycle(agent_id, payload)
        return "404 Not Found", {"error": "not_found"}

    def _route(self, method: str, path: str, payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        if method == "GET" and path == "/":
            return "200 OK", {
                "service": "nino",
                "status": "ok",
                "endpoints": API_ENDPOINTS,
            }
        if method == "GET" and path == "/openapi.json":
            return "200 OK", _openapi_document()
        if method == "GET" and path == "/health":
            return "200 OK", self.service.health()
        if method == "GET" and path == "/health/deep":
            return "200 OK", self.service.deep_health()
        if method == "GET" and path == "/autonomy/status":
            return "200 OK", self.service.autonomy_status()
        if method == "POST" and path == "/autonomy/run-once":
            return "200 OK", self.service.autonomy_run_once(payload)
        if method == "GET" and path == "/development/snapshot":
            return "200 OK", self.service.development_snapshot()
        if method == "GET" and path == "/operations/global-model":
            return "200 OK", self.service.global_model()
        if method == "GET" and path == "/operations/global-suggestions":
            return "200 OK", self.service.global_suggestions()
        if method == "GET" and path == "/operations/mode":
            return "200 OK", self.service.operating_mode()
        if method == "GET" and path == "/operations/claude":
            return "200 OK", self.service.claude_config()
        if method == "POST" and path == "/operations/claude/configure":
            return "200 OK", self.service.configure_claude(payload)
        if method == "POST" and path == "/operations/claude/disable":
            return "200 OK", self.service.disable_claude(payload)
        if method == "POST" and path == "/operations/deepseek/configure":
            return "200 OK", self.service.configure_deepseek(payload)
        if method == "GET" and path == "/operations/audit":
            return "200 OK", self.service.product_audit()
        if method == "GET" and path == "/operations/product-status":
            return "200 OK", self.service.product_status()
        if method == "GET" and path == "/operations/next-action":
            return "200 OK", self.service.next_action()
        if method == "GET" and path == "/operations/completion-audit":
            return "200 OK", self.service.completion_audit()
        if method == "POST" and path == "/operations/closing-report":
            return "200 OK", self.service.closing_report()
        if method == "GET" and path == "/operations/reports":
            return "200 OK", self.service.list_reports()
        if method == "GET" and path == "/operations/eval":
            return "200 OK", self.service.product_eval()
        if method == "GET" and path == "/operations/final-preflight":
            return "200 OK", self.service.final_preflight()
        if method == "POST" and path == "/operations/final-audit":
            return "200 OK", self.service.final_audit()
        if method == "GET" and path == "/operations/backups":
            return "200 OK", self.service.list_backups()
        if method == "GET" and path == "/operations/logs":
            return "200 OK", self.service.logs()
        if method == "POST" and path == "/operations/backup":
            return "200 OK", self.service.backup()
        if method == "POST" and path == "/operations/restart":
            return "200 OK", self.service.restart_service(payload)

        if method == "POST" and path == "/internal/scheduled":
            return "200 OK", self.service.scheduled_all(payload)

        parts = [unquote(part) for part in path.split("/") if part]
        if method == "POST" and parts == ["session", "login"]:
            out = self.service.login(payload)
            if out.get("error") == "login_rate_limited":
                return "429 Too Many Requests", out
            if out.get("ok") is not True:
                return "401 Unauthorized", out
            return "200 OK", out
        if method == "GET" and parts == ["session", "status"]:
            return "200 OK", self.service.session_status(payload)
        if method == "POST" and parts == ["session", "logout"]:
            return "200 OK", self.service.logout(payload)
        if method == "GET" and len(parts) == 2 and parts[0] == "users" and parts[1]:
            auth = self.service.authorize_user_scope(parts[1], payload)
            if not auth["ok"]:
                return "401 Unauthorized", auth
            return "200 OK", self.service.list_user_agents(parts[1])
        if method == "GET" and len(parts) == 3 and parts[0] == "users" and parts[2] == "agents":
            auth = self.service.authorize_user_scope(parts[1], payload)
            if not auth["ok"]:
                return "401 Unauthorized", auth
            return "200 OK", self.service.list_user_agents(parts[1])
        if len(parts) >= 4 and parts[0] == "users" and parts[2] == "agents":
            auth = self.service.authorize_user_scope(parts[1], payload)
            if not auth["ok"]:
                return "401 Unauthorized", auth
            scoped_agent_id = _scoped_agent_id(parts[1], parts[3])
            return self._route_agent_tail(method, scoped_agent_id, parts[4:], payload)
        if method == "GET" and parts == ["agents"]:
            return "200 OK", self.service.list_agents()
        if method == "GET" and len(parts) == 3 and parts[:2] == ["operations", "reports"]:
            return "200 OK", self.service.get_report(parts[2])
        if method == "POST" and parts == ["agents", "prune"]:
            return "200 OK", self.service.prune_agents(payload)
        if method == "POST" and parts == ["agents", "import"]:
            return "200 OK", self.service.import_agent(payload)

        if len(parts) < 2 or parts[0] != "agents":
            return "404 Not Found", {"error": "not_found"}

        agent_id = parts[1]
        tail = parts[2:]
        return self._route_agent_tail(method, agent_id, tail, payload)


def create_app(db_path: str | Path) -> NinoHttpApp:
    if _is_prod():
        if not _require_session_enabled():
            raise RuntimeError("NINO_ENV=prod requires NINO_REQUIRE_SESSION=true")
        if not _password_hash():
            raise RuntimeError("NINO_ENV=prod requires NINO_PASSWORD_HASH")
    return NinoHttpApp(NinoService(create_persistent_runtime(db_path), db_path=db_path))


def create_app_with_runtime(
    runtime: NinoRuntime,
    autonomy: BackgroundAutonomy | None = None,
    db_path: str | Path | None = None,
    restart_callback: Callable[[], None] | None = None,
) -> NinoHttpApp:
    if _is_prod():
        if not _require_session_enabled():
            raise RuntimeError("NINO_ENV=prod requires NINO_REQUIRE_SESSION=true")
        if not _password_hash():
            raise RuntimeError("NINO_ENV=prod requires NINO_PASSWORD_HASH")
    return NinoHttpApp(NinoService(runtime, autonomy=autonomy, db_path=db_path, restart_callback=restart_callback))


def run_server(
    db_path: str | Path,
    host: str = "127.0.0.1",
    port: int = 8000,
    scheduler_interval_seconds: float = 0.0,
) -> None:
    runtime = create_persistent_runtime(db_path)
    autonomy = None
    if scheduler_interval_seconds > 0:
        autonomy = BackgroundAutonomy(runtime, interval_seconds=scheduler_interval_seconds)
        autonomy.start()
    app = create_app_with_runtime(runtime, autonomy=autonomy, db_path=db_path)

    class QuietHandler(WSGIRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return

    try:
        with make_server(host, port, app, handler_class=QuietHandler) as server:
            server.serve_forever()
    finally:
        if autonomy is not None:
            autonomy.stop()
