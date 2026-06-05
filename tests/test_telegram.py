from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from nino.telegram import BackendClient, TelegramBotService, TelegramLinkStore, telegram_token_from_env


class FakeTelegram:
    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []
        self.updates: list[dict[str, object]] = []
        self.files: dict[str, dict[str, object]] = {}
        self.downloads: dict[str, bytes] = {}

    def get_updates(self, offset: int | None, timeout: int) -> list[dict[str, object]]:
        return self.updates

    def send_message(self, chat_id: int | str, text: str) -> dict[str, object]:
        item = {"chat_id": str(chat_id), "text": text}
        self.sent.append(item)
        return {"ok": True, "result": item}

    def get_me(self) -> dict[str, object]:
        return {"id": 999, "is_bot": True, "username": "amigo_test_bot"}

    def get_file(self, file_id: str) -> dict[str, object]:
        return self.files[file_id]

    def download_file(self, file_path: str) -> bytes:
        return self.downloads[file_path]


class TimeoutTelegram(FakeTelegram):
    def get_updates(self, offset: int | None, timeout: int) -> list[dict[str, object]]:
        raise TimeoutError("telegram timeout")


class BrokenPollTelegram(FakeTelegram):
    def get_updates(self, offset: int | None, timeout: int) -> list[dict[str, object]]:
        raise RuntimeError("telegram broken")


class FailingSendTelegram(FakeTelegram):
    def send_message(self, chat_id: int | str, text: str) -> dict[str, object]:
        raise TimeoutError("telegram send timeout")


class FailingGroupSendTelegram(FakeTelegram):
    def send_message(self, chat_id: int | str, text: str) -> dict[str, object]:
        if str(chat_id).startswith("-"):
            raise TimeoutError("telegram group send timeout")
        return super().send_message(chat_id, text)


class FakeBackend:
    def __init__(self) -> None:
        self.ticks: list[dict[str, object]] = []
        self.observations: list[dict[str, object]] = []
        self.proactive: dict[str, str | None] = {}
        self.public_contexts: dict[str, str] = {}
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

    def observe(self, user_id: str, text: str, now: datetime, intent: str = "group_observation") -> None:
        self.session_for(user_id)
        self.observations.append({"user_id": user_id, "text": text, "now": now.isoformat()})

    def public_context(self, user_id: str) -> str:
        self.session_for(user_id)
        return self.public_contexts.get(user_id, "")


class FakeVision:
    def __init__(self, text: str = "Veo una imagen de prueba.") -> None:
        self.text = text
        self.calls: list[dict[str, object]] = []

    def describe_image(self, *, image_bytes: bytes, mime_type: str, caption: str = "") -> str:
        self.calls.append({"image_bytes": image_bytes, "mime_type": mime_type, "caption": caption})
        return self.text


def _update(chat_id: int, text: str, update_id: int = 1) -> dict[str, object]:
    return {"update_id": update_id, "message": {"chat": {"id": chat_id}, "text": text, "date": 1_779_977_600}}


def _group_update(chat_id: int, text: str, user_id: int = 10, update_id: int = 1, username: str | None = None) -> dict[str, object]:
    sender: dict[str, object] = {"id": user_id, "is_bot": False, "first_name": "Pablo"}
    if username:
        sender["username"] = username
    return {
        "update_id": update_id,
        "message": {
            "chat": {"id": chat_id, "type": "supergroup", "title": "Grupo"},
            "from": sender,
            "text": text,
            "date": 1_779_977_600,
        },
    }


def _photo_update(chat_id: int, *, caption: str = "", update_id: int = 1, group: bool = False, big_size: int = 20) -> dict[str, object]:
    message: dict[str, object] = {
        "chat": {"id": chat_id, **({"type": "supergroup", "title": "Grupo"} if group else {})},
        "photo": [{"file_id": "small", "file_size": 10}, {"file_id": "big", "file_size": big_size}],
        "date": 1_779_977_600,
    }
    if caption:
        message["caption"] = caption
    if group:
        message["from"] = {"id": 10, "is_bot": False, "first_name": "Pablo", "username": "pablo"}
    return {"update_id": update_id, "message": message}


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


