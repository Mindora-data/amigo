from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import re

from .memory import Episode

PREFERENCE_RE = re.compile(
    r"\b(prefiero|me gusta)\s+(?P<value>[^.?!\n\r,;]{1,160})",
    re.IGNORECASE,
)
ADDRESS_NEGATIVE_RE = re.compile(
    r"\bno\s+me\s+(?:llames|digas|trates\s+de)\s+(?P<value>[^.?!\n\r,;]{1,80})",
    re.IGNORECASE,
)
ADDRESS_POSITIVE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:trátame|tratame)\s+(?:de|como\s+)?(?P<value>[^.?!\n\r,;]{1,80})",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:quiero|prefiero|me gusta)\s+que\s+me\s+(?:llames|trates)\s+(?:de|como\s+)?(?P<value>[^.?!\n\r,;]{1,80})",
        re.IGNORECASE,
    ),
)
FACT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "user_name",
        re.compile(
            r"\b(?:me llamo|mi nombre es)\s+(?P<value>[\wáéíóúñ]+(?:\s+[\wáéíóúñ]+){0,2})(?=\s+(?:y|pero|mi|trabajo)\b|[.?!\n\r,;]|$)",
            re.IGNORECASE,
        ),
    ),
    ("user_role", re.compile(r"\btrabajo como\s+(?P<value>[^.?!\n\r,;]{1,120})", re.IGNORECASE)),
    ("project_name", re.compile(r"\bmi proyecto se llama\s+(?P<value>[^.?!\n\r,;]{1,120})", re.IGNORECASE)),
    ("user_location", re.compile(r"\bvivo en\s+(?P<value>[^.?!\n\r,;]{1,120})", re.IGNORECASE)),
    ("user_study", re.compile(r"\bestudio\s+(?P<value>[^.?!\n\r,;]{1,120})", re.IGNORECASE)),
    (
        "current_project_focus",
        re.compile(r"\b(?:estoy|estamos)\s+trabajando en\s+(?P<value>[^.?!\n\r,;]{1,160})", re.IGNORECASE),
    ),
    (
        "user_expectation",
        re.compile(r"\b(?:quiero que|necesito que|tienes que|debes)\s+(?P<value>[^.?!\n\r,;]{1,160})", re.IGNORECASE),
    ),
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


def _clean_fact_value(value: str) -> str:
    words = re.findall(r"[\wáéíóúñ]+", value.lower())
    while words and words[0] in {"el", "la", "los", "las", "un", "una"}:
        words.pop(0)
    for i, word in enumerate(words):
        if word in {"y", "pero", "aunque"}:
            words = words[:i]
            break
    return " ".join(words[:12])


def _clean_address_value(value: str) -> str:
    value = value.strip().strip("\"'“”‘’").lower()
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r"\b(?:por favor|gracias)\b", "", value).strip()
    return value[:80]


def _preferences_conflict(old_value: str, new_value: str) -> bool:
    old = set(re.findall(r"[\wáéíóúñ]+", old_value.lower()))
    new = set(re.findall(r"[\wáéíóúñ]+", new_value.lower()))
    time_words = {"mañana", "mañanas", "noche", "noches", "tarde", "tardes"}
    if old & time_words and new & time_words:
        return True
    return bool(old & new) and old_value != new_value


SINGLETON_FACT_KEYS = {
    "user_name",
    "user_role",
    "user_location",
    "user_study",
    "project_name",
    "current_project_focus",
    "address_preference",
}


def _facts_conflict(key: str, old_value: str, new_value: str) -> bool:
    if old_value == new_value:
        return False
    if key == "preference":
        return _preferences_conflict(old_value, new_value)
    return key in SINGLETON_FACT_KEYS


def _contextual_memory_facts(text: str) -> list[tuple[str, str]]:
    plain = text.lower()
    facts: list[tuple[str, str]] = []
    if "no pares" in plain or "no te pares" in plain or "no te frenes" in plain:
        facts.append(("working_agreement", "avanzar sin detenerse hasta que el usuario pare"))
    if "sprint tras sprint" in plain or "siguiente sprint" in plain:
        facts.append(("working_agreement", "trabajar sprint tras sprint"))
    if "sin pedir permisos" in plain or "no pidas permisos" in plain:
        facts.append(("user_expectation", "avanzar sin pedir permisos salvo bloqueo del sistema"))
    return facts


def extract_address_preference(text: str) -> str | None:
    negative = ADDRESS_NEGATIVE_RE.search(text)
    if negative:
        value = _clean_address_value(negative.group("value"))
        if value:
            return f"no usar '{value}' para dirigirse al usuario"
    for pattern in ADDRESS_POSITIVE_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        value = _clean_address_value(match.group("value"))
        if value:
            return f"dirigirse al usuario como '{value}'"
    return None


def _cold_fact_id(episode_id: str, key: str, value: str) -> str:
    if key == "preference":
        return f"cold::{episode_id}"
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:10]
    return f"cold::{episode_id}::{key}::{digest}"


def _extract_memory_facts(text: str) -> list[tuple[str, str]]:
    facts: list[tuple[str, str]] = []
    address_preference = extract_address_preference(text)
    if address_preference:
        facts.append(("address_preference", address_preference))
    preference = PREFERENCE_RE.search(text)
    if preference:
        value = _clean_preference_value(preference.group("value"))
        if value:
            facts.append(("preference", value))
    for key, pattern in FACT_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        value = _clean_fact_value(match.group("value"))
        if value:
            facts.append((key, value))
    facts.extend(_contextual_memory_facts(text))
    return facts


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

            extracted = _extract_memory_facts(ep.text)
            if not extracted:
                continue

            for key, value in extracted:
                new_fact_id = _cold_fact_id(ep.episode_id, key, value)

                # idempotencia por episodio consolidado
                if new_fact_id in fact_ids:
                    continue

                active = [f for f in facts if f.key == key and f.valid_to is None]
                for old in active:
                    if _facts_conflict(key, old.value, value):
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
