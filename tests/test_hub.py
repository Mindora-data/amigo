from __future__ import annotations

import io
import json
from datetime import datetime, timezone

from nino.auth import hash_password
from nino.hub import HubConfig, amigo_snapshot, create_hub_app
from nino.telegram import TelegramLinkStore


NOW = datetime(2026, 6, 18, 12, 0, tzinfo=timezone.utc)


def _seed_amigo_db(path) -> None:
    links = TelegramLinkStore(path)
    code = links.create_code("mindora", NOW)
    links.link_with_code("264946533", code, NOW)
    links.record_group(-100, "puntuacomics", NOW)
    links.record_known_work("Watchmen", medium="cómic", summary="vigilantes", source="telegram_document", now=NOW)
    links.record_social_decision(
        chat_id=-100, message_id=1, sender_id=10, text="hola",
        decision="observe", reason="observe_and_learn", now=NOW,
    )
    links.record_social_decision(
        chat_id=-100, message_id=2, sender_id=11, text="watchmen",
        decision="reply", reason="known_work", now=NOW,
    )
    links.record_action(
        action_type="group_reply", chat_id=-100, target_title="puntuacomics",
        text="hola", sent_ok=True, now=NOW,
    )
    relation = {
        "group_maturity": {
            "participation_guidance": "observa y aprende",
            "observed_messages": 12,
            "questions_seen": 3,
            "bot_replies": 1,
            "social_learning": {"signal_counts": {"general_question": 2, "greeting": 1}},
            "topic_counts": {"comics": 4},
        }
    }
    conn = links.conn
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_states (
            agent_id TEXT PRIMARY KEY, tick INTEGER NOT NULL, drive_vector_json TEXT NOT NULL,
            active_goals_json TEXT NOT NULL, energy REAL NOT NULL, relation_state_json TEXT NOT NULL,
            cognitive_time_json TEXT NOT NULL, self_model_json TEXT NOT NULL,
            world_model_json TEXT NOT NULL, updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT INTO agent_states VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("telegram-group-100", 1, "{}", "[]", 1.0, json.dumps(relation), "{}", "{}", "{}", NOW.isoformat()),
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS learning_journal_entries (
            entry_id TEXT PRIMARY KEY, agent_id TEXT NOT NULL, title TEXT NOT NULL, lesson TEXT NOT NULL,
            source TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            source_episode_id TEXT, tags_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT INTO learning_journal_entries VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("e1", "mindora", "Le gustan los cómics", "Recordar gusto por cómics", "chat", "active",
         NOW.isoformat(), NOW.isoformat(), None, "[]"),
    )
    conn.commit()
    conn.close()


def _call(app, method, path, body=None, cookie=""):
    raw = json.dumps(body).encode("utf-8") if body is not None else b""
    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "wsgi.input": io.BytesIO(raw),
        "CONTENT_LENGTH": str(len(raw)),
        "HTTP_COOKIE": cookie,
    }
    captured: dict = {}

    def start_response(status, headers):
        captured["status"] = status
        captured["headers"] = headers

    chunks = app(environ, start_response)
    captured["body"] = b"".join(chunks).decode("utf-8")
    return captured


def _cookie_from(resp) -> str:
    for key, value in resp["headers"]:
        if key.lower() == "set-cookie":
            return value.split(";")[0]
    return ""


def _config(tmp_path):
    db = tmp_path / "nino.db"
    _seed_amigo_db(db)
    return HubConfig(username="admin", password_hash=hash_password("superseguro12"), amigo_db_path=str(db))


def test_overview_requires_login(tmp_path) -> None:
    app = create_hub_app(_config(tmp_path))
    resp = _call(app, "GET", "/dashboard/api/overview")
    assert resp["status"].startswith("401")


def test_login_rejects_bad_credentials(tmp_path) -> None:
    app = create_hub_app(_config(tmp_path))
    resp = _call(app, "POST", "/dashboard/login", {"user": "admin", "password": "malisima0000"})
    assert resp["status"].startswith("401")
    assert _cookie_from(resp) == ""


def test_login_then_overview_shows_amigo_data(tmp_path) -> None:
    app = create_hub_app(_config(tmp_path))
    login = _call(app, "POST", "/dashboard/login", {"user": "admin", "password": "superseguro12"})
    assert login["status"].startswith("200")
    cookie = _cookie_from(login)
    assert cookie.startswith("hub_session=")

    resp = _call(app, "GET", "/dashboard/api/overview", cookie=cookie)
    assert resp["status"].startswith("200")
    data = json.loads(resp["body"])
    by_id = {i["id"]: i for i in data["initiatives"]}
    assert by_id["amigo"]["status"] == "connected"
    assert by_id["aliado"]["status"] == "not_connected"
    assert by_id["taskos"]["status"] == "not_connected"

    amigo = by_id["amigo"]["snapshot"]
    assert amigo["users"]["linked_count"] == 1
    assert amigo["users"]["links"][0]["user_id"] == "mindora"
    assert amigo["usage"]["group_replies"] == 1
    assert amigo["usage"]["decisions"]["reply"] == 1
    assert amigo["usage"]["decisions"]["observe"] == 1
    assert any(w["title"] == "Watchmen" for w in amigo["learnings"]["known_works"])
    assert amigo["learnings"]["journal_counts"]["active"] == 1
    group_learn = amigo["learnings"]["groups"]
    assert group_learn and group_learn[0]["participation_guidance"] == "observa y aprende"
    assert group_learn[0]["signal_counts"]["general_question"] == 2


def test_login_without_configured_password_is_unavailable(tmp_path) -> None:
    db = tmp_path / "nino.db"
    _seed_amigo_db(db)
    app = create_hub_app(HubConfig(amigo_db_path=str(db)))  # no password_hash
    resp = _call(app, "POST", "/dashboard/login", {"user": "admin", "password": "whatever12345"})
    assert resp["status"].startswith("503")


def test_html_is_served_without_auth(tmp_path) -> None:
    app = create_hub_app(_config(tmp_path))
    resp = _call(app, "GET", "/dashboard")
    assert resp["status"].startswith("200")
    assert "Mindora Labs" in resp["body"]


def test_amigo_snapshot_empty_when_no_db(tmp_path) -> None:
    snap = amigo_snapshot(str(tmp_path / "missing.db"))
    assert snap["connected"] is False
