from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from nino.telegram import BackendClient, TelegramBotService, TelegramLinkStore, telegram_token_from_env


class FakeTelegram:
    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []
        self.updates: list[dict[str, object]] = []

    def get_updates(self, offset: int | None, timeout: int) -> list[dict[str, object]]:
        return self.updates

    def send_message(self, chat_id: int | str, text: str) -> dict[str, object]:
        item = {"chat_id": str(chat_id), "text": text}
        self.sent.append(item)
        return {"ok": True, "result": item}

    def get_me(self) -> dict[str, object]:
        return {"id": 999, "is_bot": True, "username": "amigo_test_bot"}


class FakeBackend:
    def __init__(self) -> None:
        self.ticks: list[dict[str, object]] = []
        self.proactive: dict[str, str | None] = {}
        self.sessions: dict[str, str] = {}

    def session_for(self, user_id: str) -> str:
        self.sessions.setdefault(user_id, f"session-{user_id}")
        return self.sessions[user_id]

    def tick(self, user_id: str, text: str, now: datetime) -> str:
        self.session_for(user_id)
        self.ticks.append({"user_id": user_id, "text": text, "now": now.isoformat()})
        return f"respuesta para {user_id}: {text}"

    def evaluate_proactivity(self, user_id: str, now: datetime) -> str | None:
        self.session_for(user_id)
        return self.proactive.get(user_id)


def _update(chat_id: int, text: str, update_id: int = 1) -> dict[str, object]:
    return {"update_id": update_id, "message": {"chat": {"id": chat_id}, "text": text, "date": 1_779_977_600}}


def _group_update(chat_id: int, text: str, user_id: int = 10, update_id: int = 1) -> dict[str, object]:
    return {
        "update_id": update_id,
        "message": {
            "chat": {"id": chat_id, "type": "supergroup", "title": "Grupo"},
            "from": {"id": user_id, "is_bot": False, "first_name": "Pablo"},
            "text": text,
            "date": 1_779_977_600,
        },
    }


def test_linked_chat_resolves_user_and_replies(tmp_path) -> None:
    links = TelegramLinkStore(tmp_path / "nino.db")
    code = links.create_code("Ana")
    assert links.link_with_code(111, code)
    telegram = FakeTelegram()
    backend = FakeBackend()
    bot = TelegramBotService(telegram, backend, links)

    bot.handle_update(_update(111, "hola"), now=datetime(2026, 5, 28, 10, tzinfo=timezone.utc))

    assert backend.ticks == [{"user_id": "ana", "text": "hola", "now": "2026-05-28T10:00:00+00:00"}]
    assert telegram.sent[-1]["chat_id"] == "111"
    assert "respuesta para ana" in str(telegram.sent[-1]["text"])


def test_telegram_message_date_is_passed_to_backend_in_local_timezone(tmp_path) -> None:
    links = TelegramLinkStore(tmp_path / "nino.db")
    assert links.link_with_code(111, links.create_code("Ana"))
    telegram = FakeTelegram()
    backend = FakeBackend()
    bot = TelegramBotService(telegram, backend, links, local_tz=ZoneInfo("Europe/Madrid"))

    bot.handle_update(_update(111, "recuérdame en 5 minutos que beba agua"))

    assert backend.ticks == [{"user_id": "ana", "text": "recuérdame en 5 minutos que beba agua", "now": "2026-05-28T16:13:20+02:00"}]


def test_unlinked_chat_gets_linking_flow_without_backend_access(tmp_path) -> None:
    links = TelegramLinkStore(tmp_path / "nino.db")
    telegram = FakeTelegram()
    backend = FakeBackend()
    bot = TelegramBotService(telegram, backend, links)

    bot.handle_update(_update(222, "hola"))

    assert backend.ticks == []
    assert telegram.sent[-1]["chat_id"] == "222"
    assert "asistente con memoria, no una persona" in str(telegram.sent[-1]["text"])
    assert "/link CODIGO" in str(telegram.sent[-1]["text"])


def test_two_chat_ids_keep_memory_routes_isolated(tmp_path) -> None:
    links = TelegramLinkStore(tmp_path / "nino.db")
    ana_code = links.create_code("Ana")
    assert links.link_with_code(111, ana_code)
    bob_code = links.create_code("Bob")
    assert links.link_with_code(222, bob_code)
    telegram = FakeTelegram()
    backend = FakeBackend()
    bot = TelegramBotService(telegram, backend, links)

    bot.handle_update(_update(111, "soy Ana", 1))
    bot.handle_update(_update(222, "soy Bob", 2))

    assert [tick["user_id"] for tick in backend.ticks] == ["ana", "bob"]
    assert telegram.sent[0]["chat_id"] == "111"
    assert telegram.sent[1]["chat_id"] == "222"
    assert "bob" not in str(telegram.sent[0]["text"]).lower()
    assert "ana" not in str(telegram.sent[1]["text"]).lower()


