from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime
import json
from pathlib import Path
import sqlite3
from typing import Any
from urllib.parse import urlparse
from wsgiref.simple_server import WSGIRequestHandler, make_server

from .autonomy import BackgroundAutonomy
from .contracts import ConsolidationRequest, ProactivitySettings, RetrieveRequest
from .internal_loop import InternalLoop
from .llm import llm_config_status
from .memory import Episode
from .persistence import create_persistent_runtime
from .runtime import NinoRuntime
from .scheduler import NinoScheduler


APP_HTML = """<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>NIÑO</title>
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
    .topbar { display: grid; grid-template-columns: auto minmax(220px, 360px); align-items: end; gap: 16px; }
    .agent { display: grid; grid-template-columns: 58px minmax(0, 1fr); align-items: center; gap: 8px; }
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
    .muted { color: #667781; font-size: 12px; }
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
        <h1>NIÑO</h1>
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
        <textarea id="text" rows="2" placeholder="Escribe algo para NIÑO"></textarea>
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
        <button id="audit" class="secondary" style="margin-top:8px;width:100%">Auditoría</button>
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
          <input id="memoryQuery" value="sprints" aria-label="buscar memoria">
          <button id="memorySearch" class="secondary">Buscar</button>
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
        <div id="inboxList" class="list"></div>
        <div class="output"><pre id="proactivity">{}</pre></div>
      </div>
      <div class="panel">
        <div class="panelHead">
          <h2>LLM</h2>
          <button id="llmStatus" class="secondary">Estado</button>
        </div>
        <div id="llmSummary" class="muted">Sin comprobar.</div>
        <div class="row">
          <button id="claudeConfig" class="secondary">Config</button>
          <button id="llmProbe" class="secondary">Probar Claude</button>
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
          <button id="mode" class="secondary">Modo</button>
          <button id="reset" class="danger">Reset agente</button>
        </div>
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
    const api = (path, options = {}) => fetch(path, {
      headers: {"Content-Type": "application/json"},
      ...options
    }).then(async (res) => {
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || res.statusText);
      return data;
    });
    const agentPath = (tail) => `/agents/${encodeURIComponent($("agentId").value || "demo")}${tail}`;
    const print = (target, value) => { target.textContent = JSON.stringify(value, null, 2); };
    const status = (text) => { $("status").textContent = text; };
    const fmt = (value) => Number.isFinite(Number(value)) ? Number(value).toFixed(3).replace(/0+$/, "").replace(/[.]$/, "") : "0";
    const clearList = (target) => { target.textContent = ""; };
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
    async function loadLLMStatus() {
      const out = await api(agentPath("/llm/status"));
      const llm = out.llm;
      const mode = llm.enabled ? `Claude · ${llm.model || "modelo no indicado"}` : "Reglas locales";
      const last = llm.last_response?.source ? ` · último origen: ${llm.last_response.source}` : "";
      const error = llm.last_response?.error ? ` · error: ${llm.last_response.error}` : "";
      $("llmSummary").textContent = `${mode}${last}${error}`;
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
    $("send").onclick = async () => {
      const payload = {intent: $("intent").value || "chat", text: $("text").value, salience: 0.7, confidence: 0.9};
      if (!payload.text.trim()) return;
      $("send").disabled = true;
      try {
        addEntry("usuario", payload.text);
        const out = await api(agentPath("/tick"), {method: "POST", body: JSON.stringify(payload)});
        $("text").value = "";
        print($("state"), out);
        await refreshState();
        await loadMemorySearch();
        await loadConversation();
        await loadLLMStatus();
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
      const out = await api("/agents");
      clearList($("agentList"));
      out.agents.forEach((agent) => {
        const item = document.createElement("button");
        item.className = "secondary";
        item.textContent = agent;
        item.onclick = async () => {
          $("agentId").value = agent;
          await refreshState();
          await loadMemorySearch();
          await loadConversation();
        };
        $("agentList").appendChild(item);
      });
      print($("state"), out);
    }
    $("agents").onclick = loadAgents;
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
    };
    $("mode").onclick = async () => print($("state"), await api("/operations/mode"));
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
    $("clearDelivered").onclick = async () => {
      const out = await api(agentPath("/proactivity/inbox/clear-delivered"), {method: "POST", body: "{}"});
      print($("proactivity"), out);
      await loadInbox();
      await refreshState();
    };
    async function loadEpisodes() {
      const out = await api(agentPath("/episodes"));
      clearList($("memoryList"));
      out.episodes.slice().reverse().forEach((episode) => addListItem($("memoryList"), episode.text, `${episode.intent} · salience ${episode.salience}`));
      print($("memory"), out);
    }
    async function loadFacts() {
      const out = await api(agentPath("/memory/facts"));
      clearList($("memoryList"));
      out.facts.forEach((fact) => addListItem($("memoryList"), `${fact.key}: ${fact.value}`, `confidence ${fact.confidence}`));
      print($("memory"), out);
    }
    async function loadMemorySearch() {
      const query = $("memoryQuery").value || "";
      const out = await api(agentPath("/memory/search"), {method: "POST", body: JSON.stringify({query_intent: query, time_scope: "long"})});
      clearList($("memoryList"));
      out.memory_candidates.forEach((candidate) => addListItem($("memoryList"), candidate.statement, `score ${fmt(candidate.score)} · confidence ${fmt(candidate.confidence)}`));
      print($("memory"), out);
    }
    $("episodes").onclick = loadEpisodes;
    $("facts").onclick = loadFacts;
    $("memorySearch").onclick = loadMemorySearch;
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
    $("claudeConfig").onclick = async () => print($("llm"), await api("/operations/claude"));
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
    refreshState()
      .then(loadAgents)
      .then(loadMemorySearch)
      .then(loadConversation)
      .then(loadLLMStatus)
      .then(() => $("permissions").click())
      .then(() => $("tasks").click())
      .catch((err) => status(err.message));
  </script>
</body>
</html>
"""


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if is_dataclass(value):
        return asdict(value)
    raise TypeError(f"{type(value).__name__} is not JSON serializable")


