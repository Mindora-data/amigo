from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import re
from typing import Protocol

from .contracts import MemoryCandidate
from .memory import Episode, InMemoryEpisodeStore


@dataclass(slots=True)
class GraphNode:
    node_id: str
    agent_id: str
    label: str
    entity_type: str
    first_seen_at: datetime
    last_seen_at: datetime
    mention_count: int = 1


@dataclass(slots=True)
class GraphEdge:
    edge_id: str
    agent_id: str
    source_node_id: str
    relation: str
    target_node_id: str
    source_episode_id: str
    timestamp: datetime
    confidence: float


class MemoryGraphStoreProtocol(Protocol):
    def upsert_node(self, node: GraphNode) -> GraphNode: ...
    def upsert_edge(self, edge: GraphEdge) -> None: ...
    def nodes_for_agent(self, agent_id: str) -> list[GraphNode]: ...
    def edges_for_agent(self, agent_id: str) -> list[GraphEdge]: ...
    def delete_for_agent(self, agent_id: str) -> int: ...


class InMemoryGraphStore:
    def __init__(self) -> None:
        self._nodes: dict[str, list[GraphNode]] = {}
        self._edges: dict[str, list[GraphEdge]] = {}

    def upsert_node(self, node: GraphNode) -> GraphNode:
        nodes = self._nodes.setdefault(node.agent_id, [])
        for idx, existing in enumerate(nodes):
            if _node_key(existing) == _node_key(node):
                updated = GraphNode(
                    node_id=existing.node_id,
                    agent_id=existing.agent_id,
                    label=existing.label,
                    entity_type=existing.entity_type,
                    first_seen_at=min(existing.first_seen_at, node.first_seen_at),
                    last_seen_at=max(existing.last_seen_at, node.last_seen_at),
                    mention_count=existing.mention_count + node.mention_count,
                )
                nodes[idx] = updated
                return updated
        nodes.append(node)
        return node

    def upsert_edge(self, edge: GraphEdge) -> None:
        edges = self._edges.setdefault(edge.agent_id, [])
        if any(_edge_key(existing) == _edge_key(edge) for existing in edges):
            return
        edges.append(edge)

    def nodes_for_agent(self, agent_id: str) -> list[GraphNode]:
        return list(self._nodes.get(agent_id, []))

    def edges_for_agent(self, agent_id: str) -> list[GraphEdge]:
        return list(self._edges.get(agent_id, []))

    def delete_for_agent(self, agent_id: str) -> int:
        count = len(self._nodes.pop(agent_id, [])) + len(self._edges.pop(agent_id, []))
        return count


ENTITY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("persona", re.compile(r"\b(?:cliente|clienta|jefe|jefa|ana|pablo|maria|maría)\b", re.IGNORECASE)),
    ("evento", re.compile(r"\b(?:reunion|reunión|cita|dentista|examen|viaje|llamada|entrevista)\b", re.IGNORECASE)),
    ("concepto", re.compile(r"\b(?:estres|estrés|ansiedad|preocupacion|preocupación|diseño|memoria|proactividad)\b", re.IGNORECASE)),
    ("proyecto", re.compile(r"\b(?:proyecto\s+(?P<project>[\wáéíóúñ-]+)|mi proyecto se llama\s+(?P<named_project>[\wáéíóúñ-]+))\b", re.IGNORECASE)),
    ("lugar", re.compile(r"\b(?:madrid|barcelona|valencia|castellon|castellón)\b", re.IGNORECASE)),
)


def _norm(value: str) -> str:
    value = value.lower()
    value = value.replace("á", "a").replace("é", "e").replace("í", "i").replace("ó", "o").replace("ú", "u").replace("ñ", "n")
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s-]", " ", value)).strip()


def _node_id(agent_id: str, entity_type: str, label: str) -> str:
    digest = hashlib.sha1(f"{agent_id}:{entity_type}:{_norm(label)}".encode("utf-8")).hexdigest()[:16]
    return f"node::{digest}"


def _edge_id(agent_id: str, source: str, relation: str, target: str, episode_id: str) -> str:
    digest = hashlib.sha1(f"{agent_id}:{source}:{relation}:{target}:{episode_id}".encode("utf-8")).hexdigest()[:16]
    return f"edge::{digest}"


