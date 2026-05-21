from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .contracts import ConsolidationRequest
from .runtime import NinoRuntime


@dataclass(slots=True)
class InternalCycleResult:
    agent_id: str
    consolidated_count: int
    contradictions: list[dict[str, Any]] = field(default_factory=list)
    proactive_action: dict[str, Any] | None = None
    reason_trace: list[str] = field(default_factory=list)


class InternalLoop:
    def __init__(self, runtime: NinoRuntime) -> None:
        self.runtime = runtime

    def cycle_once(
        self,
        agent_id: str,
        now: datetime | None = None,
        record_proactive_send: bool = True,
    ) -> InternalCycleResult:
        episodes = self.runtime.episode_store.list_for_agent(agent_id)
        consolidation = self.runtime.consolidate(
            ConsolidationRequest(agent_id=agent_id, episodes=episodes)
        )
        proactivity = self.runtime.evaluate_proactivity(
            agent_id,
            now=now,
            record_send=record_proactive_send,
        )

        return InternalCycleResult(
            agent_id=agent_id,
            consolidated_count=len(consolidation.cold_memory_updates),
            contradictions=consolidation.contradictions,
            proactive_action=proactivity.action if proactivity.should_send else None,
            reason_trace=proactivity.reason_trace,
        )
