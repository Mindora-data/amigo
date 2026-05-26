from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import sqlite3

from .consolidation import MemoryFact
from .contracts import AgentState
from .memory import Episode
from .proactivity import InMemoryProactiveCandidateStore
from .runtime import NinoRuntime

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
        payload["pattern_outcomes"] = self._pattern_outcomes()
        return payload

    def put(self, model: dict[str, object]) -> None:
        payload = {
            "schema_version": int(model.get("schema_version", 1)),
            "conversation_count": int(model.get("conversation_count", 0)),
            "intent_counts": dict(model.get("intent_counts", {})),
            "tag_counts": dict(model.get("tag_counts", {})),
            "concept_counts": dict(model.get("concept_counts", {})),
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


def create_persistent_runtime(path: str | Path) -> NinoRuntime:
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return NinoRuntime(
        state_store=SQLiteStateStore(db_path),
        episode_store=SQLiteEpisodeStore(db_path),
        cold_store=SQLiteColdStore(db_path),
        global_model_store=SQLiteGlobalModelStore(db_path),
        proactive_candidate_store=SQLiteProactiveCandidateStore(db_path),
    )
