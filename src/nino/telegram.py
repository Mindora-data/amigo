from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import sqlite3
import sys
import subprocess
import time
from typing import Any, Protocol
from urllib import error, parse, request
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


AGENT_ID = "nino"
GROUP_AMBIENT_COOLDOWN_SECONDS = 600
GROUP_AMBIENT_COOLDOWN_FAST_SECONDS = 180
GROUP_AMBIENT_COOLDOWN_SLOW_SECONDS = 900
GROUP_MENTION_AFTER_SECONDS = 120
GROUP_AMBIENT_CUES = (
    "?",
    "¿",
    "alguin sabe",
    "que opinais",
    "qué opináis",
    "que opinas",
    "qué opinas",
    "alguna idea",
    "alguien sabe",
    "sabeis",
    "sabéis",
    "quien sabe",
    "quién sabe",
    "cual es",
    "cuál es",
    "donde esta",
    "dónde está",
    "como se",
    "cómo se",
    "como lo veis",
    "cómo lo veis",
    "como estais",
    "cómo estáis",
    "como vais",
    "cómo vais",
    "que tal estais",
    "qué tal estáis",
    "que tal vais",
    "qué tal vais",
    "consejo",
)
GROUP_PARTICIPATION_CUES = (
    "hola",
    "buenas",
    "buenos dias",
    "buenos días",
    "buenas tardes",
    "buenas noches",
    "estoy triste",
    "estoy mal",
    "me siento",
    "me preocupa",
    "tengo miedo",
    "me agobia",
    "necesito hablar",
    "no se que hacer",
    "no sé qué hacer",
    "tengo una idea",
    "os cuento",
)
GROUP_SENSITIVE_CUES = (
    "contraseña",
    "password",
    "dni",
    "tarjeta",
    "cuenta bancaria",
    "telefono",
    "teléfono",
    "direccion",
    "dirección",
)
GROUP_NEGATIVE_REACTION_CUES = (
    "no te metas",
    "pesado",
    "calla",
    "no insistas",
    "no preguntes",
)
GROUP_POSITIVE_REACTION_CUES = (
    "gracias",
    "bien visto",
    "exacto",
    "eso era",
    "correcto",
)


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


