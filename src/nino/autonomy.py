from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Event, Thread
from time import monotonic
from typing import Any

from .runtime import NinoRuntime
from .scheduler import NinoScheduler


@dataclass(slots=True)
class AutonomyStatus:
    enabled: bool
    running: bool
    interval_seconds: float
    last_run_at: str | None = None
    last_agent_count: int = 0
    last_results: list[dict[str, Any]] = field(default_factory=list)


class BackgroundAutonomy:
    def __init__(self, runtime: NinoRuntime, interval_seconds: float = 60.0) -> None:
        self.runtime = runtime
        self.interval_seconds = max(0.1, float(interval_seconds))
        self.scheduler = NinoScheduler(runtime)
        self._stop = Event()
        self._thread: Thread | None = None
        self._last_run_at: str | None = None
        self._last_agent_count = 0
        self._last_results: list[dict[str, Any]] = []

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = Thread(target=self._run_loop, name="nino-autonomy", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def run_once(self, now: datetime | None = None) -> list[dict[str, Any]]:
        now = now or datetime.now(timezone.utc)
        results = []
        agents = self.runtime.list_agents()
        for agent_id in agents:
            result = self.scheduler.run_pending(agent_id, now=now)
            results.append(
                {
                    "agent_id": result.agent_id,
                    "ran_dream": result.ran_dream,
                    "ran_proactivity": result.ran_proactivity,
                    "ran_rss_import": result.ran_rss_import,
                    "proactive_action": result.proactive_action,
                    "rss_import": result.rss_import,
                    "reason_trace": result.reason_trace,
                }
            )
        self._last_run_at = now.isoformat()
        self._last_agent_count = len(agents)
        self._last_results = results
        return results

    def status(self) -> AutonomyStatus:
        return AutonomyStatus(
            enabled=True,
            running=self._thread is not None and self._thread.is_alive(),
            interval_seconds=self.interval_seconds,
            last_run_at=self._last_run_at,
            last_agent_count=self._last_agent_count,
            last_results=list(self._last_results),
        )

    def _run_loop(self) -> None:
        next_run = monotonic()
        while not self._stop.is_set():
            now = monotonic()
            if now >= next_run:
                self.run_once()
                next_run = now + self.interval_seconds
            self._stop.wait(timeout=min(0.5, self.interval_seconds))
