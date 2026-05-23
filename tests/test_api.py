from __future__ import annotations

from io import BytesIO
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from nino.api import create_app
from nino.api import create_app_with_runtime
from nino.autonomy import BackgroundAutonomy
from nino.persistence import create_persistent_runtime


def _request(app, method: str, path: str, payload: dict | None = None) -> dict:
    data = b"" if payload is None else json.dumps(payload).encode("utf-8")
    captured: dict[str, str] = {}

    def start_response(status: str, headers: list[tuple[str, str]]) -> None:
        captured["status"] = status

    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "CONTENT_LENGTH": str(len(data)),
        "wsgi.input": BytesIO(data),
    }
    body = b"".join(app(environ, start_response))
    assert captured["status"].startswith("200"), body.decode("utf-8")
    return json.loads(body.decode("utf-8"))


def _raw_request(app, method: str, path: str) -> tuple[str, bytes]:
    captured: dict[str, str] = {}

    def start_response(status: str, headers: list[tuple[str, str]]) -> None:
        captured["status"] = status
        captured["content_type"] = dict(headers).get("Content-Type", "")

    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "CONTENT_LENGTH": "0",
        "wsgi.input": BytesIO(b""),
    }
    body = b"".join(app(environ, start_response))
    assert captured["status"].startswith("200"), body.decode("utf-8")
    return captured["content_type"], body


def test_http_api_serves_browser_app(tmp_path) -> None:
    app = create_app(tmp_path / "nino.db")

    content_type, body = _raw_request(app, "GET", "/app")

    assert content_type.startswith("text/html")
    assert b"<title>NI" in body
    assert b"/internal/cycle" in body
    assert b"Salud" in body
    assert b"Perfil" in body
    assert b"Export seguro" in body
    assert b"metricTick" in body
    assert b"Consolidar" in body
    assert b"loadConversation" in body
    assert b"/conversation" in body
    assert b"/openapi.json" in body
    assert b"/llm/status" in body
    assert b"/llm/probe" in body
    assert b"llmSetup" in body
    assert b"ninoctl configure-claude" in body
    assert b"describeClaudeConfig" in body
    assert b"config_errors" in body
    assert b"Corrige los valores indicados" in body
    assert b"Marcar entregado" in body
    assert b"clear-delivered" in body
    assert b"/operations/backup" in body
    assert b"Backup DB" in body
    assert b"Descargar seguro" in body
    assert b"Descargar completo" in body
    assert b"Importar" in body
    assert b"/agents/import" in body
    assert b"/agents/prune" in body
    assert b"Limpiar coincidentes" in body
    assert b"Guardar calidad" in body
    assert b"/eval/conversation/history" in body
    assert b"/audit" in body
    assert b"/permissions/configure" in body
    assert b"Permisos" in body
    assert b"Tareas" in body
    assert b"/tasks/run-next" in body
    assert b"activeStart" in body
    assert b"activeEnd" in body
    assert b"/operations/mode" in body
    assert b"/operations/claude" in body
    assert b"/operations/audit" in body
    assert b"Auditor" in body


