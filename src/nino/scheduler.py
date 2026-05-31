from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from .internal_loop import InternalLoop
from .runtime import NinoRuntime


@dataclass(slots=True)
class SchedulerResult:
    agent_id: str
    ran_dream: bool = False
    ran_proactivity: bool = False
    ran_rss_import: bool = False
    proactive_action: dict[str, Any] | None = None
    rss_import: dict[str, Any] | None = None
    reason_trace: list[str] = field(default_factory=list)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


class NinoScheduler:
    def __init__(
        self,
        runtime: NinoRuntime,
        dream_interval_hours: float = 8.0,
        proactivity_interval_hours: float = 2.0,
        rss_interval_hours: float = 6.0,
    ) -> None:
        self.runtime = runtime
        self.internal_loop = InternalLoop(runtime)
        self.dream_interval = timedelta(hours=dream_interval_hours)
        self.proactivity_interval = timedelta(hours=proactivity_interval_hours)
        self.rss_interval = timedelta(hours=rss_interval_hours)
        self._last_rss_import_at: datetime | None = None

    def run_pending(self, agent_id: str, now: datetime | None = None) -> SchedulerResult:
        now = now or datetime.now(timezone.utc)
        state = self.runtime.load_or_init_state(agent_id)
        scheduler_state = dict(state.relation_state.get("scheduler", {}))
        reason_trace = ["scheduler"]

        last_dream = _parse_dt(scheduler_state.get("last_dream_at"))
        dream_due = last_dream is None or now - last_dream >= self.dream_interval
        ran_dream = False
        if dream_due and state.tick > 0:
            self.internal_loop.dream_cycle(agent_id, now=now)
            state = self.runtime.load_or_init_state(agent_id)
            scheduler_state = dict(state.relation_state.get("scheduler", {}))
            scheduler_state["last_dream_at"] = now.isoformat()
            ran_dream = True
            reason_trace.append("dream_due")

        last_proactivity = _parse_dt(scheduler_state.get("last_proactivity_check_at"))
        proactivity_due = (
            last_proactivity is None
            or now - last_proactivity >= self.proactivity_interval
        )
        ran_proactivity = False
        proactive_action: dict[str, Any] | None = None
        if proactivity_due:
            proactivity = self.runtime.evaluate_proactivity(agent_id, now=now, record_send=True)
            scheduler_state["last_proactivity_check_at"] = now.isoformat()
            ran_proactivity = True
            reason_trace.extend(proactivity.reason_trace)
            if proactivity.should_send:
                proactive_action = proactivity.action

        ran_rss_import = False
        rss_import: dict[str, Any] | None = None
        rss_due = self._last_rss_import_at is None or now - self._last_rss_import_at >= self.rss_interval
        if rss_due and self.runtime.rss_culture_store.list_sources(active_only=True):
            rss_import = self.runtime.import_all_rss_sources(now=now)
            self._last_rss_import_at = now
            ran_rss_import = True
            reason_trace.append("rss_import_due")
            if not rss_import.get("ok"):
                reason_trace.append("rss_import_partial_failure")

        state = self.runtime.load_or_init_state(agent_id)
        state.relation_state = {**state.relation_state, "scheduler": scheduler_state}
        state.updated_at = now
        self.runtime.state_store.put(state)

        if not ran_dream and not ran_proactivity and not ran_rss_import:
            reason_trace.append("nothing_due")

        return SchedulerResult(
            agent_id=agent_id,
            ran_dream=ran_dream,
            ran_proactivity=ran_proactivity,
            ran_rss_import=ran_rss_import,
            proactive_action=proactive_action,
            rss_import=rss_import,
            reason_trace=reason_trace,
        )
