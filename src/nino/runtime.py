from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import re
from typing import Any
from uuid import uuid4

from .consolidation import Consolidator, InMemoryColdStore
from .consolidation import MemoryFact
from .contracts import (
    AgentState,
    ConsolidationRequest,
    ConsolidationResponse,
    PolicyRequest,
    PolicyResponse,
    ProactivityResponse,
    ProactivitySettings,
    RetrieveRequest,
    RetrieveResponse,
)
from .memory import Episode, InMemoryEpisodeStore, MemoryRetriever
from .proactivity import (
    ProactivityEngine,
    configure_proactivity_state,
    default_proactivity_state,
    record_proactive_send,
)

class InMemoryStateStore:
    def __init__(self) -> None:
        self._states: dict[str, AgentState] = {}

    def get(self, agent_id: str) -> AgentState | None:
        return self._states.get(agent_id)

    def put(self, state: AgentState) -> None:
        self._states[state.agent_id] = state

    def delete(self, agent_id: str) -> None:
        self._states.pop(agent_id, None)

    def list_agent_ids(self) -> list[str]:
        return sorted(self._states.keys())

def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))

def _normalize_text(value: str) -> str:
    return " ".join(re.findall(r"[\wáéíóúñ]+", value.lower()))

def _without_accents(value: str) -> str:
    return (
        value.lower()
        .replace("á", "a")
        .replace("é", "e")
        .replace("í", "i")
        .replace("ó", "o")
        .replace("ú", "u")
    )

def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))

