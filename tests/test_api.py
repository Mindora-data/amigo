from __future__ import annotations

from io import BytesIO
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from nino.api import create_app
from nino.api import create_app_with_runtime
from nino.auth import hash_password, token_hash
from nino.autonomy import BackgroundAutonomy
from nino.persistence import create_persistent_runtime


def _request(app, method: str, path: str, payload: dict | None = None, headers: dict[str, str] | None = None) -> dict:
    data = b"" if payload is None else json.dumps(payload).encode("utf-8")
    captured: dict[str, str] = {}
    split = urlsplit(path)

    def start_response(status: str, headers: list[tuple[str, str]]) -> None:
        captured["status"] = status

    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": split.path,
        "QUERY_STRING": split.query,
        "CONTENT_LENGTH": str(len(data)),
        "wsgi.input": BytesIO(data),
    }
    for key, value in (headers or {}).items():
        environ[f"HTTP_{key.upper().replace('-', '_')}"] = value
    body = b"".join(app(environ, start_response))
    assert captured["status"].startswith("200"), body.decode("utf-8")
    return json.loads(body.decode("utf-8"))


def _request_status(app, method: str, path: str, payload: dict | None = None, headers: dict[str, str] | None = None) -> tuple[str, dict]:
    data = b"" if payload is None else json.dumps(payload).encode("utf-8")
    captured: dict[str, str] = {}
    split = urlsplit(path)

    def start_response(status: str, headers_out: list[tuple[str, str]]) -> None:
        captured["status"] = status

    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": split.path,
        "QUERY_STRING": split.query,
        "CONTENT_LENGTH": str(len(data)),
        "wsgi.input": BytesIO(data),
    }
    for key, value in (headers or {}).items():
        environ[f"HTTP_{key.upper().replace('-', '_')}"] = value
    body = b"".join(app(environ, start_response))
    return captured["status"], json.loads(body.decode("utf-8"))


def _request_status_headers(app, method: str, path: str, payload: dict | None = None, headers: dict[str, str] | None = None) -> tuple[str, dict, dict[str, str]]:
    data = b"" if payload is None else json.dumps(payload).encode("utf-8")
    captured: dict[str, object] = {}
    split = urlsplit(path)

    def start_response(status: str, headers_out: list[tuple[str, str]]) -> None:
        captured["status"] = status
        captured["headers"] = dict(headers_out)

    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": split.path,
        "QUERY_STRING": split.query,
        "CONTENT_LENGTH": str(len(data)),
        "wsgi.input": BytesIO(data),
        "REMOTE_ADDR": "198.51.100.9",
    }
    for key, value in (headers or {}).items():
        environ[f"HTTP_{key.upper().replace('-', '_')}"] = value
    body = b"".join(app(environ, start_response))
    return str(captured["status"]), json.loads(body.decode("utf-8")), dict(captured["headers"])


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
    dashboard_type, dashboard_body = _raw_request(app, "GET", "/dashboard")

    assert content_type.startswith("text/html")
    assert dashboard_type.startswith("text/html")
    assert b"<title>amigo</title>" in body
    assert b"Aprendizaje de amigo" in dashboard_body
    assert b"/relationship-dashboard" in dashboard_body
    assert b"/internal/cycle" in body
    assert b"Salud" in body
    assert b"Perfil" in body
    assert b"Export seguro" in body
    assert b"metricTick" in body
    assert b"userId" in body
    assert b"loginUser" in body
    assert b"/session/login" in body
    assert b"/users/${encodeURIComponent(currentUserId())}/agents" in body
    assert b"nino_user_id" in body
    assert b"nino_session_token" in body
    assert b"X-Nino-Session" in body
    assert b"globalModel" in body
    assert b"globalSuggestions" in body
    assert b"/operations/global-suggestions" in body
    assert b"relationshipDashboard" in body
    assert b"/relationship-dashboard" in body
    assert b"temporalEvents" in body
    assert b"/temporal-events" in body
    assert b"loadTemporalEvents" in body
    assert b"Consolidar" in body
    assert b"loadConversation" in body
    assert b"/conversation" in body
    assert b"/openapi.json" in body
    assert b"/llm/status" in body
    assert b"/llm/probe" in body
    assert b"/operations/claude/configure" in body
    assert b"/operations/claude/disable" in body
    assert b"Guardar Claude" in body
    assert b"Guardar DeepSeek" in body
    assert b"deepseek-chat" in body
    assert b"/operations/deepseek/configure" in body
    assert b"Cierre guiado" in body
    assert b"guidedFinal" in body
    assert b"waitForHealth" in body
    assert b"Desactivar Claude" in body
    assert b"claudeSecretMode" in body
    assert b"nino-anthropic" in body
    assert b"llmSetup" in body
    assert b"ninoctl configure-claude" in body
    assert b"describeClaudeConfig" in body
    assert b"config_errors" in body
    assert b"Corrige los valores indicados" in body
    assert b"finalReadiness" in body
    assert b"renderFinalReadiness" in body
    assert b"Servicio persistente" in body
    assert b"Preflight final" in body
    assert b"Marcar entregado" in body
    assert b"clear-delivered" in body
    assert b"/operations/backup" in body
    assert b"Backup DB" in body
    assert b"backupList" in body
    assert b"renderBackups" in body
    assert b"scripts/ninoctl restore" in body
    assert b"/operations/logs" in body
    assert b"Logs" in body
    assert b"/operations/restart" in body
    assert b"Guardar Claude, reiniciar el servicio" in body
    assert b"Reiniciar servicio" in body
    assert b"/operations/eval" in body
    assert b"Eval local" in body
    assert b"/operations/final-preflight" in body
    assert b"/operations/final-audit" in body
    assert b"Preflight final" in body
    assert b"Cierre final" in body
    assert b"Descargar seguro" in body
    assert b"Descargar completo" in body
    assert b"Eliminar episodio" in body
    assert b"Eliminar hecho" in body
    assert b"decayFactor" in body
    assert b"Aplicar decay" in body
    assert b"/memory/decay" in body
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
    assert b"/operations/product-status" in body
    assert b"Estado final" in body
    assert b"productStatus" in body
    assert "Siguiente acción".encode("utf-8") in body
    assert b"recommended_next_action" in body
    assert b"latest_report_current" in body
    assert b"/operations/completion-audit" in body
    assert b"Terminaci" in body
    assert b"completionAudit" in body
    assert b"/operations/closing-report" in body
    assert b"Informe cierre" in body
    assert b"closingReport" in body
    assert b"writeClosingReportAfterAudit" in body
    assert "terminación actualizada".encode("utf-8") in body
    assert b"/operations/reports" in body
    assert b"Ver informes" in body
    assert b"renderReports" in body
    assert b"/operations/reports/" in body
    assert b"Ver JSON" in body
    assert b"/operations/reports/latest" in body
    assert "Último informe".encode("utf-8") in body
    assert b"latestReport" in body
    assert b"renderProductStatus" in body
    assert b"git_head" in body
    assert b"latest_report" in body
    assert b"renderCompletionAudit" in body
    assert b"Auditor" in body
    assert b"addContextEntry" in body
    assert "contexto amigo".encode("utf-8") in body
    assert "memoria fría".encode("utf-8") in body
    assert "memoria reciente".encode("utf-8") in body
    assert "memoria ${type}".encode("utf-8") in body
    assert "memoria fría ${status}".encode("utf-8") in body
    assert "Memoria fría: ${out.visible_facts".encode("utf-8") in body
    assert b"visible_fact_counts" in body
    assert b"factStatusFilter" in body
    assert b"factKeyFilter" in body
    assert b"URLSearchParams" in body
    assert "clave ${out.key_filter".encode("utf-8") in body
    assert "estado ${out.status_filter".encode("utf-8") in body
    assert "memoria amigo".encode("utf-8") in body
    assert b"memoryTypeFilter" in body
    assert b"memory_type_filter" in body
    assert b"memory_type_counts" in body
    assert b"visible_memory_type_counts" in body
    assert "Resultados: ${out.visible_candidates".encode("utf-8") in body
    assert "consolidada: ${labels.join".encode("utf-8") in body
    assert "origen ${candidate.source_episode_id.slice(0, 8)}".encode("utf-8") in body
    assert "origen ${fact.source_episode_id.slice(0, 8)}".encode("utf-8") in body
    assert b'parts.join("\\n")' in body
    assert b'parts.join("\n")' not in body