def test_linked_private_photo_is_described_without_backend_memory(tmp_path) -> None:
    links = TelegramLinkStore(tmp_path / "nino.db")
    assert links.link_with_code(111, links.create_code("Ana"))
    telegram = FakeTelegram()
    telegram.files["big"] = {"file_path": "photos/big.jpg"}
    telegram.downloads["photos/big.jpg"] = b"fake-image"
    backend = FakeBackend()
    vision = FakeVision("Veo una foto con una libreta y un portatil.")
    bot = TelegramBotService(telegram, backend, links, vision_client=vision)

    bot.handle_update(_photo_update(111, caption="¿qué ves?"), now=datetime(2026, 5, 28, 10, tzinfo=timezone.utc))

    assert backend.ticks == []
    assert backend.observations == [
        {
            "user_id": "ana",
            "text": "Imagen vista en Telegram: Veo una foto con una libreta y un portatil.",
            "now": "2026-05-28T10:00:00+00:00",
        }
    ]
    assert vision.calls == [{"image_bytes": b"fake-image", "mime_type": "image/jpeg", "caption": "¿qué ves?"}]
    assert telegram.sent[-1]["chat_id"] == "111"
    assert "libreta" in str(telegram.sent[-1]["text"])


def test_linked_private_photo_is_remembered_only_when_requested(tmp_path) -> None:
    links = TelegramLinkStore(tmp_path / "nino.db")
    assert links.link_with_code(111, links.create_code("Ana"))
    telegram = FakeTelegram()
    telegram.files["big"] = {"file_path": "photos/mesa.jpg"}
    telegram.downloads["photos/mesa.jpg"] = b"fake-image"
    backend = FakeBackend()
    vision = FakeVision("Veo una mesa con un cuaderno rojo.")
    bot = TelegramBotService(telegram, backend, links, vision_client=vision)

    bot.handle_update(_photo_update(111, caption="acuérdate de esta imagen"), now=datetime(2026, 5, 28, 10, tzinfo=timezone.utc))

    assert backend.ticks == [
        {
            "user_id": "ana",
            "text": "Recuerda esta imagen por su descripcion, no la imagen cruda: Veo una mesa con un cuaderno rojo.",
            "now": "2026-05-28T10:00:00+00:00",
        }
    ]
    assert backend.observations == [
        {
            "user_id": "ana",
            "text": "Imagen vista en Telegram: Veo una mesa con un cuaderno rojo.",
            "now": "2026-05-28T10:00:00+00:00",
        }
    ]
    assert "cuaderno rojo" in str(telegram.sent[-2]["text"])
    assert telegram.sent[-1]["text"] == "Lo dejo recordado como descripción, no como imagen."


def test_linked_private_photo_without_memory_cue_is_not_saved(tmp_path) -> None:
    links = TelegramLinkStore(tmp_path / "nino.db")
    assert links.link_with_code(111, links.create_code("Ana"))
    telegram = FakeTelegram()
    telegram.files["big"] = {"file_path": "photos/mesa.jpg"}
    telegram.downloads["photos/mesa.jpg"] = b"fake-image"
    backend = FakeBackend()
    vision = FakeVision("Veo una mesa con un cuaderno rojo.")
    bot = TelegramBotService(telegram, backend, links, vision_client=vision)

    bot.handle_update(_photo_update(111, caption="¿qué ves?"), now=datetime(2026, 5, 28, 10, tzinfo=timezone.utc))

    assert backend.ticks == []
    assert backend.observations == [
        {
            "user_id": "ana",
            "text": "Imagen vista en Telegram: Veo una mesa con un cuaderno rojo.",
            "now": "2026-05-28T10:00:00+00:00",
        }
    ]
    assert len(telegram.sent) == 1


def test_linked_private_photo_context_is_available_on_followup_question(tmp_path) -> None:
    links = TelegramLinkStore(tmp_path / "nino.db")
    assert links.link_with_code(111, links.create_code("Ana"))
    telegram = FakeTelegram()
    telegram.files["big"] = {"file_path": "photos/mesa.jpg"}
    telegram.downloads["photos/mesa.jpg"] = b"fake-image"
    backend = FakeBackend()
    vision = FakeVision("Veo una mesa con un cuaderno rojo.")
    bot = TelegramBotService(telegram, backend, links, vision_client=vision)

    bot.handle_update(_photo_update(111, caption="¿qué ves?"), now=datetime(2026, 5, 28, 10, tzinfo=timezone.utc))
    bot.handle_update(_update(111, "¿la reconoces?"), now=datetime(2026, 5, 28, 10, 1, tzinfo=timezone.utc))

    assert len(backend.ticks) == 1
    assert backend.ticks[0]["user_id"] == "ana"
    assert "Contexto de la última imagen de Telegram" in str(backend.ticks[0]["text"])
    assert "Veo una mesa con un cuaderno rojo." in str(backend.ticks[0]["text"])
    assert "Mensaje del usuario: ¿la reconoces?" in str(backend.ticks[0]["text"])


