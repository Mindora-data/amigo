from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

TimeScope = Literal["recent", "medium", "long"]
ProactivityConsent = Literal["unknown", "allowed", "paused", "denied"]

@dataclass(slots=True)
class MemoryCandidate:
    fact_id: str
    statement: str
    score: float
    source_episode_id: str
    confidence: float

@dataclass(slots=True)
class RetrieveRequest:
    query_intent: str
    self_state: dict[str, Any]
    relation_state: dict[str, Any]
    time_scope: TimeScope

@dataclass(slots=True)
class RetrieveResponse:
    memory_candidates: list[MemoryCandidate] = field(default_factory=list)
    temporal_query: bool = False
    temporal_window: dict[str, str] | None = None
    temporal_miss: bool = False

@dataclass(slots=True)
class PolicyRequest:
    percept_frame: dict[str, Any]
    drive_vector: dict[str, float]
    memory_candidates: list[MemoryCandidate]
    predicted_outcomes: list[dict[str, Any]]
    safety_rules: list[str]
    relation_state: dict[str, Any] = field(default_factory=dict)
    self_model: dict[str, Any] = field(default_factory=dict)
    world_model: dict[str, Any] = field(default_factory=dict)

@dataclass(slots=True)
class PolicyResponse:
    chosen_action: dict[str, Any]
    confidence: float
    reason_trace: list[str]

@dataclass(slots=True)
class ConsolidationRequest:
    agent_id: str
    episodes: list[dict[str, Any]]

@dataclass(slots=True)
class ConsolidationResponse:
    cold_memory_updates: list[dict[str, Any]]
    autobiographical_updates: list[str]
    contradictions: list[dict[str, Any]]

@dataclass(slots=True)
class ProactivitySettings:
    consent: ProactivityConsent = "unknown"
    max_messages_per_day: int = 1
    min_hours_between: float = 24.0
    active_hours_start: int = 0
    active_hours_end: int = 24

@dataclass(slots=True)
class ProactivityResponse:
    should_send: bool
    action: dict[str, Any] | None
    reason_trace: list[str]
    next_allowed_at: datetime | None = None

@dataclass(slots=True)
class AgentState:
    agent_id: str
    tick: int
    drive_vector: dict[str, float]
    active_goals: list[str]
    energy: float
    relation_state: dict[str, Any]
    cognitive_time: dict[str, float]
    self_model: dict[str, Any]
    world_model: dict[str, Any]
    updated_at: datetime
