from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re

from .memory import Episode

PREFERENCE_RE = re.compile(
    r"\b(prefiero|me gusta)\s+(?P<value>[^.?!\n\r,;]{1,160})",
    re.IGNORECASE,
)

@dataclass(slots=True)
class MemoryFact:
    fact_id: str
    agent_id: str
    key: str
    value: str
    confidence: float
    source_episode_id: str
    valid_from: datetime
    valid_to: datetime | None = None

class InMemoryColdStore:
    def __init__(self) -> None:
        self._facts: dict[str, list[MemoryFact]] = {}

    def upsert(self, fact: MemoryFact) -> None:
        facts = self._facts.setdefault(fact.agent_id, [])
        for i, existing in enumerate(facts):
            if existing.fact_id == fact.fact_id:
                facts[i] = fact
                return
        facts.append(fact)

    def list_for_agent(self, agent_id: str) -> list[MemoryFact]:
        return list(self._facts.get(agent_id, []))

    def delete_for_agent(self, agent_id: str) -> int:
        facts = self._facts.pop(agent_id, [])
        return len(facts)

    def delete_fact(self, agent_id: str, fact_id: str) -> bool:
        facts = self._facts.get(agent_id, [])
        kept = [fact for fact in facts if fact.fact_id != fact_id]
        if len(kept) == len(facts):
            return False
        self._facts[agent_id] = kept
        return True

    def list_agent_ids(self) -> list[str]:
        return sorted(self._facts.keys())

def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))

def _clean_preference_value(value: str) -> str:
    words = re.findall(r"[\wáéíóúñ]+", value.lower())
    while words and words[0] in {"el", "la", "los", "las", "un", "una"}:
        words.pop(0)
    return " ".join(words)


def _preferences_conflict(old_value: str, new_value: str) -> bool:
    old = set(re.findall(r"[\wáéíóúñ]+", old_value.lower()))
    new = set(re.findall(r"[\wáéíóúñ]+", new_value.lower()))
    time_words = {"mañana", "mañanas", "noche", "noches", "tarde", "tardes"}
    if old & time_words and new & time_words:
        return True
    return bool(old & new) and old_value != new_value


class Consolidator:
    def __init__(self, cold_store: InMemoryColdStore) -> None:
        self.cold_store = cold_store

    def consolidate(
        self,
        agent_id: str,
        episodes: list[Episode],
        since: datetime | None = None,
        until: datetime | None = None,
        min_confidence: float = 0.55,
    ) -> dict[str, list[dict]]:
        now = datetime.now(timezone.utc)
        window_start = since or (now - timedelta(hours=24))
        window_end = until or now

        cold_updates: list[dict] = []
        contradictions: list[dict] = []

        facts = self.cold_store.list_for_agent(agent_id)
        fact_ids = {f.fact_id for f in facts}

        filtered = [e for e in episodes if window_start <= e.timestamp <= window_end]
        filtered.sort(key=lambda e: (e.timestamp, e.episode_id))

        for ep in filtered:
            conf = _clamp01(ep.confidence)
            if conf < min_confidence:
                continue

            m = PREFERENCE_RE.search(ep.text)
            if not m:
                continue

            value = _clean_preference_value(m.group("value"))
            key = "preference"
            new_fact_id = f"cold::{ep.episode_id}"

            # idempotencia por episodio consolidado
            if new_fact_id in fact_ids:
                continue

            active = [f for f in facts if f.key == key and f.valid_to is None]
            for old in active:
                if old.value != value and _preferences_conflict(old.value, value):
                    old.valid_to = ep.timestamp
                    self.cold_store.upsert(old)
                    contradictions.append(
                        {
                            "key": key,
                            "old_value": old.value,
                            "new_value": value,
                            "resolved_at": ep.timestamp.isoformat(),
                            "old_source": old.source_episode_id,
                            "new_source": ep.episode_id,
                            "reason": "newer_high_confidence_signal",
                        }
                    )

            new_fact = MemoryFact(
                fact_id=new_fact_id,
                agent_id=agent_id,
                key=key,
                value=value,
                confidence=conf,
                source_episode_id=ep.episode_id,
                valid_from=ep.timestamp,
            )
            self.cold_store.upsert(new_fact)
            facts.append(new_fact)
            fact_ids.add(new_fact_id)

            cold_updates.append(
                {
                    "fact_id": new_fact.fact_id,
                    "key": new_fact.key,
                    "value": new_fact.value,
                    "source_episode_id": new_fact.source_episode_id,
                    "confidence": new_fact.confidence,
                    "valid_from": new_fact.valid_from.isoformat(),
                }
            )

        return {"cold_memory_updates": cold_updates, "contradictions": contradictions}