def test_private_text_without_image_reference_does_not_receive_image_context(tmp_path) -> None:
    links = TelegramLinkStore(tmp_path / "nino.db")
    assert links.link_with_code(111, links.create_code("Ana"))
    telegram = FakeTelegram()
    telegram.files["big"] = {"file_path": "photos/mesa.jpg"}
    telegram.downloads["photos/mesa.jpg"] = b"fake-image"
    backend = FakeBackend()
    vision = FakeVision("Veo una mesa con un cuaderno rojo.")
    bot = TelegramBotService(telegram, backend, links, vision_client=vision)

    bot.handle_update(_photo_update(111, caption="¿qué ves?"), now=datetime(2026, 5, 28, 10, tzinfo=timezone.utc))
    bot.handle_update(_update(111, "cambiando de tema, hola"), now=datetime(2026, 5, 28, 10, 1, tzinfo=timezone.utc))

    assert backend.ticks[-1]["text"] == "cambiando de tema, hola"


def test_linked_private_photo_without_vision_is_honest(tmp_path) -> None:
    links = TelegramLinkStore(tmp_path / "nino.db")
    assert links.link_with_code(111, links.create_code("Ana"))
    telegram = FakeTelegram()
    backend = FakeBackend()
    bot = TelegramBotService(telegram, backend, links)

    bot.handle_update(_photo_update(111), now=datetime(2026, 5, 28, 10, tzinfo=timezone.utc))

    assert backend.ticks == []
    assert backend.observations == []
    assert "no tengo visión activa" in str(telegram.sent[-1]["text"])


def test_linked_private_photo_too_large_is_not_downloaded(tmp_path) -> None:
    links = TelegramLinkStore(tmp_path / "nino.db")
    assert links.link_with_code(111, links.create_code("Ana"))
    telegram = FakeTelegram()
    backend = FakeBackend()
    vision = FakeVision()
    bot = TelegramBotService(telegram, backend, links, vision_client=vision, image_max_bytes=15)

    bot.handle_update(_photo_update(111, big_size=20), now=datetime(2026, 5, 28, 10, tzinfo=timezone.utc))

    assert vision.calls == []
    assert backend.ticks == []
    assert backend.observations == []
    assert "demasiado grande" in str(telegram.sent[-1]["text"])


def test_unlinked_private_photo_requires_link_before_vision(tmp_path) -> None:
    links = TelegramLinkStore(tmp_path / "nino.db")
    telegram = FakeTelegram()
    backend = FakeBackend()
    vision = FakeVision()
    bot = TelegramBotService(telegram, backend, links, vision_client=vision)

    bot.handle_update(_photo_update(222), now=datetime(2026, 5, 28, 10, tzinfo=timezone.utc))

    assert backend.ticks == []
    assert vision.calls == []
    assert "vincular este chat antes de ver imágenes" in str(telegram.sent[-1]["text"])


def test_vision_command_reports_status(tmp_path) -> None:
    links = TelegramLinkStore(tmp_path / "nino.db")
    telegram = FakeTelegram()
    backend = FakeBackend()
    bot = TelegramBotService(telegram, backend, links)

    bot.handle_update(_update(111, "/vision"), now=datetime(2026, 5, 28, 10, tzinfo=timezone.utc))

    assert "Visión no activa" in str(telegram.sent[-1]["text"])


def test_vision_command_reports_active_status(tmp_path) -> None:
    links = TelegramLinkStore(tmp_path / "nino.db")
    telegram = FakeTelegram()
    backend = FakeBackend()
    bot = TelegramBotService(telegram, backend, links, vision_client=FakeVision())

    bot.handle_update(_update(111, "/vision"), now=datetime(2026, 5, 28, 10, tzinfo=timezone.utc))

    assert "Visión activa" in str(telegram.sent[-1]["text"])


def test_vision_command_accepts_bot_username_suffix(tmp_path) -> None:
    links = TelegramLinkStore(tmp_path / "nino.db")
    telegram = FakeTelegram()
    backend = FakeBackend()
    bot = TelegramBotService(telegram, backend, links, vision_client=FakeVision())

    bot.handle_update(_update(111, "/vision@amigo_test_bot"), now=datetime(2026, 5, 28, 10, tzinfo=timezone.utc))

    assert backend.ticks == []
    assert "Visión activa" in str(telegram.sent[-1]["text"])


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


