from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

ALLOWED_GESTURES = {"followup", "checkin", "recordatorio", "pregunta_perfil"}
ALLOWED_CONTEXTS = {"evento_con_carga", "dia_neutro", "perfil", "alarma_explicita"}
ALLOWED_OUTCOMES = {"positive", "ignored", "stop"}
MIN_PATTERN_SAMPLE = 50


def validate_global_pattern(gesture: str, context: str, outcome: str) -> tuple[str, str, str]:
    if gesture not in ALLOWED_GESTURES:
        raise ValueError("invalid_gesture")
    if context not in ALLOWED_CONTEXTS:
        raise ValueError("invalid_context")
    if outcome not in ALLOWED_OUTCOMES:
        raise ValueError("invalid_outcome")
    return gesture, context, outcome


def distill_to_global(gesture: str, context: str, outcome: str, store: Any) -> None:
    gesture, context, outcome = validate_global_pattern(gesture, context, outcome)
    store.bump_global_pattern(gesture, context, outcome, now=datetime.now(timezone.utc))


def starting_prior(gesture: str, context: str, store: Any, *, min_sample: int = MIN_PATTERN_SAMPLE) -> float:
    if gesture not in ALLOWED_GESTURES or context not in ALLOWED_CONTEXTS:
        return 0.5
    stats = store.global_pattern_stats(gesture, context)
    total = int(stats.get("total", 0)) if stats else 0
    if total < min_sample:
        return 0.5
    return max(0.0, min(1.0, float(stats.get("positive", 0)) / total))


def pattern_context_for_candidate(candidate: dict[str, Any] | None) -> str:
    if not candidate:
        return "dia_neutro"
    kind = str(candidate.get("kind", ""))
    if kind == "checkin":
        return "dia_neutro"
    if kind == "followup":
        return "evento_con_carga"
    return "dia_neutro"