def _to_jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, default=_json_default))


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value)


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
    ) -> None:
        self.runtime = runtime
        self.internal_loop = InternalLoop(runtime)
        self.scheduler = NinoScheduler(runtime)
        self.autonomy = autonomy
        self.db_path = Path(db_path) if db_path is not None else None

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

    def prune_agents(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.runtime.prune_agents(
            prefixes=list(payload.get("prefixes") or []),
            agent_ids=list(payload.get("agent_ids") or []),
            dry_run=bool(payload.get("dry_run", True)),
        )

    def development_snapshot(self) -> dict[str, Any]:
        return {"snapshot": self.runtime.development_snapshot()}

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

    def operating_mode(self) -> dict[str, Any]:
        llm_client = self.runtime.llm_client
        config = llm_config_status()
        return {
            "local_first": True,
            "storage": {
                "type": "sqlite" if self.db_path is not None else "runtime",
                "path": str(self.db_path) if self.db_path is not None else None,
            },
            "external_llm": {
                "enabled": llm_client is not None,
                "provider": "claude" if llm_client is not None else None,
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
            "external_capabilities": ["claude_responses"] if llm_client is not None else [],
        }

    def claude_config(self) -> dict[str, Any]:
        client = self.runtime.llm_client
        config = llm_config_status()
        return {
            "configured": config["enabled"],
            "runtime_enabled": client is not None,
            "provider": "claude" if client is not None else config["provider"],
            "model": getattr(client, "model", None) if client is not None else config["model"],
            "api_key_present": config["api_key_present"],
            "missing": config["missing"],
            "probe_endpoint": "/agents/{agent_id}/llm/probe",
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

    def list_memory_facts(self, agent_id: str) -> dict[str, Any]:
        facts = self.runtime.cold_store.list_for_agent(agent_id)
        return {"facts": _to_jsonable(facts)}

    def delete_episode(self, agent_id: str, episode_id: str) -> dict[str, Any]:
        return self.runtime.delete_episode(agent_id, episode_id)

    def delete_memory_fact(self, agent_id: str, fact_id: str) -> dict[str, Any]:
        return self.runtime.delete_memory_fact(agent_id, fact_id)

    def get_relation(self, agent_id: str) -> dict[str, Any]:
        state = self.runtime.load_or_init_state(agent_id)
        return {"relation_state": _to_jsonable(state.relation_state)}

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
        return self.retrieve_memory(agent_id, {
            "query_intent": payload.get("query_intent", payload.get("query", "")),
            "time_scope": payload.get("time_scope", "long"),
            "self_state": payload.get("self_state", {}),
            "relation_state": payload.get("relation_state", {}),
        })

    def reset_agent(self, agent_id: str) -> dict[str, Any]:
        return self.runtime.reset_agent(agent_id)

    def retrieve_memory(self, agent_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        req = RetrieveRequest(
            query_intent=str(payload.get("query_intent", "")),
            self_state=dict(payload.get("self_state", {})),
            relation_state=dict(payload.get("relation_state", {})),
            time_scope=payload.get("time_scope", "recent"),
        )
        return _to_jsonable(self.runtime.retrieve_memory(agent_id, req))

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
        try:
            if method == "GET" and path == "/app":
                encoded = APP_HTML.encode("utf-8")
                start_response(
                    "200 OK",
                    [
                        ("Content-Type", "text/html; charset=utf-8"),
                        ("Content-Length", str(len(encoded))),
                    ],
                )
                return [encoded]
            payload = self._read_json(environ)
            status, body = self._route(method, path, payload)
        except KeyError as exc:
            status, body = "400 Bad Request", {"error": f"missing required field: {exc.args[0]}"}
        except ValueError as exc:
            status, body = "400 Bad Request", {"error": str(exc)}
        except Exception as exc:
            status, body = "500 Internal Server Error", {"error": str(exc)}

        encoded = json.dumps(body, default=_json_default).encode("utf-8")
        start_response(
            status,
            [
                ("Content-Type", "application/json; charset=utf-8"),
                ("Content-Length", str(len(encoded))),
            ],
        )
        return [encoded]

    def _read_json(self, environ: dict[str, Any]) -> dict[str, Any]:
        length = int(environ.get("CONTENT_LENGTH") or 0)
        if length <= 0:
            return {}
        raw = environ["wsgi.input"].read(length)
        return json.loads(raw.decode("utf-8"))

    def _route(self, method: str, path: str, payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        if method == "GET" and path == "/":
            return "200 OK", {
                "service": "nino",
                "status": "ok",
                "endpoints": [
                    "GET /health",
                    "GET /health/deep",
                "GET /autonomy/status",
                "POST /autonomy/run-once",
                "GET /development/snapshot",
                "GET /operations/mode",
                "GET /operations/claude",
                "POST /operations/backup",
                "GET /agents",
                    "POST /agents/prune",
                    "POST /agents/import",
                    "POST /agents/{agent_id}/tick",
                    "GET /agents/{agent_id}/state",
                    "GET /agents/{agent_id}/conversation",
                    "GET /agents/{agent_id}/episodes",
                    "GET /agents/{agent_id}/llm/status",
                    "POST /agents/{agent_id}/llm/probe",
                    "GET /agents/{agent_id}/memory/facts",
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
                    "POST /agents/{agent_id}/proactivity/configure",
                    "POST /agents/{agent_id}/proactivity/evaluate",
                ],
            }
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
        if method == "GET" and path == "/operations/mode":
            return "200 OK", self.service.operating_mode()
        if method == "GET" and path == "/operations/claude":
            return "200 OK", self.service.claude_config()
        if method == "POST" and path == "/operations/backup":
            return "200 OK", self.service.backup()

        if method == "POST" and path == "/internal/scheduled":
            return "200 OK", self.service.scheduled_all(payload)

        parts = [part for part in path.split("/") if part]
        if method == "GET" and parts == ["agents"]:
            return "200 OK", self.service.list_agents()
        if method == "POST" and parts == ["agents", "prune"]:
            return "200 OK", self.service.prune_agents(payload)
        if method == "POST" and parts == ["agents", "import"]:
            return "200 OK", self.service.import_agent(payload)

        if len(parts) < 2 or parts[0] != "agents":
            return "404 Not Found", {"error": "not_found"}

        agent_id = parts[1]
        tail = parts[2:]

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
            return "200 OK", self.service.list_memory_facts(agent_id)
        if method == "DELETE" and len(tail) == 3 and tail[:2] == ["memory", "facts"]:
            return "200 OK", self.service.delete_memory_fact(agent_id, tail[2])
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


def create_app(db_path: str | Path) -> NinoHttpApp:
    return NinoHttpApp(NinoService(create_persistent_runtime(db_path), db_path=db_path))


def create_app_with_runtime(
    runtime: NinoRuntime,
    autonomy: BackgroundAutonomy | None = None,
    db_path: str | Path | None = None,
) -> NinoHttpApp:
    return NinoHttpApp(NinoService(runtime, autonomy=autonomy, db_path=db_path))


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