def test_http_api_ticks_and_restores_state(tmp_path) -> None:
    app = create_app(tmp_path / "nino.db")

    root = _request(app, "GET", "/")
    health = _request(app, "GET", "/health")
    openapi = _request(app, "GET", "/openapi.json")
    mode = _request(app, "GET", "/operations/mode")
    claude = _request(app, "GET", "/operations/claude")
    _request(app, "POST", "/operations/backup", {})
    product_audit = _request(app, "GET", "/operations/audit")
    tick = _request(
        app,
        "POST",
        "/agents/api-agent/tick",
        {"intent": "music", "text": "me gusta piano", "salience": 0.9},
    )
    state = _request(app, "GET", "/agents/api-agent/state")
    episodes = _request(app, "GET", "/agents/api-agent/episodes")
    conversation = _request(app, "GET", "/agents/api-agent/conversation")
    llm = _request(app, "GET", "/agents/api-agent/llm/status")
    probe = _request(app, "POST", "/agents/api-agent/llm/probe", {})
    audit = _request(app, "GET", "/agents/api-agent/audit")
    permissions = _request(app, "GET", "/agents/api-agent/permissions")
    configured_permission = _request(
        app,
        "POST",
        "/agents/api-agent/permissions/configure",
        {"action_type": "tool_call", "allowed": True, "delivery": "inbox_only"},
    )
    queued_task = _request(
        app,
        "POST",
        "/agents/api-agent/tasks",
        {"text": "recordar respirar", "description": "recordatorio"},
    )
    tasks_before_run = _request(app, "GET", "/agents/api-agent/tasks")
    ran_task = _request(app, "POST", "/agents/api-agent/tasks/run-next", {})
    tasks_after_run = _request(app, "GET", "/agents/api-agent/tasks")

    assert root["service"] == "nino"
    assert "GET /health" in root["endpoints"]
    assert "GET /openapi.json" in root["endpoints"]
    assert "GET /operations/mode" in root["endpoints"]
    assert "GET /operations/claude" in root["endpoints"]
    assert "GET /operations/audit" in root["endpoints"]
    assert "POST /agents/{agent_id}/tasks/run-next" in root["endpoints"]
    assert openapi["openapi"] == "3.1.0"
    assert "/agents/{agent_id}/tick" in openapi["paths"]
    assert "post" in openapi["paths"]["/agents/{agent_id}/tick"]
    assert "/operations/claude" in openapi["paths"]
    assert "/operations/audit" in openapi["paths"]
    assert health == {"ok": True, "service": "nino"}
    assert mode["local_first"] is True
    assert mode["network_required_for_core"] is False
    assert mode["external_llm"]["enabled"] is False
    assert mode["external_llm"]["config"]["enabled"] is False
    assert "memory" in mode["offline_capabilities"]
    assert claude["configured"] is False
    assert claude["api_key_present"] is False
    assert claude["api_key_source"] is None
    assert claude["keychain_service"] is None
    assert claude["config_errors"] == []
    assert "NINO_LLM_PROVIDER" in claude["missing"]
    assert "scripts/ninoctl configure-claude --keychain-service nino-anthropic" in claude["setup_commands"]
    assert "scripts/ninoctl final-audit" in claude["setup_commands"]
    assert "scripts/ninoctl live-audit" not in claude["setup_commands"]
    assert "ANTHROPIC_API_KEY" not in json.dumps(claude["setup_commands"])
    assert product_audit["ok"] is True
    assert {check["name"] for check in product_audit["checks"]} >= {
        "sqlite_database_exists",
        "backup_directory_available",
        "claude_live",
    }
    assert tick["tick"] == 1
    assert state["tick"] == 1
    assert len(episodes["episodes"]) == 1
    assert episodes["episodes"][0]["text"] == "me gusta piano"
    assert [turn["role"] for turn in conversation["turns"]] == ["user", "assistant"]
    assert llm["llm"]["enabled"] is False
    assert probe["probe"]["error"] == "llm_not_configured"
    assert audit["audit"][0]["type"] == "tick_decision"
    assert permissions["permissions"]["tool_call"]["allowed"] is False
    assert configured_permission["permissions"]["tool_call"]["allowed"] is True
    assert queued_task["ok"] is True
    assert tasks_before_run["tasks"][0]["status"] == "pending"
    assert ran_task["ok"] is True
    assert tasks_after_run["tasks"][0]["status"] == "completed"


def test_http_api_openapi_matches_root_endpoint_catalog(tmp_path) -> None:
    app = create_app(tmp_path / "nino.db")

    root = _request(app, "GET", "/")
    openapi = _request(app, "GET", "/openapi.json")
    documented = {
        f"{method.upper()} {path}"
        for path, operations in openapi["paths"].items()
        for method in operations
    }

    expected = set(root["endpoints"]) - {"GET /app"}
    assert documented == expected


def test_http_api_creates_database_backup(tmp_path) -> None:
    db_path = tmp_path / "nino.db"
    app = create_app(db_path)
    _request(app, "POST", "/agents/api-agent/tick", {"intent": "chat", "text": "hola"})

    backup = _request(app, "POST", "/operations/backup", {})
    backups = _request(app, "GET", "/operations/backups")

    assert backup["ok"] is True
    assert (tmp_path / "backups").exists()
    assert Path(backup["path"]).exists()
    assert backups["ok"] is True
    assert backups["backups"][0]["path"] == backup["path"]
    assert backups["backups"][0]["size_bytes"] > 0


