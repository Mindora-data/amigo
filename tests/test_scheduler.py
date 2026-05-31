from __future__ import annotations

from datetime import datetime, timedelta, timezone

from nino.contracts import ProactivitySettings
from nino.persistence import create_persistent_runtime
from nino.scheduler import NinoScheduler


def test_scheduler_runs_due_dream_and_proactivity(tmp_path) -> None:
    now = datetime(2026, 5, 21, 10, tzinfo=timezone.utc)
    runtime = create_persistent_runtime(tmp_path / "nino.db")
    runtime.configure_proactivity(
        "agent-s",
        ProactivitySettings(consent="allowed", max_messages_per_day=3, min_hours_between=0),
    )
    runtime.tick(
        "agent-s",
        {"intent": "question", "text": "por qué la música me calma?", "salience": 0.6, "confidence": 0.9},
    )

    out = NinoScheduler(runtime).run_pending("agent-s", now=now)
    state = runtime.load_or_init_state("agent-s")

    assert out.ran_dream is True
    assert out.ran_proactivity is True
    assert out.proactive_action is not None
    assert state.relation_state["scheduler"]["last_dream_at"] == now.isoformat()
    assert state.relation_state["scheduler"]["last_proactivity_check_at"] == now.isoformat()


def test_scheduler_does_not_repeat_before_intervals(tmp_path) -> None:
    now = datetime(2026, 5, 21, 10, tzinfo=timezone.utc)
    runtime = create_persistent_runtime(tmp_path / "nino.db")
    runtime.configure_proactivity("agent-s", ProactivitySettings(consent="allowed"))
    runtime.tick("agent-s", {"intent": "music", "text": "me gusta el piano", "salience": 0.9})
    scheduler = NinoScheduler(runtime)

    first = scheduler.run_pending("agent-s", now=now)
    second = scheduler.run_pending("agent-s", now=now + timedelta(minutes=30))

    assert first.ran_dream is True
    assert second.ran_dream is False
    assert second.ran_proactivity is False
    assert "nothing_due" in second.reason_trace


def test_scheduler_runs_again_after_intervals(tmp_path) -> None:
    now = datetime(2026, 5, 21, 10, tzinfo=timezone.utc)
    runtime = create_persistent_runtime(tmp_path / "nino.db")
    runtime.configure_proactivity("agent-s", ProactivitySettings(consent="allowed"))
    runtime.tick("agent-s", {"intent": "music", "text": "me gusta el piano", "salience": 0.9})
    scheduler = NinoScheduler(runtime)

    scheduler.run_pending("agent-s", now=now)
    later = scheduler.run_pending("agent-s", now=now + timedelta(hours=9))

    assert later.ran_dream is True
    assert later.ran_proactivity is True


def test_scheduler_imports_rss_on_global_interval(tmp_path) -> None:
    now = datetime(2026, 5, 21, 10, tzinfo=timezone.utc)
    runtime = create_persistent_runtime(tmp_path / "nino.db")
    runtime.add_rss_source({"name": "Mercados Test", "url": "https://example.test/rss", "theme": "mercados"})
    calls: list[datetime] = []

    def fake_import_all(now: datetime | None = None) -> dict:
        assert now is not None
        calls.append(now)
        return {"ok": True, "results": [], "added_count": 3}

    runtime.import_all_rss_sources = fake_import_all  # type: ignore[method-assign]
    scheduler = NinoScheduler(runtime, rss_interval_hours=6)

    first = scheduler.run_pending("agent-s", now=now)
    second = scheduler.run_pending("agent-s", now=now + timedelta(hours=1))
    third = scheduler.run_pending("agent-s", now=now + timedelta(hours=7))

    assert first.ran_rss_import is True
    assert first.rss_import == {"ok": True, "results": [], "added_count": 3}
    assert second.ran_rss_import is False
    assert third.ran_rss_import is True
    assert calls == [now, now + timedelta(hours=7)]