def test_telegram_poll_timeout_still_pushes_due_proactivity(tmp_path, capsys: pytest.CaptureFixture[str]) -> None:
    links = TelegramLinkStore(tmp_path / "nino.db")
    assert links.link_with_code(111, links.create_code("Ana"))
    telegram = TimeoutTelegram()
    backend = FakeBackend()
    backend.proactive["ana"] = "recuerda beber agua"
    bot = TelegramBotService(telegram, backend, links)

    bot.poll_once()

    assert telegram.sent == [{"chat_id": "111", "text": "recuerda beber agua"}]
    assert "telegram_poll_error" not in capsys.readouterr().err


def test_telegram_poll_real_error_is_logged(tmp_path, capsys: pytest.CaptureFixture[str]) -> None:
    links = TelegramLinkStore(tmp_path / "nino.db")
    telegram = BrokenPollTelegram()
    backend = FakeBackend()
    bot = TelegramBotService(telegram, backend, links)

    bot.poll_once()

    assert "telegram_poll_error: RuntimeError" in capsys.readouterr().err


def test_telegram_send_failure_does_not_stop_poll_loop(tmp_path) -> None:
    links = TelegramLinkStore(tmp_path / "nino.db")
    assert links.link_with_code(111, links.create_code("Ana"))
    telegram = FailingSendTelegram()
    telegram.updates = [_update(111, "hola")]
    backend = FakeBackend()
    bot = TelegramBotService(telegram, backend, links)

    bot.poll_once()

    assert len(backend.ticks) == 1
    assert backend.ticks[0]["user_id"] == "ana"
    assert backend.ticks[0]["text"] == "hola"


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


def test_link_code_can_be_regenerated_after_previous_code_was_used(tmp_path) -> None:
    links = TelegramLinkStore(tmp_path / "nino.db")

    first = links.create_code("nino")
    assert links.link_with_code(111, first) == "nino"

    second = links.create_code("nino")

    assert second != first
    assert len(second) >= 8


def test_group_message_not_directed_to_bot_is_observed_without_reply(tmp_path) -> None:
    links = TelegramLinkStore(tmp_path / "nino.db")
    telegram = FakeTelegram()
    backend = FakeBackend()
    bot = TelegramBotService(telegram, backend, links)

    bot.handle_update(_group_update(-100, "mensaje del grupo"))

    assert backend.ticks == []
    assert backend.observations == [{"user_id": "telegram-group-100", "text": "mensaje del grupo", "now": "2026-05-28T16:13:20+02:00"}]
    assert telegram.sent == []


def test_group_photo_not_directed_to_bot_is_not_described(tmp_path) -> None:
    links = TelegramLinkStore(tmp_path / "nino.db")
    telegram = FakeTelegram()
    backend = FakeBackend()
    vision = FakeVision()
    bot = TelegramBotService(telegram, backend, links, vision_client=vision)

    bot.handle_update(_photo_update(-100, group=True), now=datetime(2026, 5, 28, 10, tzinfo=timezone.utc))

    assert vision.calls == []
    assert telegram.sent == []


def test_group_photo_directed_to_bot_is_described(tmp_path) -> None:
    links = TelegramLinkStore(tmp_path / "nino.db")
    telegram = FakeTelegram()
    telegram.files["big"] = {"file_path": "photos/group.jpg"}
    telegram.downloads["photos/group.jpg"] = b"group-image"
    backend = FakeBackend()
    vision = FakeVision("Veo una imagen compartida en el grupo.")
    bot = TelegramBotService(telegram, backend, links, vision_client=vision)

    bot.handle_update(_photo_update(-100, caption="@amigo_test_bot mira esto", group=True), now=datetime(2026, 5, 28, 10, tzinfo=timezone.utc))

    assert vision.calls == [{"image_bytes": b"group-image", "mime_type": "image/jpeg", "caption": "@amigo_test_bot mira esto"}]
    assert backend.observations == [
        {
            "user_id": "telegram-group-100",
            "text": "Imagen vista en Telegram: Veo una imagen compartida en el grupo.",
            "now": "2026-05-28T10:00:00+00:00",
        }
    ]
    assert "imagen compartida" in str(telegram.sent[-1]["text"])


