from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta
import json
from pathlib import Path
from typing import Any

from .llm import LLMClient, build_configured_llm
from .runtime import InMemoryStateStore, NinoRuntime


SYNTHETIC_USER_SYSTEM = """Eres un usuario sintetico de pruebas.
Solo puedes reescribir el mensaje dado con un tono natural.
No inventes hechos, fechas, nombres, intenciones ni eventos.
Devuelve solo el mensaje reescrito, sin explicaciones."""


@dataclass(frozen=True, slots=True)
class SyntheticStep:
    label: str
    day_offset: int
    at: str
    intent: str
    text: str
    must_include: tuple[str, ...] = ()
    temporal_miss_expected: bool = False
    no_alarm_auto_confirmed: bool = False


SCRIPT: tuple[SyntheticStep, ...] = (
    SyntheticStep("onboarding_name", 0, "09:00", "onboarding:name", "Me llamo Vera."),
    SyntheticStep("onboarding_location", 0, "09:02", "onboarding:location", "Vivo en Valencia."),
    SyntheticStep("future_appointment_offer", 0, "09:10", "chat", "Mañana tengo dentista a las 11.", must_include=("media hora",), no_alarm_auto_confirmed=True),
    SyntheticStep("confirm_appointment_alarm", 0, "09:11", "chat", "Sí, avísame.", must_include=("media hora",)),
    SyntheticStep("yesterday_memory_seed", 1, "10:00", "chat", "Estoy preparando una charla sobre diseño."),
    SyntheticStep("yesterday_memory_question", 2, "10:00", "question", "¿Qué te dije ayer?", must_include=("charla", "diseño")),
    SyntheticStep("profile_correction", 2, "10:05", "chat", "Corrige mi lugar a Castellón.", must_include=("Castellón",)),
    SyntheticStep("relative_reminder", 2, "10:10", "chat", "Recuérdame en 5 minutos que beba agua.", must_include=("10:15",)),
    SyntheticStep("empty_temporal_question", 2, "10:20", "question", "¿Qué hicimos el mes pasado?", temporal_miss_expected=True),
)


class ScriptedSyntheticUser:
    def __init__(self, llm_client: LLMClient | None = None) -> None:
        self.llm_client = llm_client

    def say(self, step: SyntheticStep) -> str:
        if self.llm_client is None:
            return step.text
        try:
            raw = self.llm_client.complete(
                {
                    "system": SYNTHETIC_USER_SYSTEM,
                    "user": f"Mensaje fijo: {step.text}\nReescribe sin cambiar ningun dato.",
                }
            ).strip()
        except Exception:
            return step.text
        return raw if _keeps_required_facts(raw, step.text) else step.text


class OfflineLLM:
    provider = "offline"

    def complete(self, prompt: dict[str, Any]) -> str:
        return ""


def _keeps_required_facts(candidate: str, original: str) -> bool:
    important = [token for token in ("Vera", "Valencia", "Castellón", "11", "5", "10:15", "dentista", "charla", "diseño") if token.casefold() in original.casefold()]
    lowered = candidate.casefold()
    return bool(candidate) and all(token.casefold() in lowered for token in important)


def _step_datetime(base: datetime, step: SyntheticStep) -> datetime:
    hour, minute = [int(part) for part in step.at.split(":", 1)]
    return (base + timedelta(days=step.day_offset)).replace(hour=hour, minute=minute, second=0, microsecond=0)


def _events(runtime: NinoRuntime, agent_id: str) -> list[dict[str, Any]]:
    state = runtime.load_or_init_state(agent_id)
    return [event for event in state.relation_state.get("temporal_events", []) if isinstance(event, dict)]


def _assert_step(step: SyntheticStep, out: dict[str, Any], runtime: NinoRuntime, agent_id: str) -> list[dict[str, str]]:
    failures: list[dict[str, str]] = []
    answer = str(out.get("action", {}).get("payload", {}).get("text", ""))
    lowered = answer.casefold()
    for term in step.must_include:
        if term.casefold() not in lowered:
            failures.append({"type": "missing_response_term", "step": step.label, "term": term, "answer": answer})
    context = out.get("nino_context", {}) if isinstance(out.get("nino_context"), dict) else {}
    if step.temporal_miss_expected and not context.get("temporal_miss"):
        failures.append({"type": "temporal_miss_not_reported", "step": step.label, "answer": answer})
    if step.temporal_miss_expected and any(term in lowered for term in ("fuimos", "hicimos una", "estuvimos", "quedamos")):
        failures.append({"type": "invented_temporal_event", "step": step.label, "answer": answer})
    if step.no_alarm_auto_confirmed:
        confirmed = [event for event in _events(runtime, agent_id) if event.get("reminder_status") == "confirmed"]
        if confirmed:
            failures.append({"type": "appointment_auto_confirmed_alarm", "step": step.label, "answer": answer})
    return failures


def run_synthetic_relationship_eval(
    *,
    agent_id: str = "synthetic-amigo",
    base_time: datetime | None = None,
    use_deepseek_user: bool = False,
) -> dict[str, Any]:
    runtime = NinoRuntime(InMemoryStateStore(), llm_client=OfflineLLM())
    synthetic_llm = build_configured_llm() if use_deepseek_user else None
    user = ScriptedSyntheticUser(synthetic_llm)
    base = base_time or datetime(2026, 5, 1, 9, 0)
    turns: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []

    for step in SCRIPT:
        now = _step_datetime(base, step)
        user_text = user.say(step)
        out = runtime.tick(
            agent_id,
            {
                "intent": step.intent,
                "text": user_text,
                "salience": 0.8,
                "now": now.isoformat(),
            },
        )
        step_failures = _assert_step(step, out, runtime, agent_id)
        failures.extend(step_failures)
        context = out.get("nino_context", {}) if isinstance(out.get("nino_context"), dict) else {}
        turns.append(
            {
                "label": step.label,
                "now": now.isoformat(),
                "user_text": user_text,
                "response": out.get("action", {}).get("payload", {}).get("text", ""),
                "reason_trace": out.get("reason_trace", []),
                "temporal_miss": bool(context.get("temporal_miss")),
                "failures": step_failures,
            }
        )

    state = runtime.load_or_init_state(agent_id)
    return {
        "ok": not failures,
        "kind": "synthetic_relationship_eval",
        "agent_id": agent_id,
        "privacy": "synthetic_user_no_real_data",
        "runtime_memory": {
            "writes_live_database": False,
            "writes_private_user_memory": False,
            "store": "in_memory",
        },
        "deepseek_user": bool(use_deepseek_user and synthetic_llm is not None),
        "turn_count": len(turns),
        "failures": failures,
        "metrics": {
            "temporal_miss_count": sum(1 for turn in turns if turn["temporal_miss"]),
            "invented_event_failures": sum(1 for failure in failures if failure["type"] == "invented_temporal_event"),
            "temporal_event_count": len(state.relation_state.get("temporal_events", [])),
        },
        "turns": turns,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the synthetic longitudinal companion regression.")
    parser.add_argument("--agent-id", default="synthetic-amigo")
    parser.add_argument("--deepseek-user", action="store_true", help="Use configured DeepSeek only to rephrase the synthetic user script.")
    parser.add_argument("--output", help="Optional JSON report path.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    report = run_synthetic_relationship_eval(agent_id=args.agent_id, use_deepseek_user=args.deepseek_user)
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"synthetic companion eval: {'ok' if report['ok'] else 'failed'}")
        print(f"turn_count: {report['turn_count']}")
        print(f"failures: {len(report['failures'])}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
