from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re
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

    def delete_for_agent(self, agent_id: str) -> int:
        episodes = self._episodes.pop(agent_id, [])
        return len(episodes)

    def delete_episode(self, agent_id: str, episode_id: str) -> bool:
        episodes = self._episodes.get(agent_id, [])
        kept = [ep for ep in episodes if ep.episode_id != episode_id]
        if len(kept) == len(episodes):
            return False
        self._episodes[agent_id] = kept
        return True

    def list_agent_ids(self) -> list[str]:
        return sorted(self._episodes.keys())

def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))

STOPWORDS = {
    "el", "la", "los", "las", "un", "una", "de", "del", "que", "y", "a", "en",
    "me", "mi", "mis", "tu", "tus", "soy", "eres", "gusta", "prefiero",
    "hablemos", "hola", "buenas", "con", "por", "para", "chat", "question",
    "greeting", "unknown", "saludo", "intent",
}
SYNONYMS = {
    "musica": "música",
    "musical": "música",
    "pianista": "piano",
    "sprints": "sprint",
    "donde": "ubicacion",
    "direccion": "ubicacion",
    "ciudad": "ubicacion",
    "vivo": "ubicacion",
    "vivir": "ubicacion",
    "estudio": "formacion",
    "estudias": "formacion",
    "aprendo": "formacion",
    "aprendes": "formacion",
    "trabajo": "ocupacion",
    "trabajas": "ocupacion",
    "rol": "ocupacion",
    "proyecto": "proyecto",
    "trabajamos": "foco",
    "trabajando": "foco",
    "haciendo": "foco",
    "ayer": "tiempo",
    "hoy": "tiempo",
    "antes": "tiempo",
    "mes": "tiempo",
    "semanas": "tiempo",
    "dias": "tiempo",
    "semana": "tiempo",
    "pasada": "tiempo",
}
FACT_KEY_TERMS = {
    "user_name": "nombre identidad persona",
    "user_role": "trabajo ocupacion rol profesion",
    "user_location": "ubicacion ciudad donde vivo vivir",
    "user_study": "formacion estudio estudias aprender",
    "project_name": "proyecto nombre",
    "current_project_focus": "foco proyecto trabajando trabajamos haciendo",
    "preference": "preferencia gusto interesa",
    "working_agreement": "acuerdo trabajo forma avanzar sprint sprints continuidad",
    "user_expectation": "expectativa instruccion usuario permisos autonomia",
}

def _without_accents(value: str) -> str:
    return (
        value.lower()
        .replace("á", "a")
        .replace("é", "e")
        .replace("í", "i")
        .replace("ó", "o")
        .replace("ú", "u")
    )

def _tokenize(text: str) -> set[str]:
    tokens: set[str] = set()
    for raw in re.findall(r"[\wáéíóúñ]+", text.lower()):
        normalized = _without_accents(raw)
        if len(normalized) <= 2 or normalized in STOPWORDS:
            continue
        tokens.add(SYNONYMS.get(normalized, normalized))
    return tokens

def _semantic_overlap(query: str, text: str) -> float:
    q = _tokenize(query)
    t = _tokenize(text)
    if not q or not t:
        return 0.0
    return len(q.intersection(t)) / max(len(q), 1)

def _cold_statement(key: str, value: str) -> str:
    terms = FACT_KEY_TERMS.get(key, key.replace("_", " "))
    return f"{terms} {value}".strip()

def _recency_score(ts: datetime, now: datetime, scope: str) -> float:
    age_hours = max((now - ts).total_seconds() / 3600.0, 0.0)
    if scope == "recent":
        horizon = 72.0
    elif scope == "medium":
        horizon = 24.0 * 14
    else:
        horizon = 24.0 * 365
    return _clamp01(1.0 - (age_hours / horizon))

NUMBER_WORDS = {
    "un": 1,
    "una": 1,
    "uno": 1,
    "dos": 2,
    "tres": 3,
    "cuatro": 4,
    "cinco": 5,
    "seis": 6,
    "siete": 7,
    "ocho": 8,
    "nueve": 9,
    "diez": 10,
}

def _number_from_text(raw: str) -> int | None:
    raw = _without_accents(raw)
    if raw.isdigit():
        return int(raw)
    return NUMBER_WORDS.get(raw)

def _day_window(day: datetime) -> tuple[datetime, datetime]:
    start = day.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)

def _looks_like_temporal_recall_query(plain: str) -> bool:
    return any(
        marker in plain
        for marker in (
            "que ",
            "qué ",
            "?",
            "recuerd",
            "dije",
            "dijiste",
            "hablamos",
            "hicimos",
        )
    )

def _temporal_window(query: str, now: datetime) -> tuple[datetime, datetime] | None:
    plain = _without_accents(query)
    recall_query = _looks_like_temporal_recall_query(plain)
    if ("antes de ayer" in plain or "anteayer" in plain) and recall_query:
        return _day_window(now - timedelta(days=2))
    match = re.search(r"hace\s+(\d+|un|una|uno|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez)\s+dias?", plain)
    if match:
        days = _number_from_text(match.group(1))
        if days is not None:
            return _day_window(now - timedelta(days=days))
    match = re.search(r"hace\s+(\d+|un|una|uno|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez)\s+semanas?", plain)
    if match:
        weeks = _number_from_text(match.group(1))
        if weeks is not None:
            end = now - timedelta(days=7 * weeks)
            start = end - timedelta(days=7)
            return start, end
    if "mes pasado" in plain:
        return now - timedelta(days=60), now - timedelta(days=30)
    if "semana pasada" in plain:
        return now - timedelta(days=14), now - timedelta(days=7)
    if "ayer" in plain and recall_query:
        return _day_window(now - timedelta(days=1))
    if "hoy" in plain and recall_query:
        return _day_window(now)
    return None

def _temporal_window_payload(window: tuple[datetime, datetime] | None) -> dict[str, str] | None:
    if window is None:
        return None
    return {"start": window[0].isoformat(), "end": window[1].isoformat()}

class MemoryRetriever:
    def __init__(self, episode_store: InMemoryEpisodeStore, cold_store: ColdStoreProtocol | None = None) -> None:
        self.episode_store = episode_store
        self.cold_store = cold_store

    def retrieve(self, agent_id: str, request: RetrieveRequest, top_k: int = 5) -> RetrieveResponse:
        now = datetime.now(timezone.utc)
        temporal_window = _temporal_window(request.query_intent, now)
        temporal_hit = False
        scored: list[MemoryCandidate] = []

        # HOT
        for ep in self.episode_store.list_for_agent(agent_id):
            sem = _semantic_overlap(request.query_intent, f"{ep.intent} {ep.text}")
            if temporal_window is not None and temporal_window[0] <= ep.timestamp <= temporal_window[1]:
                sem = max(sem, 0.35)
                temporal_hit = True
            if sem <= 0:
                continue
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
                key = str(getattr(fact, "key", ""))
                value = str(getattr(fact, "value", ""))
                statement = _cold_statement(key, value)
                sem = _semantic_overlap(request.query_intent, statement)
                if sem <= 0:
                    continue
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
        return RetrieveResponse(
            memory_candidates=scored[:top_k],
            temporal_query=temporal_window is not None,
            temporal_window=_temporal_window_payload(temporal_window),
            temporal_miss=temporal_window is not None and not temporal_hit,
        )
