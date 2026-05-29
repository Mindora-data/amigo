from __future__ import annotations

from datetime import datetime, timedelta, timezone

from nino.contracts import ConsolidationRequest, RetrieveRequest
from nino.internal_loop import InternalLoop
from nino.memory import Episode
from nino.runtime import InMemoryStateStore, NinoRuntime


def _episode(episode_id: str, agent_id: str, text: str, offset: int) -> Episode:
    now = datetime.now(timezone.utc)
    return Episode(
        episode_id=episode_id,
        agent_id=agent_id,
        timestamp=now - timedelta(minutes=offset),
        text=text,
        intent="chat",
        salience=0.8,
        confidence=0.95,
    )


def test_consolidation_builds_graph_edges_for_shared_entities() -> None:
    runtime = NinoRuntime(InMemoryStateStore(), llm_client=None)
    episodes = [
        _episode("e1", "agent-g", "El cliente estaba nervioso por el estrés", 20),
        _episode("e2", "agent-g", "La reunión con el cliente fue el jueves", 10),
    ]
    for episode in episodes:
        runtime.episode_store.append(episode)

    runtime.consolidate(ConsolidationRequest(agent_id="agent-g", episodes=episodes))

    nodes = runtime.graph_store.nodes_for_agent("agent-g")
    edges = runtime.graph_store.edges_for_agent("agent-g")
    labels = {node.label for node in nodes}
    assert {"cliente", "estres", "reunion"}.issubset(labels)
    assert edges
    assert all(edge.agent_id == "agent-g" for edge in edges)


def test_graph_retrieval_brings_connected_episode_without_semantic_overlap() -> None:
    runtime = NinoRuntime(InMemoryStateStore(), llm_client=None)
    episodes = [
        _episode("e1", "agent-g", "El cliente estaba nervioso por el estrés", 20),
        _episode("e2", "agent-g", "La reunión con el cliente fue el jueves", 10),
    ]
    for episode in episodes:
        runtime.episode_store.append(episode)
    runtime.consolidate(ConsolidationRequest(agent_id="agent-g", episodes=episodes))

    out = runtime.retrieve_memory(
        "agent-g",
        RetrieveRequest(query_intent="estrés", self_state={}, relation_state={}, time_scope="long"),
    )

    graph_candidates = [candidate for candidate in out.memory_candidates if candidate.retrieval_source == "graph"]
    assert any(candidate.source_episode_id == "e2" for candidate in graph_candidates)
    assert any(candidate.fact_id.startswith("graph::") for candidate in graph_candidates)


def test_graph_is_isolated_by_user_agent() -> None:
    runtime = NinoRuntime(InMemoryStateStore(), llm_client=None)
    alice = [_episode("a1", "user::alice::agent::nino", "La reunión con el cliente fue intensa", 10)]
    bob = [_episode("b1", "user::bob::agent::nino", "Hablamos de piano", 10)]
    for episode in [*alice, *bob]:
        runtime.episode_store.append(episode)
    runtime.consolidate(ConsolidationRequest(agent_id="user::alice::agent::nino", episodes=alice))
    runtime.consolidate(ConsolidationRequest(agent_id="user::bob::agent::nino", episodes=bob))

    out = runtime.retrieve_memory(
        "user::bob::agent::nino",
        RetrieveRequest(query_intent="cliente", self_state={}, relation_state={}, time_scope="long"),
    )

    assert all("cliente" not in candidate.statement.lower() for candidate in out.memory_candidates)
    assert runtime.graph_store.nodes_for_agent("user::alice::agent::nino")
    assert not any(node.agent_id == "user::alice::agent::nino" for node in runtime.graph_store.nodes_for_agent("user::bob::agent::nino"))


def test_graph_does_not_invent_edges_for_unknown_entity() -> None:
    runtime = NinoRuntime(InMemoryStateStore(), llm_client=None)
    episodes = [_episode("e1", "agent-g", "Hablamos de piano", 10)]
    runtime.episode_store.append(episodes[0])
    runtime.consolidate(ConsolidationRequest(agent_id="agent-g", episodes=episodes))

    out = runtime.retrieve_memory(
        "agent-g",
        RetrieveRequest(query_intent="cliente", self_state={}, relation_state={}, time_scope="long"),
    )

    assert [candidate for candidate in out.memory_candidates if candidate.retrieval_source == "graph"] == []


def test_dream_cycle_rebuilds_graph_links_from_existing_episodes() -> None:
    runtime = NinoRuntime(InMemoryStateStore(), llm_client=None)
    episodes = [
        _episode("e1", "agent-dream", "El cliente habló del estrés", 20),
        _episode("e2", "agent-dream", "La reunión con el cliente fue larga", 10),
    ]
    for episode in episodes:
        runtime.episode_store.append(episode)

    out = InternalLoop(runtime).dream_cycle("agent-dream")

    assert "memory_consolidation" in out.reason_trace
    graph_out = runtime.retrieve_memory(
        "agent-dream",
        RetrieveRequest(query_intent="estrés", self_state={}, relation_state={}, time_scope="long"),
    )
    assert any(candidate.retrieval_source == "graph" and candidate.source_episode_id == "e2" for candidate in graph_out.memory_candidates)
