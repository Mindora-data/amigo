from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from .contracts import MemoryCandidate, RetrieveRequest, RetrieveResponse

class ColdStoreProtocol(Protocol):
    def list_for_agent(self, agent_id: str) -> list[object]: ...

@dataclass(slots=True)
class Episode:
    episode_id: str
    agent_id: str
    timestamp: datetime
    text: str
    intent: str
    salience: float
    confidence: float

class InMemoryEpisodeStore:
    def __init__(self) -> None:
        self._episodes: dict[str, list[Episode]] = {}

    def append(self, episode: Episode) -> None:
        self._episodes.setdefault(episode.agent_id, []).append(episode)

    def list_for_agent(self, agent_id: str) -> list[Episode]:
        return list(self._episodes.get(agent_id, []))

def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))

def _tokenize(text: str) -> set[str]:
    return {tok.strip(".,!?;:\"'()[]{}").lower() for tok in text.split() if tok.strip()}

def _semantic_overlap(query: str, text: str) -> float:
    q = _tokenize(query)
    t = _tokenize(text)
    if not q or not t:
        return 0.0
    return len(q.intersection(t)) / max(len(q), 1)

def _recency_score(ts: datetime, now: datetime, scope: str) -> float:
    age_hours = max((now - ts).total_seconds() / 3600.0, 0.0)
    if scope == "recent":
        horizon = 72.0
    elif scope == "medium":
        horizon = 24.0 * 14
    else:
        horizon = 24.0 * 365
    return _clamp01(1.0 - (age_hours / horizon))

class MemoryRetriever:
    def __init__(self, episode_store: InMemoryEpisodeStore, cold_store: ColdStoreProtocol | None = None) -> None:
        self.episode_store = episode_store
        self.cold_store = cold_store

    def retrieve(self, agent_id: str, request: RetrieveRequest, top_k: int = 5) -> RetrieveResponse:
        now = datetime.now(timezone.utc)
        scored: list[MemoryCandidate] = []

        # HOT
        for ep in self.episode_store.list_for_agent(agent_id):
            sem = _semantic_overlap(request.query_intent, f"{ep.intent} {ep.text}")
            rec = _recency_score(ep.timestamp, now, request.time_scope)
            sal = _clamp01(ep.salience)
            conf = _clamp01(ep.confidence)
            score = _clamp01((0.45 * sem) + (0.25 * rec) + (0.20 * sal) + (0.10 * conf))
            if score > 0:
                scored.append(
                    MemoryCandidate(
                        fact_id=f"hot::{ep.episode_id}",
                        statement=ep.text,
                        score=round(score, 6),
                        source_episode_id=ep.episode_id,
                        confidence=conf,
                    )
                )

        # COLD
        if self.cold_store is not None:
            for fact in self.cold_store.list_for_agent(agent_id):
                if getattr(fact, "valid_to", None) is not None:
                    continue
                statement = f"{getattr(fact, 'key', '')} {getattr(fact, 'value', '')}".strip()
                sem = _semantic_overlap(request.query_intent, statement)
                rec = _recency_score(getattr(fact, "valid_from"), now, request.time_scope)
                conf = _clamp01(float(getattr(fact, "confidence", 0.0)))
                score = _clamp01((0.55 * sem) + (0.15 * rec) + (0.30 * conf))
                if score > 0:
                    scored.append(
                        MemoryCandidate(
                            fact_id=str(getattr(fact, "fact_id")),
                            statement=statement,
                            score=round(score, 6),
                            source_episode_id=str(getattr(fact, "source_episode_id")),
                            confidence=conf,
                        )
                    )

        scored.sort(key=lambda c: c.score, reverse=True)
        return RetrieveResponse(memory_candidates=scored[:top_k])
