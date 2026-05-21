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

    assert "No lo sé todavía" in out["action"]["payload"]["text"]
    assert "question_detected" in out["reason_trace"]


def test_policy_uses_retrieved_memory_on_followup() -> None:
    runtime = NinoRuntime(InMemoryStateStore())
    runtime.tick("agent-policy", {"intent": "music", "text": "me gusta piano", "salience": 0.9})

    out = runtime.tick("agent-policy", {"intent": "music piano", "text": "hablemos de piano"})

    assert "recuerdo" in out["action"]["payload"]["text"]
    assert "me gusta piano" in out["action"]["payload"]["text"]
    assert "memory_continuity" in out["reason_trace"]


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

    assert out["action"]["payload"]["text"] == "Estoy aquí. Sigamos construyendo memoria juntos."
    assert "greeting" in out["reason_trace"]


def test_policy_prioritizes_new_preference_over_old_related_memory() -> None:
    runtime = NinoRuntime(InMemoryStateStore())
    runtime.tick("agent-policy", {"intent": "chat", "text": "me gusta piano", "salience": 0.9})

    out = runtime.tick("agent-policy", {"intent": "chat", "text": "me gusta el piano", "salience": 0.9})

    assert "guardo que piano" in out["action"]["payload"]["text"]
    assert "memory_continuity" not in out["reason_trace"]


def test_policy_ignores_repeated_same_memory_on_followup() -> None:
    runtime = NinoRuntime(InMemoryStateStore())
    runtime.tick("agent-policy", {"intent": "chat", "text": "me gusta piano", "salience": 0.9})
    runtime.tick("agent-policy", {"intent": "chat", "text": "hablemos de piano", "salience": 0.7})

    out = runtime.tick("agent-policy", {"intent": "chat", "text": "hablemos de piano", "salience": 0.7})

    assert "me gusta piano" in out["action"]["payload"]["text"]
    assert "hablemos de piano. Lo añado" not in out["action"]["payload"]["text"]
