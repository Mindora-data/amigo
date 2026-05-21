from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import sqlite3

from .consolidation import MemoryFact
from .contracts import AgentState
from .memory import Episode
from .runtime import NinoRuntime


def _connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
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
                updated_at TEXT NOT NULL
            )
            """
        )
        self.conn.commit()

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
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def put(self, state: AgentState) -> None:
        self.conn.execute(
            """
            INSERT INTO agent_states (
                agent_id, tick, drive_vector_json, active_goals_json,
                energy, relation_state_json, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(agent_id) DO UPDATE SET
                tick = excluded.tick,
                drive_vector_json = excluded.drive_vector_json,
                active_goals_json = excluded.active_goals_json,
                energy = excluded.energy,
                relation_state_json = excluded.relation_state_json,
                updated_at = excluded.updated_at
            """,
            (
                state.agent_id,
                state.tick,
                json.dumps(state.drive_vector, sort_keys=True),
                json.dumps(state.active_goals, sort_keys=True),
                state.energy,
                json.dumps(state.relation_state, sort_keys=True),
                state.updated_at.isoformat(),
            ),
        )
        self.conn.commit()

    def delete(self, agent_id: str) -> None:
        self.conn.execute("DELETE FROM agent_states WHERE agent_id = ?", (agent_id,))
        self.conn.commit()


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


def create_persistent_runtime(path: str | Path) -> NinoRuntime:
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return NinoRuntime(
        state_store=SQLiteStateStore(db_path),
        episode_store=SQLiteEpisodeStore(db_path),
        cold_store=SQLiteColdStore(db_path),
    )
