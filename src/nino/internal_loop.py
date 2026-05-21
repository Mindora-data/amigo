from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
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


@dataclass(slots=True)
class DreamCycleResult:
    agent_id: str
    consolidated_count: int
    reflection_count: int
    maturity: float
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

    def dream_cycle(self, agent_id: str, now: datetime | None = None) -> DreamCycleResult:
        now = now or datetime.now(timezone.utc)
        state = self.runtime.load_or_init_state(agent_id)
        episodes = self.runtime.episode_store.list_for_agent(agent_id)
        consolidation = self.runtime.consolidate(
            ConsolidationRequest(agent_id=agent_id, episodes=episodes)
        )

        recent = episodes[-8:]
        reflection = self._build_reflection(state.self_model, state.world_model, recent, now)
        reflections = list(state.self_model.get("dream_reflections", []))
        if reflection is not None:
            reflections.append(reflection)
        state.self_model["dream_reflections"] = reflections[-30:]

        cognitive_time = dict(state.cognitive_time)
        cognitive_time["sleep_cycles"] = float(cognitive_time.get("sleep_cycles", 0.0)) + 1.0
        cognitive_time["experience_mass"] = round(
            float(cognitive_time.get("experience_mass", 0.0)) + (0.15 * max(len(recent), 1)),
            6,
        )
        cognitive_time["maturity"] = round(min(cognitive_time["experience_mass"] / 80.0, 1.0), 6)
        state.cognitive_time = cognitive_time

        drives = dict(state.drive_vector)
        drives["coherence"] = min(float(drives.get("coherence", 0.5)) + 0.04, 1.0)
        drives["energy"] = min(float(drives.get("energy", state.energy)) + 0.08, 1.0)
        drives["curiosity"] = max(float(drives.get("curiosity", 0.5)) - 0.01, 0.0)
        state.drive_vector = drives
        state.energy = drives["energy"]
        state.updated_at = now
        self.runtime.state_store.put(state)

        return DreamCycleResult(
            agent_id=agent_id,
            consolidated_count=len(consolidation.cold_memory_updates),
            reflection_count=1 if reflection is not None else 0,
            maturity=state.cognitive_time["maturity"],
            reason_trace=["dream_cycle", "memory_consolidation", "self_reflection"],
        )

    def _build_reflection(
        self,
        self_model: dict[str, Any],
        world_model: dict[str, Any],
        episodes: list[Any],
        now: datetime,
    ) -> dict[str, Any] | None:
        if not episodes and not world_model.get("concept_counts"):
            return None
        concepts = sorted(
            world_model.get("concept_counts", {}).items(),
            key=lambda item: item[1],
            reverse=True,
        )[:3]
        concept_text = ", ".join(key for key, _ in concepts) if concepts else "experiencias recientes"
        salient = [ep.text for ep in episodes if getattr(ep, "salience", 0.0) >= 0.7]
        summary = (
            f"He integrado {len(episodes)} experiencias recientes. "
            f"Los conceptos más activos son: {concept_text}."
        )
        if salient:
            summary += f" Una experiencia con peso fue: {salient[-1][:120]}."
        return {
            "recorded_at": now.isoformat(),
            "summary": summary,
            "source_episode_count": len(episodes),
            "identity_stage": self_model.get("identity_stage", "early_childhood"),
        }
