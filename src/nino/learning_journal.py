from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from typing import Protocol
from uuid import uuid4


ACTIVE_STATUSES = {"active", "draft", "archived"}
SOURCES = {"auto", "manual"}


@dataclass(slots=True)
class LearningJournalEntry:
    entry_id: str
    agent_id: str
    title: str
    lesson: str
    source: str
    status: str
    created_at: datetime
    updated_at: datetime
    source_episode_id: str | None = None
    tags: list[str] | None = None


class LearningJournalStoreProtocol(Protocol):
    def upsert(self, entry: LearningJournalEntry) -> None: ...
    def list_for_agent(self, agent_id: str, status: str = "all") -> list[LearningJournalEntry]: ...
    def get(self, agent_id: str, entry_id: str) -> LearningJournalEntry | None: ...
    def delete_for_agent(self, agent_id: str) -> int: ...


class InMemoryLearningJournalStore:
    def __init__(self) -> None:
        self._entries: dict[str, list[LearningJournalEntry]] = {}

    def upsert(self, entry: LearningJournalEntry) -> None:
        entries = self._entries.setdefault(entry.agent_id, [])
        for idx, existing in enumerate(entries):
            if existing.entry_id == entry.entry_id:
                entries[idx] = entry
                return
        entries.append(entry)

    def list_for_agent(self, agent_id: str, status: str = "all") -> list[LearningJournalEntry]:
        entries = sorted(self._entries.get(agent_id, []), key=lambda item: item.updated_at, reverse=True)
        if status == "all":
            return entries
        return [entry for entry in entries if entry.status == status]

    def get(self, agent_id: str, entry_id: str) -> LearningJournalEntry | None:
        for entry in self._entries.get(agent_id, []):
            if entry.entry_id == entry_id:
                return entry
        return None

    def delete_for_agent(self, agent_id: str) -> int:
        count = len(self._entries.get(agent_id, []))
        self._entries.pop(agent_id, None)
        return count


EXPLICIT_LESSON_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\baprende que\s+(.+)", re.IGNORECASE | re.DOTALL), "lesson"),
    (re.compile(r"\blecci[oó]n\s*:\s*(.+)", re.IGNORECASE | re.DOTALL), "lesson"),
    (re.compile(r"\bpara la bit[aá]cora\s*:\s*(.+)", re.IGNORECASE | re.DOTALL), "lesson"),
    (re.compile(r"\bbit[aá]cora\s*:\s*(.+)", re.IGNORECASE | re.DOTALL), "lesson"),
    (re.compile(r"\brecuerda como aprendizaje que\s+(.+)", re.IGNORECASE | re.DOTALL), "lesson"),
    (re.compile(r"\bno vuelvas a\s+(.+)", re.IGNORECASE | re.DOTALL), "behavior"),
    (re.compile(r"\bcuando\s+(.+?),\s*(?:mejor\s+)?(?:haz|responde|act[uú]a)\s+(.+)", re.IGNORECASE | re.DOTALL), "behavior"),
)


def _clean_lesson(value: str) -> str:
    value = re.sub(r"\s+", " ", value).strip(" .\n\t")
    return value[:500]


def _title_from_lesson(lesson: str, tag: str) -> str:
    words = lesson.split()
    title = " ".join(words[:8]).strip()
    if not title:
        title = "aprendizaje"
    if tag == "behavior" and not title.lower().startswith("comportamiento"):
        title = f"Comportamiento: {title}"
    return title[:90]


def _new_entry(
    *,
    agent_id: str,
    lesson: str,
    tag: str,
    source: str,
    now: datetime,
    source_episode_id: str | None = None,
    title: str | None = None,
    status: str = "active",
) -> LearningJournalEntry:
    if source not in SOURCES:
        raise ValueError("invalid_learning_journal_source")
    if status not in ACTIVE_STATUSES:
        raise ValueError("invalid_learning_journal_status")
    clean = _clean_lesson(lesson)
    if not clean:
        raise ValueError("empty_learning_journal_lesson")
    return LearningJournalEntry(
        entry_id=f"journal::{uuid4()}",
        agent_id=agent_id,
        title=(title or _title_from_lesson(clean, tag))[:90],
        lesson=clean,
        source=source,
        status=status,
        created_at=now,
        updated_at=now,
        source_episode_id=source_episode_id,
        tags=[tag],
    )


def extract_learning_journal_entries(
    text: str,
    *,
    agent_id: str,
    source_episode_id: str,
    now: datetime,
) -> list[LearningJournalEntry]:
    out: list[LearningJournalEntry] = []
    for pattern, tag in EXPLICIT_LESSON_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        if tag == "behavior" and len(match.groups()) == 2:
            lesson = f"Cuando {match.group(1).strip()}, {match.group(2).strip()}"
        elif tag == "behavior" and pattern.pattern.startswith("\\bno vuelvas"):
            lesson = f"No volver a {match.group(1).strip()}"
        else:
            lesson = match.group(1).strip()
        try:
            out.append(
                _new_entry(
                    agent_id=agent_id,
                    lesson=lesson,
                    tag=tag,
                    source="auto",
                    now=now,
                    source_episode_id=source_episode_id,
                )
            )
        except ValueError:
            continue
    return out[:3]


def make_manual_learning_entry(
    *,
    agent_id: str,
    lesson: str,
    now: datetime,
    title: str | None = None,
    tags: list[str] | None = None,
    status: str = "active",
) -> LearningJournalEntry:
    entry = _new_entry(
        agent_id=agent_id,
        lesson=lesson,
        tag=(tags or ["manual"])[0] if tags else "manual",
        source="manual",
        now=now,
        title=title,
        status=status,
    )
    entry.tags = [str(tag)[:40] for tag in (tags or ["manual"]) if str(tag).strip()][:6]
    return entry
