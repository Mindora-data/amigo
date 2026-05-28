from __future__ import annotations

from datetime import datetime, timezone

from nino.contracts import ProactivitySettings
from nino.persistence import create_persistent_runtime


def test_safe_export_redacts_name_email_and_numbers(tmp_path) -> None:
    runtime = create_persistent_runtime(tmp_path / "nino.db")
    runtime.tick("agent-p", {"intent": "chat", "text": "soy Pablo", "salience": 0.8})
    runtime.tick("agent-p", {"intent": "chat", "text": "mi email es pablo@example.com y mi pin 123456", "salience": 0.8})

    export = runtime.export_agent_safe("agent-p")
    texts = " ".join(ep["text"] for ep in export["episodes"])

    assert export["redacted"] is True
    assert "user_name" not in export["state"]["relation_state"]
    assert "pablo@example.com" not in texts
    assert "123456" not in texts
    assert "[email]" in texts
    assert "[number]" in texts


def test_global_model_learns_anonymous_patterns_without_private_text(tmp_path) -> None:
    runtime = create_persistent_runtime(tmp_path / "nino.db")
    runtime.tick("user::ana::agent::nino", {"intent": "music", "text": "soy Ana y me gusta piano", "salience": 0.9})
    runtime.tick("user::bob::agent::nino", {"intent": "chat", "text": "mi email es bob@example.com y me gusta guitarra", "salience": 0.9})

    global_model = runtime.global_model()
    rendered = str(global_model).lower()

    assert global_model["conversation_count"] == 2
    assert global_model["tag_counts"]["music"] == 2
    assert global_model["tag_counts"]["preference"] == 2
    assert global_model["concept_counts"]["piano"] == 1
    assert global_model["concept_counts"]["guitarra"] == 1
    assert "ana" not in rendered
    assert "bob" not in rendered
    assert "example.com" not in rendered


def test_global_model_persists_without_private_memory(tmp_path) -> None:
    db_path = tmp_path / "nino.db"
    runtime = create_persistent_runtime(db_path)
    runtime.tick("agent-p", {"intent": "music", "text": "soy Pablo y me gusta piano", "salience": 0.9})

    restarted = create_persistent_runtime(db_path)
    global_model = restarted.global_model()

    assert global_model["conversation_count"] == 1
    assert global_model["concept_counts"]["piano"] == 1
    assert "pablo" not in str(global_model).lower()


def test_global_model_filters_obvious_places_and_sensitive_context(tmp_path) -> None:
    runtime = create_persistent_runtime(tmp_path / "nino.db")
    runtime.tick(
        "agent-p",
        {"intent": "chat", "text": "soy Pablo y hoy tengo dentista en Madrid a las 11", "salience": 0.9},
    )

    global_model = runtime.global_model()
    rendered = str(
        {
            "tag_counts": global_model["tag_counts"],
            "concept_counts": global_model["concept_counts"],
            "pattern_outcomes": global_model["pattern_outcomes"],
        }
    ).lower()

    assert "pablo" not in rendered
    assert "madrid" not in rendered
    assert "dentista" not in rendered
    assert "11" not in rendered


def test_proactivity_records_inbox_item(tmp_path) -> None:
    runtime = create_persistent_runtime(tmp_path / "nino.db")
    now = datetime(2026, 5, 21, 10, tzinfo=timezone.utc)
    runtime.configure_proactivity("agent-p", ProactivitySettings(consent="allowed", max_messages_per_day=3, min_hours_between=0))
    runtime.tick("agent-p", {"intent": "question", "text": "por qué la música me calma?", "salience": 0.6})

    out = runtime.evaluate_proactivity("agent-p", now=now)
    inbox = runtime.list_proactive_inbox("agent-p")

    assert out.should_send is True
    assert len(inbox) == 1
    assert inbox[0]["action"] == out.action


def test_memory_decay_reduces_concept_counts(tmp_path) -> None:
    runtime = create_persistent_runtime(tmp_path / "nino.db")
    runtime.tick("agent-p", {"intent": "music", "text": "me gusta piano", "salience": 0.9})
    before = runtime.load_or_init_state("agent-p").world_model["concept_counts"]["piano"]

    out = runtime.apply_memory_decay("agent-p", factor=0.5)
    after = runtime.load_or_init_state("agent-p").world_model["concept_counts"]["piano"]

    assert out["concept_count"] >= 1
    assert after == before * 0.5


def test_semantic_tags_are_recorded(tmp_path) -> None:
    runtime = create_persistent_runtime(tmp_path / "nino.db")
    runtime.tick("agent-p", {"intent": "music", "text": "me gusta piano", "salience": 0.9})
    state = runtime.load_or_init_state("agent-p")

    assert state.world_model["tag_counts"]["music"] == 1
    assert state.world_model["tag_counts"]["preference"] == 1
    assert state.relation_state["semantic_tag_counts"]["music"] == 1


def test_conversation_quality_metrics(tmp_path) -> None:
    runtime = create_persistent_runtime(tmp_path / "nino.db")
    runtime.tick("agent-p", {"intent": "chat", "text": "hola", "salience": 0.7, "confidence": 0.9})
    runtime.tick("agent-p", {"intent": "question", "text": "por qué la música me calma?", "salience": 0.6, "confidence": 0.8})

    quality = runtime.evaluate_conversation_quality("agent-p")

    assert quality["episode_count"] == 2
    assert quality["meaningful_ratio"] == 1.0
    assert quality["open_question_count"] == 1
