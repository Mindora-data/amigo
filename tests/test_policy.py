from __future__ import annotations

from nino.runtime import InMemoryStateStore, NinoRuntime


def test_policy_acknowledges_preference_signal() -> None:
    runtime = NinoRuntime(InMemoryStateStore())

    out = runtime.tick("agent-policy", {"intent": "chat", "text": "me gusta piano"})

    assert "guardo que piano" in out["action"]["payload"]["text"]
    assert "preference_signal" in out["reason_trace"]


def test_policy_handles_questions_without_default_placeholder() -> None:
    runtime = NinoRuntime(InMemoryStateStore())

    out = runtime.tick("agent-policy", {"intent": "question", "text": "qué recuerdas de mí?"})

    assert "Todavía tengo poca memoria" in out["action"]["payload"]["text"]
    assert "user_memory_query" in out["reason_trace"]


def test_policy_uses_retrieved_memory_on_followup() -> None:
    runtime = NinoRuntime(InMemoryStateStore())
    runtime.tick("agent-policy", {"intent": "music", "text": "me gusta piano", "salience": 0.9})

    out = runtime.tick("agent-policy", {"intent": "music piano", "text": "hablemos de piano"})

    assert "Recuerdo que tiene peso para ti" in out["action"]["payload"]["text"]
    assert "piano" in out["action"]["payload"]["text"]
    assert "topic_continuation" in out["reason_trace"]


def test_policy_answers_user_memory_query_from_relation() -> None:
    runtime = NinoRuntime(InMemoryStateStore())
    runtime.tick("agent-policy", {"intent": "chat", "text": "soy Pablo"})
    runtime.tick("agent-policy", {"intent": "music", "text": "me gusta el piano", "salience": 0.9})

    out = runtime.tick("agent-policy", {"intent": "question", "text": "que sabes de mi?"})

    assert "eres Pablo" in out["action"]["payload"]["text"]
    assert "piano" in out["action"]["payload"]["text"]
    assert "user_memory_query" in out["reason_trace"]


def test_policy_answers_temporal_memory_miss_without_llm() -> None:
    runtime = NinoRuntime(InMemoryStateStore())

    out = runtime.tick("agent-policy", {"intent": "question", "text": "que hablamos hace dos semanas?"})

    assert "No encuentro recuerdos guardados de esa fecha" in out["action"]["payload"]["text"]
    assert "temporal_memory_miss" in out["reason_trace"]
    assert out["nino_context"]["temporal_miss"] is True


def test_policy_does_not_use_unrelated_recent_memory() -> None:
    runtime = NinoRuntime(InMemoryStateStore())
    runtime.tick("agent-policy", {"intent": "question", "text": "quien eres?", "salience": 0.9})

    out = runtime.tick("agent-policy", {"intent": "chat", "text": "me gusta piano", "salience": 0.9})

    assert "quien eres" not in out["action"]["payload"]["text"]
    assert "preference_signal" in out["reason_trace"]


def test_policy_acknowledges_user_identity_signal() -> None:
    runtime = NinoRuntime(InMemoryStateStore())
    runtime.tick("agent-policy", {"intent": "chat", "text": "me gusta piano", "salience": 0.9})

    out = runtime.tick("agent-policy", {"intent": "chat", "text": "soy Pablo"})

    assert "Encantado, Pablo" in out["action"]["payload"]["text"]
    assert "identity_signal" in out["reason_trace"]


def test_policy_prioritizes_current_greeting_over_old_greeting_memory() -> None:
    runtime = NinoRuntime(InMemoryStateStore())
    runtime.tick("agent-policy", {"intent": "chat", "text": "hola", "salience": 0.7})

    out = runtime.tick("agent-policy", {"intent": "chat", "text": "hola", "salience": 0.7})

    assert out["action"]["payload"]["text"] == "Estoy aquí. ¿Qué tal vas?"
    assert "greeting" in out["reason_trace"]


def test_policy_prioritizes_new_preference_over_old_related_memory() -> None:
    runtime = NinoRuntime(InMemoryStateStore())
    runtime.tick("agent-policy", {"intent": "chat", "text": "me gusta piano", "salience": 0.9})

    out = runtime.tick("agent-policy", {"intent": "chat", "text": "me gusta el piano", "salience": 0.9})

    assert "guardo que piano" in out["action"]["payload"]["text"]
    assert "memory_continuity" not in out["reason_trace"]