def test_http_api_exposes_autonomy_status_and_run_once(tmp_path) -> None:
    db_path = tmp_path / "nino.db"
    runtime = create_persistent_runtime(db_path)
    runtime.tick("api-auto", {"intent": "chat", "text": "hola"})
    autonomy = BackgroundAutonomy(runtime, interval_seconds=10)
    app = create_app_with_runtime(runtime, autonomy=autonomy, db_path=db_path)

    status = _request(app, "GET", "/autonomy/status")
    run_once = _request(app, "POST", "/autonomy/run-once", {"now": "2026-05-21T10:00:00+00:00"})
    mode = _request(app, "GET", "/operations/mode")

    assert status["enabled"] is True
    assert run_once["enabled"] is True
    assert run_once["results"][0]["agent_id"] == "api-auto"
    assert mode["storage"]["type"] == "sqlite"
    assert mode["storage"]["path"] == str(db_path)


def test_http_api_lists_agents(tmp_path) -> None:
    app = create_app(tmp_path / "nino.db")

    _request(app, "POST", "/agents/api-a/tick", {"intent": "chat", "text": "hola"})
    _request(app, "POST", "/agents/api-b/proactivity/configure", {"consent": "allowed"})
    agents = _request(app, "GET", "/agents")

    assert agents["agents"] == ["api-a", "api-b"]


def test_http_api_deep_health_profile_and_prune_agents(tmp_path) -> None:
    app = create_app(tmp_path / "nino.db")

    _request(app, "POST", "/agents/demo-one/tick", {"intent": "chat", "text": "soy Pablo", "salience": 0.8})
    _request(app, "POST", "/agents/keep-one/tick", {"intent": "music", "text": "me gusta piano", "salience": 0.9})

    health = _request(app, "GET", "/health/deep")
    profile = _request(app, "GET", "/agents/demo-one/profile")
    dry_run = _request(app, "POST", "/agents/prune", {"prefixes": ["demo-"], "dry_run": True})
    pruned = _request(app, "POST", "/agents/prune", {"prefixes": ["demo-"], "dry_run": False})
    agents = _request(app, "GET", "/agents")

    assert health["ok"] is True
    assert health["agent_count"] == 2
    assert profile["profile"]["known_user"] == "Pablo"
    assert profile["profile"]["episode_count"] == 1
    assert dry_run["matched"] == ["demo-one"]
    assert pruned["deleted"][0]["agent_id"] == "demo-one"
    assert agents["agents"] == ["keep-one"]


def test_http_api_exposes_relation_state(tmp_path) -> None:
    app = create_app(tmp_path / "nino.db")

    _request(app, "POST", "/agents/api-agent/tick", {"intent": "chat", "text": "soy Pablo"})
    _request(app, "POST", "/agents/api-agent/tick", {"intent": "chat", "text": "me gusta el piano"})
    relation = _request(app, "GET", "/agents/api-agent/relation")

    assert relation["relation_state"]["user_name"] == "Pablo"
    assert "piano" in relation["relation_state"]["preferences"]


def test_http_api_exposes_self_and_world_models(tmp_path) -> None:
    app = create_app(tmp_path / "nino.db")

    _request(
        app,
        "POST",
        "/agents/api-agent/tick",
        {"intent": "music", "text": "me gusta el piano", "salience": 0.9},
    )
    self_model = _request(app, "GET", "/agents/api-agent/self-model")
    world_model = _request(app, "GET", "/agents/api-agent/world-model")

    assert self_model["self_model"]["interaction_count"] == 1
    assert self_model["self_model"]["identity_stage"] == "early_childhood"
    assert world_model["world_model"]["concept_counts"]["piano"] == 1


def test_http_api_exposes_narrative(tmp_path) -> None:
    app = create_app(tmp_path / "nino.db")

    _request(app, "POST", "/agents/api-agent/tick", {"intent": "chat", "text": "soy Pablo", "salience": 0.8})
    _request(app, "POST", "/agents/api-agent/tick", {"intent": "music", "text": "me gusta el piano", "salience": 0.9})
    narrative = _request(app, "GET", "/agents/api-agent/narrative")

    assert narrative["narrative"]["known_user"] == "Pablo"
    assert "piano" in narrative["narrative"]["preferences"]
    assert "Soy NIÑO" in narrative["narrative"]["summary"]


def test_http_api_exports_imports_and_reports_metrics(tmp_path) -> None:
    app = create_app(tmp_path / "nino.db")
    _request(app, "POST", "/agents/api-agent/tick", {"intent": "chat", "text": "soy Pablo", "salience": 0.8})
    exported = _request(app, "GET", "/agents/api-agent/export")
    metrics = _request(app, "GET", "/agents/api-agent/metrics")
    imported = _request(app, "POST", "/agents/import", {"export": exported["export"], "replace": True})

    assert exported["export"]["agent_id"] == "api-agent"
    assert metrics["metrics"]["episode_count"] == 1
    assert imported["agent_id"] == "api-agent"
    assert imported["imported_episodes"] == 1


