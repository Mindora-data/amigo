from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import sqlite3

from .consolidation import MemoryFact
from .contracts import AgentState
from .learning_journal import LearningJournalEntry
from .memory import Episode
from .memory_graph import GraphEdge, GraphNode
from .proactivity import InMemoryProactiveCandidateStore
from .rss_culture import RssItem, RssSource
from .runtime import NinoRuntime, _safe_global_concept_counts

DEFAULT_COGNITIVE_TIME_JSON = '{"age_ticks": 0.0, "experience_mass": 0.0, "maturity": 0.0}'
DEFAULT_SELF_MODEL_JSON = (
    '{"identity_stage": "early_childhood", "interaction_count": 0, '
    '"known_capabilities": ["remember_episodes", "retrieve_context", "safe_proactivity"], '
    '"known_limits": ["minimal_language_policy", "no_background_daemon_yet"], '
    '"autobiographical_timeline": []}'
)
DEFAULT_WORLD_MODEL_JSON = (
    '{"concept_counts": {}, "intent_counts": {}, "open_questions": [], "causal_observations": []}'
)


def _connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def _dt_to_text(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _dt_from_text(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value is not None else None


class SQLiteStateStore:
    def __init__(self, path: str | Path) -> None:
        self.conn = _connect(path)
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_states (
                agent_id TEXT PRIMARY KEY,
                tick INTEGER NOT NULL,
                drive_vector_json TEXT NOT NULL,
                active_goals_json TEXT NOT NULL,
                energy REAL NOT NULL,
                relation_state_json TEXT NOT NULL,
                cognitive_time_json TEXT NOT NULL,
                self_model_json TEXT NOT NULL,
                world_model_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self._ensure_column("cognitive_time_json", f"TEXT NOT NULL DEFAULT '{DEFAULT_COGNITIVE_TIME_JSON}'")
        self._ensure_column("self_model_json", f"TEXT NOT NULL DEFAULT '{DEFAULT_SELF_MODEL_JSON}'")
        self._ensure_column("world_model_json", f"TEXT NOT NULL DEFAULT '{DEFAULT_WORLD_MODEL_JSON}'")
        self.conn.commit()

    def _ensure_column(self, name: str, definition: str) -> None:
        rows = self.conn.execute("PRAGMA table_info(agent_states)").fetchall()
        existing = {row["name"] for row in rows}
        if name not in existing:
            self.conn.execute(f"ALTER TABLE agent_states ADD COLUMN {name} {definition}")

    def get(self, agent_id: str) -> AgentState | None:
        row = self.conn.execute(
            "SELECT * FROM agent_states WHERE agent_id = ?",
            (agent_id,),
        ).fetchone()
        if row is None:
            return None
        return AgentState(
            agent_id=row["agent_id"],
            tick=int(row["tick"]),
            drive_vector=json.loads(row["drive_vector_json"]),
            active_goals=json.loads(row["active_goals_json"]),
            energy=float(row["energy"]),
            relation_state=json.loads(row["relation_state_json"]),
            cognitive_time=json.loads(row["cognitive_time_json"]),
            self_model=json.loads(row["self_model_json"]),
            world_model=json.loads(row["world_model_json"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def put(self, state: AgentState) -> None:
        self.conn.execute(
            """
            INSERT INTO agent_states (
                agent_id, tick, drive_vector_json, active_goals_json,
                energy, relation_state_json, cognitive_time_json,
                self_model_json, world_model_json, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(agent_id) DO UPDATE SET
                tick = excluded.tick,
                drive_vector_json = excluded.drive_vector_json,
                active_goals_json = excluded.active_goals_json,
                energy = excluded.energy,
                relation_state_json = excluded.relation_state_json,
                cognitive_time_json = excluded.cognitive_time_json,
                self_model_json = excluded.self_model_json,
                world_model_json = excluded.world_model_json,
                updated_at = excluded.updated_at
            """,
            (
                state.agent_id,
                state.tick,
                json.dumps(state.drive_vector, sort_keys=True),
                json.dumps(state.active_goals, sort_keys=True),
                state.energy,
                json.dumps(state.relation_state, sort_keys=True),
                json.dumps(state.cognitive_time, sort_keys=True),
                json.dumps(state.self_model, sort_keys=True),
                json.dumps(state.world_model, sort_keys=True),
                state.updated_at.isoformat(),
            ),
        )
        self.conn.commit()

    def delete(self, agent_id: str) -> None:
        self.conn.execute("DELETE FROM agent_states WHERE agent_id = ?", (agent_id,))
        self.conn.commit()

    def list_agent_ids(self) -> list[str]:
        rows = self.conn.execute("SELECT agent_id FROM agent_states ORDER BY agent_id").fetchall()
        return [row["agent_id"] for row in rows]


class SQLiteEpisodeStore:
    def __init__(self, path: str | Path) -> None:
        self.conn = _connect(path)
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS episodes (
                episode_id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                text TEXT NOT NULL,
                intent TEXT NOT NULL,
                salience REAL NOT NULL,
                confidence REAL NOT NULL
            )
            """
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_episodes_agent_time ON episodes(agent_id, timestamp)"
        )
        self.conn.commit()

    def append(self, episode: Episode) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO episodes (
                episode_id, agent_id, timestamp, text, intent, salience, confidence
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                episode.episode_id,
                episode.agent_id,
                episode.timestamp.isoformat(),
                episode.text,
                episode.intent,
                episode.salience,
                episode.confidence,
            ),
        )
        self.conn.commit()

    def list_for_agent(self, agent_id: str) -> list[Episode]:
        rows = self.conn.execute(
            "SELECT * FROM episodes WHERE agent_id = ? ORDER BY timestamp, episode_id",
            (agent_id,),
        ).fetchall()
        return [
            Episode(
                episode_id=row["episode_id"],
                agent_id=row["agent_id"],
                timestamp=datetime.fromisoformat(row["timestamp"]),
                text=row["text"],
                intent=row["intent"],
                salience=float(row["salience"]),
                confidence=float(row["confidence"]),
            )
            for row in rows
        ]

    def delete_for_agent(self, agent_id: str) -> int:
        cursor = self.conn.execute("DELETE FROM episodes WHERE agent_id = ?", (agent_id,))
        self.conn.commit()
        return cursor.rowcount

    def delete_episode(self, agent_id: str, episode_id: str) -> bool:
        cursor = self.conn.execute(
            "DELETE FROM episodes WHERE agent_id = ? AND episode_id = ?",
            (agent_id, episode_id),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def list_agent_ids(self) -> list[str]:
        rows = self.conn.execute("SELECT DISTINCT agent_id FROM episodes ORDER BY agent_id").fetchall()
        return [row["agent_id"] for row in rows]


class SQLiteColdStore:
    def __init__(self, path: str | Path) -> None:
        self.conn = _connect(path)
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_facts (
                fact_id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                confidence REAL NOT NULL,
                source_episode_id TEXT NOT NULL,
                valid_from TEXT NOT NULL,
                valid_to TEXT
            )
            """
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_memory_facts_agent_key ON memory_facts(agent_id, key)"
        )
        self.conn.commit()

    def upsert(self, fact: MemoryFact) -> None:
        self.conn.execute(
            """
            INSERT INTO memory_facts (
                fact_id, agent_id, key, value, confidence,
                source_episode_id, valid_from, valid_to
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(fact_id) DO UPDATE SET
                agent_id = excluded.agent_id,
                key = excluded.key,
                value = excluded.value,
                confidence = excluded.confidence,
                source_episode_id = excluded.source_episode_id,
                valid_from = excluded.valid_from,
                valid_to = excluded.valid_to
            """,
            (
                fact.fact_id,
                fact.agent_id,
                fact.key,
                fact.value,
                fact.confidence,
                fact.source_episode_id,
                fact.valid_from.isoformat(),
                _dt_to_text(fact.valid_to),
            ),
        )
        self.conn.commit()

    def list_for_agent(self, agent_id: str) -> list[MemoryFact]:
        rows = self.conn.execute(
            "SELECT * FROM memory_facts WHERE agent_id = ? ORDER BY valid_from, fact_id",
            (agent_id,),
        ).fetchall()
        return [
            MemoryFact(
                fact_id=row["fact_id"],
                agent_id=row["agent_id"],
                key=row["key"],
                value=row["value"],
                confidence=float(row["confidence"]),
                source_episode_id=row["source_episode_id"],
                valid_from=datetime.fromisoformat(row["valid_from"]),
                valid_to=_dt_from_text(row["valid_to"]),
            )
            for row in rows
        ]

    def delete_for_agent(self, agent_id: str) -> int:
        cursor = self.conn.execute("DELETE FROM memory_facts WHERE agent_id = ?", (agent_id,))
        self.conn.commit()
        return cursor.rowcount

    def delete_fact(self, agent_id: str, fact_id: str) -> bool:
        cursor = self.conn.execute(
            "DELETE FROM memory_facts WHERE agent_id = ? AND fact_id = ?",
            (agent_id, fact_id),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def list_agent_ids(self) -> list[str]:
        rows = self.conn.execute("SELECT DISTINCT agent_id FROM memory_facts ORDER BY agent_id").fetchall()
        return [row["agent_id"] for row in rows]


class SQLiteGraphStore:
    def __init__(self, path: str | Path) -> None:
        self.conn = _connect(path)
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_graph_nodes (
                node_id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                label TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                mention_count INTEGER NOT NULL,
                UNIQUE(agent_id, entity_type, label)
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_graph_edges (
                edge_id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                source_node_id TEXT NOT NULL,
                relation TEXT NOT NULL,
                target_node_id TEXT NOT NULL,
                source_episode_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                confidence REAL NOT NULL,
                UNIQUE(agent_id, source_node_id, relation, target_node_id, source_episode_id)
            )
            """
        )
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_graph_nodes_agent ON memory_graph_nodes(agent_id)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_graph_edges_agent ON memory_graph_edges(agent_id)")
        self.conn.commit()

    def upsert_node(self, node: GraphNode) -> GraphNode:
        existing = self.conn.execute(
            "SELECT * FROM memory_graph_nodes WHERE agent_id = ? AND entity_type = ? AND label = ?",
            (node.agent_id, node.entity_type, node.label),
        ).fetchone()
        if existing is not None:
            updated = GraphNode(
                node_id=str(existing["node_id"]),
                agent_id=str(existing["agent_id"]),
                label=str(existing["label"]),
                entity_type=str(existing["entity_type"]),
                first_seen_at=min(datetime.fromisoformat(existing["first_seen_at"]), node.first_seen_at),
                last_seen_at=max(datetime.fromisoformat(existing["last_seen_at"]), node.last_seen_at),
                mention_count=int(existing["mention_count"]) + node.mention_count,
            )
            node = updated
        self.conn.execute(
            """
            INSERT INTO memory_graph_nodes (
                node_id, agent_id, label, entity_type, first_seen_at, last_seen_at, mention_count
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(agent_id, entity_type, label) DO UPDATE SET
                first_seen_at = excluded.first_seen_at,
                last_seen_at = excluded.last_seen_at,
                mention_count = excluded.mention_count
            """,
            (
                node.node_id,
                node.agent_id,
                node.label,
                node.entity_type,
                node.first_seen_at.isoformat(),
                node.last_seen_at.isoformat(),
                node.mention_count,
            ),
        )
        self.conn.commit()
        return node

    def upsert_edge(self, edge: GraphEdge) -> None:
        self.conn.execute(
            """
            INSERT OR IGNORE INTO memory_graph_edges (
                edge_id, agent_id, source_node_id, relation, target_node_id,
                source_episode_id, timestamp, confidence
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                edge.edge_id,
                edge.agent_id,
                edge.source_node_id,
                edge.relation,
                edge.target_node_id,
                edge.source_episode_id,
                edge.timestamp.isoformat(),
                edge.confidence,
            ),
        )
        self.conn.commit()

    def nodes_for_agent(self, agent_id: str) -> list[GraphNode]:
        rows = self.conn.execute(
            "SELECT * FROM memory_graph_nodes WHERE agent_id = ? ORDER BY first_seen_at, node_id",
            (agent_id,),
        ).fetchall()
        return [
            GraphNode(
                node_id=row["node_id"],
                agent_id=row["agent_id"],
                label=row["label"],
                entity_type=row["entity_type"],
                first_seen_at=datetime.fromisoformat(row["first_seen_at"]),
                last_seen_at=datetime.fromisoformat(row["last_seen_at"]),
                mention_count=int(row["mention_count"]),
            )
            for row in rows
        ]

    def edges_for_agent(self, agent_id: str) -> list[GraphEdge]:
        rows = self.conn.execute(
            "SELECT * FROM memory_graph_edges WHERE agent_id = ? ORDER BY timestamp, edge_id",
            (agent_id,),
        ).fetchall()
        return [
            GraphEdge(
                edge_id=row["edge_id"],
                agent_id=row["agent_id"],
                source_node_id=row["source_node_id"],
                relation=row["relation"],
                target_node_id=row["target_node_id"],
                source_episode_id=row["source_episode_id"],
                timestamp=datetime.fromisoformat(row["timestamp"]),
                confidence=float(row["confidence"]),
            )
            for row in rows
        ]

    def delete_for_agent(self, agent_id: str) -> int:
        edge_cursor = self.conn.execute("DELETE FROM memory_graph_edges WHERE agent_id = ?", (agent_id,))
        node_cursor = self.conn.execute("DELETE FROM memory_graph_nodes WHERE agent_id = ?", (agent_id,))
        self.conn.commit()
        return edge_cursor.rowcount + node_cursor.rowcount


class SQLiteLearningJournalStore:
    def __init__(self, path: str | Path) -> None:
        self.conn = _connect(path)
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS learning_journal_entries (
                entry_id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                title TEXT NOT NULL,
                lesson TEXT NOT NULL,
                source TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                source_episode_id TEXT,
                tags_json TEXT NOT NULL
            )
            """
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_learning_journal_agent_status ON learning_journal_entries(agent_id, status, updated_at)"
        )
        self.conn.commit()

    def upsert(self, entry: LearningJournalEntry) -> None:
        self.conn.execute(
            """
            INSERT INTO learning_journal_entries (
                entry_id, agent_id, title, lesson, source, status,
                created_at, updated_at, source_episode_id, tags_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(entry_id) DO UPDATE SET
                title = excluded.title,
                lesson = excluded.lesson,
                source = excluded.source,
                status = excluded.status,
                updated_at = excluded.updated_at,
                source_episode_id = excluded.source_episode_id,
                tags_json = excluded.tags_json
            """,
            (
                entry.entry_id,
                entry.agent_id,
                entry.title,
                entry.lesson,
                entry.source,
                entry.status,
                entry.created_at.isoformat(),
                entry.updated_at.isoformat(),
                entry.source_episode_id,
                json.dumps(entry.tags or [], sort_keys=True),
            ),
        )
        self.conn.commit()

    def _row_to_entry(self, row: sqlite3.Row) -> LearningJournalEntry:
        return LearningJournalEntry(
            entry_id=row["entry_id"],
            agent_id=row["agent_id"],
            title=row["title"],
            lesson=row["lesson"],
            source=row["source"],
            status=row["status"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            source_episode_id=row["source_episode_id"],
            tags=json.loads(row["tags_json"]),
        )

    def list_for_agent(self, agent_id: str, status: str = "all") -> list[LearningJournalEntry]:
        if status == "all":
            rows = self.conn.execute(
                "SELECT * FROM learning_journal_entries WHERE agent_id = ? ORDER BY updated_at DESC, entry_id",
                (agent_id,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """
                SELECT * FROM learning_journal_entries
                WHERE agent_id = ? AND status = ?
                ORDER BY updated_at DESC, entry_id
                """,
                (agent_id, status),
            ).fetchall()
        return [self._row_to_entry(row) for row in rows]

    def get(self, agent_id: str, entry_id: str) -> LearningJournalEntry | None:
        row = self.conn.execute(
            "SELECT * FROM learning_journal_entries WHERE agent_id = ? AND entry_id = ?",
            (agent_id, entry_id),
        ).fetchone()
        return self._row_to_entry(row) if row is not None else None

    def delete(self, agent_id: str, entry_id: str) -> bool:
        cursor = self.conn.execute(
            "DELETE FROM learning_journal_entries WHERE agent_id = ? AND entry_id = ?",
            (agent_id, entry_id),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def delete_for_agent(self, agent_id: str) -> int:
        cursor = self.conn.execute("DELETE FROM learning_journal_entries WHERE agent_id = ?", (agent_id,))
        self.conn.commit()
        return cursor.rowcount


class SQLiteGlobalModelStore:
    def __init__(self, path: str | Path) -> None:
        self.conn = _connect(path)
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS global_models (
                model_id TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self.conn.commit()
        self._ensure_pattern_table()

    def get(self) -> dict[str, object]:
        row = self.conn.execute("SELECT payload_json FROM global_models WHERE model_id = ?", ("anonymous",)).fetchone()
        if row is None:
            return {
                "schema_version": 1,
                "conversation_count": 0,
                "intent_counts": {},
                "tag_counts": {},
                "concept_counts": {},
                "pattern_outcomes": self._pattern_outcomes(),
                "updated_at": None,
            }
        payload = json.loads(row["payload_json"])
        payload["concept_counts"] = _safe_global_concept_counts(payload.get("concept_counts", {}))
        payload["pattern_outcomes"] = self._pattern_outcomes()
        return payload

    def put(self, model: dict[str, object]) -> None:
        payload = {
            "schema_version": int(model.get("schema_version", 1)),
            "conversation_count": int(model.get("conversation_count", 0)),
            "intent_counts": dict(model.get("intent_counts", {})),
            "tag_counts": dict(model.get("tag_counts", {})),
            "concept_counts": _safe_global_concept_counts(model.get("concept_counts", {})),
            "updated_at": model.get("updated_at"),
        }
        updated_at = str(payload.get("updated_at") or datetime.now().isoformat())
        self.conn.execute(
            """
            INSERT INTO global_models (model_id, payload_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(model_id) DO UPDATE SET
                payload_json = excluded.payload_json,
                updated_at = excluded.updated_at
            """,
            ("anonymous", json.dumps(payload, sort_keys=True), updated_at),
        )
        self.conn.commit()

    def _ensure_pattern_table(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS global_pattern_outcome (
                id INTEGER PRIMARY KEY,
                gesture TEXT NOT NULL,
                context TEXT NOT NULL,
                outcome TEXT NOT NULL,
                count INTEGER DEFAULT 1,
                updated_at TEXT NOT NULL,
                UNIQUE (gesture, context, outcome)
            )
            """
        )
        self.conn.commit()

    def _pattern_outcomes(self) -> dict[str, dict[str, object]]:
        self._ensure_pattern_table()
        rows = self.conn.execute(
            "SELECT gesture, context, outcome, count, updated_at FROM global_pattern_outcome ORDER BY gesture, context, outcome"
        ).fetchall()
        return {
            f"{row['gesture']}:{row['context']}:{row['outcome']}": {
                "gesture": row["gesture"],
                "context": row["context"],
                "outcome": row["outcome"],
                "count": int(row["count"]),
                "updated_at": row["updated_at"],
            }
            for row in rows
        }

    def bump_global_pattern(
        self,
        gesture: str,
        context: str,
        outcome: str,
        now: datetime | None = None,
    ) -> None:
        self._ensure_pattern_table()
        updated_at = (now or datetime.now()).isoformat()
        self.conn.execute(
            """
            INSERT INTO global_pattern_outcome (gesture, context, outcome, count, updated_at)
            VALUES (?, ?, ?, 1, ?)
            ON CONFLICT(gesture, context, outcome) DO UPDATE SET
                count = count + 1,
                updated_at = excluded.updated_at
            """,
            (gesture, context, outcome, updated_at),
        )
        self.conn.commit()

    def global_pattern_stats(self, gesture: str, context: str) -> dict[str, int]:
        self._ensure_pattern_table()
        rows = self.conn.execute(
            """
            SELECT outcome, count FROM global_pattern_outcome
            WHERE gesture = ? AND context = ?
            """,
            (gesture, context),
        ).fetchall()
        stats = {"positive": 0, "ignored": 0, "stop": 0, "total": 0}
        for row in rows:
            outcome = str(row["outcome"])
            count = int(row["count"])
            if outcome in stats:
                stats[outcome] += count
                stats["total"] += count
        return stats


class SQLiteProactiveCandidateStore(InMemoryProactiveCandidateStore):
    def __init__(self, path: str | Path) -> None:
        self.conn = _connect(path)
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS proactive_candidate (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                source_ref TEXT,
                topic TEXT NOT NULL,
                earliest_at TEXT NOT NULL,
                expires_at TEXT,
                status TEXT NOT NULL,
                attempts INTEGER DEFAULT 0,
                weight INTEGER DEFAULT 2,
                created_at TEXT NOT NULL,
                delivered_at TEXT,
                user_reacted INTEGER
            )
            """
        )
        self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_proactive_pending
            ON proactive_candidate (user_id, status, earliest_at)
            """
        )
        self.conn.commit()

    def add(self, user_id: str, candidate: dict[str, object]) -> dict[str, object]:
        item = {
            "id": str(candidate.get("id") or ""),
            "user_id": user_id,
            "kind": str(candidate.get("kind", "followup")),
            "source_ref": candidate.get("source_ref"),
            "topic": str(candidate.get("topic", ""))[:120],
            "earliest_at": str(candidate.get("earliest_at")),
            "expires_at": candidate.get("expires_at"),
            "status": str(candidate.get("status", "pending")),
            "attempts": int(candidate.get("attempts", 0)),
            "weight": max(1, min(3, int(candidate.get("weight", 2)))),
            "created_at": str(candidate.get("created_at") or datetime.now().isoformat()),
            "delivered_at": candidate.get("delivered_at"),
            "user_reacted": candidate.get("user_reacted"),
        }
        if not item["id"]:
            from uuid import uuid4
            item["id"] = str(uuid4())
        self.conn.execute(
            """
            INSERT INTO proactive_candidate (
                id, user_id, kind, source_ref, topic, earliest_at, expires_at,
                status, attempts, weight, created_at, delivered_at, user_reacted
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item["id"], item["user_id"], item["kind"], item["source_ref"], item["topic"],
                item["earliest_at"], item["expires_at"], item["status"], item["attempts"],
                item["weight"], item["created_at"], item["delivered_at"], item["user_reacted"],
            ),
        )
        self.conn.commit()
        return dict(item)

    def _row_to_item(self, row: sqlite3.Row) -> dict[str, object]:
        return {
            "id": row["id"],
            "user_id": row["user_id"],
            "kind": row["kind"],
            "source_ref": row["source_ref"],
            "topic": row["topic"],
            "earliest_at": row["earliest_at"],
            "expires_at": row["expires_at"],
            "status": row["status"],
            "attempts": int(row["attempts"]),
            "weight": int(row["weight"]),
            "created_at": row["created_at"],
            "delivered_at": row["delivered_at"],
            "user_reacted": row["user_reacted"],
        }

    def pending(self, user_id: str, now: datetime) -> list[dict[str, object]]:
        rows = self.conn.execute(
            """
            SELECT * FROM proactive_candidate
            WHERE user_id = ? AND status = 'pending' AND earliest_at <= ?
              AND (expires_at IS NULL OR expires_at >= ?)
            ORDER BY weight DESC, earliest_at ASC, id ASC
            """,
            (user_id, now.isoformat(), now.isoformat()),
        ).fetchall()
        return [self._row_to_item(row) for row in rows]

    def expire_stale(self, user_id: str, now: datetime) -> int:
        cursor = self.conn.execute(
            """
            UPDATE proactive_candidate
            SET status = 'expired'
            WHERE user_id = ? AND status = 'pending' AND expires_at IS NOT NULL AND expires_at < ?
            """,
            (user_id, now.isoformat()),
        )
        self.conn.commit()
        return cursor.rowcount

    def mark_delivered(self, candidate_id: str, now: datetime) -> dict[str, object] | None:
        self.conn.execute(
            """
            UPDATE proactive_candidate
            SET status = 'delivered', delivered_at = ?, attempts = attempts + 1, user_reacted = 0
            WHERE id = ?
            """,
            (now.isoformat(), candidate_id),
        )
        self.conn.commit()
        row = self.conn.execute("SELECT * FROM proactive_candidate WHERE id = ?", (candidate_id,)).fetchone()
        return self._row_to_item(row) if row else None

    def recent_delivered(self, user_id: str, limit: int = 2) -> list[dict[str, object]]:
        rows = self.conn.execute(
            """
            SELECT * FROM proactive_candidate
            WHERE user_id = ? AND status = 'delivered' AND delivered_at IS NOT NULL
            ORDER BY delivered_at DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
        return [self._row_to_item(row) for row in rows]

    def count_delivered_since(self, user_id: str, since: datetime) -> int:
        row = self.conn.execute(
            """
            SELECT COUNT(*) AS count FROM proactive_candidate
            WHERE user_id = ? AND status = 'delivered' AND delivered_at >= ?
            """,
            (user_id, since.isoformat()),
        ).fetchone()
        return int(row["count"]) if row else 0

    def last_delivered_at(self, user_id: str) -> datetime | None:
        row = self.conn.execute(
            """
            SELECT delivered_at FROM proactive_candidate
            WHERE user_id = ? AND status = 'delivered' AND delivered_at IS NOT NULL
            ORDER BY delivered_at DESC LIMIT 1
            """,
            (user_id,),
        ).fetchone()
        return datetime.fromisoformat(row["delivered_at"]) if row else None

    def mark_latest_delivered_reacted(self, user_id: str, now: datetime) -> dict[str, object] | None:
        row = self.conn.execute(
            """
            SELECT * FROM proactive_candidate
            WHERE user_id = ? AND status = 'delivered' AND user_reacted = 0
            ORDER BY delivered_at DESC LIMIT 1
            """,
            (user_id,),
        ).fetchone()
        if row is None:
            return None
        self.conn.execute("UPDATE proactive_candidate SET user_reacted = 1 WHERE id = ?", (row["id"],))
        self.conn.commit()
        updated = self.conn.execute("SELECT * FROM proactive_candidate WHERE id = ?", (row["id"],)).fetchone()
        return self._row_to_item(updated) if updated else None

    def has_recent_checkin_candidate(self, user_id: str, since: datetime) -> bool:
        row = self.conn.execute(
            """
            SELECT 1 FROM proactive_candidate
            WHERE user_id = ? AND kind = 'checkin' AND created_at >= ?
            LIMIT 1
            """,
            (user_id, since.isoformat()),
        ).fetchone()
        return row is not None

    def delete_for_agent(self, user_id: str) -> int:
        cursor = self.conn.execute("DELETE FROM proactive_candidate WHERE user_id = ?", (user_id,))
        self.conn.commit()
        return cursor.rowcount


class SQLiteRssCultureStore:
    def __init__(self, path: str | Path) -> None:
        self.conn = _connect(path)
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS rss_source (
                source_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                url TEXT NOT NULL,
                theme TEXT NOT NULL,
                active INTEGER NOT NULL,
                trust REAL NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self._ensure_source_column("last_import_at", "TEXT")
        self._ensure_source_column("last_import_ok", "INTEGER")
        self._ensure_source_column("last_import_error", "TEXT")
        self._ensure_source_column("last_added_count", "INTEGER NOT NULL DEFAULT 0")
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS rss_item (
                item_id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL,
                title TEXT NOT NULL,
                link TEXT NOT NULL,
                published_at TEXT,
                summary TEXT NOT NULL,
                theme TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                UNIQUE(source_id, link),
                FOREIGN KEY(source_id) REFERENCES rss_source(source_id) ON DELETE CASCADE
            )
            """
        )
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_rss_item_theme_time ON rss_item(theme, fetched_at)")
        self.conn.commit()

    def _ensure_source_column(self, name: str, definition: str) -> None:
        rows = self.conn.execute("PRAGMA table_info(rss_source)").fetchall()
        existing = {row["name"] for row in rows}
        if name not in existing:
            self.conn.execute(f"ALTER TABLE rss_source ADD COLUMN {name} {definition}")

    def upsert_source(self, source: RssSource) -> None:
        self.conn.execute(
            """
            INSERT INTO rss_source (
                source_id, name, url, theme, active, trust, created_at, updated_at,
                last_import_at, last_import_ok, last_import_error, last_added_count
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id) DO UPDATE SET
                name = excluded.name,
                url = excluded.url,
                theme = excluded.theme,
                active = excluded.active,
                trust = excluded.trust,
                updated_at = excluded.updated_at,
                last_import_at = excluded.last_import_at,
                last_import_ok = excluded.last_import_ok,
                last_import_error = excluded.last_import_error,
                last_added_count = excluded.last_added_count
            """,
            (
                source.source_id,
                source.name,
                source.url,
                source.theme,
                1 if source.active else 0,
                source.trust,
                source.created_at.isoformat(),
                source.updated_at.isoformat(),
                source.last_import_at.isoformat() if source.last_import_at else None,
                None if source.last_import_ok is None else 1 if source.last_import_ok else 0,
                source.last_import_error,
                int(source.last_added_count),
            ),
        )
        self.conn.commit()

    def _source_from_row(self, row: sqlite3.Row) -> RssSource:
        return RssSource(
            source_id=str(row["source_id"]),
            name=str(row["name"]),
            url=str(row["url"]),
            theme=str(row["theme"]),
            active=bool(row["active"]),
            trust=float(row["trust"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            last_import_at=datetime.fromisoformat(row["last_import_at"]) if row["last_import_at"] else None,
            last_import_ok=None if row["last_import_ok"] is None else bool(row["last_import_ok"]),
            last_import_error=row["last_import_error"],
            last_added_count=int(row["last_added_count"] or 0),
        )

    def list_sources(self, active_only: bool = False) -> list[RssSource]:
        sql = "SELECT * FROM rss_source"
        params: tuple[object, ...] = ()
        if active_only:
            sql += " WHERE active = 1"
        sql += " ORDER BY updated_at DESC, source_id"
        return [self._source_from_row(row) for row in self.conn.execute(sql, params).fetchall()]

    def get_source(self, source_id: str) -> RssSource | None:
        row = self.conn.execute("SELECT * FROM rss_source WHERE source_id = ?", (source_id,)).fetchone()
        return self._source_from_row(row) if row else None

    def upsert_item(self, item: RssItem) -> bool:
        try:
            self.conn.execute(
                """
                INSERT INTO rss_item (item_id, source_id, title, link, published_at, summary, theme, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.item_id,
                    item.source_id,
                    item.title,
                    item.link,
                    item.published_at.isoformat() if item.published_at else None,
                    item.summary,
                    item.theme,
                    item.fetched_at.isoformat(),
                ),
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            self.conn.rollback()
            return False

    def _item_from_row(self, row: sqlite3.Row) -> RssItem:
        return RssItem(
            item_id=str(row["item_id"]),
            source_id=str(row["source_id"]),
            title=str(row["title"]),
            link=str(row["link"]),
            published_at=datetime.fromisoformat(row["published_at"]) if row["published_at"] else None,
            summary=str(row["summary"]),
            theme=str(row["theme"]),
            fetched_at=datetime.fromisoformat(row["fetched_at"]),
        )

    def list_items(self, theme: str = "all", limit: int = 20) -> list[RssItem]:
        params: list[object] = []
        sql = "SELECT * FROM rss_item"
        if theme and theme != "all":
            sql += " WHERE theme = ?"
            params.append(theme)
        sql += " ORDER BY COALESCE(published_at, fetched_at) DESC, item_id LIMIT ?"
        params.append(int(limit))
        return [self._item_from_row(row) for row in self.conn.execute(sql, tuple(params)).fetchall()]

    def delete_source(self, source_id: str) -> int:
        cursor = self.conn.execute("DELETE FROM rss_source WHERE source_id = ?", (source_id,))
        self.conn.commit()
        return cursor.rowcount


def create_persistent_runtime(path: str | Path) -> NinoRuntime:
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return NinoRuntime(
        state_store=SQLiteStateStore(db_path),
        episode_store=SQLiteEpisodeStore(db_path),
        cold_store=SQLiteColdStore(db_path),
        global_model_store=SQLiteGlobalModelStore(db_path),
        proactive_candidate_store=SQLiteProactiveCandidateStore(db_path),
        graph_store=SQLiteGraphStore(db_path),
        learning_journal_store=SQLiteLearningJournalStore(db_path),
        rss_culture_store=SQLiteRssCultureStore(db_path),
    )