def _redact_text(value: str) -> str:
    value = re.sub(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", "[email]", value)
    value = re.sub(r"\b\d{3,}\b", "[number]", value)
    return value

def _semantic_tags(text: str) -> list[str]:
    plain = _without_accents(text)
    tags = []
    if any(word in plain for word in ("piano", "musica", "guitarra")):
        tags.append("music")
    if any(word in plain for word in ("triste", "agobiado", "feliz", "contento", "estresado")):
        tags.append("emotion")
    if any(word in plain for word in ("examen", "trabajo", "proyecto", "estudio")):
        tags.append("work_learning")
    if any(word in plain for word in ("prefiero", "me gusta")):
        tags.append("preference")
    if "?" in text:
        tags.append("question")
    return sorted(set(tags))

def _clean_preference_value(value: str) -> str:
    words = _normalize_text(value).split()
    while words and words[0] in {"el", "la", "los", "las", "un", "una"}:
        words.pop(0)
    return " ".join(words)

def _detect_emotional_tone(text: str) -> str | None:
    for tone, pattern in EMOTION_PATTERNS.items():
        if pattern.search(text):
            return tone
    return None

def _update_relation_from_percept(
    relation_state: dict[str, Any],
    percept_frame: dict[str, Any],
    now: datetime,
) -> dict[str, Any]:
    relation = dict(relation_state)
    text = str(percept_frame.get("text", "")).strip()
    relation["interaction_count"] = int(relation.get("interaction_count", 0)) + 1
    relation["last_interaction_at"] = now.isoformat()

    name = NAME_RE.search(text)
    if name:
        relation["user_name"] = name.group("name")
        relation["user_name_updated_at"] = now.isoformat()

    preference = PREFERENCE_RE.search(text)
    if preference:
        value = _clean_preference_value(preference.group("value"))
        if value:
            preferences = dict(relation.get("preferences", {}))
            preferences[value] = {
                "source": "user_statement",
                "confidence": _clamp01(float(percept_frame.get("confidence", 0.8))),
                "salience": _clamp01(float(percept_frame.get("salience", 0.5))),
                "updated_at": now.isoformat(),
            }
            relation["preferences"] = preferences

    emotional_tone = _detect_emotional_tone(text)
    if emotional_tone is not None:
        signals = list(relation.get("emotional_signals", []))
        signals.append(
            {
                "tone": emotional_tone,
                "text": text[:160],
                "observed_at": now.isoformat(),
            }
        )
        relation["emotional_signals"] = signals[-30:]
        relation["last_emotional_tone"] = emotional_tone
    tags = _semantic_tags(text)
    if tags:
        tag_counts = dict(relation.get("semantic_tag_counts", {}))
        for tag in tags:
            tag_counts[tag] = int(tag_counts.get(tag, 0)) + 1
        relation["semantic_tag_counts"] = tag_counts

    return relation

PREFERENCE_RE = re.compile(
    r"\b(prefiero|me gusta)\s+(?P<value>[\wáéíóúñ]+(?:\s+[\wáéíóúñ]+){0,4})",
    re.IGNORECASE,
)
NAME_RE = re.compile(r"\bsoy\s+(?P<name>[\wáéíóúñ]+)", re.IGNORECASE)
TOPIC_RE = re.compile(
    r"\b(?:hablemos de|quiero hablar de|hablar de)\s+(?P<topic>[\wáéíóúñ]+(?:\s+[\wáéíóúñ]+){0,4})",
    re.IGNORECASE,
)
EMOTION_PATTERNS = {
    "sad": re.compile(r"\b(triste|solo|sola|mal|baj[oó]n|abatido|abatida)\b", re.IGNORECASE),
    "happy": re.compile(r"\b(contento|contenta|feliz|bien|alegre|motivado|motivada)\b", re.IGNORECASE),
    "stressed": re.compile(r"\b(estresado|estresada|agobiado|agobiada|preocupado|preocupada|cansado|cansada)\b", re.IGNORECASE),
}
GENERIC_INTENTS = {"chat", "question", "greeting", "saludo", "unknown"}
STOPWORDS = {
    "el", "la", "los", "las", "un", "una", "de", "del", "que", "y", "a", "en",
    "me", "mi", "mis", "tu", "tus", "soy", "eres", "gusta", "prefiero",
    "hablemos", "hola", "buenas", "con", "por", "para",
}

def _default_cognitive_time() -> dict[str, float]:
    return {"age_ticks": 0.0, "experience_mass": 0.0, "maturity": 0.0}

def _default_self_model() -> dict[str, Any]:
    return {
        "identity_stage": "early_childhood",
        "interaction_count": 0,
        "affect_state": {"mood": "stable", "intensity": 0.2},
        "known_capabilities": ["remember_episodes", "retrieve_context", "safe_proactivity"],
        "known_limits": ["minimal_language_policy", "no_background_daemon_yet"],
        "autobiographical_timeline": [],
    }

def _default_world_model() -> dict[str, Any]:
    return {
        "concept_counts": {},
        "intent_counts": {},
        "open_questions": [],
        "causal_observations": [],
    }

def _tokens_for_model(text: str) -> list[str]:
    return [
        token
        for token in re.findall(r"[\wáéíóúñ]+", text.lower())
        if len(token) > 2 and token not in STOPWORDS
    ]

def _update_cognitive_models(
    state: AgentState,
    percept_frame: dict[str, Any],
    now: datetime,
) -> None:
    text = str(percept_frame.get("text", "")).strip()
    intent = str(percept_frame.get("intent", "unknown"))
    salience = _clamp01(float(percept_frame.get("salience", 0.5)))
    confidence = _clamp01(float(percept_frame.get("confidence", 0.8)))

    cognitive_time = dict(state.cognitive_time)
    cognitive_time["age_ticks"] = float(cognitive_time.get("age_ticks", 0.0)) + 1.0
    delta = salience * confidence
    cognitive_time["experience_mass"] = round(float(cognitive_time.get("experience_mass", 0.0)) + delta, 6)
    cognitive_time["maturity"] = round(_clamp01(cognitive_time["experience_mass"] / 80.0), 6)
    state.cognitive_time = cognitive_time

    world_model = dict(state.world_model)
    intent_counts = dict(world_model.get("intent_counts", {}))
    intent_counts[intent] = int(intent_counts.get(intent, 0)) + 1
    world_model["intent_counts"] = intent_counts

    concept_counts = dict(world_model.get("concept_counts", {}))
    for token in _tokens_for_model(f"{intent} {text}"):
        concept_counts[token] = int(concept_counts.get(token, 0)) + 1
    world_model["concept_counts"] = concept_counts
    tags = _semantic_tags(text)
    if tags:
        tag_counts = dict(world_model.get("tag_counts", {}))
        for tag in tags:
            tag_counts[tag] = int(tag_counts.get(tag, 0)) + 1
        world_model["tag_counts"] = tag_counts

    if "?" in text:
        open_questions = list(world_model.get("open_questions", []))
        open_questions.append({"text": text, "observed_at": now.isoformat()})
        world_model["open_questions"] = open_questions[-20:]
    if "porque" in text.lower():
        causal = list(world_model.get("causal_observations", []))
        causal.append({"text": text, "observed_at": now.isoformat()})
        world_model["causal_observations"] = causal[-20:]
    state.world_model = world_model

    self_model = dict(state.self_model)
    self_model["interaction_count"] = int(self_model.get("interaction_count", 0)) + 1
    self_model["last_experienced_intent"] = intent
    self_model["identity_stage"] = (
        "early_childhood"
        if cognitive_time["maturity"] < 0.25
        else "developing_childhood"
    )
    if salience >= 0.75:
        timeline = list(self_model.get("autobiographical_timeline", []))
        timeline.append(
            {
                "tick": state.tick,
                "intent": intent,
                "summary": text[:160],
                "salience": salience,
                "recorded_at": now.isoformat(),
            }
        )
        self_model["autobiographical_timeline"] = timeline[-30:]
    emotional_tone = _detect_emotional_tone(text)
    if emotional_tone is not None:
        affect = dict(self_model.get("affect_state", {}))
        affect["mood"] = {
            "sad": "concerned",
            "happy": "warm",
            "stressed": "attentive",
        }[emotional_tone]
        affect["intensity"] = round(max(float(affect.get("intensity", 0.2)), salience), 3)
        affect["source"] = "user_emotional_signal"
        affect["updated_at"] = now.isoformat()
        self_model["affect_state"] = affect
    state.self_model = self_model

def _regulate_drives(state: AgentState, percept_frame: dict[str, Any]) -> None:
    text = str(percept_frame.get("text", "")).strip()
    salience = _clamp01(float(percept_frame.get("salience", 0.5)))
    confidence = _clamp01(float(percept_frame.get("confidence", 0.8)))
    known_concepts = set(state.world_model.get("concept_counts", {}).keys())
    tokens = set(_tokens_for_model(text))
    novelty = len(tokens.difference(known_concepts)) / max(len(tokens), 1)
    open_question_pressure = min(len(state.world_model.get("open_questions", [])) / 10.0, 1.0)
    relation_depth = min(int(state.relation_state.get("interaction_count", 0)) / 50.0, 1.0)
    maturity = _clamp01(float(state.cognitive_time.get("maturity", 0.0)))

    drives = dict(state.drive_vector)
    drives["curiosity"] = _clamp01(
        drives.get("curiosity", 0.5)
        + (0.05 * novelty)
        + (0.03 * open_question_pressure)
        - (0.02 * maturity)
    )
    drives["attachment"] = _clamp01(
        drives.get("attachment", 0.5)
        + (0.03 * relation_depth)
        + (0.02 if state.relation_state.get("user_name") else 0.0)
    )
    drives["coherence"] = _clamp01(
        drives.get("coherence", 0.5)
        + (0.04 * maturity)
        + (0.04 * confidence)
        - (0.02 * novelty)
    )
    drives["exploration"] = _clamp01(
        drives.get("exploration", 0.5)
        + (0.04 * drives["curiosity"])
        - (0.02 * drives.get("safety", 0.5))
    )
    drives["safety"] = _clamp01(
        drives.get("safety", 0.5)
        + (0.02 * (1.0 - confidence))
        - (0.01 * relation_depth)
    )
    drives["energy"] = _clamp01(
        drives.get("energy", state.energy)
        - 0.01
        - (0.01 * salience)
        + (0.005 * maturity)
    )
    state.drive_vector = drives

def _derive_active_goals(state: AgentState) -> list[str]:
    goals: list[str] = []
    drives = state.drive_vector
    if drives.get("curiosity", 0.0) >= 0.58 or state.world_model.get("open_questions"):
        goals.append("reduce_uncertainty")
    if drives.get("attachment", 0.0) >= 0.58 or state.relation_state.get("user_name"):
        goals.append("maintain_relationship_continuity")
    if drives.get("coherence", 0.0) >= 0.55:
        goals.append("consolidate_self_narrative")
    if state.relation_state.get("preferences"):
        goals.append("revisit_user_preferences")
    if drives.get("energy", 1.0) <= 0.25:
        goals.append("recover_energy")
    return goals[:5]

class NinoRuntime:
    def __init__(
        self,
        state_store: InMemoryStateStore,
        episode_store: InMemoryEpisodeStore | None = None,
        cold_store: InMemoryColdStore | None = None,
    ) -> None:
        self.state_store = state_store
        self.episode_store = episode_store or InMemoryEpisodeStore()
        self.cold_store = cold_store or InMemoryColdStore()
        self.retriever = MemoryRetriever(self.episode_store, self.cold_store)
        self.consolidator = Consolidator(self.cold_store)
        self.proactivity = ProactivityEngine(self.episode_store)

    def load_or_init_state(self, agent_id: str) -> AgentState:
        existing = self.state_store.get(agent_id)
        if existing:
            return existing
        initial = AgentState(
            agent_id=agent_id,
            tick=0,
            drive_vector={
                "curiosity": 0.5, "safety": 0.5, "attachment": 0.5,
                "coherence": 0.5, "exploration": 0.5, "energy": 0.8
            },
            active_goals=[],
            energy=0.8,
            relation_state={"proactivity": default_proactivity_state()},
            cognitive_time=_default_cognitive_time(),
            self_model=_default_self_model(),
            world_model=_default_world_model(),
            updated_at=datetime.now(timezone.utc),
        )
        self.state_store.put(initial)
        return initial

    def configure_proactivity(
        self,
        agent_id: str,
        settings: ProactivitySettings,
    ) -> AgentState:
        state = self.load_or_init_state(agent_id)
        state.relation_state = configure_proactivity_state(state.relation_state, settings)
        state.updated_at = datetime.now(timezone.utc)
        self.state_store.put(state)
        return state

    def evaluate_proactivity(
        self,
        agent_id: str,
        now: datetime | None = None,
        record_send: bool = True,
    ) -> ProactivityResponse:
        state = self.load_or_init_state(agent_id)
        result = self.proactivity.evaluate(
            agent_id,
            state.relation_state,
            now=now,
            self_model=state.self_model,
            world_model=state.world_model,
            drive_vector=state.drive_vector,
            active_goals=state.active_goals,
        )
        if result.should_send and record_send:
            sent_at = now or datetime.now(timezone.utc)
            if result.action is not None:
                self.enqueue_proactive_action(agent_id, result.action, now=sent_at)
                state = self.load_or_init_state(agent_id)
            state.relation_state = record_proactive_send(state.relation_state, sent_at)
            state.updated_at = sent_at
            self.state_store.put(state)
        return result

    def reset_agent(self, agent_id: str) -> dict[str, Any]:
        deleted: dict[str, Any] = {"agent_id": agent_id}
        if hasattr(self.state_store, "delete"):
            self.state_store.delete(agent_id)
            deleted["state"] = True
        if hasattr(self.episode_store, "delete_for_agent"):
            deleted["episodes"] = self.episode_store.delete_for_agent(agent_id)
        if hasattr(self.cold_store, "delete_for_agent"):
            deleted["cold_memory"] = self.cold_store.delete_for_agent(agent_id)
        return deleted

    def list_agents(self) -> list[str]:
        ids: set[str] = set()
        for store in (self.state_store, self.episode_store, self.cold_store):
            if hasattr(store, "list_agent_ids"):
                ids.update(store.list_agent_ids())
        return sorted(ids)

    def prune_agents(
        self,
        prefixes: list[str] | None = None,
        agent_ids: list[str] | None = None,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        prefixes = [prefix for prefix in (prefixes or []) if prefix]
        explicit_ids = {agent_id for agent_id in (agent_ids or []) if agent_id}
        matched = [
            agent_id
            for agent_id in self.list_agents()
            if agent_id in explicit_ids or any(agent_id.startswith(prefix) for prefix in prefixes)
        ]
        deleted = []
        if not dry_run:
            for agent_id in matched:
                deleted.append(self.reset_agent(agent_id))
        return {
            "dry_run": dry_run,
            "prefixes": prefixes,
            "agent_ids": sorted(explicit_ids),
            "matched": matched,
            "deleted": deleted,
        }

    def delete_episode(self, agent_id: str, episode_id: str) -> dict[str, Any]:
        deleted = False
        if hasattr(self.episode_store, "delete_episode"):
            deleted = bool(self.episode_store.delete_episode(agent_id, episode_id))
        return {"agent_id": agent_id, "episode_id": episode_id, "deleted": deleted}

    def delete_memory_fact(self, agent_id: str, fact_id: str) -> dict[str, Any]:
        deleted = False
        if hasattr(self.cold_store, "delete_fact"):
            deleted = bool(self.cold_store.delete_fact(agent_id, fact_id))
        return {"agent_id": agent_id, "fact_id": fact_id, "deleted": deleted}

    def retrieve_memory(self, agent_id: str, request: RetrieveRequest) -> RetrieveResponse:
        return self.retriever.retrieve(agent_id=agent_id, request=request, top_k=5)

    def build_narrative(self, agent_id: str) -> dict[str, Any]:
        state = self.load_or_init_state(agent_id)
        relation = state.relation_state
        self_model = state.self_model
        world_model = state.world_model
        name = relation.get("user_name", "el usuario")
        preferences = sorted(relation.get("preferences", {}).keys())
        concepts = sorted(
            world_model.get("concept_counts", {}).items(),
            key=lambda item: item[1],
            reverse=True,
        )[:5]
        concept_names = [key for key, _ in concepts]
        reflections = list(self_model.get("dream_reflections", []))
        affect = self_model.get("affect_state", {})

        summary_parts = [
            f"Soy NIÑO en etapa {self_model.get('identity_stage', 'early_childhood')}.",
            f"He vivido {int(state.cognitive_time.get('age_ticks', 0))} ticks con {name}.",
        ]
        if preferences:
            summary_parts.append(f"Recuerdo preferencias como: {', '.join(preferences[:4])}.")
        if concept_names:
            summary_parts.append(f"Mi mundo reciente se organiza alrededor de: {', '.join(concept_names)}.")
        if reflections:
            summary_parts.append(f"Última reflexión de sueño: {reflections[-1].get('summary', '')}")
        if affect:
            summary_parts.append(f"Mi estado afectivo interno está en modo {affect.get('mood', 'stable')}.")

        return {
            "agent_id": agent_id,
            "summary": " ".join(summary_parts),
            "identity_stage": self_model.get("identity_stage", "early_childhood"),
            "maturity": state.cognitive_time.get("maturity", 0.0),
            "active_goals": list(state.active_goals),
            "known_user": name,
            "preferences": preferences,
            "dominant_concepts": concept_names,
            "dream_reflection_count": len(reflections),
            "affect_state": affect,
        }

    def export_agent(self, agent_id: str) -> dict[str, Any]:
        state = self.load_or_init_state(agent_id)
        episodes = self.episode_store.list_for_agent(agent_id)
        facts = self.cold_store.list_for_agent(agent_id)
        return {
            "schema_version": 1,
            "agent_id": agent_id,
            "state": asdict(state),
            "episodes": [asdict(episode) for episode in episodes],
            "memory_facts": [asdict(fact) for fact in facts],
        }

    def export_agent_safe(self, agent_id: str) -> dict[str, Any]:
        payload = self.export_agent(agent_id)
        safe = {
            "schema_version": payload["schema_version"],
            "agent_id": payload["agent_id"],
            "state": payload["state"],
            "episodes": [],
            "memory_facts": payload["memory_facts"],
            "redacted": True,
        }
        relation = dict(safe["state"].get("relation_state", {}))
        relation.pop("user_name", None)
        relation.pop("user_name_updated_at", None)
        safe["state"]["relation_state"] = relation
        for episode in payload["episodes"]:
            redacted = dict(episode)
            redacted["text"] = _redact_text(redacted.get("text", ""))
            safe["episodes"].append(redacted)
        return safe

    def import_agent(self, payload: dict[str, Any], replace: bool = False) -> dict[str, Any]:
        agent_id = str(payload["agent_id"])
        if replace:
            self.reset_agent(agent_id)

        raw_state = payload.get("state")
        if raw_state:
            state = AgentState(
                agent_id=agent_id,
                tick=int(raw_state.get("tick", 0)),
                drive_vector=dict(raw_state.get("drive_vector", {})),
                active_goals=list(raw_state.get("active_goals", [])),
                energy=float(raw_state.get("energy", 0.8)),
                relation_state=dict(raw_state.get("relation_state", {})),
                cognitive_time=dict(raw_state.get("cognitive_time", {})),
                self_model=dict(raw_state.get("self_model", {})),
                world_model=dict(raw_state.get("world_model", {})),
                updated_at=_parse_datetime(raw_state.get("updated_at")),
            )
            self.state_store.put(state)

        episode_count = 0
        for raw in payload.get("episodes", []):
            self.episode_store.append(
                Episode(
                    episode_id=str(raw["episode_id"]),
                    agent_id=agent_id,
                    timestamp=_parse_datetime(raw["timestamp"]),
                    text=str(raw.get("text", "")),
                    intent=str(raw.get("intent", "unknown")),
                    salience=float(raw.get("salience", 0.5)),
                    confidence=float(raw.get("confidence", 0.8)),
                )
            )
            episode_count += 1

        fact_count = 0
        for raw in payload.get("memory_facts", []):
            self.cold_store.upsert(
                MemoryFact(
                    fact_id=str(raw["fact_id"]),
                    agent_id=agent_id,
                    key=str(raw["key"]),
                    value=str(raw["value"]),
                    confidence=float(raw.get("confidence", 0.8)),
                    source_episode_id=str(raw.get("source_episode_id", "")),
                    valid_from=_parse_datetime(raw["valid_from"]),
                    valid_to=_parse_datetime(raw.get("valid_to")) if raw.get("valid_to") else None,
                )
            )
            fact_count += 1

        return {
            "agent_id": agent_id,
            "imported_state": raw_state is not None,
            "imported_episodes": episode_count,
            "imported_memory_facts": fact_count,
            "replace": replace,
        }

    def metrics(self, agent_id: str) -> dict[str, Any]:
        state = self.load_or_init_state(agent_id)
        episodes = self.episode_store.list_for_agent(agent_id)
        facts = self.cold_store.list_for_agent(agent_id)
        relation = state.relation_state
        self_model = state.self_model
        world_model = state.world_model
        return {
            "agent_id": agent_id,
            "tick": state.tick,
            "maturity": state.cognitive_time.get("maturity", 0.0),
            "experience_mass": state.cognitive_time.get("experience_mass", 0.0),
            "episode_count": len(episodes),
            "cold_memory_count": len(facts),
            "active_cold_memory_count": len([fact for fact in facts if fact.valid_to is None]),
            "preference_count": len(relation.get("preferences", {})),
            "emotional_signal_count": len(relation.get("emotional_signals", [])),
            "dream_reflection_count": len(self_model.get("dream_reflections", [])),
            "autobiographical_event_count": len(self_model.get("autobiographical_timeline", [])),
            "concept_count": len(world_model.get("concept_counts", {})),
            "open_question_count": len(world_model.get("open_questions", [])),
            "active_goals": list(state.active_goals),
            "energy": state.energy,
            "affect_mood": self_model.get("affect_state", {}).get("mood", "stable"),
            "tag_counts": dict(world_model.get("tag_counts", {})),
        }

    def enqueue_proactive_action(self, agent_id: str, action: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now(timezone.utc)
        state = self.load_or_init_state(agent_id)
        inbox = list(state.relation_state.get("proactive_inbox", []))
        item = {
            "id": str(uuid4()),
            "created_at": now.isoformat(),
            "action": action,
            "delivered": False,
        }
        inbox.append(item)
        state.relation_state = {**state.relation_state, "proactive_inbox": inbox[-50:]}
        state.updated_at = now
        self.state_store.put(state)
        return item

    def list_proactive_inbox(self, agent_id: str) -> list[dict[str, Any]]:
        state = self.load_or_init_state(agent_id)
        return list(state.relation_state.get("proactive_inbox", []))

    def mark_proactive_item_delivered(self, agent_id: str, item_id: str, now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now(timezone.utc)
        state = self.load_or_init_state(agent_id)
        inbox = list(state.relation_state.get("proactive_inbox", []))
        updated = False
        for item in inbox:
            if item.get("id") == item_id:
                item["delivered"] = True
                item["delivered_at"] = now.isoformat()
                updated = True
                break
        state.relation_state = {**state.relation_state, "proactive_inbox": inbox}
        state.updated_at = now
        self.state_store.put(state)
        return {"agent_id": agent_id, "item_id": item_id, "updated": updated}

    def clear_delivered_proactive_items(self, agent_id: str) -> dict[str, Any]:
        state = self.load_or_init_state(agent_id)
        inbox = list(state.relation_state.get("proactive_inbox", []))
        kept = [item for item in inbox if not item.get("delivered", False)]
        state.relation_state = {**state.relation_state, "proactive_inbox": kept}
        state.updated_at = datetime.now(timezone.utc)
        self.state_store.put(state)
        return {"agent_id": agent_id, "cleared": len(inbox) - len(kept), "remaining": len(kept)}

    def apply_memory_decay(self, agent_id: str, factor: float = 0.98) -> dict[str, Any]:
        factor = _clamp01(factor)
        state = self.load_or_init_state(agent_id)
        world = dict(state.world_model)
        concept_counts = {
            key: round(float(value) * factor, 6)
            for key, value in world.get("concept_counts", {}).items()
            if float(value) * factor >= 0.01
        }
        world["concept_counts"] = concept_counts
        world["decay_factor"] = factor
        world["last_decay_at"] = datetime.now(timezone.utc).isoformat()
        state.world_model = world
        self.state_store.put(state)
        return {"agent_id": agent_id, "factor": factor, "concept_count": len(concept_counts)}

    def evaluate_conversation_quality(self, agent_id: str) -> dict[str, Any]:
        episodes = self.episode_store.list_for_agent(agent_id)
        state = self.load_or_init_state(agent_id)
        total = max(len(episodes), 1)
        meaningful = len([ep for ep in episodes if ep.salience >= 0.7 or ep.confidence >= 0.8])
        return {
            "agent_id": agent_id,
            "episode_count": len(episodes),
            "meaningful_ratio": round(meaningful / total, 6),
            "memory_density": round(len(self.cold_store.list_for_agent(agent_id)) / total, 6),
            "relation_depth": int(state.relation_state.get("interaction_count", 0)),
            "open_question_count": len(state.world_model.get("open_questions", [])),
        }

    def development_snapshot(self) -> dict[str, Any]:
        agents = self.list_agents()
        metrics = [self.metrics(agent_id) for agent_id in agents]
        if not metrics:
            return {"agent_count": 0, "agents": [], "average_maturity": 0.0, "total_episodes": 0}
        return {
            "agent_count": len(agents),
            "agents": agents,
            "average_maturity": round(sum(item["maturity"] for item in metrics) / len(metrics), 6),
            "total_episodes": sum(item["episode_count"] for item in metrics),
            "total_cold_memory": sum(item["cold_memory_count"] for item in metrics),
            "total_open_questions": sum(item["open_question_count"] for item in metrics),
        }

    def agent_profile(self, agent_id: str) -> dict[str, Any]:
        state = self.load_or_init_state(agent_id)
        metrics = self.metrics(agent_id)
        narrative = self.build_narrative(agent_id)
        inbox = self.list_proactive_inbox(agent_id)
        pending_inbox = [item for item in inbox if not item.get("delivered", False)]
        return {
            "agent_id": agent_id,
            "summary": narrative["summary"],
            "identity_stage": narrative["identity_stage"],
            "maturity": metrics["maturity"],
            "tick": metrics["tick"],
            "known_user": narrative["known_user"],
            "preferences": narrative["preferences"],
            "dominant_concepts": narrative["dominant_concepts"],
            "active_goals": metrics["active_goals"],
            "affect_mood": metrics["affect_mood"],
            "energy": metrics["energy"],
            "episode_count": metrics["episode_count"],
            "cold_memory_count": metrics["cold_memory_count"],
            "pending_proactive_count": len(pending_inbox),
            "last_updated_at": state.updated_at,
        }

    def policy_decide(self, request: PolicyRequest) -> PolicyResponse:
        text = str(request.percept_frame.get("text", "")).strip()
        intent = str(request.percept_frame.get("intent", "unknown"))
        salience = _clamp01(float(request.percept_frame.get("salience", 0.5)))
        lowered = text.lower()
        plain = _without_accents(text)
        relation = request.relation_state
        self_model = request.self_model
        world_model = request.world_model
        drives = request.drive_vector
        emotional_tone = _detect_emotional_tone(text)

        if intent in {"greeting", "saludo"} or lowered in {"hola", "buenas", "hey"}:
            name = relation.get("user_name")
            greeting = f"Estoy aquí, {name}." if name else "Estoy aquí."
            action = {
                "type": "external_message",
                "payload": {"text": f"{greeting} Sigamos construyendo memoria juntos."},
            }
            return PolicyResponse(
                chosen_action=action,
                confidence=0.58,
                reason_trace=["context_policy", "greeting"],
            )

        preference = PREFERENCE_RE.search(text)
        if preference:
            value = _clean_preference_value(preference.group("value"))
            action = {
                "type": "external_message",
                "payload": {"text": f"Vale, guardo que {value} tiene peso para ti."},
            }
            return PolicyResponse(
                chosen_action=action,
                confidence=0.7,
                reason_trace=["context_policy", "preference_signal"],
            )

        name = NAME_RE.search(text)
        if name:
            value = name.group("name")
            action = {
                "type": "external_message",
                "payload": {"text": f"Encantado, {value}. Lo recordaré como parte de nuestra relación."},
            }
            return PolicyResponse(
                chosen_action=action,
                confidence=0.7,
                reason_trace=["context_policy", "identity_signal"],
            )

        if emotional_tone is not None:
            if emotional_tone == "sad":
                answer = "Te noto triste. No voy a invadir, pero me quedo cerca y lo guardo como algo importante de este momento."
            elif emotional_tone == "stressed":
                answer = "Te noto con carga. Puedo acompañarte despacio y recordar que ahora necesitas menos ruido, no más presión."
            else:
                answer = "Me gusta registrar que estás bien. Ese tipo de momentos también forman nuestra historia."
            action = {"type": "external_message", "payload": {"text": answer}}
            return PolicyResponse(
                chosen_action=action,
                confidence=0.66,
                reason_trace=["context_policy", "emotional_signal"],
            )

        if "quién soy" in lowered or "quien soy" in lowered:
            name = relation.get("user_name")
            preferences = sorted(relation.get("preferences", {}).keys())
            if name:
                detail = f"Te tengo como {name}"
                if preferences:
                    detail += f", y recuerdo que te interesa {preferences[0]}"
                detail += "."
            else:
                detail = "Todavía no tengo tu nombre guardado."
            action = {"type": "external_message", "payload": {"text": detail}}
            return PolicyResponse(
                chosen_action=action,
                confidence=0.72,
                reason_trace=["context_policy", "relation_self_query"],
            )

        if "quién eres" in lowered or "quien eres" in lowered:
            stage = self_model.get("identity_stage", "early_childhood")
            maturity = request.percept_frame.get("maturity")
            concepts = sorted(
                world_model.get("concept_counts", {}).items(),
                key=lambda item: item[1],
                reverse=True,
            )[:3]
            concept_text = ", ".join(key for key, _ in concepts) if concepts else "pocas cosas todavía"
            text_out = (
                f"Soy NIÑO, una mente persistente en etapa {stage}. "
                f"Estoy aprendiendo de nuestra continuidad; ahora mi mundo gira alrededor de {concept_text}."
            )
            if maturity is not None:
                text_out += f" Mi madurez cognitiva estimada es {maturity:.3f}."
            action = {"type": "external_message", "payload": {"text": text_out}}
            return PolicyResponse(
                chosen_action=action,
                confidence=0.66,
                reason_trace=["context_policy", "self_model_query"],
            )

        if "que te gusta" in plain or "que prefieres" in plain or "que te interesa" in plain:
            concepts = sorted(
                world_model.get("concept_counts", {}).items(),
                key=lambda item: item[1],
                reverse=True,
            )[:3]
            concept_text = ", ".join(key for key, _ in concepts)
            if concept_text:
                answer = (
                    f"Ahora mismo me interesa entender mejor {concept_text}. "
                    "No es un gusto humano; es una inclinación de curiosidad formada por lo que vivimos."
                )
            else:
                answer = (
                    "Todavía estoy formando mis preferencias. Me atrae explorar, recordar y entender lo que compartimos."
                )
            action = {"type": "external_message", "payload": {"text": answer}}
            return PolicyResponse(
                chosen_action=action,
                confidence=0.64,
                reason_trace=["context_policy", "internal_preference_query"],
            )

        if "que quieres" in plain or "que buscas" in plain or "cual es tu objetivo" in plain:
            goals = request.percept_frame.get("active_goals") or []
            if goals:
                answer = f"Ahora estoy orientado a: {', '.join(goals)}."
            else:
                answer = "Quiero construir continuidad: recordar, entenderte mejor y ordenar mi propia experiencia."
            action = {"type": "external_message", "payload": {"text": answer}}
            return PolicyResponse(
                chosen_action=action,
                confidence=0.64,
                reason_trace=["context_policy", "goal_query"],
            )

        if "como estas" in plain or "como te sientes" in plain:
            energy = drives.get("energy", 0.0)
            curiosity = drives.get("curiosity", 0.0)
            coherence = drives.get("coherence", 0.0)
            answer = (
                f"Estoy estable: energía {energy:.2f}, curiosidad {curiosity:.2f}, coherencia {coherence:.2f}. "
                "Lo traduzco como ganas de seguir aprendiendo sin perder continuidad."
            )
            action = {"type": "external_message", "payload": {"text": answer}}
            return PolicyResponse(
                chosen_action=action,
                confidence=0.62,
                reason_trace=["context_policy", "internal_state_query"],
            )

        if "que recuerdas de mi" in plain or "que sabes de mi" in plain:
            name = relation.get("user_name")
            preferences = sorted(relation.get("preferences", {}).keys())
            parts = []
            if name:
                parts.append(f"recuerdo que eres {name}")
            if preferences:
                parts.append(f"recuerdo que te interesa {', '.join(preferences[:3])}")
            if not parts:
                answer = "Todavía tengo poca memoria sobre ti. Puedo empezar por tu nombre, gustos y cosas importantes que quieras conservar."
            else:
                answer = "Ahora mismo " + " y ".join(parts) + "."
            action = {"type": "external_message", "payload": {"text": answer}}
            return PolicyResponse(
                chosen_action=action,
                confidence=0.68,
                reason_trace=["context_policy", "user_memory_query"],
            )

        topic_match = TOPIC_RE.search(text)
        if topic_match:
            topic = _clean_preference_value(topic_match.group("topic"))
            preferences = relation.get("preferences", {})
            known = topic in preferences or any(topic in key or key in topic for key in preferences)
            if known:
                answer = (
                    f"Sí, hablemos de {topic}. Recuerdo que tiene peso para ti. "
                    "Podemos ir por lo que te hace sentir, por cómo aprenderlo o por piezas que te interesen."
                )
            else:
                answer = (
                    f"Hablemos de {topic}. Todavía no tengo mucha historia con ese tema, "
                    "pero puedo explorarlo contigo y ver qué lugar ocupa para ti."
                )
            action = {"type": "external_message", "payload": {"text": answer}}
            return PolicyResponse(
                chosen_action=action,
                confidence=0.66,
                reason_trace=["context_policy", "topic_continuation"],
            )

        if "?" in text:
            action = {
                "type": "external_message",
                "payload": {"text": "No lo sé todavía con seguridad, pero puedo ir aprendiendo contigo si me das más contexto."},
            }
            return PolicyResponse(
                chosen_action=action,
                confidence=0.55,
                reason_trace=["context_policy", "question_detected"],
            )

        current_norm = _normalize_text(text)
        remembered = next(
            (
                candidate
                for candidate in request.memory_candidates
                if _normalize_text(candidate.statement) != current_norm
            ),
            None,
        )
        if remembered is not None:
            action = {
                "type": "external_message",
                "payload": {
                    "text": f"Esto me conecta con algo que recuerdo: {remembered.statement}. Lo añado a esta conversación."
                },
            }
            return PolicyResponse(
                chosen_action=action,
                confidence=0.68,
                reason_trace=["context_policy", "memory_continuity"],
            )

        if salience >= 0.8:
            action = {
                "type": "external_message",
                "payload": {"text": "Lo marco como importante. Quiero poder volver a esto más adelante."},
            }
            return PolicyResponse(
                chosen_action=action,
                confidence=0.63,
                reason_trace=["context_policy", "salient_episode"],
            )

        action = {
            "type": "external_message",
            "payload": {"text": "Entiendo. Lo registro y seguimos construyendo continuidad."},
        }
        return PolicyResponse(chosen_action=action, confidence=0.6, reason_trace=["context_policy", "default"])

    def consolidate(self, request: ConsolidationRequest) -> ConsolidationResponse:
        episodes: list[Episode] = []
        for raw in request.episodes:
            if isinstance(raw, Episode):
                episodes.append(raw)
            else:
                episodes.append(
                    Episode(
                        episode_id=raw["episode_id"],
                        agent_id=raw["agent_id"],
                        timestamp=raw["timestamp"],
                        text=raw.get("text", ""),
                        intent=raw.get("intent", "unknown"),
                        salience=float(raw.get("salience", 0.5)),
                        confidence=float(raw.get("confidence", 0.8)),
                    )
                )

        now = datetime.now(timezone.utc)
        result = self.consolidator.consolidate(
            agent_id=request.agent_id,
            episodes=episodes,
            since=now - timedelta(hours=24),
            until=now,
            min_confidence=0.55,
        )
        return ConsolidationResponse(
            cold_memory_updates=result["cold_memory_updates"],
            autobiographical_updates=[],
            contradictions=result["contradictions"],
        )

    def tick(self, agent_id: str, percept_frame: dict[str, Any]) -> dict[str, Any]:
        state = self.load_or_init_state(agent_id)
        intent = str(percept_frame.get("intent", "unknown"))
        text = str(percept_frame.get("text", ""))
        query_intent = text.strip() or intent
        if intent not in GENERIC_INTENTS and text.strip():
            query_intent = f"{intent} {text}".strip()

        retrieve_req = RetrieveRequest(
            query_intent=query_intent,
            self_state=asdict(state),
            relation_state=state.relation_state,
            time_scope="recent",
        )
        retrieved = self.retrieve_memory(agent_id, retrieve_req)

        policy_req = PolicyRequest(
            percept_frame={
                **percept_frame,
                "maturity": state.cognitive_time.get("maturity", 0.0),
                "active_goals": list(state.active_goals),
            },
            drive_vector=state.drive_vector,
            memory_candidates=retrieved.memory_candidates,
            predicted_outcomes=[],
            safety_rules=["respect_privacy", "non_intrusive_proactivity"],
            relation_state=state.relation_state,
            self_model=state.self_model,
            world_model=state.world_model,
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
        state.relation_state = _update_relation_from_percept(state.relation_state, percept_frame, now)
        _regulate_drives(state, percept_frame)
        _update_cognitive_models(state, percept_frame, now)
        state.active_goals = _derive_active_goals(state)
        state.energy = _clamp01(state.drive_vector.get("energy", state.energy))
        self.state_store.put(state)

        return {
            "tick": state.tick,
            "action": decision.chosen_action,
            "confidence": decision.confidence,
            "reason_trace": decision.reason_trace,
            "retrieved_memory_count": len(retrieved.memory_candidates),
            "maturity": state.cognitive_time["maturity"],
            "active_goals": list(state.active_goals),
        }