def test_proactive_candidate_sends_only_to_linked_chat(tmp_path) -> None:
    links = TelegramLinkStore(tmp_path / "nino.db")
    assert links.link_with_code(111, links.create_code("Ana"))
    assert links.link_with_code(222, links.create_code("Bob"))
    telegram = FakeTelegram()
    backend = FakeBackend()
    backend.proactive["ana"] = "recuerda beber agua"
    backend.proactive["bob"] = None
    bot = TelegramBotService(telegram, backend, links)

    sent = bot.push_proactivity_once(now=datetime(2026, 5, 28, 10, tzinfo=timezone.utc))

    assert sent == 1
    assert telegram.sent == [{"chat_id": "111", "text": "recuerda beber agua"}]


def test_blocked_proactive_candidate_sends_nothing(tmp_path) -> None:
    links = TelegramLinkStore(tmp_path / "nino.db")
    assert links.link_with_code(111, links.create_code("Ana"))
    telegram = FakeTelegram()
    backend = FakeBackend()
    backend.proactive["ana"] = None
    bot = TelegramBotService(telegram, backend, links)

    assert bot.push_proactivity_once(now=datetime(2026, 5, 28, 10, tzinfo=timezone.utc)) == 0
    assert telegram.sent == []


def test_telegram_user_reaction_goes_through_backend_tick(tmp_path) -> None:
    links = TelegramLinkStore(tmp_path / "nino.db")
    assert links.link_with_code(111, links.create_code("Ana"))
    telegram = FakeTelegram()
    backend = FakeBackend()
    backend.proactive["ana"] = "¿cómo fue el dentista?"
    bot = TelegramBotService(telegram, backend, links)

    bot.push_proactivity_once(now=datetime(2026, 5, 28, 10, tzinfo=timezone.utc))
    bot.handle_update(_update(111, "fue bien", 3), now=datetime(2026, 5, 28, 10, 1, tzinfo=timezone.utc))

    assert backend.ticks[-1]["user_id"] == "ana"
    assert backend.ticks[-1]["text"] == "fue bien"


def test_missing_telegram_token_blocks_service_start(monkeypatch) -> None:
    monkeypatch.delenv("NINO_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("NINO_TELEGRAM_KEYCHAIN_SERVICE", raising=False)

    with pytest.raises(RuntimeError, match="missing_telegram_bot_token"):
        telegram_token_from_env()


def test_link_code_cli_path_does_not_require_telegram_token(tmp_path, capsys) -> None:
    from nino.telegram import main

    assert main(["--db", str(tmp_path / "nino.db"), "--create-link-code", "Ana"]) == 0
    code = capsys.readouterr().out.strip()
    assert len(code) >= 8


def test_group_message_not_directed_to_bot_is_ignored(tmp_path) -> None:
    links = TelegramLinkStore(tmp_path / "nino.db")
    telegram = FakeTelegram()
    backend = FakeBackend()
    bot = TelegramBotService(telegram, backend, links)

    bot.handle_update(_group_update(-100, "hola grupo"))

    assert backend.ticks == []
    assert telegram.sent == []


def test_group_mention_uses_group_memory_not_private_memory(tmp_path) -> None:
    links = TelegramLinkStore(tmp_path / "nino.db")
    telegram = FakeTelegram()
    backend = FakeBackend()
    bot = TelegramBotService(telegram, backend, links)

    bot.handle_update(_group_update(-100, "@amigo_test_bot qué opinas?", user_id=10))

    assert backend.ticks == [{"user_id": "telegram-group-100", "text": "qué opinas?", "now": "2026-05-28T16:13:20+02:00"}]
    assert telegram.sent[-1]["chat_id"] == "-100"


def test_group_reply_to_bot_is_directed_and_uses_group_memory(tmp_path) -> None:
    links = TelegramLinkStore(tmp_path / "nino.db")
    telegram = FakeTelegram()
    backend = FakeBackend()
    bot = TelegramBotService(telegram, backend, links)
    update = _group_update(-100, "sí, sigue", user_id=10)
    update["message"]["reply_to_message"] = {"from": {"id": 999, "is_bot": True, "username": "amigo_test_bot"}, "text": "respuesta previa"}

    bot.handle_update(update)

    assert backend.ticks[-1]["user_id"] == "telegram-group-100"
    assert backend.ticks[-1]["text"] == "sí, sigue"


def test_group_linked_sender_still_uses_group_memory_when_directed(tmp_path) -> None:
    links = TelegramLinkStore(tmp_path / "nino.db")
    code = links.create_code("Ana")
    assert links.link_with_code("user:10", code)
    telegram = FakeTelegram()
    backend = FakeBackend()
    bot = TelegramBotService(telegram, backend, links)

    bot.handle_update(_group_update(-100, "@amigo_test_bot mi perfil", user_id=10))

    assert backend.ticks[-1]["user_id"] == "telegram-group-100"
    assert backend.ticks[-1]["text"] == "mi perfil"
    assert "ana" not in str(telegram.sent[-1]["text"]).lower()
