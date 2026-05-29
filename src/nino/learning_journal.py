from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from typing import Protocol
from uuid import uuid4


ACTIVE_STATUSES = {"active", "draft", "archived"}
SOURCES = {"auto", "manual", "detected"}
THEMES = {"vida", "cultura", "amistad", "trabajo", "comportamiento", "salud", "otros"}


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

DETECTED_LESSON_PATTERNS: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (
        re.compile(r"\b(?:lo has interpretado mal|has interpretado mal|eso fue un error|te has equivocado)\b(.{0,220})", re.IGNORECASE | re.DOTALL),
        "correction",
        "Cuando el usuario marque una mala interpretacion, corregir el rumbo sin insistir ni justificar demasiado.",
    ),
    (
        re.compile(r"\bno (?:me )?gusta que\s+(.{4,220})", re.IGNORECASE | re.DOTALL),
        "preference",
        "Al usuario no le gusta que {detail}. Evitar ese patron salvo que lo pida claramente.",
    ),
    (
        re.compile(r"\bprefiero que\s+(.{4,220})", re.IGNORECASE | re.DOTALL),
        "preference",
        "El usuario prefiere que {detail}. Tenerlo como pauta de trato.",
    ),
    (
        re.compile(r"\bmejor que\s+(.{4,220})", re.IGNORECASE | re.DOTALL),
        "behavior",
        "Mejor que {detail}. Aplicarlo como pauta conversacional si encaja.",
    ),
    (
        re.compile(r"\bno hace falta que\s+(.{4,220})", re.IGNORECASE | re.DOTALL),
        "behavior",
        "No hace falta que {detail}. Reducir esa conducta.",
    ),
    (
        re.compile(r"\bno quiero que\s+(.{4,220})", re.IGNORECASE | re.DOTALL),
        "boundary",
        "El usuario no quiere que {detail}. Respetar este limite conversacional.",
    ),
    (
        re.compile(r"\b(?:deberias|deberías|tendrias que|tendrías que)\s+(.{4,220})", re.IGNORECASE | re.DOTALL),
        "behavior",
        "El usuario sugiere que deberia {detail}. Revisarlo como posible pauta.",
    ),
)
PRIVATE_PREFERENCE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"\bme encanta\s+(.{3,120})", re.IGNORECASE | re.DOTALL),
        "Al usuario le encanta {detail}. Tratarlo como gusto personal, no como regla general.",
    ),
    (
        re.compile(r"\bmi favorit[oa]\s+(?:es|son)\s+(.{3,120})", re.IGNORECASE | re.DOTALL),
        "El usuario marca como favorito: {detail}. Usarlo como gusto personal privado.",
    ),
    (
        re.compile(r"\bme gusta mucho\s+(.{3,120})", re.IGNORECASE | re.DOTALL),
        "Al usuario le gusta mucho {detail}. Usarlo como preferencia personal privada.",
    ),
)

FRIEND_BEHAVIOR_TERMS = (
    "amigo",
    "contestas",
    "contestes",
    "respondes",
    "respondas",
    "preguntas",
    "preguntes",
    "insistes",
    "insistas",
    "interpretas",
    "interpretes",
    "recuerdas",
    "recuerdes",
    "avisas",
    "avises",
    "hables",
    "hablas",
    "termines",
    "alargues",
)
PERSONAL_PREFERENCE_OBJECTS = (
    "cafe",
    "café",
    "pizza",
    "futbol",
    "fútbol",
    "musica",
    "música",
    "cine",
    "serie",
    "libro",
    "comic",
    "cómic",
    "calor",
    "frio",
    "frío",
    "llueva",
    "lluvia",
)


def _clean_lesson(value: str) -> str:
    value = re.sub(r"\s+", " ", value).strip(" .\n\t")
    return value[:500]


def _norm_lesson(value: str) -> str:
    value = value.lower()
    value = value.replace("á", "a").replace("é", "e").replace("í", "i").replace("ó", "o").replace("ú", "u").replace("ñ", "n")
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", value)).strip()


def _looks_like_friend_behavior(detail: str) -> bool:
    text = _norm_lesson(detail)
    if not text:
        return False
    if any(term in text.split() for term in ("tu", "amigo")):
        return True
    return any(term in text for term in FRIEND_BEHAVIOR_TERMS)


def _looks_like_personal_preference(detail: str) -> bool:
    text = _norm_lesson(detail)
    if not text:
        return False
    return any(term in text for term in PERSONAL_PREFERENCE_OBJECTS) and not _looks_like_friend_behavior(detail)