def test_policy_stops_reasking_after_repeated_closed_ok_replies() -> None:
    runtime = NinoRuntime(InMemoryStateStore())
    runtime.tick("agent-policy", {"intent": "chat", "text": "hola", "salience": 0.7})

    first = runtime.tick("agent-policy", {"intent": "chat", "text": "todo bien", "salience": 0.4})
    second = runtime.tick("agent-policy", {"intent": "chat", "text": "Bien", "salience": 0.4})

    assert first["action"]["payload"]["text"] == "Me alegro. Te dejo tranquilo; si aparece algo, me dices."
    assert "closed_reply_ack" in first["reason_trace"]
    assert second["action"]["type"] == "no_response"
    assert second["action"]["payload"]["text"] == ""
    assert "closed_reply_silence" in second["reason_trace"]


def test_policy_ignores_repeated_same_memory_on_followup() -> None:
    runtime = NinoRuntime(InMemoryStateStore())
    runtime.tick("agent-policy", {"intent": "chat", "text": "me gusta piano", "salience": 0.9})
    runtime.tick("agent-policy", {"intent": "chat", "text": "hablemos de piano", "salience": 0.7})

    out = runtime.tick("agent-policy", {"intent": "chat", "text": "hablemos de piano", "salience": 0.7})

    assert "Recuerdo que tiene peso para ti" in out["action"]["payload"]["text"]
    assert "hablemos de piano. Lo añado" not in out["action"]["payload"]["text"]


def test_policy_answers_internal_preference_query() -> None:
    runtime = NinoRuntime(InMemoryStateStore())
    runtime.tick("agent-policy", {"intent": "music", "text": "me gusta el piano", "salience": 0.9})

    out = runtime.tick("agent-policy", {"intent": "question", "text": "que te gusta"})

    assert "me interesa entender mejor" in out["action"]["payload"]["text"]
    assert "piano" in out["action"]["payload"]["text"]
    assert "internal_preference_query" in out["reason_trace"]


def test_policy_answers_user_memory_query_with_cold_context_facts() -> None:
    runtime = NinoRuntime(InMemoryStateStore())
    runtime.tick("agent-policy", {"intent": "chat", "text": "vivo en Madrid", "salience": 0.8, "confidence": 0.95})
    runtime.tick(
        "agent-policy",
        {"intent": "chat", "text": "estamos trabajando en memoria persistente", "salience": 0.8, "confidence": 0.95},
    )

    out = runtime.tick("agent-policy", {"intent": "question", "text": "que sabes de mi"})

    answer = out["action"]["payload"]["text"]
    assert "vives en madrid" in answer
    assert "estamos trabajando en memoria persistente" in answer
    assert "user_memory_query" in out["reason_trace"]


def test_policy_answers_identity_query_with_cold_context_facts() -> None:
    runtime = NinoRuntime(InMemoryStateStore())
    runtime.tick("agent-policy", {"intent": "chat", "text": "mi proyecto se llama NIÑO", "salience": 0.8, "confidence": 0.95})

    out = runtime.tick("agent-policy", {"intent": "question", "text": "quién soy"})

    assert "tu proyecto se llama niño" in out["action"]["payload"]["text"]
    assert "relation_self_query" in out["reason_trace"]


def test_policy_answers_goal_query() -> None:
    runtime = NinoRuntime(InMemoryStateStore())
    runtime.tick("agent-policy", {"intent": "question", "text": "por qué me calma la música?", "salience": 0.9})

    out = runtime.tick("agent-policy", {"intent": "question", "text": "que quieres"})

    assert "orientado a" in out["action"]["payload"]["text"]
    assert "reduce_uncertainty" in out["action"]["payload"]["text"]
    assert "goal_query" in out["reason_trace"]


def test_policy_answers_internal_state_query() -> None:
    runtime = NinoRuntime(InMemoryStateStore())
    runtime.tick("agent-policy", {"intent": "chat", "text": "hola"})

    out = runtime.tick("agent-policy", {"intent": "question", "text": "como estas"})

    assert "energía" in out["action"]["payload"]["text"]
    assert "curiosidad" in out["action"]["payload"]["text"]
    assert "internal_state_query" in out["reason_trace"]