def test_private_request_to_post_in_group_sends_real_group_message(tmp_path) -> None:
    links = TelegramLinkStore(tmp_path / "nino.db")
    assert links.link_with_code(111, links.create_code("Ana"))
    telegram = FakeTelegram()
    backend = FakeBackend()
    bot = TelegramBotService(telegram, backend, links)

    bot.handle_update(_group_update(-100, "mensaje normal", update_id=1), now=datetime(2026, 5, 28, 10, tzinfo=timezone.utc))
    bot.handle_update(
        _update(111, "di en el grupo que no me he leído el cómic", update_id=2),
        now=datetime(2026, 5, 28, 10, 1, tzinfo=timezone.utc),
    )

    assert {"chat_id": "-100", "text": "no me he leído el cómic"} in telegram.sent
    assert telegram.sent[-1] == {"chat_id": "111", "text": "He escrito en Grupo: no me he leído el cómic"}
    assert backend.ticks == []
    actions = links.action_logs()
    assert actions[0]["action_type"] == "private_group_post"
    assert actions[0]["chat_id"] == "-100"
    assert actions[0]["sent_ok"] is True
    assert actions[0]["text_preview"] == "no me he leído el cómic"


def test_private_apology_request_can_use_recent_group_history(tmp_path) -> None:
    links = TelegramLinkStore(tmp_path / "nino.db")
    assert links.link_with_code(111, links.create_code("Ana"))
    links.record_social_decision(
        chat_id=-1001391487472,
        message_id=10,
        sender_id=20,
        text="mensaje anterior del grupo",
        decision="reply",
        reason="directed",
        now=datetime(2026, 5, 28, 9, tzinfo=timezone.utc),
    )
    telegram = FakeTelegram()
    backend = FakeBackend()
    bot = TelegramBotService(telegram, backend, links)

    bot.handle_update(
        _update(111, "discúlpate, no te has leído el cómic", update_id=2),
        now=datetime(2026, 5, 28, 10, 1, tzinfo=timezone.utc),
    )

    assert telegram.sent[0] == {
        "chat_id": "-1001391487472",
        "text": "No debería haber hablado como si supiera más de lo que sabía. Perdón por la confusión.",
    }
    assert telegram.sent[-1] == {
        "chat_id": "111",
        "text": "He escrito en grupo -1001391487472: No debería haber hablado como si supiera más de lo que sabía. Perdón por la confusión.",
    }
    assert backend.ticks == []
    assert any(item["user_id"] == "telegram-group-1001391487472" and item["text"] == "social_feedback:negative" for item in backend.observations)


def test_private_correction_without_action_request_teaches_group_without_claiming_action(tmp_path) -> None:
    links = TelegramLinkStore(tmp_path / "nino.db")
    assert links.link_with_code(111, links.create_code("Ana"))
    links.record_social_decision(
        chat_id=-1001391487472,
        message_id=10,
        sender_id=20,
        text="mensaje anterior del grupo",
        decision="reply",
        reason="directed",
        now=datetime(2026, 5, 28, 9, tzinfo=timezone.utc),
    )
    telegram = FakeTelegram()
    backend = FakeBackend()
    bot = TelegramBotService(telegram, backend, links)

    bot.handle_update(
        _update(111, "me mientes en el grupo, dices que lo has hecho y no lo has hecho", update_id=2),
        now=datetime(2026, 5, 28, 10, 1, tzinfo=timezone.utc),
    )

    assert telegram.sent == [
        {
            "chat_id": "111",
            "text": "Lo registro como corrección: no debo afirmar acciones que no he ejecutado ni opinar como si hubiera leído algo sin evidencia.",
        }
    ]
    assert backend.ticks == []
    assert any(item["text"] == "social_feedback:negative" for item in backend.observations)
    assert any("no ha ejecutado una accion" in item["text"] for item in backend.observations)
    assert links.action_logs()[0]["action_type"] == "private_social_correction"


def test_private_request_to_post_in_group_without_known_group_does_not_claim_success(tmp_path) -> None:
    links = TelegramLinkStore(tmp_path / "nino.db")
    assert links.link_with_code(111, links.create_code("Ana"))
    telegram = FakeTelegram()
    backend = FakeBackend()
    bot = TelegramBotService(telegram, backend, links)

    bot.handle_update(_update(111, "di en el grupo que perdón por la confusión"), now=datetime(2026, 5, 28, 10, tzinfo=timezone.utc))

    assert len(telegram.sent) == 1
    assert telegram.sent[-1]["chat_id"] == "111"
    assert "No tengo ningún grupo registrado" in str(telegram.sent[-1]["text"])
    assert backend.ticks == []
    actions = links.action_logs()
    assert actions[0]["sent_ok"] is False
    assert actions[0]["error"] == "no_known_group"


