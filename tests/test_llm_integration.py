from __future__ import annotations

from typing import Any

from nino.consolidation import MemoryFact
from nino.llm import DeepSeekClient, build_configured_llm, llm_config_status
from nino.runtime import InMemoryStateStore, NinoRuntime


class FakeLLM:
    def __init__(self, text: str = "Respuesta desde Claude con memoria.") -> None:
        self.text = text
        self.prompts: list[dict[str, Any]] = []

    def complete(self, prompt: dict[str, Any]) -> str:
        self.prompts.append(prompt)
        return self.text


class FakeDeepSeekLLM(FakeLLM):
    provider = "deepseek"
    model = "deepseek-chat"
    max_tokens = 320


class FailingLLM:
    def complete(self, prompt: dict[str, Any]) -> str:
        raise RuntimeError("network down")


def test_tick_uses_configured_llm_response() -> None:
    llm = FakeLLM()
    runtime = NinoRuntime(InMemoryStateStore(), llm_client=llm)
    runtime.tick("agent-llm", {"intent": "chat", "text": "prefiero sprints", "salience": 0.9})

    out = runtime.tick("agent-llm", {"intent": "question", "text": "que recuerdas?"})

    assert out["action"]["payload"]["text"] == "Respuesta desde Claude con memoria."
    assert out["llm_provider"] == "claude"
    assert out["llm_error"] is None
    assert "llm_provider_claude" in out["reason_trace"]
    assert "Preferencias conocidas: sprints" in llm.prompts[-1]["user"]
    assert out["nino_context"]["response_source"] == "llm_claude"
    assert out["nino_context"]["llm_provider"] == "claude"
    assert out["nino_context"]["llm_error"] is None
    assert "llm_context_memory_count" in out["nino_context"]
    assert isinstance(out["nino_context"]["memory_candidates"], list)
    assert "Modo continuidad: activo" in llm.prompts[-1]["user"]
    assert "Usa al menos un recuerdo" in llm.prompts[-1]["system"]


def test_tick_context_memory_candidates_include_origin_fields() -> None:
    llm = FakeLLM()
    runtime = NinoRuntime(InMemoryStateStore(), llm_client=llm)
    runtime.tick("agent-llm", {"intent": "chat", "text": "prefiero sprints", "salience": 0.9, "confidence": 0.95})

    out = runtime.tick("agent-llm", {"intent": "question", "text": "prefiero sprints"})

    candidate = out["nino_context"]["memory_candidates"][0]
    assert candidate["fact_id"]
    assert candidate["source_episode_id"]
    assert candidate["memory_type"] in {"hot", "cold"}


def test_tick_reports_actual_llm_provider() -> None:
    llm = FakeDeepSeekLLM("Respuesta desde DeepSeek.")
    runtime = NinoRuntime(InMemoryStateStore(), llm_client=llm)

    out = runtime.tick("agent-llm", {"intent": "chat", "text": "hola"})

    assert out["action"]["payload"]["text"] == "Respuesta desde DeepSeek."
    assert out["llm_provider"] == "deepseek"
    assert "llm_provider_deepseek" in out["reason_trace"]
    assert out["nino_context"]["response_source"] == "llm_deepseek"
    assert runtime.llm_status("agent-llm")["provider"] == "deepseek"
    assert runtime.llm_status("agent-llm")["last_response"]["source"] == "llm_deepseek"


def test_llm_prompt_keeps_internal_context_passive_for_plain_chat() -> None:
    llm = FakeLLM()
    runtime = NinoRuntime(InMemoryStateStore(), llm_client=llm)

    runtime.tick("agent-llm", {"intent": "chat", "text": "hola, responde breve"})

    assert "Modo continuidad: pasivo" in llm.prompts[-1]["user"]
    assert "sin explicar tus mecanismos internos" in llm.prompts[-1]["system"]


def test_llm_prompt_loads_amigo_ethics() -> None:
    llm = FakeLLM()
    runtime = NinoRuntime(InMemoryStateStore(), llm_client=llm)

    runtime.tick("agent-llm", {"intent": "chat", "text": "quiero que seas mi amigo"})

    system = llm.prompts[-1]["system"]
    assert "Etica de amigo" in system
    assert "no inventes recuerdos" in system
    assert "no compartas informacion personal entre usuarios" in system
    assert "acompaña sin presionar ni manipular" in system
    assert "no te presentes como humano" in system


