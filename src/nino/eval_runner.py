from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .contracts import ConsolidationRequest, RetrieveRequest
from .runtime import InMemoryStateStore, NinoRuntime


def _contains_terms(value: str, terms: list[str]) -> bool:
    lowered = value.lower()
    return all(term.lower() in lowered for term in terms)


def run_eval_case(path: str | Path, runtime: NinoRuntime | None = None) -> dict[str, Any]:
    case_path = Path(path)
    case = json.loads(case_path.read_text(encoding="utf-8"))
    runtime = runtime or NinoRuntime(InMemoryStateStore(), llm_client=None)
    agent_id = case["agent_id"]
    failures: list[dict[str, Any]] = []

    for step in case.get("steps", []):
        runtime.tick(agent_id, step)

    runtime.consolidate(
        ConsolidationRequest(
            agent_id=agent_id,
            episodes=runtime.episode_store.list_for_agent(agent_id),
        )
    )

    state = runtime.load_or_init_state(agent_id)
    for query in case.get("queries", []):
        retrieved = runtime.retrieve_memory(
            agent_id,
            RetrieveRequest(
                query_intent=query["query_intent"],
                self_state={},
                relation_state=state.relation_state,
                time_scope="long",
            ),
        )
        combined = " ".join(candidate.statement for candidate in retrieved.memory_candidates)
        if not _contains_terms(combined, list(query.get("expected_terms", []))):
            failures.append(
                {
                    "type": "memory_query",
                    "query": query["query_intent"],
                    "expected_terms": query.get("expected_terms", []),
                    "actual": combined,
                }
            )

    for question in case.get("conversation_questions", []):
        out = runtime.tick(agent_id, {"intent": "question", "text": question["text"], "salience": 0.7})
        answer = out["action"]["payload"]["text"]
        if not _contains_terms(answer, list(question.get("expected_terms", []))):
            failures.append(
                {
                    "type": "conversation_question",
                    "question": question["text"],
                    "expected_terms": question.get("expected_terms", []),
                    "actual": answer,
                }
            )

    metrics = runtime.metrics(agent_id)
    return {
        "name": case.get("name", case_path.stem),
        "agent_id": agent_id,
        "ok": not failures,
        "failures": failures,
        "metrics": metrics,
    }


def run_eval_dir(path: str | Path) -> dict[str, Any]:
    root = Path(path)
    results = [run_eval_case(item) for item in sorted(root.glob("*.json"))]
    return {
        "ok": all(result["ok"] for result in results),
        "case_count": len(results),
        "results": results,
    }