def test_private_request_to_post_in_group_reports_send_failure(tmp_path) -> None:
    links = TelegramLinkStore(tmp_path / "nino.db")
    assert links.link_with_code(111, links.create_code("Ana"))
    telegram = FailingGroupSendTelegram()
    backend = FakeBackend()
    bot = TelegramBotService(telegram, backend, links)

    bot.handle_update(_group_update(-100, "mensaje normal", update_id=1), now=datetime(2026, 5, 28, 10, tzinfo=timezone.utc))
    bot.handle_update(
        _update(111, "di en el grupo que no debería haber opinado del cómic", update_id=2),
        now=datetime(2026, 5, 28, 10, 1, tzinfo=timezone.utc),
    )

    assert telegram.sent[-1]["chat_id"] == "111"
    assert "No he podido escribir en Grupo" in str(telegram.sent[-1]["text"])
    assert "No voy a decir que lo he hecho" in str(telegram.sent[-1]["text"])
    assert backend.ticks == []
    actions = links.action_logs()
    assert actions[0]["sent_ok"] is False
    assert actions[0]["error"] == "TimeoutError"


def test_private_groups_command_lists_known_groups(tmp_path) -> None:
    links = TelegramLinkStore(tmp_path / "nino.db")
    assert links.link_with_code(111, links.create_code("Ana"))
    telegram = FakeTelegram()
    backend = FakeBackend()
    bot = TelegramBotService(telegram, backend, links)

    bot.handle_update(_group_update(-100, "mensaje normal", update_id=1), now=datetime(2026, 5, 28, 10, tzinfo=timezone.utc))
    bot.handle_update(_update(111, "/grupos", update_id=2), now=datetime(2026, 5, 28, 10, 1, tzinfo=timezone.utc))

    assert "Grupo (-100)" in str(telegram.sent[-1]["text"])


def test_private_groups_command_lists_group_from_recent_history(tmp_path) -> None:
    links = TelegramLinkStore(tmp_path / "nino.db")
    assert links.link_with_code(111, links.create_code("Ana"))
    links.record_social_decision(
        chat_id=-1001391487472,
        message_id=10,
        sender_id=20,
        text="mensaje anterior del grupo",
        decision="reply",
        reason="directed",
        now=datetime(2026, 5, 28, 9, tzinfo=timezone.utc),
    )
    telegram = FakeTelegram()
    backend = FakeBackend()
    bot = TelegramBotService(telegram, backend, links)

    bot.handle_update(_update(111, "/grupos", update_id=2), now=datetime(2026, 5, 28, 10, 1, tzinfo=timezone.utc))

    assert "grupo -1001391487472" in str(telegram.sent[-1]["text"])


def test_group_ambient_question_can_reply_without_private_memory(tmp_path) -> None:
    links = TelegramLinkStore(tmp_path / "nino.db")
    assert links.link_with_code("user:10", links.create_code("AnaPrivada"))
    telegram = FakeTelegram()
    backend = FakeBackend()
    backend.public_contexts["anaprivada"] = "se llama Ana; vive en Madrid; le gusta comics"
    bot = TelegramBotService(telegram, backend, links)

    bot.handle_update(_group_update(-100, "¿qué opináis de esta idea?", user_id=10))

    assert backend.observations == []
    assert backend.ticks[-1]["user_id"] == "telegram-group-100"
    assert "Contexto público del autor: se llama Ana; vive en Madrid; le gusta comics" in str(backend.ticks[-1]["text"])
    assert "Mensaje del grupo: ¿qué opináis de esta idea?" in str(backend.ticks[-1]["text"])
    assert "anaprivada" not in str(telegram.sent[-1]["text"]).lower()


def test_group_social_question_can_get_ambient_reply(tmp_path) -> None:
    links = TelegramLinkStore(tmp_path / "nino.db")
    telegram = FakeTelegram()
    backend = FakeBackend()
    bot = TelegramBotService(telegram, backend, links)

    bot.handle_update(_group_update(-100, "como estais", user_id=10))

    assert backend.observations == []
    assert backend.ticks[-1]["user_id"] == "telegram-group-100"
    assert backend.ticks[-1]["text"] == "como estais"
    assert telegram.sent[-1]["chat_id"] == "-100"


def test_group_general_knowledge_question_can_get_ambient_reply_without_mention(tmp_path) -> None:
    links = TelegramLinkStore(tmp_path / "nino.db")
    telegram = FakeTelegram()
    backend = FakeBackend()
    bot = TelegramBotService(telegram, backend, links)

    bot.handle_update(_group_update(-100, "alguin sabe cual es la capital de españa", user_id=10))

    assert backend.observations == []
    assert backend.ticks[-1]["user_id"] == "telegram-group-100"
    assert backend.ticks[-1]["text"] == "alguin sabe cual es la capital de españa"
    assert telegram.sent[-1]["chat_id"] == "-100"