def test_http_api_privacy_inbox_decay_and_quality(tmp_path) -> None:
    app = create_app(tmp_path / "nino.db")
    _request(app, "POST", "/agents/api-agent/proactivity/configure", {"consent": "allowed", "max_messages_per_day": 3, "min_hours_between": 0})
    _request(app, "POST", "/agents/api-agent/tick", {"intent": "chat", "text": "soy Pablo", "salience": 0.8})
    _request(app, "POST", "/agents/api-agent/tick", {"intent": "question", "text": "por qué la música me calma?", "salience": 0.6})
    _request(app, "POST", "/agents/api-agent/proactivity/evaluate", {})

    safe = _request(app, "GET", "/agents/api-agent/export-safe")
    inbox = _request(app, "GET", "/agents/api-agent/proactivity/inbox")
    decay = _request(app, "POST", "/agents/api-agent/memory/decay", {"factor": 0.5})
    quality = _request(app, "GET", "/agents/api-agent/eval/conversation")
    recorded = _request(app, "POST", "/agents/api-agent/eval/conversation/record", {})
    history = _request(app, "GET", "/agents/api-agent/eval/conversation/history")

    assert safe["export"]["redacted"] is True
    assert inbox["inbox"]
    assert decay["factor"] == 0.5
    assert quality["quality"]["episode_count"] == 2
    assert recorded["history_count"] == 1
    assert len(history["history"]) == 1


def test_http_api_inbox_delivery_memory_search_and_snapshot(tmp_path) -> None:
    app = create_app(tmp_path / "nino.db")
    _request(app, "POST", "/agents/api-agent/proactivity/configure", {"consent": "allowed", "max_messages_per_day": 3, "min_hours_between": 0})
    _request(app, "POST", "/agents/api-agent/tick", {"intent": "question", "text": "por qué la música me calma?", "salience": 0.6})
    _request(app, "POST", "/agents/api-agent/proactivity/evaluate", {})
    inbox = _request(app, "GET", "/agents/api-agent/proactivity/inbox")
    item_id = inbox["inbox"][0]["id"]

    marked = _request(app, "POST", f"/agents/api-agent/proactivity/inbox/{item_id}/delivered", {})
    cleared = _request(app, "POST", "/agents/api-agent/proactivity/inbox/clear-delivered", {})
    search = _request(app, "POST", "/agents/api-agent/memory/search", {"query": "música"})
    snapshot = _request(app, "GET", "/development/snapshot")

    assert marked["updated"] is True
    assert cleared["cleared"] == 1
    assert search["memory_candidates"]
    assert snapshot["snapshot"]["agent_count"] == 1


def test_http_api_proactivity_consent_and_frequency(tmp_path) -> None:
    app = create_app(tmp_path / "nino.db")
    now = datetime(2026, 5, 21, 10, tzinfo=timezone.utc)

    configured = _request(
        app,
        "POST",
        "/agents/api-agent/proactivity/configure",
        {
            "consent": "allowed",
            "max_messages_per_day": 1,
            "min_hours_between": 0,
            "active_hours_start": 9,
            "active_hours_end": 18,
        },
    )
    _request(
        app,
        "POST",
        "/agents/api-agent/tick",
        {
            "intent": "school",
            "text": "mañana tengo examen",
            "salience": 0.9,
            "confidence": 0.9,
        },
    )
    first = _request(
        app,
        "POST",
        "/agents/api-agent/proactivity/evaluate",
        {"now": now.isoformat()},
    )
    second = _request(
        app,
        "POST",
        "/agents/api-agent/proactivity/evaluate",
        {"now": (now + timedelta(hours=1)).isoformat()},
    )

    assert first["should_send"] is True
    assert second["should_send"] is False
    assert "daily_frequency_cap" in second["reason_trace"]
    assert configured["state"]["relation_state"]["proactivity"]["settings"]["active_hours_start"] == 9


