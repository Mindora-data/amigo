from __future__ import annotations

from io import BytesIO
from datetime import datetime, timedelta, timezone
import json

from nino.api import create_app


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


def test_http_api_ticks_and_restores_state(tmp_path) -> None:
    app = create_app(tmp_path / "nino.db")

    root = _request(app, "GET", "/")
    health = _request(app, "GET", "/health")
    tick = _request(
        app,
        "POST",
        "/agents/api-agent/tick",
        {"intent": "music", "text": "me gusta piano", "salience": 0.9},
    )
    state = _request(app, "GET", "/agents/api-agent/state")
    episodes = _request(app, "GET", "/agents/api-agent/episodes")

    assert root["service"] == "nino"
    assert "GET /health" in root["endpoints"]
    assert health == {"ok": True, "service": "nino"}
    assert tick["tick"] == 1
    assert state["tick"] == 1
    assert len(episodes["episodes"]) == 1
    assert episodes["episodes"][0]["text"] == "me gusta piano"


def test_http_api_exposes_relation_state(tmp_path) -> None:
    app = create_app(tmp_path / "nino.db")

    _request(app, "POST", "/agents/api-agent/tick", {"intent": "chat", "text": "soy Pablo"})
    _request(app, "POST", "/agents/api-agent/tick", {"intent": "chat", "text": "me gusta el piano"})
    relation = _request(app, "GET", "/agents/api-agent/relation")

    assert relation["relation_state"]["user_name"] == "Pablo"
    assert "piano" in relation["relation_state"]["preferences"]


def test_http_api_proactivity_consent_and_frequency(tmp_path) -> None:
    app = create_app(tmp_path / "nino.db")
    now = datetime(2026, 5, 21, 10, tzinfo=timezone.utc)

    _request(
        app,
        "POST",
        "/agents/api-agent/proactivity/configure",
        {"consent": "allowed", "max_messages_per_day": 1, "min_hours_between": 0},
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
