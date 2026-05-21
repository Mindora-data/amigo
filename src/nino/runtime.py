from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import re
from typing import Any
from uuid import uuid4

from .consolidation import Consolidator, InMemoryColdStore
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

def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))

def _normalize_text(value: str) -> str:
    return " ".join(re.findall(r"[\wáéíóúñ]+", value.lower()))

def _clean_preference_value(value: str) -> str:
    words = _normalize_text(value).split()
    while words and words[0] in {"el", "la", "los", "las", "un", "una"}:
        words.pop(0)
    return " ".join(words)

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

    return relation

PREFERENCE_RE = re.compile(
    r"\b(prefiero|me gusta)\s+(?P<value>[\wáéíóúñ]+(?:\s+[\wáéíóúñ]+){0,4})",
    re.IGNORECASE,
)
NAME_RE = re.compile(r"\bsoy\s+(?P<name>[\wáéíóúñ]+)", re.IGNORECASE)
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
    state.self_model = self_model

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
        result = self.proactivity.evaluate(agent_id, state.relation_state, now=now)
        if result.should_send and record_send:
            sent_at = now or datetime.now(timezone.utc)
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

    def retrieve_memory(self, agent_id: str, request: RetrieveRequest) -> RetrieveResponse:
        return self.retriever.retrieve(agent_id=agent_id, request=request, top_k=5)

    def policy_decide(self, request: PolicyRequest) -> PolicyResponse:
        text = str(request.percept_frame.get("text", "")).strip()
        intent = str(request.percept_frame.get("intent", "unknown"))
        salience = _clamp01(float(request.percept_frame.get("salience", 0.5)))
        lowered = text.lower()
        relation = request.relation_state
        self_model = request.self_model
        world_model = request.world_model

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
        state.energy = _clamp01(state.energy - 0.01)
        state.relation_state = _update_relation_from_percept(state.relation_state, percept_frame, now)
        _update_cognitive_models(state, percept_frame, now)
        self.state_store.put(state)

        return {
            "tick": state.tick,
            "action": decision.chosen_action,
            "confidence": decision.confidence,
            "reason_trace": decision.reason_trace,
            "retrieved_memory_count": len(retrieved.memory_candidates),
            "maturity": state.cognitive_time["maturity"],
        }