def test_http_api_serves_minimal_user_app(tmp_path) -> None:
    app = create_app(tmp_path / "nino.db")

    content_type, body = _raw_request(app, "GET", "/user")
    chat_content_type, chat_body = _raw_request(app, "GET", "/chat")

    assert content_type.startswith("text/html")
    assert chat_content_type.startswith("text/html")
    assert chat_body == body
    assert b"minimalUserApp" in body
    assert b"<title>amigo</title>" in body
    assert b"<h1>amigo</h1>" in body
    assert "amigo está pensando".encode("utf-8") in body
    assert b"loginView" in body
    assert b"chatView" in body
    assert b"voiceButton" in body
    assert b"/session/login" in body
    assert b"/session/status" in body
    assert b"/session/logout" in body
    assert b"current-password" in body
    assert b"type=\"password\"" in body
    assert b"credentials: \"same-origin\"" in body
    assert b"resumeSession" in body
    assert b"enterChat" in body
    assert b"deliveredInboxText" in body
    assert b"nino_session_token" not in body
    assert b"x-nino-session" not in body
    assert b"loginUser();" not in body
    assert b"/users/${encodeURIComponent(currentUserId())}/agents/${encodeURIComponent(AGENT_ID)}" in body
    assert b"out.turns || out.conversation" in body
    assert b"/conversation" in body
    assert b"/tick" in body
    assert b"SpeechRecognition" in body
    assert b"webkitSpeechRecognition" in body
    assert b"speechSynthesis" in body
    assert b"SpeechSynthesisUtterance" in body
    assert b"localNowIso" in body
    assert b"now: localNowIso()" in body
    assert b"ONBOARDING" in body
    assert "¿Cómo te llamas?".encode("utf-8") in body
    assert "¿Qué esperas de mí como amigo?".encode("utf-8") in body
    assert "queda entre nosotros".encode("utf-8") in body
    assert b"onboarding:${current.key}" in body
    assert b"loadProactiveInbox" in body
    assert b"startProactiveConversation" in body
    assert b"/proactivity/configure" in body
    assert b"/proactivity/evaluate" in body
    assert b"loadProactiveInbox() {\n      if" in body
    assert b"body: JSON.stringify({now: localNowIso()})" in body
    assert "Estoy aquí. ¿Qué tal vas hoy?".encode("utf-8") in body
    assert b"/proactivity/inbox" in body
    assert b"/delivered" in body
    assert b"temporal_miss" in body
    assert b"No encuentro recuerdos guardados de esa fecha." in body
    assert b"setInterval" in body
    assert b"/operations/" not in body
    assert b"/memory/facts" not in body
    assert b"Cierre final" not in body