def _title_from_lesson(lesson: str, tag: str) -> str:
    words = lesson.split()
    title = " ".join(words[:8]).strip()
    if not title:
        title = "aprendizaje"
    if tag == "behavior" and not title.lower().startswith("comportamiento"):
        title = f"Comportamiento: {title}"
    return title[:90]


def classify_learning_theme(title: str, lesson: str, tags: list[str] | None = None) -> str:
    text = _norm_lesson(f"{title} {lesson} {' '.join(tags or [])}")
    if any(word in text for word in ("comic", "comics", "superman", "autor", "libro", "cine", "serie", "musica", "cultura", "brubaker", "byrne")):
        return "cultura"
    if any(word in text for word in ("amigo", "amistad", "grupo", "confianza", "cercano", "relacion")):
        return "amistad"
    if any(word in text for word in ("trabajo", "proyecto", "cliente", "reunion", "sprint", "tarea")):
        return "trabajo"
    if any(word in text for word in ("salud", "dentista", "medico", "cansado", "triste", "alegre", "ansiedad", "estres")):
        return "salud"
    if any(word in text for word in ("responde", "pregunt", "insist", "corrige", "interpreta", "tono", "comportamiento", "limite", "moldear")):
        return "comportamiento"
    if any(word in text for word in ("vida", "familia", "casa", "madrid", "rutina", "hobby", "gusta")):
        return "vida"
    return "otros"


def _tags_with_theme(tag: str, title: str, lesson: str, tags: list[str] | None = None) -> list[str]:
    clean = [str(item)[:40] for item in ([tag, *(tags or [])]) if str(item).strip()]
    theme = next((item for item in clean if item in THEMES), None) or classify_learning_theme(title, lesson, clean)
    out: list[str] = []
    for item in [*clean, theme]:
        if item not in out:
            out.append(item)
    return out[:8]


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
    resolved_title = (title or _title_from_lesson(clean, tag))[:90]
    return LearningJournalEntry(
        entry_id=f"journal::{uuid4()}",
        agent_id=agent_id,
        title=resolved_title,
        lesson=clean,
        source=source,
        status=status,
        created_at=now,
        updated_at=now,
        source_episode_id=source_episode_id,
        tags=_tags_with_theme(tag, resolved_title, clean),
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


def extract_detected_learning_journal_entries(
    text: str,
    *,
    agent_id: str,
    source_episode_id: str,
    now: datetime,
    existing_lessons: list[str] | None = None,
    is_group_context: bool = False,
) -> list[LearningJournalEntry]:
    if len(text.strip()) < 12:
        return []
    existing = {_norm_lesson(item) for item in (existing_lessons or [])}
    out: list[LearningJournalEntry] = []
    if not is_group_context:
        for pattern, template in PRIVATE_PREFERENCE_PATTERNS:
            match = pattern.search(text)
            if not match:
                continue
            detail = _clean_lesson(match.group(1))
            if _looks_like_friend_behavior(detail):
                continue
            lesson = template.format(detail=detail)
            if _norm_lesson(lesson) in existing:
                continue
            existing.add(_norm_lesson(lesson))
            try:
                out.append(
                    _new_entry(
                        agent_id=agent_id,
                        lesson=lesson,
                        tag="personal_preference",
                        source="detected",
                        status="draft",
                        now=now,
                        source_episode_id=source_episode_id,
                        title=f"Detectado: {_title_from_lesson(detail, 'preference')}",
                    )
                )
            except ValueError:
                continue
    for pattern, tag, template in DETECTED_LESSON_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        detail = _clean_lesson(match.group(1) if match.groups() else "")
        if "{detail}" in template:
            if not detail:
                continue
            if tag in {"preference", "boundary", "behavior"}:
                if _looks_like_personal_preference(detail):
                    continue
                if is_group_context and not _looks_like_friend_behavior(detail):
                    continue
            lesson = template.format(detail=detail)
        else:
            lesson = template
        if is_group_context and tag == "correction" and "amigo" not in _norm_lesson(text):
            continue
        if _norm_lesson(lesson) in existing:
            continue
        existing.add(_norm_lesson(lesson))
        try:
            out.append(
                _new_entry(
                    agent_id=agent_id,
                    lesson=lesson,
                    tag=tag,
                    source="detected",
                    status="draft",
                    now=now,
                    source_episode_id=source_episode_id,
                    title=f"Detectado: {_title_from_lesson(lesson, tag)}",
                )
            )
        except ValueError:
            continue
    return out[:2]


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
    entry.tags = _tags_with_theme("manual", entry.title, entry.lesson, tags or ["manual"])
    return entry
