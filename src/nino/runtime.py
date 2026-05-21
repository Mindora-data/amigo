from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from .contracts import (
    AgentState,
    ConsolidationRequest,
    ConsolidationResponse,
    PolicyRequest,
    PolicyResponse,
    RetrieveRequest,
    RetrieveResponse,
)
from .memory import Episode, InMemoryEpisodeStore, MemoryRetriever

class InMemoryStateStore:
    def __init__(self) -> None:
        self._states: dict[str, AgentState] = {}

    def get(self, agent_id: str) -> AgentState | None:
        return self._states.get(agent_id)

    def put(self, state: AgentState) -> None:
        self._states[state.agent_id] = state

def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))

class NinoRuntime:
    def __init__(self, state_store: InMemoryStateStore, episode_store: InMemoryEpisodeStore | None = None) -> None:
        self.state_store = state_store
        self.episode_store = episode_store or InMemoryEpisodeStore()
        self.retriever = MemoryRetriever(self.episode_store)

    def load_or_init_state(self, agent_id: str) -> AgentState:
        existing = self.state_store.get(agent_id)
        if existing:
            return existing
        initial = AgentState(
            agent_id=agent_id,
            tick=0,
            drive_vector={
                "curiosity": 0.5,
                "safety": 0.5,
                "attachment": 0.5,
                "coherence": 0.5,
                "exploration": 0.5,
                "energy": 0.8,
            },
            active_goals=[],
            energy=0.8,
            relation_state={},
            updated_at=datetime.now(timezone.utc),
        )
        self.state_store.put(initial)
        return initial

    def retrieve_memory(self, agent_id: str, request: RetrieveRequest) -> RetrieveResponse:
        return self.retriever.retrieve(agent_id=agent_id, request=request, top_k=5)

    def policy_decide(self, request: PolicyRequest) -> PolicyResponse:
        action = {
            "type": "external_message",
            "payload": {"text": "Entiendo. Lo registro y seguimos construyendo continuidad."},
        }
        return PolicyResponse(chosen_action=action, confidence=0.6, reason_trace=["default_f1_policy"])

    def consolidate(self, request: ConsolidationRequest) -> ConsolidationResponse:
        return ConsolidationResponse(cold_memory_updates=[], autobiographical_updates=[], contradictions=[])

    def tick(self, agent_id: str, percept_frame: dict[str, Any]) -> dict[str, Any]:
        state = self.load_or_init_state(agent_id)

        retrieve_req = RetrieveRequest(
            query_intent=percept_frame.get("intent", "unknown"),
            self_state=asdict(state),
            relation_state=state.relation_state,
            time_scope="recent",
        )
        retrieved = self.retrieve_memory(agent_id, retrieve_req)

        policy_req = PolicyRequest(
            percept_frame=percept_frame,
            drive_vector=state.drive_vector,
            memory_candidates=retrieved.memory_candidates,
            predicted_outcomes=[],
            safety_rules=["respect_privacy", "non_intrusive_proactivity"],
        )
        decision = self.policy_decide(policy_req)

        now = datetime.now(timezone.utc)
        self.episode_store.append(
            Episode(
                episode_id=str(uuid4()),
                agent_id=agent_id,
                timestamp=now,
                text=percept_frame.get("text", ""),
                intent=percept_frame.get("intent", "unknown"),
                salience=_clamp01(float(percept_frame.get("salience", 0.5))),
                confidence=_clamp01(float(percept_frame.get("confidence", 0.8))),
            )
        )

        state.tick += 1
        state.updated_at = now
        state.energy = _clamp01(state.energy - 0.01)
        self.state_store.put(state)

        return {
            "tick": state.tick,
            "action": decision.chosen_action,
            "confidence": decision.confidence,
            "reason_trace": decision.reason_trace,
            "retrieved_memory_count": len(retrieved.memory_candidates),
        }