def test_direct_reminder_creation_does_not_call_llm() -> None:
    llm = FakeLLM("Respuesta que no debe usarse.")
    runtime = NinoRuntime(InMemoryStateStore(), llm_client=llm)

    out = runtime.tick(
        "agent-llm",
        {"intent": "chat", "text": "recuérdame en 5 minutos que beba agua", "now": "2026-05-26T12:27:00+02:00"},
    )

    assert out["action"]["payload"]["text"] == "Vale, te aviso a las 12:32 para que beba agua."
    assert "direct_reminder_created" in out["reason_trace"]
    assert llm.prompts == []


def test_bare_time_does_not_create_reminder_or_call_llm_action_path() -> None:
    llm = FakeLLM("No debería convertir esto en recordatorio.")
    runtime = NinoRuntime(InMemoryStateStore(), llm_client=llm)

    out = runtime.tick("agent-llm", {"intent": "chat", "text": "a las 18", "now": "2026-05-26T10:00:00+02:00"})
    relation = runtime.load_or_init_state("agent-llm").relation_state

    assert relation.get("temporal_events", []) == []
    assert "direct_reminder_created" not in out["reason_trace"]


def test_llm_prompt_instructs_single_brief_recovery_apology() -> None:
    llm = FakeLLM()
    runtime = NinoRuntime(InMemoryStateStore(), llm_client=llm)

    runtime.tick("agent-llm", {"intent": "chat", "text": "no he pedido que me recuerdes nada"})

    system = llm.prompts[-1]["system"]
    assert "Si malinterpretas al usuario" in system
    assert "no encadenes disculpas" in system


def test_llm_prompt_activates_continuity_for_temporal_memory_question() -> None:
    llm = FakeLLM()
    runtime = NinoRuntime(InMemoryStateStore(), llm_client=llm)

    runtime.tick("agent-llm", {"intent": "question", "text": "que hablamos hace dos semanas?"})

    assert "Modo continuidad: activo" in llm.prompts[-1]["user"]
    assert "Usa al menos un recuerdo" in llm.prompts[-1]["system"]


def test_llm_prompt_marks_temporal_memory_miss() -> None:
    llm = FakeLLM()
    runtime = NinoRuntime(InMemoryStateStore(), llm_client=llm)

    out = runtime.tick("agent-llm", {"intent": "question", "text": "que hablamos hace dos semanas?"})

    assert out["nino_context"]["temporal_query"] is True
    assert out["nino_context"]["temporal_miss"] is True
    assert "Consulta temporal: sin resultados" in llm.prompts[-1]["user"]
    assert "no hay recuerdos recuperados" in llm.prompts[-1]["system"]


def test_llm_prompt_includes_current_time_and_temporal_events() -> None:
    llm = FakeLLM()
    runtime = NinoRuntime(InMemoryStateStore(), llm_client=llm)

    runtime.tick(
        "agent-llm",
        {
            "intent": "chat",
            "text": "hoy tengo dentista a las 11",
            "salience": 0.9,
            "confidence": 0.95,
            "now": "2026-05-26T09:30:00+02:00",
        },
    )
    out = runtime.tick(
        "agent-llm",
        {
            "intent": "chat",
            "text": "se paso la tristeza. hoy estoy super alegre",
            "now": "2026-05-26T09:45:00+02:00",
        },
    )

    assert out["nino_context"]["temporal_query"] is False
    assert out["nino_context"]["temporal_miss"] is False
    assert "Fecha/hora actual: 2026-05-26T09:45:00+02:00" in llm.prompts[-1]["user"]
    assert "Eventos temporales activos:" in llm.prompts[-1]["user"]
    assert "dentista" in llm.prompts[-1]["user"]
    assert "2026-05-26T11:00:00+02:00" in llm.prompts[-1]["user"]
    assert "no preguntes si ya pasó" in llm.prompts[-1]["system"]


def test_conversation_persists_user_and_assistant_turns_without_extra_episodes() -> None:
    llm = FakeLLM("Te respondo usando memoria persistente.")
    runtime = NinoRuntime(InMemoryStateStore(), llm_client=llm)

    runtime.tick("agent-llm", {"intent": "chat", "text": "hola"})

    turns = runtime.conversation("agent-llm")
    episodes = runtime.episode_store.list_for_agent("agent-llm")
    assert len(episodes) == 1
    assert [turn["role"] for turn in turns] == ["user", "assistant"]
    assert any(turn["text"] == "hola" and turn["role"] == "user" for turn in turns)
    assert any(turn["text"] == "Te respondo usando memoria persistente." and turn["role"] == "assistant" for turn in turns)
    status = runtime.llm_status("agent-llm")
    assert status["enabled"] is True
    assert status["provider"] == "claude"
    assert status["last_response"]["source"] == "llm_claude"
    assert status["last_response"]["error"] is None
    assert runtime.audit_log("agent-llm")[0]["payload"]["response_source"] == "llm_claude"