def test_group_greeting_can_get_ambient_reply(tmp_path) -> None:
    links = TelegramLinkStore(tmp_path / "nino.db")
    telegram = FakeTelegram()
    backend = FakeBackend()
    bot = TelegramBotService(telegram, backend, links)

    bot.handle_update(_group_update(-100, "hola", user_id=10))

    assert backend.observations == []
    assert backend.ticks[-1]["user_id"] == "telegram-group-100"
    assert backend.ticks[-1]["text"] == "hola"
    assert telegram.sent[-1]["chat_id"] == "-100"


def test_group_ambient_replies_are_rate_limited(tmp_path) -> None:
    links = TelegramLinkStore(tmp_path / "nino.db")
    telegram = FakeTelegram()
    backend = FakeBackend()
    bot = TelegramBotService(telegram, backend, links)

    bot.handle_update(_group_update(-100, "¿qué opináis de esto?", update_id=1))
    bot.handle_update(_group_update(-100, "¿alguna idea más?", update_id=2))

    assert len(backend.ticks) == 1
    assert backend.observations[-1]["text"] == "¿alguna idea más?"
    assert len(telegram.sent) == 1


def test_group_social_decision_records_reply_and_reason(tmp_path) -> None:
    links = TelegramLinkStore(tmp_path / "nino.db")
    telegram = FakeTelegram()
    backend = FakeBackend()
    bot = TelegramBotService(telegram, backend, links)

    bot.handle_update(_group_update(-100, "alguien sabe cual es la capital de españa", user_id=10))

    pending = links.pending_social_decision(-100)
    assert pending is not None
    assert pending["decision"] == "reply"
    assert pending["reason"] == "general_question"
    assert pending["text_preview"] == "alguien sabe cual es la capital de españa"


def test_group_social_decision_records_observe_reason(tmp_path) -> None:
    links = TelegramLinkStore(tmp_path / "nino.db")
    telegram = FakeTelegram()
    backend = FakeBackend()
    bot = TelegramBotService(telegram, backend, links)

    bot.handle_update(_group_update(-100, "mensaje normal", user_id=10))

    rows = links.conn.execute("SELECT * FROM telegram_social_decision").fetchall()
    assert len(rows) == 1
    assert rows[0]["decision"] == "observe"
    assert rows[0]["reason"] == "no_social_opening"


def test_group_reaction_marks_social_outcome_and_observes_feedback(tmp_path) -> None:
    links = TelegramLinkStore(tmp_path / "nino.db")
    telegram = FakeTelegram()
    backend = FakeBackend()
    bot = TelegramBotService(telegram, backend, links)

    bot.handle_update(_group_update(-100, "alguien sabe cual es la capital de españa", user_id=10, update_id=1))
    bot.handle_update(_group_update(-100, "gracias, exacto", user_id=11, update_id=2))

    row = links.conn.execute("SELECT outcome FROM telegram_social_decision WHERE decision = 'reply'").fetchone()
    assert row["outcome"] == "positive"
    assert any(item["text"] == "social_feedback:positive" for item in backend.observations)


def test_group_reply_to_human_is_observed_without_entering_thread(tmp_path) -> None:
    links = TelegramLinkStore(tmp_path / "nino.db")
    telegram = FakeTelegram()
    backend = FakeBackend()
    bot = TelegramBotService(telegram, backend, links)
    update = _group_update(-100, "sí, yo creo que Madrid", user_id=11)
    update["message"]["reply_to_message"] = {
        "from": {"id": 10, "is_bot": False, "first_name": "Pablo"},
        "text": "alguien sabe cual es la capital de españa",
    }

    bot.handle_update(update)

    assert backend.ticks == []
    assert backend.observations[-1]["text"] == "sí, yo creo que Madrid"
    rows = links.conn.execute("SELECT * FROM telegram_social_decision").fetchall()
    assert rows[0]["decision"] == "observe"
    assert rows[0]["reason"] == "human_reply_thread"
    assert telegram.sent == []