def test_http_api_internal_cycle_consolidates_memory(tmp_path) -> None:
    app = create_app(tmp_path / "nino.db")
    now = datetime(2026, 5, 21, 10, tzinfo=timezone.utc)

    _request(
        app,
        "POST",
        "/agents/api-agent/tick",
        {
            "intent": "music",
            "text": "prefiero piano",
            "salience": 0.9,
            "confidence": 0.9,
        },
    )
    cycle = _request(
        app,
        "POST",
        "/agents/api-agent/internal/cycle",
        {"now": now.isoformat(), "record_proactive_send": False},
    )
    memory = _request(
        app,
        "POST",
        "/agents/api-agent/memory/retrieve",
        {"query_intent": "preference piano", "time_scope": "long"},
    )

    assert cycle["consolidated_count"] == 1
    assert any(candidate["fact_id"].startswith("cold::") for candidate in memory["memory_candidates"])


def test_http_api_internal_dream_creates_reflection(tmp_path) -> None:
    app = create_app(tmp_path / "nino.db")

    _request(
        app,
        "POST",
        "/agents/api-agent/tick",
        {"intent": "music", "text": "me gusta el piano", "salience": 0.9, "confidence": 0.9},
    )
    dream = _request(app, "POST", "/agents/api-agent/internal/dream", {})
    self_model = _request(app, "GET", "/agents/api-agent/self-model")

    assert dream["reflection_count"] == 1
    assert dream["maturity"] > 0
    assert self_model["self_model"]["dream_reflections"]


def test_http_api_scheduled_cycle_runs_pending_work(tmp_path) -> None:
    app = create_app(tmp_path / "nino.db")
    now = datetime(2026, 5, 21, 10, tzinfo=timezone.utc)

    _request(app, "POST", "/agents/api-agent/proactivity/configure", {"consent": "allowed"})
    _request(
        app,
        "POST",
        "/agents/api-agent/tick",
        {"intent": "question", "text": "por qué la música me calma?", "salience": 0.6},
    )
    out = _request(app, "POST", "/agents/api-agent/internal/scheduled", {"now": now.isoformat()})

    assert out["ran_dream"] is True
    assert out["ran_proactivity"] is True
    assert out["proactive_action"] is not None


def test_http_api_reset_agent_clears_persistent_data(tmp_path) -> None:
    app = create_app(tmp_path / "nino.db")

    _request(
        app,
        "POST",
        "/agents/api-agent/tick",
        {"intent": "chat", "text": "me gusta piano", "salience": 0.9, "confidence": 0.9},
    )
    _request(app, "POST", "/agents/api-agent/internal/cycle", {})

    reset = _request(app, "POST", "/agents/api-agent/reset", {})
    state = _request(app, "GET", "/agents/api-agent/state")
    episodes = _request(app, "GET", "/agents/api-agent/episodes")
    relation = _request(app, "GET", "/agents/api-agent/relation")

    assert reset["episodes"] == 1
    assert reset["cold_memory"] == 1
    assert state["tick"] == 0
    assert episodes["episodes"] == []
    assert "user_name" not in relation["relation_state"]


def test_http_api_lists_and_deletes_memory_items(tmp_path) -> None:
    app = create_app(tmp_path / "nino.db")
    _request(
        app,
        "POST",
        "/agents/api-agent/tick",
        {"intent": "chat", "text": "me gusta piano", "salience": 0.9},
    )
    _request(app, "POST", "/agents/api-agent/consolidate", {})

    episodes = _request(app, "GET", "/agents/api-agent/episodes")
    facts = _request(app, "GET", "/agents/api-agent/memory/facts")

    episode_id = episodes["episodes"][0]["episode_id"]
    fact_id = facts["facts"][0]["fact_id"]
    deleted_episode = _request(app, "DELETE", f"/agents/api-agent/episodes/{episode_id}")
    deleted_fact = _request(app, "DELETE", f"/agents/api-agent/memory/facts/{fact_id}")

    assert deleted_episode["deleted"] is True
    assert deleted_fact["deleted"] is True
    assert _request(app, "GET", "/agents/api-agent/episodes")["episodes"] == []
    assert _request(app, "GET", "/agents/api-agent/memory/facts")["facts"] == []


def test_http_api_runs_global_scheduled_cycle(tmp_path) -> None:
    app = create_app(tmp_path / "nino.db")
    _request(app, "POST", "/agents/api-agent/proactivity/configure", {"consent": "allowed"})
    _request(app, "POST", "/agents/api-agent/tick", {"intent": "chat", "text": "hola"})

    out = _request(app, "POST", "/internal/scheduled", {})

    assert len(out["results"]) == 1
    assert out["results"][0]["agent_id"] == "api-agent"