def test_tick_falls_back_to_policy_when_llm_fails() -> None:
    runtime = NinoRuntime(InMemoryStateStore(), llm_client=FailingLLM())

    out = runtime.tick("agent-llm", {"intent": "question", "text": "que sabes de mi?"})

    assert "Todavía tengo poca memoria" in out["action"]["payload"]["text"]
    assert out["llm_provider"] == "claude"
    assert out["llm_error"] == "RuntimeError"
    assert "llm_provider_claude" not in out["reason_trace"]
    assert out["nino_context"]["response_source"] == "policy"
    assert out["nino_context"]["llm_error"] == "RuntimeError"


def test_llm_probe_does_not_create_episode() -> None:
    llm = FakeLLM("Claude conectado a amigo.")
    runtime = NinoRuntime(InMemoryStateStore(), llm_client=llm)

    out = runtime.llm_probe("agent-llm")

    assert out["ok"] is True
    assert out["text"] == "Claude conectado a amigo."
    assert runtime.episode_store.list_for_agent("agent-llm") == []


def test_llm_probe_reports_unconfigured_runtime() -> None:
    runtime = NinoRuntime(InMemoryStateStore(), llm_client=None)
    runtime.llm_client = None

    out = runtime.llm_probe("agent-llm")

    assert out["ok"] is False
    assert out["error"] == "llm_not_configured"