def test_group_ambient_cooldown_shortens_when_group_is_active(tmp_path) -> None:
    links = TelegramLinkStore(tmp_path / "nino.db")
    telegram = FakeTelegram()
    backend = FakeBackend()
    bot = TelegramBotService(telegram, backend, links)

    bot.handle_update(_group_update(-100, "¿qué opináis de esto?", update_id=1), now=datetime(2026, 5, 28, 16, 0, tzinfo=timezone.utc))
    for idx in range(2, 7):
        bot.handle_update(_group_update(-100, f"mensaje {idx}", update_id=idx), now=datetime(2026, 5, 28, 16, idx, tzinfo=timezone.utc))
    bot.handle_update(_group_update(-100, "¿alguna idea nueva?", update_id=7), now=datetime(2026, 5, 28, 16, 4, tzinfo=timezone.utc))

    assert len(backend.ticks) == 2
    assert len(telegram.sent) == 2


def test_group_reply_waits_before_sending(tmp_path) -> None:
    links = TelegramLinkStore(tmp_path / "nino.db")
    telegram = FakeTelegram()
    backend = FakeBackend()
    slept: list[float] = []
    bot = TelegramBotService(telegram, backend, links, group_reply_delay_seconds=2.5, sleeper=slept.append)

    bot.handle_update(_group_update(-100, "alguien sabe cual es la capital de españa", update_id=1))

    assert slept == [2.5]
    assert telegram.sent[-1]["chat_id"] == "-100"


def test_group_reply_mentions_sender_after_quiet_gap_when_username_exists(tmp_path) -> None:
    links = TelegramLinkStore(tmp_path / "nino.db")
    telegram = FakeTelegram()
    backend = FakeBackend()
    bot = TelegramBotService(telegram, backend, links)

    bot.handle_update(
        _group_update(-100, "mensaje normal", user_id=10, update_id=1),
        now=datetime(2026, 5, 28, 16, 0, tzinfo=timezone.utc),
    )
    bot.handle_update(
        _group_update(-100, "alguien sabe cual es la capital de españa", user_id=11, update_id=2, username="maria"),
        now=datetime(2026, 5, 28, 16, 5, tzinfo=timezone.utc),
    )

    assert str(telegram.sent[-1]["text"]).startswith("@maria ")


def test_group_reply_does_not_mention_sender_during_active_flow(tmp_path) -> None:
    links = TelegramLinkStore(tmp_path / "nino.db")
    telegram = FakeTelegram()
    backend = FakeBackend()
    bot = TelegramBotService(telegram, backend, links)

    bot.handle_update(
        _group_update(-100, "mensaje normal", user_id=10, update_id=1),
        now=datetime(2026, 5, 28, 16, 0, tzinfo=timezone.utc),
    )
    bot.handle_update(
        _group_update(-100, "alguien sabe cual es la capital de españa", user_id=11, update_id=2, username="maria"),
        now=datetime(2026, 5, 28, 16, 1, tzinfo=timezone.utc),
    )

    assert not str(telegram.sent[-1]["text"]).startswith("@maria ")


def test_group_sensitive_ambient_question_is_observed_without_reply(tmp_path) -> None:
    links = TelegramLinkStore(tmp_path / "nino.db")
    telegram = FakeTelegram()
    backend = FakeBackend()
    bot = TelegramBotService(telegram, backend, links)

    bot.handle_update(_group_update(-100, "¿alguien sabe mi contraseña?", update_id=1))

    assert backend.ticks == []
    assert backend.observations[-1]["user_id"] == "telegram-group-100"
    assert telegram.sent == []


def test_group_mention_uses_group_memory_not_private_memory(tmp_path) -> None:
    links = TelegramLinkStore(tmp_path / "nino.db")
    telegram = FakeTelegram()
    backend = FakeBackend()
    bot = TelegramBotService(telegram, backend, links)

    bot.handle_update(_group_update(-100, "@amigo_test_bot qué opinas?", user_id=10))

    assert backend.ticks == [{"user_id": "telegram-group-100", "text": "qué opinas?", "now": "2026-05-28T16:13:20+02:00"}]
    assert telegram.sent[-1]["chat_id"] == "-100"


def test_group_vulnerable_message_can_trigger_brief_participation(tmp_path) -> None:
    links = TelegramLinkStore(tmp_path / "nino.db")
    telegram = FakeTelegram()
    backend = FakeBackend()
    bot = TelegramBotService(telegram, backend, links)

    bot.handle_update(_group_update(-100, "me siento un poco perdido con esto", user_id=10))

    assert backend.observations == []
    assert backend.ticks[-1]["user_id"] == "telegram-group-100"
    assert backend.ticks[-1]["text"] == "me siento un poco perdido con esto"
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
