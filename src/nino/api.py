from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from wsgiref.simple_server import WSGIRequestHandler, make_server

from .autonomy import BackgroundAutonomy
from .contracts import ConsolidationRequest, ProactivitySettings, RetrieveRequest
from .internal_loop import InternalLoop
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
      background: #eef2f5;
    }
    * { box-sizing: border-box; }
    body { margin: 0; min-height: 100vh; }
    main {
      display: grid;
      grid-template-columns: minmax(0, 1fr) 360px;
      min-height: 100vh;
    }
    section, aside { padding: 20px; }
    section { display: flex; flex-direction: column; gap: 14px; border-right: 1px solid #cfd8df; }
    h1 { margin: 0; font-size: 20px; letter-spacing: 0; }
    h2 { margin: 0 0 10px; font-size: 14px; letter-spacing: 0; }
    .topbar { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
    .agent { display: flex; align-items: center; gap: 8px; }
    input, textarea, select, button {
      font: inherit;
      border: 1px solid #b9c5cc;
      border-radius: 6px;
      background: #fff;
      color: #172026;
    }
    input, textarea, select { padding: 9px 10px; width: 100%; }
    button { padding: 9px 12px; cursor: pointer; background: #1f6f78; color: #fff; border-color: #1f6f78; }
    button.secondary { background: #fff; color: #172026; border-color: #b9c5cc; }
    button:disabled { opacity: 0.55; cursor: not-allowed; }
    .log {
      flex: 1;
      overflow: auto;
      background: #fff;
      border: 1px solid #cfd8df;
      border-radius: 8px;
      padding: 12px;
      min-height: 360px;
    }
    .entry { border-bottom: 1px solid #edf1f3; padding: 10px 0; }
    .entry:last-child { border-bottom: 0; }
    .role { font-size: 12px; color: #5a6a72; margin-bottom: 4px; }
    .composer { display: grid; grid-template-columns: 130px minmax(0, 1fr) 92px; gap: 8px; }
    aside { display: flex; flex-direction: column; gap: 14px; background: #f8fafb; }
    .panel {
      background: #fff;
      border: 1px solid #cfd8df;
      border-radius: 8px;
      padding: 12px;
    }
    .row { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-top: 8px; }
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
          <input id="agentId" value="demo">
        </div>
      </div>
      <div id="log" class="log"></div>
      <div class="composer">
        <input id="intent" value="chat" aria-label="intent">
        <textarea id="text" rows="2" placeholder="Escribe algo para NIÑO"></textarea>
        <button id="send">Enviar</button>
      </div>
    </section>
    <aside>
      <div class="panel">
        <h2>Estado</h2>
        <div class="row">
          <button id="refresh" class="secondary">Actualizar</button>
          <button id="cycle" class="secondary">Ciclo interno</button>
        </div>
        <button id="dream" class="secondary" style="margin-top:8px;width:100%">Sueño</button>
        <button id="scheduled" class="secondary" style="margin-top:8px;width:100%">Scheduler</button>
        <button id="agents" class="secondary" style="margin-top:8px;width:100%">Agentes</button>
        <button id="reset" class="secondary" style="margin-top:8px;width:100%">Reset agente</button>
        <pre id="state">{}</pre>
      </div>
      <div class="panel">
        <h2>Proactividad</h2>
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
          <button id="saveProactivity" class="secondary">Guardar</button>
          <button id="evalProactivity" class="secondary">Evaluar</button>
        </div>
        <pre id="proactivity">{}</pre>
      </div>
      <div class="panel">
        <h2>Memoria</h2>
        <div class="row">
          <button id="episodes" class="secondary">Episodios</button>
          <button id="relation" class="secondary">Relación</button>
        </div>
        <button id="facts" class="secondary" style="margin-top:8px;width:100%">Memoria fría</button>
        <div class="row">
          <button id="selfModel" class="secondary">Self</button>
          <button id="worldModel" class="secondary">Mundo</button>
        </div>
        <button id="narrative" class="secondary" style="margin-top:8px;width:100%">Narrativa</button>
        <pre id="memory">{}</pre>
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
    const addEntry = (role, text) => {
      const item = document.createElement("div");
      item.className = "entry";
      item.innerHTML = `<div class="role"></div><div></div>`;
      item.children[0].textContent = role;
      item.children[1].textContent = text;
      log.appendChild(item);
      log.scrollTop = log.scrollHeight;
    };
    async function refreshState() {
      print($("state"), await api(agentPath("/state")));
    }
    $("send").onclick = async () => {
      const payload = {intent: $("intent").value || "chat", text: $("text").value, salience: 0.7, confidence: 0.9};
      if (!payload.text.trim()) return;
      $("send").disabled = true;
      try {
        addEntry("usuario", payload.text);
        const out = await api(agentPath("/tick"), {method: "POST", body: JSON.stringify(payload)});
        addEntry("niño", out.action.payload.text);
        $("text").value = "";
        await refreshState();
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
    $("agents").onclick = async () => print($("state"), await api("/agents"));
    $("reset").onclick = async () => {
      const out = await api(agentPath("/reset"), {method: "POST", body: "{}"});
      log.textContent = "";
      print($("state"), out);
      print($("memory"), {});
      print($("proactivity"), {});
    };
    $("saveProactivity").onclick = async () => {
      const payload = {
        consent: $("consent").value,
        max_messages_per_day: Number($("maxDay").value),
        min_hours_between: Number($("minHours").value)
      };
      print($("proactivity"), await api(agentPath("/proactivity/configure"), {method: "POST", body: JSON.stringify(payload)}));
    };
    $("evalProactivity").onclick = async () => {
      const out = await api(agentPath("/proactivity/evaluate"), {method: "POST", body: "{}"});
      print($("proactivity"), out);
      if (out.should_send) addEntry("niño · proactivo", out.action.payload.text);
    };
    $("episodes").onclick = async () => print($("memory"), await api(agentPath("/episodes")));
    $("facts").onclick = async () => print($("memory"), await api(agentPath("/memory/facts")));
    $("relation").onclick = async () => print($("memory"), await api(agentPath("/relation")));
    $("selfModel").onclick = async () => print($("memory"), await api(agentPath("/self-model")));
    $("worldModel").onclick = async () => print($("memory"), await api(agentPath("/world-model")));
    $("narrative").onclick = async () => print($("memory"), await api(agentPath("/narrative")));
    refreshState();
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
    def __init__(self, runtime: NinoRuntime, autonomy: BackgroundAutonomy | None = None) -> None:
        self.runtime = runtime
        self.internal_loop = InternalLoop(runtime)
        self.scheduler = NinoScheduler(runtime)
        self.autonomy = autonomy

    def health(self) -> dict[str, Any]:
        return {"ok": True, "service": "nino"}

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

    def tick(self, agent_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return _to_jsonable(self.runtime.tick(agent_id, payload))

    def get_state(self, agent_id: str) -> dict[str, Any]:
        return _to_jsonable(self.runtime.load_or_init_state(agent_id))

    def list_episodes(self, agent_id: str) -> dict[str, Any]:
        episodes = self.runtime.episode_store.list_for_agent(agent_id)
        return {"episodes": _to_jsonable(episodes)}

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
                    "GET /autonomy/status",
                    "POST /autonomy/run-once",
                    "POST /agents/{agent_id}/tick",
                    "GET /agents/{agent_id}/state",
                    "GET /agents/{agent_id}/episodes",
                    "GET /agents/{agent_id}/memory/facts",
                    "GET /agents/{agent_id}/relation",
                    "GET /agents/{agent_id}/self-model",
                    "GET /agents/{agent_id}/world-model",
                    "GET /agents/{agent_id}/narrative",
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
        if method == "GET" and path == "/autonomy/status":
            return "200 OK", self.service.autonomy_status()
        if method == "POST" and path == "/autonomy/run-once":
            return "200 OK", self.service.autonomy_run_once(payload)

        if method == "POST" and path == "/internal/scheduled":
            return "200 OK", self.service.scheduled_all(payload)

        parts = [part for part in path.split("/") if part]
        if method == "GET" and parts == ["agents"]:
            return "200 OK", self.service.list_agents()

        if len(parts) < 2 or parts[0] != "agents":
            return "404 Not Found", {"error": "not_found"}

        agent_id = parts[1]
        tail = parts[2:]

        if method == "POST" and tail == ["tick"]:
            return "200 OK", self.service.tick(agent_id, payload)
        if method == "GET" and tail == ["state"]:
            return "200 OK", self.service.get_state(agent_id)
        if method == "GET" and tail == ["episodes"]:
            return "200 OK", self.service.list_episodes(agent_id)
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
    return NinoHttpApp(NinoService(create_persistent_runtime(db_path)))


def create_app_with_runtime(
    runtime: NinoRuntime,
    autonomy: BackgroundAutonomy | None = None,
) -> NinoHttpApp:
    return NinoHttpApp(NinoService(runtime, autonomy=autonomy))


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
    app = create_app_with_runtime(runtime, autonomy=autonomy)

    class QuietHandler(WSGIRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return

    try:
        with make_server(host, port, app, handler_class=QuietHandler) as server:
            server.serve_forever()
    finally:
        if autonomy is not None:
            autonomy.stop()
