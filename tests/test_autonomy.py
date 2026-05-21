from __future__ import annotations

from datetime import datetime, timezone
from time import sleep

from nino.autonomy import BackgroundAutonomy
from nino.persistence import create_persistent_runtime


def test_background_autonomy_run_once_updates_status(tmp_path) -> None:
    runtime = create_persistent_runtime(tmp_path / "nino.db")
    runtime.tick("agent-auto", {"intent": "chat", "text": "hola"})
    autonomy = BackgroundAutonomy(runtime, interval_seconds=10)
    now = datetime(2026, 5, 21, 10, tzinfo=timezone.utc)

    results = autonomy.run_once(now=now)
    status = autonomy.status()

    assert len(results) == 1
    assert results[0]["agent_id"] == "agent-auto"
    assert status.enabled is True
    assert status.running is False
    assert status.last_run_at == now.isoformat()
    assert status.last_agent_count == 1


def test_background_autonomy_thread_can_use_persistent_runtime(tmp_path) -> None:
    runtime = create_persistent_runtime(tmp_path / "nino.db")
    runtime.tick("agent-auto", {"intent": "chat", "text": "hola"})
    autonomy = BackgroundAutonomy(runtime, interval_seconds=0.1)

    autonomy.start()
    sleep(0.2)
    autonomy.stop()
    status = autonomy.status()

    assert status.last_agent_count == 1
    assert status.last_run_at is not None
