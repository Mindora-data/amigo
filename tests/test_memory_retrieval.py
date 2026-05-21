from __future__ import annotations

from datetime import datetime, timedelta, timezone

from nino.contracts import ConsolidationRequest, RetrieveRequest
from nino.memory import Episode, InMemoryEpisodeStore, MemoryRetriever
from nino.runtime import InMemoryStateStore, NinoRuntime

def test_retrieval_ranks_relevant_episode_first() -> None:
    store = InMemoryEpisodeStore()
    now = datetime.now(timezone.utc)

    store.append(Episode("e1", "a1", now - timedelta(hours=2), "hablamos de guitarra clasica", "music", 0.9, 0.9))
    store.append(Episode("e2", "a1", now - timedelta(hours=1), "plan de compras supermercado", "shopping", 0.5, 0.8))

    retriever = MemoryRetriever(store)
    req = RetrieveRequest(query_intent="music guitarra", self_state={}, relation_state={}, time_scope="recent")
    out = retriever.retrieve(agent_id="a1", request=req, top_k=2)

    assert out.memory_candidates
    assert out.memory_candidates[0].source_episode_id == "e1"

def test_retrieval_ignores_recent_irrelevant_episode_without_semantic_overlap() -> None:
    store = InMemoryEpisodeStore()
    now = datetime.now(timezone.utc)

    store.append(Episode("e1", "a1", now - timedelta(minutes=5), "quien eres?", "question", 0.9, 0.9))

    retriever = MemoryRetriever(store)
    req = RetrieveRequest(query_intent="chat me gusta piano", self_state={}, relation_state={}, time_scope="recent")
    out = retriever.retrieve(agent_id="a1", request=req, top_k=2)

    assert out.memory_candidates == []

def test_retrieval_ignores_generic_intent_overlap() -> None:
    store = InMemoryEpisodeStore()
    now = datetime.now(timezone.utc)
    store.append(Episode("e1", "a1", now - timedelta(minutes=5), "hola", "chat", 0.9, 0.9))

    retriever = MemoryRetriever(store)
    req = RetrieveRequest(query_intent="chat me gusta piano", self_state={}, relation_state={}, time_scope="recent")
    out = retriever.retrieve(agent_id="a1", request=req, top_k=2)

    assert out.memory_candidates == []

def test_retrieval_normalizes_accents_and_basic_synonyms() -> None:
    store = InMemoryEpisodeStore()
    now = datetime.now(timezone.utc)
    store.append(Episode("e1", "a1", now - timedelta(minutes=5), "me calma la música", "music", 0.9, 0.9))

    retriever = MemoryRetriever(store)
    req = RetrieveRequest(query_intent="musica calma", self_state={}, relation_state={}, time_scope="recent")
    out = retriever.retrieve(agent_id="a1", request=req, top_k=2)

    assert out.memory_candidates
    assert out.memory_candidates[0].source_episode_id == "e1"

def test_runtime_tick_persists_state_and_writes_episode() -> None:
    runtime = NinoRuntime(InMemoryStateStore())

    out1 = runtime.tick("agent-x", {"intent": "greeting", "text": "hola"})
    out2 = runtime.tick("agent-x", {"intent": "music", "text": "me gusta el piano", "salience": 0.9})
    out3 = runtime.tick("agent-x", {"intent": "music", "text": "tocaba guitarra de niño"})

    assert out1["tick"] == 1
    assert out2["tick"] == 2
    assert out3["tick"] == 3
    assert out3["retrieved_memory_count"] >= 1

def test_hybrid_retrieval_uses_cold_and_hot() -> None:
    runtime = NinoRuntime(InMemoryStateStore())
    runtime.tick("agent-h", {"intent": "chat", "text": "prefiero mañanas", "confidence": 0.95})
    runtime.tick("agent-h", {"intent": "chat", "text": "hoy entrené", "confidence": 0.9})

    episodes = runtime.episode_store.list_for_agent("agent-h")
    runtime.consolidate(ConsolidationRequest(agent_id="agent-h", episodes=episodes))

    req = RetrieveRequest(query_intent="preference mañanas", self_state={}, relation_state={}, time_scope="long")
    out = runtime.retrieve_memory("agent-h", req)
    assert any(c.fact_id.startswith("cold::") for c in out.memory_candidates)