def _node_key(node: GraphNode) -> tuple[str, str, str]:
    return node.agent_id, node.entity_type, _norm(node.label)


def _edge_key(edge: GraphEdge) -> tuple[str, str, str, str, str]:
    return edge.agent_id, edge.source_node_id, edge.relation, edge.target_node_id, edge.source_episode_id


def extract_entities(text: str, *, include_user: bool = True) -> list[tuple[str, str]]:
    entities: list[tuple[str, str]] = [("persona", "usuario")] if include_user else []
    seen = set(entities)
    for entity_type, pattern in ENTITY_PATTERNS:
        for match in pattern.finditer(text):
            label = match.groupdict().get("project") or match.groupdict().get("named_project") or match.group(0)
            clean = _norm(label)
            if clean and (entity_type, clean) not in seen:
                entities.append((entity_type, clean))
                seen.add((entity_type, clean))
    return entities


def ingest_episode_graph(store: MemoryGraphStoreProtocol, agent_id: str, episode: Episode) -> int:
    entities = extract_entities(episode.text)
    nodes: list[GraphNode] = []
    for entity_type, label in entities:
        node = store.upsert_node(
            GraphNode(
                node_id=_node_id(agent_id, entity_type, label),
                agent_id=agent_id,
                label=label,
                entity_type=entity_type,
                first_seen_at=episode.timestamp,
                last_seen_at=episode.timestamp,
                mention_count=1,
            )
        )
        nodes.append(node)
    edge_count = 0
    for source in nodes:
        for target in nodes:
            if source.node_id == target.node_id:
                continue
            relation = "mentioned_with"
            store.upsert_edge(
                GraphEdge(
                    edge_id=_edge_id(agent_id, source.node_id, relation, target.node_id, episode.episode_id),
                    agent_id=agent_id,
                    source_node_id=source.node_id,
                    relation=relation,
                    target_node_id=target.node_id,
                    source_episode_id=episode.episode_id,
                    timestamp=episode.timestamp,
                    confidence=episode.confidence,
                )
            )
            edge_count += 1
    return edge_count


def ingest_episodes_graph(store: MemoryGraphStoreProtocol, agent_id: str, episodes: list[Episode]) -> int:
    return sum(ingest_episode_graph(store, agent_id, episode) for episode in episodes if episode.agent_id == agent_id)


def graph_candidates(
    store: MemoryGraphStoreProtocol,
    episode_store: InMemoryEpisodeStore,
    agent_id: str,
    query: str,
    *,
    limit: int = 3,
) -> list[MemoryCandidate]:
    query_entities = extract_entities(query, include_user=False)
    if not query_entities:
        return []
    nodes = store.nodes_for_agent(agent_id)
    edges = store.edges_for_agent(agent_id)
    activated = {
        node.node_id
        for node in nodes
        for entity_type, label in query_entities
        if node.entity_type == entity_type and _norm(node.label) == _norm(label)
    }
    if not activated:
        return []
    expanded = set(activated)
    for edge in edges:
        if edge.source_node_id in activated:
            expanded.add(edge.target_node_id)
        if edge.target_node_id in activated:
            expanded.add(edge.source_node_id)
    by_episode = {episode.episode_id: episode for episode in episode_store.list_for_agent(agent_id)}
    out: list[MemoryCandidate] = []
    seen_episodes: set[str] = set()
    for edge in sorted(edges, key=lambda item: item.timestamp, reverse=True):
        if edge.source_node_id not in expanded and edge.target_node_id not in expanded:
            continue
        if edge.source_episode_id in seen_episodes:
            continue
        episode = by_episode.get(edge.source_episode_id)
        if episode is None:
            continue
        seen_episodes.add(edge.source_episode_id)
        out.append(
            MemoryCandidate(
                fact_id=f"graph::{edge.edge_id}",
                statement=episode.text,
                score=round(0.42 + (0.2 * min(edge.confidence, 1.0)), 6),
                source_episode_id=episode.episode_id,
                confidence=edge.confidence,
                retrieval_source="graph",
            )
        )
        if len(out) >= limit:
            break
    return out