def telegram_local_timezone() -> ZoneInfo:
    name = (
        os.environ.get("NINO_TELEGRAM_TIMEZONE", "").strip()
        or os.environ.get("NINO_LOCAL_TIMEZONE", "").strip()
        or os.environ.get("TZ", "").strip()
        or "Europe/Madrid"
    )
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


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
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS telegram_social_decision (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT NOT NULL,
                message_id TEXT,
                sender_id TEXT,
                text_preview TEXT NOT NULL,
                decision TEXT NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL,
                outcome TEXT,
                outcome_at TEXT
            )
            """
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_telegram_social_decision_chat_time ON telegram_social_decision(chat_id, created_at)"
        )
        self.conn.commit()

    def create_code(self, user_id: str, now: datetime | None = None) -> str:
        user = _slug(user_id)
        code = secrets.token_urlsafe(8).replace("-", "").replace("_", "")[:10].upper()
        created_at = (now or datetime.now(timezone.utc)).isoformat()
        self.conn.execute("DELETE FROM telegram_link_code WHERE user_id = ?", (user,))
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

    def record_social_decision(
        self,
        *,
        chat_id: int | str,
        message_id: int | str | None,
        sender_id: int | str | None,
        text: str,
        decision: str,
        reason: str,
        now: datetime,
    ) -> int:
        cursor = self.conn.execute(
            """
            INSERT INTO telegram_social_decision (
                chat_id, message_id, sender_id, text_preview, decision, reason, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (str(chat_id), None if message_id is None else str(message_id), None if sender_id is None else str(sender_id), text[:180], decision, reason, now.isoformat()),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def pending_social_decision(self, chat_id: int | str) -> dict[str, str] | None:
        row = self.conn.execute(
            """
            SELECT * FROM telegram_social_decision
            WHERE chat_id = ? AND decision = 'reply' AND outcome IS NULL
            ORDER BY id DESC
            LIMIT 1
            """,
            (str(chat_id),),
        ).fetchone()
        return dict(row) if row else None

    def mark_social_outcome(self, decision_id: int, outcome: str, now: datetime) -> None:
        self.conn.execute(
            "UPDATE telegram_social_decision SET outcome = ?, outcome_at = ? WHERE id = ? AND outcome IS NULL",
            (outcome, now.isoformat(), decision_id),
        )
        self.conn.commit()


class TelegramAPI(Protocol):
    def get_updates(self, offset: int | None, timeout: int) -> list[dict[str, Any]]:
        ...

    def send_message(self, chat_id: int | str, text: str) -> dict[str, Any]:
        ...

    def get_me(self) -> dict[str, Any]:
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

    def get_me(self) -> dict[str, Any]:
        return dict(self._call("getMe", {}).get("result", {}))


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
        intent = "group_chat" if user.startswith("telegram-group-") else "chat"
        out = self._json(
            "POST",
            f"/users/{parse.quote(user)}/agents/{AGENT_ID}/tick",
            {"intent": intent, "text": text, "salience": 0.7, "confidence": 0.8, "now": now.isoformat()},
            user_id=user,
        )
        action = out.get("action") if isinstance(out, dict) else None
        payload = action.get("payload") if isinstance(action, dict) else None
        return str(payload.get("text") or "").strip() if isinstance(payload, dict) else ""

    def observe(self, user_id: str, text: str, now: datetime) -> None:
        user = _slug(user_id)
        self._json(
            "POST",
            f"/users/{parse.quote(user)}/agents/{AGENT_ID}/observe",
            {"intent": "group_observation", "text": text, "salience": 0.35, "confidence": 0.7, "now": now.isoformat()},
            user_id=user,
        )

    def public_context(self, user_id: str) -> str:
        user = _slug(user_id)
        out = self._json("GET", f"/users/{parse.quote(user)}/agents/{AGENT_ID}/public-context", user_id=user)
        return str(out.get("text") or "").strip()

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
        bot_username: str | None = None,
        local_tz: ZoneInfo | None = None,
        group_reply_delay_seconds: float = 0.0,
        sleeper: Any = time.sleep,
    ) -> None:
        self.telegram = telegram
        self.backend = backend
        self.links = links
        self.poll_timeout = poll_timeout
        self.bot_username = bot_username.lower().lstrip("@") if bot_username else None
        self.local_tz = local_tz or telegram_local_timezone()
        self.group_reply_delay_seconds = max(0.0, float(group_reply_delay_seconds))
        self.sleeper = sleeper
        self.offset: int | None = None
        self._group_last_ambient_reply: dict[str, datetime] = {}
        self._group_last_message_at: dict[str, datetime] = {}
        self._group_messages_since_ambient_reply: dict[str, int] = {}
        self._group_social_scores: dict[str, int] = {}

    def _bot_username(self) -> str | None:
        if self.bot_username is None:
            try:
                me = self.telegram.get_me()
            except Exception:
                return None
            username = str(me.get("username") or "").strip()
            self.bot_username = username.lower().lstrip("@") if username else None
        return self.bot_username

    def _is_group_chat(self, chat: dict[str, Any]) -> bool:
        return str(chat.get("type") or "").lower() in {"group", "supergroup"}

    def _is_directed_at_bot(self, message: dict[str, Any], text: str) -> bool:
        if text.startswith("/"):
            return True
        username = self._bot_username()
        if username and f"@{username}" in text.lower():
            return True
        if "amigo" in text.lower():
            return True
        reply_to = message.get("reply_to_message") if isinstance(message.get("reply_to_message"), dict) else {}
        reply_from = reply_to.get("from") if isinstance(reply_to.get("from"), dict) else {}
        if reply_from.get("is_bot") and username and str(reply_from.get("username", "")).lower() == username:
            return True
        return False

    def _is_reply_to_human(self, message: dict[str, Any]) -> bool:
        reply_to = message.get("reply_to_message") if isinstance(message.get("reply_to_message"), dict) else {}
        if not reply_to:
            return False
        reply_from = reply_to.get("from") if isinstance(reply_to.get("from"), dict) else {}
        return not bool(reply_from.get("is_bot"))

    def _clean_group_text(self, text: str) -> str:
        username = self._bot_username()
        cleaned = text
        if username:
            cleaned = cleaned.replace(f"@{username}", "").replace(f"@{username.lower()}", "")
        if cleaned.startswith("/"):
            parts = cleaned.split(maxsplit=1)
            cleaned = parts[1] if len(parts) > 1 else ""
        return cleaned.strip()

    def _group_user_id(self, chat_id: int | str) -> str:
        return f"telegram-group-{_slug(str(chat_id))}"

    def _linked_sender_user_id(self, message: dict[str, Any]) -> str | None:
        sender = message.get("from") if isinstance(message.get("from"), dict) else {}
        sender_id = sender.get("id")
        if sender_id is None:
            return None
        return self.links.user_for_chat(f"user:{sender_id}") or self.links.user_for_chat(str(sender_id))

    def _with_public_sender_context(self, message: dict[str, Any], text: str) -> str:
        user_id = self._linked_sender_user_id(message)
        if not user_id:
            return text
        try:
            context = self.backend.public_context(user_id)
        except Exception:
            context = ""
        if not context:
            return text
        return f"Contexto público del autor: {context}\nMensaje del grupo: {text}"

    def _record_group_message(self, chat_id: int | str, now: datetime) -> None:
        key = str(chat_id)
        self._group_messages_since_ambient_reply[key] = self._group_messages_since_ambient_reply.get(key, 0) + 1
        self._group_last_message_at[key] = now

    def _sender_mention(self, message: dict[str, Any]) -> str:
        sender = message.get("from") if isinstance(message.get("from"), dict) else {}
        username = str(sender.get("username") or "").strip().lstrip("@")
        if username:
            return f"@{username}"
        return ""

    def _should_mention_sender(self, chat_id: int | str, now: datetime) -> bool:
        last = self._group_last_message_at.get(str(chat_id))
        return last is not None and (now - last).total_seconds() >= GROUP_MENTION_AFTER_SECONDS

    def _format_group_reply(self, message: dict[str, Any], reply: str, should_mention: bool) -> str:
        clean = reply.strip()
        mention = self._sender_mention(message) if should_mention else ""
        if mention and not clean.startswith(mention):
            return f"{mention} {clean}"
        return clean

    def _send_group_reply(self, chat_id: int | str, text: str) -> None:
        if self.group_reply_delay_seconds > 0:
            self.sleeper(self.group_reply_delay_seconds)
        self.telegram.send_message(chat_id, text)

    def _ambient_cooldown_seconds(self, chat_id: int | str) -> int:
        count = self._group_messages_since_ambient_reply.get(str(chat_id), 0)
        score = self._group_social_scores.get(str(chat_id), 0)
        if score <= -2:
            return max(GROUP_AMBIENT_COOLDOWN_SLOW_SECONDS, GROUP_AMBIENT_COOLDOWN_SECONDS * 2)
        if score >= 2 and count >= 3:
            return GROUP_AMBIENT_COOLDOWN_FAST_SECONDS
        if count >= 5:
            return GROUP_AMBIENT_COOLDOWN_FAST_SECONDS
        if count <= 1:
            return GROUP_AMBIENT_COOLDOWN_SLOW_SECONDS
        return GROUP_AMBIENT_COOLDOWN_SECONDS

    def _ambient_group_reason(self, chat_id: int | str, text: str, now: datetime) -> tuple[bool, str]:
        lowered = text.lower()
        normalized = _slug(text).replace("-", " ")
        if any(cue in lowered for cue in GROUP_SENSITIVE_CUES):
            return False, "sensitive"
        cues = (*GROUP_AMBIENT_CUES, *GROUP_PARTICIPATION_CUES)
        if not any(cue in lowered or _slug(cue).replace("-", " ") in normalized for cue in cues):
            return False, "no_social_opening"
        last = self._group_last_ambient_reply.get(str(chat_id))
        if last is not None and (now - last).total_seconds() < self._ambient_cooldown_seconds(chat_id):
            return False, "cooldown"
        self._group_last_ambient_reply[str(chat_id)] = now
        self._group_messages_since_ambient_reply[str(chat_id)] = 0
        if "?" in text or "¿" in text or any(cue in normalized for cue in ("alguien sabe", "alguin sabe", "cual es", "quien sabe", "sabeis")):
            return True, "general_question"
        if any(_slug(cue).replace("-", " ") in normalized for cue in ("hola", "buenas", "buenos dias", "buenas tardes", "buenas noches")):
            return True, "greeting"
        return True, "social_signal"

    def _should_reply_ambiently_in_group(self, chat_id: int | str, text: str, now: datetime) -> bool:
        should, _ = self._ambient_group_reason(chat_id, text, now)
        return should

    def _sender_id(self, message: dict[str, Any]) -> str | None:
        sender = message.get("from") if isinstance(message.get("from"), dict) else {}
        value = sender.get("id")
        return None if value is None else str(value)

    def _classify_group_reaction(self, chat_id: int | str, message: dict[str, Any], text: str, now: datetime) -> str | None:
        pending = self.links.pending_social_decision(chat_id)
        if not pending:
            return None
        sender_id = self._sender_id(message)
        if sender_id and pending.get("sender_id") == sender_id:
            return None
        lowered = text.lower()
        if any(cue in lowered for cue in GROUP_NEGATIVE_REACTION_CUES):
            outcome = "negative"
        elif any(cue in lowered for cue in GROUP_POSITIVE_REACTION_CUES):
            outcome = "positive"
        else:
            outcome = "reacted"
        self.links.mark_social_outcome(int(pending["id"]), outcome, now)
        key = str(chat_id)
        if outcome == "negative":
            self._group_social_scores[key] = self._group_social_scores.get(key, 0) - 2
        elif outcome == "positive":
            self._group_social_scores[key] = self._group_social_scores.get(key, 0) + 1
        return outcome

    def handle_update(self, update: dict[str, Any], now: datetime | None = None) -> None:
        self.offset = int(update.get("update_id", 0)) + 1
        message = update.get("message") if isinstance(update.get("message"), dict) else {}
        chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
        chat_id = chat.get("id")
        text = str(message.get("text") or "").strip()
        if chat_id is None or not text:
            return
        current_time = now or datetime.fromtimestamp(int(message.get("date") or time.time()), tz=timezone.utc).astimezone(self.local_tz)
        if self._is_group_chat(chat):
            self._handle_group_message(message, chat_id, text, current_time)
            return
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

    def _handle_group_message(self, message: dict[str, Any], chat_id: int | str, text: str, now: datetime) -> None:
        directed = self._is_directed_at_bot(message, text)
        clean_text = self._clean_group_text(text)
        if not clean_text:
            if directed:
                self._send_group_reply(chat_id, "Estoy aquí. Escríbeme la pregunta en el mismo mensaje o respóndeme directamente.")
            self._record_group_message(chat_id, now)
            return
        reply_should_mention = self._should_mention_sender(chat_id, now)
        self._record_group_message(chat_id, now)
        target_user_id = self._group_user_id(chat_id)
        context_text = self._with_public_sender_context(message, clean_text)
        reaction = self._classify_group_reaction(chat_id, message, clean_text, now)
        if reaction:
            self.backend.observe(target_user_id, f"social_feedback:{reaction}", now)
        if not directed and self._is_reply_to_human(message):
            self.links.record_social_decision(
                chat_id=chat_id,
                message_id=message.get("message_id"),
                sender_id=self._sender_id(message),
                text=clean_text,
                decision="observe",
                reason="human_reply_thread",
                now=now,
            )
            self.backend.observe(target_user_id, context_text, now)
            return
        should_reply, reason = (True, "directed") if directed else self._ambient_group_reason(chat_id, clean_text, now)
        if not should_reply:
            self.links.record_social_decision(
                chat_id=chat_id,
                message_id=message.get("message_id"),
                sender_id=self._sender_id(message),
                text=clean_text,
                decision="observe",
                reason=reason,
                now=now,
            )
            self.backend.observe(target_user_id, context_text, now)
            return
        reply = self.backend.tick(target_user_id, context_text, now)
        if reply:
            text_to_send = self._format_group_reply(message, reply, reply_should_mention)
            self._send_group_reply(chat_id, text_to_send)
            self.links.record_social_decision(
                chat_id=chat_id,
                message_id=message.get("message_id"),
                sender_id=self._sender_id(message),
                text=clean_text,
                decision="reply",
                reason=reason,
                now=now,
            )

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
        try:
            updates = self.telegram.get_updates(self.offset, self.poll_timeout)
        except Exception as exc:
            print(f"telegram_poll_error: {exc.__class__.__name__}", file=sys.stderr)
            updates = []
        for update in updates:
            try:
                self.handle_update(update)
            except Exception as exc:
                print(f"telegram_update_error: {exc.__class__.__name__}", file=sys.stderr)
        try:
            self.push_proactivity_once()
        except Exception as exc:
            print(f"telegram_proactivity_error: {exc.__class__.__name__}", file=sys.stderr)

    def run_forever(self, interval_seconds: float = 1.0) -> None:
        while True:
            try:
                self.poll_once()
            except Exception as exc:
                print(f"telegram_loop_error: {exc.__class__.__name__}", file=sys.stderr)
            time.sleep(interval_seconds)


def build_service(db_path: Path, base_url: str) -> TelegramBotService:
    token = telegram_token_from_env()
    password = os.environ.get("NINO_TELEGRAM_BACKEND_PASSWORD", "").strip()
    delay = float(os.environ.get("NINO_TELEGRAM_GROUP_REPLY_DELAY_SECONDS", "2.5"))
    return TelegramBotService(
        HTTPTelegramAPI(token),
        BackendClient(base_url, password=password),
        TelegramLinkStore(db_path),
        group_reply_delay_seconds=delay,
    )


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