def test_http_api_ticks_and_restores_state(tmp_path) -> None:
    app = create_app(tmp_path / "nino.db")

    root = _request(app, "GET", "/")
    health = _request(app, "GET", "/health")
    openapi = _request(app, "GET", "/openapi.json")
    mode = _request(app, "GET", "/operations/mode")
    claude = _request(app, "GET", "/operations/claude")
    global_model_initial = _request(app, "GET", "/operations/global-model")
    global_suggestions_initial = _request(app, "GET", "/operations/global-suggestions")
    _request(app, "POST", "/operations/backup", {})
    tick = _request(
        app,
        "POST",
        "/agents/nino/tick",
        {"intent": "identity", "text": "soy nino persistente", "salience": 0.9, "confidence": 0.9},
    )
    product_audit = _request(app, "GET", "/operations/audit")
    product_status = _request(app, "GET", "/operations/product-status")
    next_action = _request(app, "GET", "/operations/next-action")
    completion_audit = _request(app, "GET", "/operations/completion-audit")
    closing_report = _request(app, "POST", "/operations/closing-report", {})
    reports = _request(app, "GET", "/operations/reports")
    report = _request(app, "GET", f"/operations/reports/{reports['reports'][0]['name']}")
    latest_report = _request(app, "GET", "/operations/reports/latest")
    invalid_report = _request(app, "GET", "/operations/reports/bad.json")
    product_eval = _request(app, "GET", "/operations/eval")
    final_preflight = _request(app, "GET", "/operations/final-preflight")
    final_audit = _request(app, "POST", "/operations/final-audit", {})
    tick = _request(
        app,
        "POST",
        "/agents/api-agent/tick",
        {"intent": "music", "text": "me gusta piano", "salience": 0.9, "confidence": 0.95},
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
    relationship_dashboard = _request(app, "GET", "/agents/api-agent/relationship-dashboard")

    assert root["service"] == "nino"
    assert "GET /user" in root["endpoints"]
    assert "GET /chat" in root["endpoints"]
    assert "GET /health" in root["endpoints"]
    assert "GET /openapi.json" in root["endpoints"]
    assert "GET /dashboard" in root["endpoints"]
    assert "GET /operations/mode" in root["endpoints"]
    assert "GET /operations/claude" in root["endpoints"]
    assert "POST /operations/claude/configure" in root["endpoints"]
    assert "POST /operations/claude/disable" in root["endpoints"]
    assert "POST /operations/deepseek/configure" in root["endpoints"]
    assert "GET /operations/global-model" in root["endpoints"]
    assert "GET /operations/global-suggestions" in root["endpoints"]
    assert "GET /operations/audit" in root["endpoints"]
    assert "GET /operations/product-status" in root["endpoints"]
    assert "GET /operations/next-action" in root["endpoints"]
    assert "GET /operations/completion-audit" in root["endpoints"]
    assert "POST /operations/closing-report" in root["endpoints"]
    assert "GET /operations/reports" in root["endpoints"]
    assert "GET /operations/reports/latest" in root["endpoints"]
    assert "GET /operations/reports/{report_name}" in root["endpoints"]
    assert "GET /operations/eval" in root["endpoints"]
    assert "GET /operations/final-preflight" in root["endpoints"]
    assert "POST /operations/final-audit" in root["endpoints"]
    assert "GET /operations/logs" in root["endpoints"]
    assert "POST /operations/restart" in root["endpoints"]
    assert "POST /session/login" in root["endpoints"]
    assert "GET /session/status" in root["endpoints"]
    assert "POST /session/logout" in root["endpoints"]
    assert "POST /agents/{agent_id}/tasks/run-next" in root["endpoints"]
    assert "GET /agents/{agent_id}/relationship-dashboard" in root["endpoints"]
    assert "GET /agents/{agent_id}/temporal-events" in root["endpoints"]
    assert "PATCH /agents/{agent_id}/temporal-events/{event_id}" in root["endpoints"]
    assert "DELETE /agents/{agent_id}/temporal-events/{event_id}" in root["endpoints"]
    assert openapi["openapi"] == "3.1.0"
    assert "/agents/{agent_id}/tick" in openapi["paths"]
    assert "/dashboard" in openapi["paths"]
    assert "post" in openapi["paths"]["/agents/{agent_id}/tick"]
    assert "delete" in openapi["paths"]["/agents/{agent_id}/episodes/{episode_id}"]
    assert "delete" in openapi["paths"]["/agents/{agent_id}/memory/facts/{fact_id}"]
    assert "/agents/{agent_id}/temporal-events/{event_id}" in openapi["paths"]
    assert "patch" in openapi["paths"]["/agents/{agent_id}/temporal-events/{event_id}"]
    assert "/agents/{agent_id}/relationship-dashboard" in openapi["paths"]
    assert "/operations/claude" in openapi["paths"]
    assert "/operations/claude/configure" in openapi["paths"]
    assert "/operations/claude/disable" in openapi["paths"]
    assert "/operations/deepseek/configure" in openapi["paths"]
    assert "/operations/global-model" in openapi["paths"]
    assert "/operations/global-suggestions" in openapi["paths"]
    assert "/operations/audit" in openapi["paths"]
    assert "/operations/product-status" in openapi["paths"]
    assert "/operations/next-action" in openapi["paths"]
    assert "/operations/completion-audit" in openapi["paths"]
    assert "/operations/closing-report" in openapi["paths"]
    assert "post" in openapi["paths"]["/operations/closing-report"]
    assert "/operations/reports" in openapi["paths"]
    assert "/operations/reports/latest" in openapi["paths"]
    assert "/operations/reports/{report_name}" in openapi["paths"]
    assert openapi["paths"]["/operations/reports/{report_name}"]["get"]["parameters"][0]["name"] == "report_name"
    assert "/operations/eval" in openapi["paths"]
    assert "/operations/final-preflight" in openapi["paths"]
    assert "/operations/final-audit" in openapi["paths"]
    assert "/operations/logs" in openapi["paths"]
    assert "/operations/restart" in openapi["paths"]
    assert "/session/status" in openapi["paths"]
    assert "/session/logout" in openapi["paths"]
    assert health == {"ok": True, "service": "nino"}
    assert mode["local_first"] is True
    assert mode["network_required_for_core"] is False
    assert mode["external_llm"]["enabled"] is False
    assert mode["external_llm"]["config"]["enabled"] is False
    assert "memory" in mode["offline_capabilities"]
    assert claude["configured"] is False
    assert global_model_initial["privacy"] == "anonymous_aggregate"
    assert global_model_initial["global_model"]["conversation_count"] == 0
    assert global_suggestions_initial["privacy"] == "anonymous_aggregate"
    assert global_suggestions_initial["suggestions"] == []
    assert claude["api_key_present"] is False
    assert claude["api_key_source"] is None
    assert claude["keychain_service"] is None
    assert claude["config_errors"] == []
    assert "NINO_LLM_PROVIDER" in claude["missing"]
    assert "scripts/ninoctl finish --key-stdin" in claude["setup_commands"]
    assert "scripts/ninoctl finish --key-env" in claude["setup_commands"]
    assert "scripts/ninoctl configure-claude --keychain-service nino-anthropic" in claude["setup_commands"]
    assert "scripts/ninoctl final-audit" in claude["setup_commands"]
    assert "scripts/ninoctl live-audit" not in claude["setup_commands"]
    assert "ANTHROPIC_API_KEY" not in json.dumps(claude["setup_commands"])
    assert product_audit["ok"] is True
    assert product_audit["final_preflight_command"] == "scripts/ninoctl final-preflight"
    assert product_audit["final_audit_command"] == "scripts/ninoctl final-audit"
    assert product_audit["final_audit_requirements"] == [
        "launchd_service",
        "runtime_database_matches",
        "claude_configured",
        "claude_live",
    ]
    assert product_audit["final_readiness"]["claude_configured"] is False
    assert product_audit["final_readiness"]["local_audit_ok"] is True
    assert isinstance(product_audit["final_readiness"]["launchd_observed"], bool)
    assert product_audit["final_readiness"]["ready_for_final_preflight"] is False
    assert product_audit["final_readiness"]["ready_for_final_audit"] is False
    assert "NINO_LLM_PROVIDER" in product_audit["final_readiness"]["blockers"]
    assert "scripts/ninoctl final-audit" in product_audit["final_readiness"]["next_commands"]
    assert {check["name"] for check in product_audit["checks"]} >= {
        "sqlite_database_exists",
        "backup_directory_available",
        "claude_live",
    }
    assert product_status["ok"] is False
    assert product_status["final_preflight_ok"] is False
    assert product_status["eval_ok"] is True
    assert product_status["eval_case_count"] >= 1
    assert any(blocker["name"] == "claude_configured" for blocker in product_status["blockers"])
    assert any(blocker["name"] == "closing_evidence" for blocker in product_status["blockers"])
    assert "scripts/ninoctl finish --key-stdin" in product_status["next_commands"]
    assert product_status["recommended_next_action"] == "scripts/ninoctl finish --key-stdin"
    assert next_action["ok"] is True
    assert next_action["recommended_next_action"] == product_status["recommended_next_action"]
    assert next_action["product_ok"] is False
    assert any(blocker["name"] == "claude_configured" for blocker in next_action["blockers"])
    assert product_status["audit"]["final_readiness"]["ready_for_final_preflight"] is False
    assert product_status["eval"]["ok"] is True
    assert product_status["latest_report"]["ok"] is False
    assert product_status["latest_report"]["error"] == "report_not_found"
    assert product_status["latest_report_current"]["ok"] is False
    assert product_status["latest_report_current"]["reason"] == "report_not_found"
    assert completion_audit["ok"] is False
    assert completion_audit["latest_report"]["ok"] is False
    assert completion_audit["latest_report"]["error"] == "report_not_found"
    assert completion_audit["latest_report_current"]["ok"] is False
    assert completion_audit["latest_report_current"]["reason"] == "report_not_found"
    assert completion_audit["recommended_next_action"] == "scripts/ninoctl finish --key-stdin"
    assert {item["id"] for item in completion_audit["requirements"]} >= {
        "runtime_persistent",
        "ui_operational",
        "memory_continuity",
        "safety_controls",
        "backups",
        "living_agent",
        "regression_eval",
        "closing_evidence",
        "claude_configured",
        "claude_live",
    }
    closing_evidence = next(item for item in completion_audit["requirements"] if item["id"] == "closing_evidence")
    assert "GET /operations/reports/latest" in closing_evidence["evidence"]
    assert "latest_report_current" in closing_evidence["evidence"]
    assert closing_evidence["ok"] is False
    assert {item["id"] for item in completion_audit["blockers"]} == {
        "closing_evidence",
        "claude_configured",
        "claude_live",
    }
    assert "scripts/ninoctl finish --key-stdin" in completion_audit["next_commands"]
    assert closing_report["ok"] is True
    assert Path(closing_report["path"]).exists()
    assert Path(closing_report["path"]).parent == tmp_path / "reports"
    assert closing_report["report"]["report_file"]["path"] == closing_report["path"]
    assert closing_report["report"]["report_file"]["name"] == Path(closing_report["path"]).name
    assert closing_report["report"]["summary"]["blockers"] == ["claude_configured", "claude_live"]
    assert closing_report["report"]["summary"]["completion_audit_ok"] is False
    assert closing_report["report"]["summary"]["recommended_next_action"] == "scripts/ninoctl finish --key-stdin"
    assert closing_report["report"]["product_status"]["eval_ok"] is True
    assert closing_report["report"]["product_status"]["latest_report"]["name"] == Path(closing_report["path"]).name
    assert closing_report["report"]["product_status"]["latest_report_current"]["ok"] is True
    assert closing_report["report"]["completion_audit"]["latest_report"]["name"] == Path(closing_report["path"]).name
    assert closing_report["report"]["completion_audit"]["latest_report_current"]["ok"] is True
    assert closing_report["report"]["nino_profile"]["profile"]["agent_id"] == "nino"
    assert reports["ok"] is True
    assert reports["report_dir"] == str(tmp_path / "reports")
    assert reports["reports"][0]["path"] == closing_report["path"]
    assert reports["reports"][0]["name"].startswith("nino-closing-")
    assert reports["reports"][0]["size_bytes"] > 0
    assert report["ok"] is True
    assert report["name"] == reports["reports"][0]["name"]
    assert report["report"]["summary"] == closing_report["report"]["summary"]
    assert latest_report["ok"] is True
    assert latest_report["name"] == report["name"]
    assert latest_report["report"]["summary"] == closing_report["report"]["summary"]
    product_status_after_report = _request(app, "GET", "/operations/product-status")
    assert product_status_after_report["latest_report"]["name"] == report["name"]
    assert product_status_after_report["latest_report"]["blockers"] == ["claude_configured", "claude_live"]
    assert product_status_after_report["latest_report_current"]["ok"] is True
    assert product_status_after_report["latest_report_current"]["report_name"] == report["name"]
    completion_audit_after_report = _request(app, "GET", "/operations/completion-audit")
    assert completion_audit_after_report["latest_report"]["name"] == report["name"]
    assert completion_audit_after_report["latest_report"]["blockers"] == ["claude_configured", "claude_live"]
    assert completion_audit_after_report["latest_report_current"]["ok"] is True
    assert completion_audit_after_report["latest_report_current"]["report_name"] == report["name"]
    assert completion_audit_after_report["recommended_next_action"] == "scripts/ninoctl finish --key-stdin"
    assert invalid_report == {"ok": False, "error": "invalid_report_name"}
    assert product_eval["ok"] is True
    assert product_eval["path"] == "eval"
    assert product_eval["case_count"] >= 1
    assert product_eval["results"][0]["ok"] is True
    assert final_preflight["ok"] is False
    assert final_preflight["require_launchd"] is True
    assert final_preflight["require_claude_config"] is True
    assert final_preflight["final_readiness"]["ready_for_final_preflight"] is False
    assert "claude_configured" in {check["name"] for check in final_preflight["checks"]}
    assert final_audit["ok"] is False
    assert final_audit["require_claude_live"] is True
    assert final_audit["audit_profile"]["strict_final"] is True
    assert final_audit["final_readiness"]["ready_for_final_audit"] is False
    assert tick["tick"] == 1
    assert tick["nino_context"]["agent_id"] == "api-agent"
    assert tick["nino_context"]["response_source"] == "policy"
    assert tick["nino_context"]["llm_provider"] is None
    assert isinstance(tick["nino_context"]["memory_candidates"], list)
    if tick["nino_context"]["memory_candidates"]:
        candidate = tick["nino_context"]["memory_candidates"][0]
        assert "fact_id" in candidate
        assert "source_episode_id" in candidate
        assert candidate["memory_type"] in {"hot", "cold"}
    assert tick["auto_consolidated_count"] == 1
    assert tick["auto_consolidation"]["cold_memory_updates"][0]["key"] == "preference"
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


def test_http_api_scopes_memory_by_logged_user(tmp_path) -> None:
    app = create_app(tmp_path / "nino.db")

    ana_login = _request(app, "POST", "/session/login", {"user_id": "Ana", "agent_id": "nino"})
    bob_login = _request(app, "POST", "/session/login", {"user_id": "Bob", "agent_id": "nino"})
    _request(
        app,
        "POST",
        "/users/ana/agents/nino/tick",
        {"intent": "chat", "text": "me llamo Ana y me gusta violin", "salience": 0.9, "confidence": 0.95},
    )
    _request(
        app,
        "POST",
        "/users/bob/agents/nino/tick",
        {"intent": "chat", "text": "me llamo Bob y me gusta piano", "salience": 0.9, "confidence": 0.95},
    )

    ana_facts = _request(app, "GET", "/users/ana/agents/nino/memory/facts?status=active")
    bob_facts = _request(app, "GET", "/users/bob/agents/nino/memory/facts?status=active")
    ana_search = _request(app, "POST", "/users/ana/agents/nino/memory/search", {"query": "como me llamo", "memory_type_filter": "cold"})
    bob_search = _request(app, "POST", "/users/bob/agents/nino/memory/search", {"query": "como me llamo", "memory_type_filter": "cold"})
    ana_agents = _request(app, "GET", "/users/ana/agents")
    bob_agents = _request(app, "GET", "/users/bob/agents")

    assert ana_login["user_id"] == "ana"
    assert ana_login["session_token"]
    assert ana_login["scoped_agent_id"] == "user::ana::agent::nino"
    assert bob_login["scoped_agent_id"] == "user::bob::agent::nino"
    assert ana_agents["agents"] == ["nino"]
    assert bob_agents["agents"] == ["nino"]
    assert "ana" in json.dumps(ana_facts).lower()
    assert "bob" not in json.dumps(ana_facts).lower()
    assert "bob" in json.dumps(bob_facts).lower()
    assert "ana" not in json.dumps(bob_facts).lower()
    assert all("bob" not in candidate["statement"].lower() for candidate in ana_search["memory_candidates"])
    assert all("ana" not in candidate["statement"].lower() for candidate in bob_search["memory_candidates"])


def test_http_api_can_require_session_token_for_user_scoped_routes(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("NINO_REQUIRE_SESSION", "true")
    app = create_app(tmp_path / "nino.db")

    login = _request(app, "POST", "/session/login", {"user_id": "Ana", "agent_id": "nino"})
    session = _request(app, "GET", "/session/status", headers={"X-Nino-Session": login["session_token"]})
    missing_status, missing = _request_status(app, "GET", "/users/ana/agents/nino/conversation")
    wrong_status, wrong = _request_status(
        app,
        "GET",
        "/users/bob/agents/nino/conversation",
        headers={"X-Nino-Session": login["session_token"]},
    )
    ok = _request(
        app,
        "GET",
        "/users/ana/agents/nino/conversation",
        headers={"X-Nino-Session": login["session_token"]},
    )
    logout = _request(app, "POST", "/session/logout", {}, headers={"X-Nino-Session": login["session_token"]})
    logged_out_status, logged_out = _request_status(
        app,
        "GET",
        "/users/ana/agents/nino/conversation",
        headers={"X-Nino-Session": login["session_token"]},
    )

    assert session["authenticated"] is True
    assert session["user_id"] == "ana"
    assert missing_status.startswith("401")
    assert missing["error"] == "session_required"
    assert wrong_status.startswith("401")
    assert wrong["error"] == "session_user_mismatch"
    assert ok["turns"] == []
    assert logout["logged_out"] is True
    assert logged_out_status.startswith("401")
    assert logged_out["error"] == "session_required"


def test_prod_refuses_to_start_without_required_sessions(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("NINO_ENV", "prod")
    monkeypatch.delenv("NINO_REQUIRE_SESSION", raising=False)
    monkeypatch.setenv("NINO_PASSWORD_HASH", hash_password("correct horse battery staple"))

    with pytest.raises(RuntimeError, match="NINO_REQUIRE_SESSION=true"):
        create_app(tmp_path / "nino.db")


def test_prod_login_requires_password_and_counts_failures(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("NINO_ENV", "prod")
    monkeypatch.setenv("NINO_REQUIRE_SESSION", "true")
    monkeypatch.setenv("NINO_PASSWORD_HASH", hash_password("correct horse battery staple"))
    app = create_app(tmp_path / "nino.db")
    headers = {"X-Forwarded-Proto": "https"}

    bad_status, bad, _ = _request_status_headers(
        app,
        "POST",
        "/session/login",
        {"user_id": "Ana", "agent_id": "nino", "password": "wrong"},
        headers=headers,
    )
    for _ in range(4):
        _request_status_headers(
            app,
            "POST",
            "/session/login",
            {"user_id": "Ana", "agent_id": "nino", "password": "wrong"},
            headers=headers,
        )
    blocked_status, blocked, _ = _request_status_headers(
        app,
        "POST",
        "/session/login",
        {"user_id": "Ana", "agent_id": "nino", "password": "correct horse battery staple"},
        headers=headers,
    )

    assert bad_status.startswith("401")
    assert bad["error"] == "bad_credentials"
    assert blocked_status.startswith("429")
    assert blocked["error"] == "login_rate_limited"
    failures = [event for event in app.service.security_audit if event["type"] == "login_failed"]
    assert failures
    assert failures[-1]["payload"]["ip"] == "198.51.100.9"


def test_prod_login_correct_password_sets_secure_cookie_and_session_valid(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("NINO_ENV", "prod")
    monkeypatch.setenv("NINO_REQUIRE_SESSION", "true")
    monkeypatch.setenv("NINO_PASSWORD_HASH", hash_password("correct horse battery staple"))
    app = create_app(tmp_path / "nino.db")

    status, login, headers = _request_status_headers(
        app,
        "POST",
        "/session/login",
        {"user_id": "Ana", "agent_id": "nino", "password": "correct horse battery staple"},
        headers={"X-Forwarded-Proto": "https"},
    )
    session = _request(
        app,
        "GET",
        "/session/status",
        headers={"X-Nino-Session": login["session_token"], "X-Forwarded-Proto": "https"},
    )

    assert status.startswith("200")
    assert session["authenticated"] is True
    assert token_hash(login["session_token"]) in app.service.sessions
    assert login["session_token"] not in app.service.sessions
    assert "HttpOnly" in headers["Set-Cookie"]
    assert "Secure" in headers["Set-Cookie"]
    assert "SameSite=Strict" in headers["Set-Cookie"]
    assert any(event["type"] == "login_ok" for event in app.service.security_audit)


def test_prod_user_route_requires_session_and_expired_session_is_rejected(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("NINO_ENV", "prod")
    monkeypatch.setenv("NINO_REQUIRE_SESSION", "true")
    monkeypatch.setenv("NINO_PASSWORD_HASH", hash_password("correct horse battery staple"))
    app = create_app(tmp_path / "nino.db")
    headers = {"X-Forwarded-Proto": "https"}
    login = _request(app, "POST", "/session/login", {"user_id": "Ana", "agent_id": "nino", "password": "correct horse battery staple"}, headers=headers)
    missing_status, missing = _request_status(app, "GET", "/users/ana/agents/nino/conversation", headers=headers)

    digest = token_hash(login["session_token"])
    app.service.sessions[digest]["expires_at"] = "2000-01-01T00:00:00+00:00"
    expired_status, expired = _request_status(
        app,
        "GET",
        "/users/ana/agents/nino/conversation",
        headers={"X-Nino-Session": login["session_token"], "X-Forwarded-Proto": "https"},
    )

    assert missing_status.startswith("401")
    assert missing["error"] == "session_required"
    assert expired_status.startswith("401")
    assert expired["error"] == "session_required"
    assert any(event["type"] == "session_expired" for event in app.service.security_audit)


def test_prod_logout_invalidates_session(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("NINO_ENV", "prod")
    monkeypatch.setenv("NINO_REQUIRE_SESSION", "true")
    monkeypatch.setenv("NINO_PASSWORD_HASH", hash_password("correct horse battery staple"))
    app = create_app(tmp_path / "nino.db")
    headers = {"X-Forwarded-Proto": "https"}
    login = _request(app, "POST", "/session/login", {"user_id": "Ana", "agent_id": "nino", "password": "correct horse battery staple"}, headers=headers)
    logout = _request(app, "POST", "/session/logout", {}, headers={"X-Nino-Session": login["session_token"], "X-Forwarded-Proto": "https"})
    status, body = _request_status(app, "GET", "/session/status", headers={"X-Nino-Session": login["session_token"], "X-Forwarded-Proto": "https"})

    assert logout["logged_out"] is True
    assert status.startswith("200")
    assert body["authenticated"] is False


def test_prod_rejects_non_https_and_disables_app(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("NINO_ENV", "prod")
    monkeypatch.setenv("NINO_REQUIRE_SESSION", "true")
    monkeypatch.setenv("NINO_PASSWORD_HASH", hash_password("correct horse battery staple"))
    app = create_app(tmp_path / "nino.db")

    insecure_status, insecure, _ = _request_status_headers(app, "GET", "/health")
    app_status, app_body, _ = _request_status_headers(app, "GET", "/app", headers={"X-Forwarded-Proto": "https"})
    dashboard_status, dashboard_body, _ = _request_status_headers(app, "GET", "/dashboard", headers={"X-Forwarded-Proto": "https"})

    assert insecure_status.startswith("403")
    assert insecure["error"] == "https_required"
    assert app_status.startswith("404")
    assert app_body["error"] == "app_disabled_in_prod"
    assert dashboard_status.startswith("404")
    assert dashboard_body["error"] == "dashboard_disabled_in_prod"


def test_http_api_tick_accepts_time_context_and_records_temporal_event(tmp_path) -> None:
    app = create_app(tmp_path / "nino.db")
    now = "2026-05-21T10:00:00+00:00"

    tick = _request(
        app,
        "POST",
        "/agents/api-agent/tick",
        {"intent": "chat", "text": "mañana tengo cita", "salience": 0.9, "confidence": 0.95, "now": now},
    )
    relation = _request(app, "GET", "/agents/api-agent/relation")

    assert tick["nino_context"]["current_time"] == now
    assert relation["relation_state"]["temporal_events"][0]["text"] == "mañana tengo cita"
    assert relation["relation_state"]["temporal_events"][0]["due_at"] == "2026-05-22T09:00:00+00:00"


def test_http_api_onboarding_stores_profile_and_returns_next_question(tmp_path) -> None:
    app = create_app(tmp_path / "nino.db")

    name = _request(
        app,
        "POST",
        "/agents/api-agent/tick",
        {"intent": "onboarding:name", "text": "Pablo", "now": "2026-05-26T10:00:00+02:00"},
    )
    location = _request(
        app,
        "POST",
        "/agents/api-agent/tick",
        {"intent": "onboarding:location", "text": "Madrid", "now": "2026-05-26T10:01:00+02:00"},
    )
    relation = _request(app, "GET", "/agents/api-agent/relation")

    assert "¿De dónde eres" in name["action"]["payload"]["text"]
    assert "¿Cuándo naciste" in location["action"]["payload"]["text"]
    assert relation["relation_state"]["user_name"] == "Pablo"
    assert relation["relation_state"]["user_location"] == "Madrid"
    assert relation["relation_state"]["onboarding"]["answers"]["name"]["value"] == "Pablo"
    assert relation["relation_state"]["onboarding"]["answers"]["location"]["value"] == "Madrid"


def test_http_api_profile_query_and_correction_use_onboarding_profile(tmp_path) -> None:
    app = create_app(tmp_path / "nino.db")

    _request(
        app,
        "POST",
        "/agents/api-agent/tick",
        {"intent": "onboarding:name", "text": "Pablo", "now": "2026-05-26T10:00:00+02:00"},
    )
    _request(
        app,
        "POST",
        "/agents/api-agent/tick",
        {"intent": "onboarding:location", "text": "Madrid", "now": "2026-05-26T10:01:00+02:00"},
    )

    profile = _request(
        app,
        "POST",
        "/agents/api-agent/tick",
        {"intent": "chat", "text": "mi perfil", "now": "2026-05-26T10:02:00+02:00"},
    )
    correction = _request(
        app,
        "POST",
        "/agents/api-agent/tick",
        {"intent": "chat", "text": "corrige mi lugar a Barcelona", "now": "2026-05-26T10:03:00+02:00"},
    )
    relation = _request(app, "GET", "/agents/api-agent/relation")

    assert "Perfil inicial" in profile["action"]["payload"]["text"] or "perfil inicial" in profile["action"]["payload"]["text"]
    assert "- Nombre: Pablo" in profile["action"]["payload"]["text"]
    assert "- Lugar: Madrid" in profile["action"]["payload"]["text"]
    assert "actualizo lugar" in correction["action"]["payload"]["text"].lower()
    assert relation["relation_state"]["user_location"] == "Barcelona"
    assert relation["relation_state"]["onboarding"]["answers"]["location"]["value"] == "Barcelona"


def test_http_api_profile_forget_removes_profile_fields(tmp_path) -> None:
    app = create_app(tmp_path / "nino.db")

    _request(
        app,
        "POST",
        "/agents/api-agent/tick",
        {"intent": "onboarding:name", "text": "Pablo", "now": "2026-05-26T10:00:00+02:00"},
    )
    _request(
        app,
        "POST",
        "/agents/api-agent/tick",
        {"intent": "onboarding:location", "text": "Madrid", "now": "2026-05-26T10:01:00+02:00"},
    )

    forgot_location = _request(
        app,
        "POST",
        "/agents/api-agent/tick",
        {"intent": "chat", "text": "olvida mi lugar", "now": "2026-05-26T10:02:00+02:00"},
    )
    relation = _request(app, "GET", "/agents/api-agent/relation")

    assert "olvido lugar" in forgot_location["action"]["payload"]["text"].lower()
    assert "user_location" not in relation["relation_state"]
    assert "location" not in relation["relation_state"]["onboarding"]["answers"]
    assert relation["relation_state"]["onboarding"]["answers"]["name"]["value"] == "Pablo"

    forgot_all = _request(
        app,
        "POST",
        "/agents/api-agent/tick",
        {"intent": "chat", "text": "borra mi perfil", "now": "2026-05-26T10:03:00+02:00"},
    )
    relation = _request(app, "GET", "/agents/api-agent/relation")

    assert "todo tu perfil inicial" in forgot_all["action"]["payload"]["text"]
    assert "user_name" not in relation["relation_state"]
    assert "onboarding" not in relation["relation_state"]


def test_http_api_temporal_event_accepts_weekday_and_exact_time(tmp_path) -> None:
    app = create_app(tmp_path / "nino.db")

    _request(
        app,
        "POST",
        "/agents/api-agent/tick",
        {
            "intent": "chat",
            "text": "el jueves a las 17:30 tengo reunión",
            "salience": 0.9,
            "confidence": 0.95,
            "now": "2026-05-25T10:00:00+00:00",
        },
    )
    relation = _request(app, "GET", "/agents/api-agent/relation")

    assert relation["relation_state"]["temporal_events"][0]["kind"] == "reunion"
    assert relation["relation_state"]["temporal_events"][0]["due_at"] == "2026-05-28T17:30:00+00:00"
    assert relation["relation_state"]["temporal_events"][0]["lead_time_hours"] == 0.5
    assert relation["relation_state"]["temporal_events"][0]["reminder_status"] == "offered"


def test_http_api_bare_time_answer_does_not_create_reminder(tmp_path) -> None:
    app = create_app(tmp_path / "nino.db")

    _request(
        app,
        "POST",
        "/agents/api-agent/tick",
        {
            "intent": "chat",
            "text": "a las 18",
            "salience": 0.4,
            "confidence": 0.9,
            "now": "2026-05-26T10:00:00+02:00",
        },
    )
    relation = _request(app, "GET", "/agents/api-agent/relation")

    assert relation["relation_state"].get("temporal_events", []) == []


def test_http_api_bare_time_without_intent_does_not_create_any_event(tmp_path) -> None:
    app = create_app(tmp_path / "nino.db")

    _request(
        app,
        "POST",
        "/agents/api-agent/tick",
        {
            "intent": "chat",
            "text": "sobre las 6",
            "salience": 0.4,
            "confidence": 0.9,
            "now": "2026-05-26T10:00:00+02:00",
        },
    )
    relation = _request(app, "GET", "/agents/api-agent/relation")

    assert relation["relation_state"].get("temporal_events", []) == []


def test_http_api_explicit_absolute_reminder_creates_confirmed_reminder(tmp_path) -> None:
    app = create_app(tmp_path / "nino.db")

    tick = _request(
        app,
        "POST",
        "/agents/api-agent/tick",
        {
            "intent": "chat",
            "text": "recuérdame a las 18 que llame a Ana",
            "salience": 0.9,
            "confidence": 0.95,
            "now": "2026-05-26T10:00:00+02:00",
        },
    )
    relation = _request(app, "GET", "/agents/api-agent/relation")
    event = relation["relation_state"]["temporal_events"][0]

    assert event["kind"] == "recordatorio"
    assert event["text"] == "llame a Ana"
    assert event["due_at"] == "2026-05-26T18:00:00+02:00"
    assert event["reminder_status"] == "confirmed"
    assert "direct_reminder_created" in tick["reason_trace"]


def test_http_api_temporal_event_accepts_dentist_with_browser_local_time(tmp_path) -> None:
    app = create_app(tmp_path / "nino.db")

    _request(
        app,
        "POST",
        "/agents/api-agent/tick",
        {
            "intent": "chat",
            "text": "hoy tengo dentista a las 11",
            "salience": 0.9,
            "confidence": 0.95,
            "now": "2026-05-26T09:30:00+02:00",
        },
    )
    relation = _request(app, "GET", "/agents/api-agent/relation")
    event = relation["relation_state"]["temporal_events"][0]

    assert event["kind"] == "dentista"
    assert event["due_at"] == "2026-05-26T11:00:00+02:00"
    assert event["next_due_at"] == "2026-05-26T11:00:00+02:00"
    assert event["reminder_status"] == "offered"
    assert event["reminder_offset_minutes"] == 30


def test_http_api_proactivity_reminds_dentist_event_at_local_time(tmp_path) -> None:
    app = create_app(tmp_path / "nino.db")
    _request(
        app,
        "POST",
        "/agents/api-agent/proactivity/configure",
        {"consent": "allowed", "max_messages_per_day": 3, "min_hours_between": 0},
    )
    first = _request(
        app,
        "POST",
        "/agents/api-agent/tick",
        {
            "intent": "chat",
            "text": "hoy tengo dentista a las 11",
            "salience": 0.9,
            "confidence": 0.95,
            "now": "2026-05-26T09:30:00+02:00",
        },
    )
    early = _request(
        app,
        "POST",
        "/agents/api-agent/proactivity/evaluate",
        {"now": "2026-05-26T10:15:00+02:00"},
    )
    confirmed = _request(
        app,
        "POST",
        "/agents/api-agent/tick",
        {"intent": "chat", "text": "sí, recuérdamelo", "now": "2026-05-26T09:31:00+02:00"},
    )
    scheduled = _request(
        app,
        "POST",
        "/agents/api-agent/proactivity/evaluate",
        {"now": "2026-05-26T10:15:00+02:00"},
    )

    out = _request(
        app,
        "POST",
        "/agents/api-agent/proactivity/evaluate",
        {"now": "2026-05-26T10:30:00+02:00"},
    )

    assert "¿Quieres que te dé un toque media hora antes?" in first["action"]["payload"]["text"]
    assert early["should_send"] is False
    assert confirmed["action"]["payload"]["text"] == "Claro, te doy un toque media hora antes."
    assert scheduled["should_send"] is False
    assert "temporal_alarm_scheduled" in scheduled["reason_trace"]
    assert out["should_send"] is True
    assert out["action"]["payload"]["due_at"] == "2026-05-26T11:00:00+02:00"
    assert out["action"]["payload"]["text"] == "Oye, acuérdate: hoy tengo dentista a las 11."
    assert "hoy tengo dentista a las 11" in out["action"]["payload"]["text"]


def test_http_api_relative_reminder_fires_from_polling_time(tmp_path) -> None:
    app = create_app(tmp_path / "nino.db")
    _request(
        app,
        "POST",
        "/agents/api-agent/proactivity/configure",
        {"consent": "allowed", "max_messages_per_day": 3, "min_hours_between": 1},
    )
    created = _request(
        app,
        "POST",
        "/agents/api-agent/tick",
        {
            "intent": "chat",
            "text": "tb recuerdame en 5 minutos que beba agua",
            "salience": 0.7,
            "confidence": 0.95,
            "now": "2026-05-26T12:27:00+02:00",
        },
    )
    relation = _request(app, "GET", "/agents/api-agent/relation")
    event = relation["relation_state"]["temporal_events"][0]
    early = _request(
        app,
        "POST",
        "/agents/api-agent/proactivity/evaluate",
        {"now": "2026-05-26T12:31:00+02:00"},
    )
    due = _request(
        app,
        "POST",
        "/agents/api-agent/proactivity/evaluate",
        {"now": "2026-05-26T12:32:00+02:00"},
    )

    assert created["action"]["payload"]["text"] == "Hecho, te doy un toque a las 12:32: beba agua."
    assert event["kind"] == "recordatorio"
    assert event["text"] == "beba agua"
    assert event["due_at"] == "2026-05-26T12:32:00+02:00"
    assert event["reminder_status"] == "confirmed"
    assert early["should_send"] is False
    assert "temporal_alarm_scheduled" in early["reason_trace"]
    assert due["should_send"] is True
    assert due["action"]["payload"]["text"] == "Oye, acuérdate: beba agua."


def test_http_api_temporal_event_accepts_weekly_recurrence(tmp_path) -> None:
    app = create_app(tmp_path / "nino.db")

    _request(
        app,
        "POST",
        "/agents/api-agent/tick",
        {
            "intent": "chat",
            "text": "cada lunes a las 9 tengo llamada",
            "salience": 0.9,
            "confidence": 0.95,
            "now": "2026-05-25T10:00:00+00:00",
        },
    )
    relation = _request(app, "GET", "/agents/api-agent/relation")
    event = relation["relation_state"]["temporal_events"][0]

    assert event["kind"] == "llamada"
    assert event["due_at"] == "2026-06-01T09:00:00+00:00"
    assert event["next_due_at"] == "2026-06-01T09:00:00+00:00"
    assert event["recurrence"] == "weekly"
    assert event["recurrence_interval_days"] == 7


def test_http_api_lists_updates_and_deletes_temporal_events(tmp_path) -> None:
    app = create_app(tmp_path / "nino.db")
    _request(
        app,
        "POST",
        "/agents/api-agent/tick",
        {
            "intent": "chat",
            "text": "mañana tengo cita",
            "salience": 0.9,
            "confidence": 0.95,
            "now": "2026-05-21T10:00:00+00:00",
        },
    )

    listed = _request(app, "GET", "/agents/api-agent/temporal-events")
    event_id = listed["events"][0]["id"]
    updated = _request(
        app,
        "PATCH",
        f"/agents/api-agent/temporal-events/{event_id}",
        {"status": "paused", "lead_time_hours": 3},
    )
    deleted = _request(app, "DELETE", f"/agents/api-agent/temporal-events/{event_id}")
    empty = _request(app, "GET", "/agents/api-agent/temporal-events")

    assert listed["count"] == 1
    assert updated["updated"] is True
    assert updated["event"]["status"] == "paused"
    assert updated["event"]["lead_time_hours"] == 3
    assert deleted["deleted"] is True
    assert empty["events"] == []


def test_http_api_global_model_is_anonymous_aggregate(tmp_path) -> None:
    app = create_app(tmp_path / "nino.db")
    _request(app, "POST", "/users/ana/agents/nino/tick", {"intent": "music", "text": "soy Ana y me gusta piano"})
    _request(app, "POST", "/users/bob/agents/nino/tick", {"intent": "chat", "text": "mi email es bob@example.com y me gusta piano"})

    out = _request(app, "GET", "/operations/global-model")
    suggestions = _request(app, "GET", "/operations/global-suggestions")
    rendered = json.dumps(out).lower()
    rendered_suggestions = json.dumps(suggestions).lower()

    assert out["privacy"] == "anonymous_aggregate"
    assert out["global_model"]["conversation_count"] == 2
    assert out["global_model"]["tag_counts"]["preference"] == 2
    assert "piano" in out["global_model"]["concept_counts"]
    assert out["global_model"]["concept_counts"]["piano"] == 2
    assert "ana" not in rendered
    assert "bob" not in rendered
    assert "example.com" not in rendered
    assert suggestions["privacy"] == "anonymous_aggregate"
    assert any(item["concept"] == "piano" for item in suggestions["suggestions"])
    assert "ana" not in rendered_suggestions
    assert "bob" not in rendered_suggestions
    assert "example.com" not in rendered_suggestions


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


def test_http_api_reads_redacted_server_logs(tmp_path) -> None:
    db_path = tmp_path / "nino.db"
    log_path = tmp_path / "nino-server.log"
    log_path.write_text(
        "ok\nANTHROPIC_API_KEY=secret-value\nheader sk-ant-12345678SECRET\n",
        encoding="utf-8",
    )
    app = create_app(db_path)

    logs = _request(app, "GET", "/operations/logs")

    assert logs["ok"] is True
    assert logs["exists"] is True
    assert logs["path"] == str(log_path)
    assert "ok" in logs["lines"]
    rendered = "\n".join(logs["lines"])
    assert "secret-value" not in rendered
    assert "sk-ant-12345678SECRET" not in rendered
    assert "[REDACTED]" in rendered


def test_http_api_configures_claude_without_returning_key(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("NINO_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("NINO_CLAUDE_MODEL", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("NINO_KEYCHAIN_SERVICE", raising=False)
    monkeypatch.setenv("NINO_LLM_PROVIDER", "")
    monkeypatch.setenv("NINO_CLAUDE_MODEL", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("NINO_KEYCHAIN_SERVICE", "")
    (tmp_path / ".env.local").write_text("NINO_PORT=8000\nANTHROPIC_API_KEY=old-secret\n", encoding="utf-8")
    app = create_app(tmp_path / "nino.db")

    configured = _request(
        app,
        "POST",
        "/operations/claude/configure",
        {"api_key": "sk-ant-test-secret", "model": "claude-test"},
    )
    status = _request(app, "GET", "/operations/claude")

    content = (tmp_path / ".env.local").read_text(encoding="utf-8")
    assert configured["ok"] is True
    assert configured["claude"]["configured"] is True
    assert configured["claude"]["api_key_present"] is True
    assert configured["claude"]["api_key_source"] == "env"
    assert configured["claude"]["model"] == "claude-test"
    assert status["configured"] is True
    assert "NINO_PORT=8000" in content
    assert "NINO_LLM_PROVIDER=claude" in content
    assert "NINO_CLAUDE_MODEL=claude-test" in content
    assert "ANTHROPIC_API_KEY=sk-ant-test-secret" in content
    assert "old-secret" not in content
    assert "sk-ant-test-secret" not in json.dumps(configured)
    assert oct((tmp_path / ".env.local").stat().st_mode & 0o777) == "0o600"


def test_http_api_configures_claude_in_keychain_without_env_secret(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("USER", "tester")
    monkeypatch.setenv("NINO_LLM_PROVIDER", "")
    monkeypatch.setenv("NINO_CLAUDE_MODEL", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("NINO_KEYCHAIN_SERVICE", "")
    calls: list[list[str]] = []

    class Completed:
        stdout = ""

    def fake_run(args: list[str], **_kwargs: object) -> Completed:
        calls.append(args)
        return Completed()

    monkeypatch.setattr("nino.api.subprocess.run", fake_run)
    monkeypatch.setattr("nino.llm._keychain_api_key", lambda service: "sk-ant-keychain-secret" if service == "nino-test" else None)
    app = create_app(tmp_path / "nino.db")

    configured = _request(
        app,
        "POST",
        "/operations/claude/configure",
        {
            "api_key": "sk-ant-keychain-secret",
            "model": "claude-test",
            "use_keychain": True,
            "keychain_service": "nino-test",
        },
    )

    content = (tmp_path / ".env.local").read_text(encoding="utf-8")
    assert configured["ok"] is True
    assert configured["mode"] == "keychain"
    assert configured["keychain_service"] == "nino-test"
    assert configured["claude"]["configured"] is True
    assert configured["claude"]["api_key_source"] == "keychain"
    assert "NINO_KEYCHAIN_SERVICE=nino-test" in content
    assert "ANTHROPIC_API_KEY" not in content
    assert "sk-ant-keychain-secret" not in content
    assert "sk-ant-keychain-secret" not in json.dumps(configured)
    assert calls[0][:4] == ["/usr/bin/security", "add-generic-password", "-U", "-a"]


def test_http_api_configures_deepseek_without_returning_key(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("NINO_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("NINO_DEEPSEEK_API_KEY", raising=False)
    (tmp_path / ".env.local").write_text(
        "NINO_PORT=8000\nANTHROPIC_API_KEY=old-secret\nNINO_DEEPSEEK_API_KEY=old-deepseek\n",
        encoding="utf-8",
    )
    app = create_app(tmp_path / "nino.db")

    configured = _request(
        app,
        "POST",
        "/operations/deepseek/configure",
        {
            "api_key": "deepseek-secret",
            "model": "deepseek-chat",
            "base_url": "https://example.test/chat/completions",
        },
    )
    status = _request(app, "GET", "/operations/claude")
    content = (tmp_path / ".env.local").read_text(encoding="utf-8")

    assert configured["ok"] is True
    assert configured["provider"] == "deepseek"
    assert configured["llm"]["configured"] is True
    assert configured["llm"]["provider"] == "deepseek"
    assert status["configured"] is True
    assert status["provider"] == "deepseek"
    assert status["api_key_present"] is True
    assert "deepseek-secret" not in json.dumps(configured)
    assert "NINO_PORT=8000" in content
    assert "NINO_LLM_PROVIDER=deepseek" in content
    assert "NINO_DEEPSEEK_MODEL=deepseek-chat" in content
    assert "NINO_DEEPSEEK_BASE_URL=https://example.test/chat/completions" in content
    assert "NINO_DEEPSEEK_API_KEY=deepseek-secret" in content
    assert "old-secret" not in content
    assert "old-deepseek" not in content
    assert oct((tmp_path / ".env.local").stat().st_mode & 0o777) == "0o600"


def test_http_api_disables_claude_and_cleans_local_config(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("NINO_LLM_PROVIDER", "claude")
    monkeypatch.setenv("NINO_CLAUDE_MODEL", "claude-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-secret")
    monkeypatch.setenv("NINO_KEYCHAIN_SERVICE", "nino-test")
    (tmp_path / ".env.local").write_text(
        "NINO_PORT=8000\n"
        "NINO_LLM_PROVIDER=claude\n"
        "NINO_CLAUDE_MODEL=claude-test\n"
        "ANTHROPIC_API_KEY=sk-ant-test-secret\n"
        "NINO_KEYCHAIN_SERVICE=nino-test\n",
        encoding="utf-8",
    )
    calls: list[list[str]] = []

    class Completed:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(args: list[str], **_kwargs: object) -> Completed:
        calls.append(args)
        return Completed()

    monkeypatch.setattr("nino.api.subprocess.run", fake_run)
    app = create_app(tmp_path / "nino.db")

    denied = _request(app, "POST", "/operations/claude/disable", {})
    disabled = _request(
        app,
        "POST",
        "/operations/claude/disable",
        {"confirm": True, "remove_keychain": True, "keychain_service": "nino-test"},
    )
    status = _request(app, "GET", "/operations/claude")

    content = (tmp_path / ".env.local").read_text(encoding="utf-8")
    assert denied["ok"] is False
    assert denied["error"] == "confirmation_required"
    assert disabled["ok"] is True
    assert disabled["keychain_removed"] is True
    assert disabled["claude"]["configured"] is False
    assert status["configured"] is False
    assert "NINO_PORT=8000" in content
    assert "NINO_LLM_PROVIDER" not in content
    assert "NINO_CLAUDE_MODEL" not in content
    assert "ANTHROPIC_API_KEY" not in content
    assert "NINO_KEYCHAIN_SERVICE" not in content
    assert "sk-ant-test-secret" not in json.dumps(disabled)
    assert ["/usr/bin/security", "delete-generic-password", "-s", "nino-test"] in calls


def test_http_api_restart_requires_confirmation_and_can_be_injected(tmp_path) -> None:
    calls: list[str] = []
    runtime = create_persistent_runtime(tmp_path / "nino.db")
    app = create_app_with_runtime(runtime, db_path=tmp_path / "nino.db", restart_callback=lambda: calls.append("restart"))

    denied = _request(app, "POST", "/operations/restart", {})
    scheduled = _request(app, "POST", "/operations/restart", {"confirm": True})

    assert denied == {"ok": False, "error": "confirmation_required"}
    assert scheduled == {"ok": True, "scheduled": True, "method": "callback"}
    assert calls == ["restart"]


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

    tick = _request(
        app,
        "POST",
        "/agents/api-agent/tick",
        {"intent": "music", "text": "me gusta el piano", "salience": 0.9},
    )
    self_model = _request(app, "GET", "/agents/api-agent/self-model")
    world_model = _request(app, "GET", "/agents/api-agent/world-model")
    relationship_dashboard = _request(app, "GET", "/agents/api-agent/relationship-dashboard")

    assert self_model["self_model"]["interaction_count"] == 1
    assert self_model["self_model"]["identity_stage"] == "early_childhood"
    assert world_model["world_model"]["concept_counts"]["piano"] == 1
    assert relationship_dashboard["dashboard"]["agent_id"] == "api-agent"
    assert relationship_dashboard["dashboard"]["privacy"]["raw_conversation_included"] is False
    assert relationship_dashboard["dashboard"]["maturity"]["interaction_count"] >= 1


def test_http_api_exposes_narrative(tmp_path) -> None:
    app = create_app(tmp_path / "nino.db")

    _request(app, "POST", "/agents/api-agent/tick", {"intent": "chat", "text": "soy Pablo", "salience": 0.8})
    _request(app, "POST", "/agents/api-agent/tick", {"intent": "music", "text": "me gusta el piano", "salience": 0.9})
    narrative = _request(app, "GET", "/agents/api-agent/narrative")

    assert narrative["narrative"]["known_user"] == "Pablo"
    assert "piano" in narrative["narrative"]["preferences"]
    assert "Soy amigo" in narrative["narrative"]["summary"]


def test_relationship_dashboard_learns_from_hits_mistakes_and_limits(tmp_path) -> None:
    app = create_app(tmp_path / "nino.db")

    _request(app, "POST", "/agents/api-agent/tick", {"intent": "chat", "text": "gracias, eso me ayuda", "salience": 0.7})
    _request(app, "POST", "/agents/api-agent/tick", {"intent": "chat", "text": "te equivocas, no era eso", "salience": 0.8})
    _request(app, "POST", "/agents/api-agent/tick", {"intent": "chat", "text": "no insistas con ese tema", "salience": 0.8})
    dashboard = _request(app, "GET", "/agents/api-agent/relationship-dashboard")["dashboard"]
    state = _request(app, "GET", "/agents/api-agent/state")

    counts = dashboard["relationship_learning"]["counts"]
    assert counts["positive"] == 1
    assert counts["negative"] == 1
    assert counts["stop"] == 1
    assert dashboard["response_style"]["caution"] > 0.5
    assert dashboard["response_style"]["initiative"] < 0.5
    assert dashboard["privacy"]["raw_conversation_included"] is False
    assert "no era eso" not in json.dumps(dashboard)
    assert state["relation_state"]["relationship_learning"]["last_outcome"] == "stop"


def test_active_conversation_thread_connects_short_followups(tmp_path) -> None:
    app = create_app(tmp_path / "nino.db")

    _request(
        app,
        "POST",
        "/agents/api-agent/tick",
        {"intent": "chat", "text": "Tengo una idea para amigo: que aprenda del tono de cada conversacion.", "salience": 0.8},
    )
    followup = _request(
        app,
        "POST",
        "/agents/api-agent/tick",
        {"intent": "chat", "text": "y que no repita el mismo error cuando se lo corregimos", "salience": 0.8},
    )
    state = _request(app, "GET", "/agents/api-agent/state")
    dashboard = _request(app, "GET", "/agents/api-agent/relationship-dashboard")["dashboard"]

    thread = state["relation_state"]["active_conversation_thread"]
    assert thread["turn_count"] == 2
    assert "aprenda del tono" in thread["summary"]
    assert "mismo error" in thread["summary"]
    assert "active_thread_continuity" in followup["reason_trace"]
    assert dashboard["active_conversation_thread"]["present"] is True
    assert dashboard["active_conversation_thread"]["turn_count"] == 2


def test_continuity_correction_is_counted_as_learning_signal(tmp_path) -> None:
    app = create_app(tmp_path / "nino.db")

    _request(app, "POST", "/agents/api-agent/tick", {"intent": "chat", "text": "Estoy explicando una idea en dos partes", "salience": 0.7})
    _request(app, "POST", "/agents/api-agent/tick", {"intent": "chat", "text": "te pierdes y no relacionas el contexto", "salience": 0.8})
    dashboard = _request(app, "GET", "/agents/api-agent/relationship-dashboard")["dashboard"]

    counts = dashboard["relationship_learning"]["counts"]
    assert counts["negative"] >= 1
    assert counts["continuity_miss"] == 1
    assert dashboard["response_style"]["caution"] > 0.5


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
    hot_search = _request(app, "POST", "/agents/api-agent/memory/search", {"query": "música", "memory_type_filter": "hot"})
    temporal_miss = _request(app, "POST", "/agents/api-agent/memory/search", {"query": "que hicimos hace dos semanas"})
    snapshot = _request(app, "GET", "/development/snapshot")

    assert marked["updated"] is True
    assert cleared["cleared"] == 1
    assert search["memory_candidates"]
    assert search["memory_type_counts"]["total"] == len(search["memory_candidates"])
    assert search["visible_memory_type_counts"] == search["memory_type_counts"]
    assert all(candidate["memory_type"] in {"cold", "hot"} for candidate in search["memory_candidates"])
    assert hot_search["memory_type_filter"] == "hot"
    assert hot_search["visible_candidates"] == len(hot_search["memory_candidates"])
    assert all(candidate["memory_type"] == "hot" for candidate in hot_search["memory_candidates"])
    assert temporal_miss["temporal_query"] is True
    assert temporal_miss["temporal_miss"] is True
    assert temporal_miss["temporal_visible_miss"] is True
    assert temporal_miss["visible_candidates"] == 0
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
            "text": "me preocupa estudiar historia",
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

    tick = _request(
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

    assert tick["auto_consolidated_count"] == 1
    assert cycle["consolidated_count"] == 0
    assert any(candidate["fact_id"].startswith("cold::") for candidate in memory["memory_candidates"])
    assert any(candidate["memory_type"] == "cold" for candidate in memory["memory_candidates"])


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
    filtered = _request(app, "GET", "/agents/api-agent/memory/facts?status=active&key=preference")

    episode_id = episodes["episodes"][0]["episode_id"]
    fact_id = facts["facts"][0]["fact_id"]
    deleted_episode = _request(app, "DELETE", f"/agents/api-agent/episodes/{episode_id}")
    deleted_fact = _request(app, "DELETE", f"/agents/api-agent/memory/facts/{fact_id}")

    assert facts["fact_counts"]["total"] == 1
    assert facts["fact_counts"]["active"] == 1
    assert facts["fact_counts"]["inactive"] == 0
    assert facts["fact_counts"]["active_by_key"]["preference"] == 1
    assert filtered["status_filter"] == "active"
    assert filtered["key_filter"] == "preference"
    assert filtered["visible_facts"] == 1
    assert filtered["visible_fact_counts"]["active_by_key"]["preference"] == 1
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
