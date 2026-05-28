from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess
import time
from typing import Any, Protocol
from urllib import error, parse, request


AGENT_ID = "nino"


def _connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def _slug(value: str) -> str:
    text = "".join(ch.lower() if ch.isalnum() else "-" for ch in value.strip())
    text = "-".join(part for part in text.split("-") if part)
    return text or "usuario"


def _keychain_secret(service: str) -> str | None:
    if not service:
        return None
    try:
        completed = subprocess.run(
            ["/usr/bin/security", "find-generic-password", "-w", "-s", service],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip() or None


def telegram_token_from_env() -> str:
    token = os.environ.get("NINO_TELEGRAM_BOT_TOKEN", "").strip() or os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if token:
        return token
    token = _keychain_secret(os.environ.get("NINO_TELEGRAM_KEYCHAIN_SERVICE", "").strip())
    if token:
        return token
    raise RuntimeError("missing_telegram_bot_token")


class TelegramLinkStore:
    def __init__(self, db_path: str | Path) -> None:
        self.conn = _connect(db_path)
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS telegram_link (
                chat_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS telegram_link_code (
                code TEXT PRIMARY KEY,
                user_id TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                used_at TEXT
            )
            """
        )
        self.conn.commit()

    def create_code(self, user_id: str, now: datetime | None = None) -> str:
        user = _slug(user_id)
        code = secrets.token_urlsafe(8).replace("-", "").replace("_", "")[:10].upper()
        created_at = (now or datetime.now(timezone.utc)).isoformat()
        self.conn.execute("DELETE FROM telegram_link_code WHERE user_id = ? AND used_at IS NULL", (user,))
        self.conn.execute(
            "INSERT INTO telegram_link_code (code, user_id, created_at, used_at) VALUES (?, ?, ?, NULL)",
            (code, user, created_at),
        )
        self.conn.commit()
        return code

    def link_with_code(self, chat_id: int | str, code: str, now: datetime | None = None) -> str | None:
        normalized = code.strip().upper()
        row = self.conn.execute(
            "SELECT * FROM telegram_link_code WHERE code = ? AND used_at IS NULL",
            (normalized,),
        ).fetchone()
        if row is None:
            return None
        user_id = str(row["user_id"])
        created_at = (now or datetime.now(timezone.utc)).isoformat()
        try:
            self.conn.execute(
                "INSERT INTO telegram_link (chat_id, user_id, created_at) VALUES (?, ?, ?)",
                (str(chat_id), user_id, created_at),
            )
        except sqlite3.IntegrityError:
            self.conn.rollback()
            return None
        self.conn.execute("UPDATE telegram_link_code SET used_at = ? WHERE code = ?", (created_at, normalized))
        self.conn.commit()
        return user_id

    def user_for_chat(self, chat_id: int | str) -> str | None:
        row = self.conn.execute("SELECT user_id FROM telegram_link WHERE chat_id = ?", (str(chat_id),)).fetchone()
        return str(row["user_id"]) if row else None

    def chat_for_user(self, user_id: str) -> str | None:
        row = self.conn.execute("SELECT chat_id FROM telegram_link WHERE user_id = ?", (_slug(user_id),)).fetchone()
        return str(row["chat_id"]) if row else None

    def links(self) -> list[dict[str, str]]:
        rows = self.conn.execute("SELECT chat_id, user_id, created_at FROM telegram_link ORDER BY created_at").fetchall()
        return [{"chat_id": str(row["chat_id"]), "user_id": str(row["user_id"]), "created_at": str(row["created_at"])} for row in rows]


class TelegramAPI(Protocol):
    def get_updates(self, offset: int | None, timeout: int) -> list[dict[str, Any]]:
        ...

    def send_message(self, chat_id: int | str, text: str) -> dict[str, Any]:
        ...


class HTTPTelegramAPI:
    def __init__(self, token: str, base_url: str = "https://api.telegram.org") -> None:
        if not token.strip():
            raise RuntimeError("missing_telegram_bot_token")
        self.base_url = f"{base_url.rstrip('/')}/bot{token}"

    def _call(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        data = json.dumps(payload).encode("utf-8")
        req = request.Request(
            f"{self.base_url}/{method}",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with request.urlopen(req, timeout=35) as response:
            out = json.loads(response.read().decode("utf-8"))
        if not out.get("ok"):
            raise RuntimeError(str(out.get("description") or "telegram_api_error"))
        return out

    def get_updates(self, offset: int | None, timeout: int) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {"timeout": timeout, "allowed_updates": ["message"]}
        if offset is not None:
            payload["offset"] = offset
        return list(self._call("getUpdates", payload).get("result", []))

    def send_message(self, chat_id: int | str, text: str) -> dict[str, Any]:
        return self._call("sendMessage", {"chat_id": chat_id, "text": text})


@dataclass
class BackendClient:
    base_url: str
    password: str = ""

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")
        self._sessions: dict[str, str] = {}

    def _json(self, method: str, path: str, payload: dict[str, Any] | None = None, user_id: str | None = None) -> dict[str, Any]:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if user_id:
            token = self.session_for(user_id)
            headers["X-Nino-Session"] = token
        req = request.Request(f"{self.base_url}{path}", data=data, headers=headers, method=method)
        try:
            with request.urlopen(req, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8")
            try:
                return json.loads(body)
            except json.JSONDecodeError:
                raise RuntimeError(body) from exc

    def session_for(self, user_id: str) -> str:
        user = _slug(user_id)
        if user not in self._sessions:
            payload = {"user_id": user, "agent_id": AGENT_ID, "password": self.password}
            out = self._json("POST", "/session/login", payload)
            token = str(out.get("session_token", "")).strip()
            if not token:
                raise RuntimeError(str(out.get("error") or "backend_login_failed"))
            self._sessions[user] = token
        return self._sessions[user]

    def tick(self, user_id: str, text: str, now: datetime) -> str:
        user = _slug(user_id)
        out = self._json(
            "POST",
            f"/users/{parse.quote(user)}/agents/{AGENT_ID}/tick",
            {"intent": "chat", "text": text, "salience": 0.7, "confidence": 0.8, "now": now.isoformat()},
            user_id=user,
        )
        action = out.get("action") if isinstance(out, dict) else None
        payload = action.get("payload") if isinstance(action, dict) else None
        return str(payload.get("text") or "").strip() if isinstance(payload, dict) else ""

    def evaluate_proactivity(self, user_id: str, now: datetime) -> str | None:
        user = _slug(user_id)
        out = self._json(
            "POST",
            f"/users/{parse.quote(user)}/agents/{AGENT_ID}/proactivity/evaluate",
            {"now": now.isoformat(), "record_send": True},
            user_id=user,
        )
        if not out.get("should_send"):
            return None
        action = out.get("action") if isinstance(out, dict) else None
        payload = action.get("payload") if isinstance(action, dict) else None
        text = str(payload.get("text") or "").strip() if isinstance(payload, dict) else ""
        return text or None


class TelegramBotService:
    def __init__(
        self,
        telegram: TelegramAPI,
        backend: BackendClient,
        links: TelegramLinkStore,
        *,
        poll_timeout: int = 25,
    ) -> None:
        self.telegram = telegram
        self.backend = backend
        self.links = links
        self.poll_timeout = poll_timeout
        self.offset: int | None = None

    def handle_update(self, update: dict[str, Any], now: datetime | None = None) -> None:
        self.offset = int(update.get("update_id", 0)) + 1
        message = update.get("message") if isinstance(update.get("message"), dict) else {}
        chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
        chat_id = chat.get("id")
        text = str(message.get("text") or "").strip()
        if chat_id is None or not text:
            return
        current_time = now or datetime.fromtimestamp(int(message.get("date") or time.time()), tz=timezone.utc)
        user_id = self.links.user_for_chat(chat_id)
        if user_id is None:
            if text.lower().startswith("/link "):
                linked = self.links.link_with_code(chat_id, text.split(maxsplit=1)[1], current_time)
                if linked:
                    self.telegram.send_message(chat_id, f"Vinculado con {linked}. Ya puedes hablar conmigo por aquí.")
                    return
                self.telegram.send_message(chat_id, "Ese código no es válido o ya se usó. Genera uno nuevo desde tu Mac.")
                return
            self.telegram.send_message(
                chat_id,
                "Soy amigo, un asistente con memoria, no una persona. Para proteger tu privacidad necesito vincular este chat. En tu Mac genera un código y envíame: /link CODIGO",
            )
            return
        reply = self.backend.tick(user_id, text, current_time)
        if reply:
            self.telegram.send_message(chat_id, reply)

    def push_proactivity_once(self, now: datetime | None = None) -> int:
        current_time = now or datetime.now(timezone.utc)
        sent = 0
        for link in self.links.links():
            text = self.backend.evaluate_proactivity(link["user_id"], current_time)
            if text:
                self.telegram.send_message(link["chat_id"], text)
                sent += 1
        return sent

    def poll_once(self) -> None:
        for update in self.telegram.get_updates(self.offset, self.poll_timeout):
            self.handle_update(update)
        self.push_proactivity_once()

    def run_forever(self, interval_seconds: float = 1.0) -> None:
        while True:
            self.poll_once()
            time.sleep(interval_seconds)


def build_service(db_path: Path, base_url: str) -> TelegramBotService:
    token = telegram_token_from_env()
    password = os.environ.get("NINO_TELEGRAM_BACKEND_PASSWORD", "").strip()
    return TelegramBotService(HTTPTelegramAPI(token), BackendClient(base_url, password=password), TelegramLinkStore(db_path))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run amigo Telegram long-polling client.")
    parser.add_argument("--db", default=os.environ.get("NINO_DB_PATH", "data/nino.db"))
    parser.add_argument("--base-url", default=os.environ.get("NINO_BASE_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--create-link-code", help="Create one-time Telegram link code for this user_id and exit.")
    parser.add_argument("--once", action="store_true", help="Run one polling/proactivity pass and exit.")
    args = parser.parse_args(argv)

    db_path = Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    links = TelegramLinkStore(db_path)
    if args.create_link_code:
        print(links.create_code(args.create_link_code))
        return 0

    service = build_service(db_path, args.base_url)
    if args.once:
        service.poll_once()
        return 0
    service.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