def test_llm_config_status_reports_missing_provider(monkeypatch) -> None:
    monkeypatch.delenv("NINO_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    status = llm_config_status()

    assert status["enabled"] is False
    assert status["provider"] is None
    assert status["api_key_present"] is False
    assert "NINO_LLM_PROVIDER" in status["missing"]
    assert build_configured_llm() is None


def test_llm_config_status_reports_missing_api_key(monkeypatch) -> None:
    monkeypatch.setenv("NINO_LLM_PROVIDER", "claude")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    status = llm_config_status()

    assert status["enabled"] is False
    assert status["provider"] == "claude"
    assert status["api_key_present"] is False
    assert status["missing"] == ["ANTHROPIC_API_KEY"]
    assert build_configured_llm() is None


def test_llm_config_status_builds_claude_client_without_exposing_key(monkeypatch) -> None:
    monkeypatch.setenv("NINO_LLM_PROVIDER", "claude")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret-key")
    monkeypatch.setenv("NINO_CLAUDE_MODEL", "claude-test")
    monkeypatch.setenv("NINO_LLM_MAX_TOKENS", "111")
    monkeypatch.setenv("NINO_LLM_TIMEOUT", "3")

    status = llm_config_status()
    client = build_configured_llm()

    assert status["enabled"] is True
    assert status["api_key_present"] is True
    assert "secret-key" not in str(status)
    assert client is not None
    assert getattr(client, "model") == "claude-test"
    assert getattr(client, "max_tokens") == 111


def test_llm_config_status_builds_deepseek_client_without_exposing_key(monkeypatch) -> None:
    monkeypatch.setenv("NINO_LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-secret")
    monkeypatch.setenv("NINO_DEEPSEEK_MODEL", "deepseek-reasoner")
    monkeypatch.setenv("NINO_DEEPSEEK_BASE_URL", "https://example.test/chat/completions")
    monkeypatch.setenv("NINO_LLM_MAX_TOKENS", "222")

    status = llm_config_status()
    client = build_configured_llm()

    assert status["enabled"] is True
    assert status["provider"] == "deepseek"
    assert status["model"] == "deepseek-reasoner"
    assert status["base_url"] == "https://example.test/chat/completions"
    assert status["api_key_present"] is True
    assert status["api_key_source"] == "env"
    assert "deepseek-secret" not in str(status)
    assert isinstance(client, DeepSeekClient)
    assert getattr(client, "provider") == "deepseek"
    assert getattr(client, "model") == "deepseek-reasoner"
    assert getattr(client, "max_tokens") == 222


def test_llm_config_status_reports_missing_deepseek_api_key(monkeypatch) -> None:
    monkeypatch.setenv("NINO_LLM_PROVIDER", "deepseek")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("NINO_DEEPSEEK_API_KEY", raising=False)

    status = llm_config_status()

    assert status["enabled"] is False
    assert status["provider"] == "deepseek"
    assert status["missing"] == ["DEEPSEEK_API_KEY"]
    assert build_configured_llm() is None


def test_llm_config_status_reports_invalid_numeric_settings(monkeypatch) -> None:
    monkeypatch.setenv("NINO_LLM_PROVIDER", "claude")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret-key")
    monkeypatch.setenv("NINO_LLM_MAX_TOKENS", "zero")
    monkeypatch.setenv("NINO_LLM_TIMEOUT", "-1")

    status = llm_config_status()

    assert status["enabled"] is False
    assert status["api_key_present"] is True
    assert status["max_tokens"] == 320
    assert status["timeout_seconds"] == 20.0
    assert status["config_errors"] == [
        {"name": "NINO_LLM_MAX_TOKENS", "error": "invalid_integer"},
        {"name": "NINO_LLM_TIMEOUT", "error": "must_be_positive"},
    ]
    assert "NINO_LLM_MAX_TOKENS" in status["missing"]
    assert "NINO_LLM_TIMEOUT" in status["missing"]
    assert "secret-key" not in str(status)
    assert build_configured_llm() is None


def test_llm_config_status_can_use_keychain_without_exposing_key(monkeypatch) -> None:
    monkeypatch.setenv("NINO_LLM_PROVIDER", "claude")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("NINO_KEYCHAIN_SERVICE", "nino-test")
    monkeypatch.setattr("nino.llm._keychain_api_key", lambda service: "keychain-secret" if service == "nino-test" else None)

    status = llm_config_status()
    client = build_configured_llm()

    assert status["enabled"] is True
    assert status["api_key_present"] is True
    assert status["api_key_source"] == "keychain"
    assert status["keychain_service"] == "nino-test"
    assert "keychain-secret" not in str(status)
    assert client is not None


def test_llm_prompt_includes_recent_turns_cold_facts_and_redacts_sensitive_context() -> None:
    llm = FakeLLM()
    runtime = NinoRuntime(InMemoryStateStore(), llm_client=llm)
    runtime.tick("agent-llm", {"intent": "chat", "text": "hola"})
    runtime.cold_store.upsert(
        MemoryFact(
            fact_id="f1",
            agent_id="agent-llm",
            key="preference",
            value="prefiere revisar sprint 6 con pin 123456",
            confidence=0.9,
            source_episode_id="e1",
            valid_from=runtime.episode_store.list_for_agent("agent-llm")[0].timestamp,
        )
    )

    runtime.tick("agent-llm", {"intent": "chat", "text": "mi email es pablo@example.com"})

    prompt = llm.prompts[-1]["user"]
    assert "Últimos turnos:" in prompt
    assert "Hechos fríos activos:" in prompt
    assert "prefiere revisar sprint 6 con pin [number]" in prompt
    assert "[email]" in prompt
    assert "pablo@example.com" not in prompt


def test_llm_prompt_includes_onboarding_profile() -> None:
    llm = FakeLLM()
    runtime = NinoRuntime(InMemoryStateStore(), llm_client=llm)

    runtime.tick("agent-llm", {"intent": "onboarding:name", "text": "Pablo"})
    runtime.tick("agent-llm", {"intent": "onboarding:location", "text": "Madrid"})
    runtime.tick("agent-llm", {"intent": "chat", "text": "qué sabes de mí?"})

    prompt = llm.prompts[-1]["user"]
    assert "Perfil inicial del usuario:" in prompt
    assert "Nombre: Pablo" in prompt
    assert "Lugar: Madrid" in prompt


def test_llm_prompt_includes_honest_group_maturity_not_human_life() -> None:
    llm = FakeLLM()
    runtime = NinoRuntime(InMemoryStateStore(), llm_client=llm)

    runtime.tick("telegram-group-100", {"intent": "group_chat", "text": "tenemos una idea para el viaje"})
    runtime.tick("telegram-group-100", {"intent": "group_chat", "text": "¿cómo lo veis?"})

    system = llm.prompts[-1]["system"]
    prompt = llm.prompts[-1]["user"]
    assert "No tienes vida humana" in system
    assert "Madurez grupal honesta:" in prompt
    assert "software_companion_no_human_life" in prompt
    assert "Historia compartida reciente:" in prompt
