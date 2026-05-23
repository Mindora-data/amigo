from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
import subprocess
from typing import Any, Protocol
from urllib import request

from .contracts import MemoryCandidate


class LLMClient(Protocol):
    def complete(self, prompt: dict[str, Any]) -> str:
        ...


@dataclass(slots=True)
class ClaudeClient:
    api_key: str
    model: str = "claude-sonnet-4-5"
    max_tokens: int = 320
    base_url: str = "https://api.anthropic.com/v1/messages"
    anthropic_version: str = "2023-06-01"
    timeout_seconds: float = 20.0

    def complete(self, prompt: dict[str, Any]) -> str:
        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": prompt["system"],
            "messages": [{"role": "user", "content": prompt["user"]}],
        }
        req = request.Request(
            self.base_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "content-type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": self.anthropic_version,
            },
            method="POST",
        )
        with request.urlopen(req, timeout=self.timeout_seconds) as res:
            data = json.loads(res.read().decode("utf-8"))
        text_blocks = [
            block.get("text", "")
            for block in data.get("content", [])
            if block.get("type") == "text"
        ]
        return "\n".join(block for block in text_blocks if block).strip()


def build_configured_llm() -> LLMClient | None:
    status = llm_config_status()
    provider = status["provider"]
    if provider not in {"claude", "anthropic"}:
        return None
    if not status["api_key_present"]:
        return None
    return ClaudeClient(
        api_key=_anthropic_api_key() or "",
        model=status["model"],
        max_tokens=status["max_tokens"],
        timeout_seconds=status["timeout_seconds"],
    )


def _keychain_api_key(service: str) -> str | None:
    if not service:
        return None
    try:
        completed = subprocess.run(
            ["/usr/bin/security", "find-generic-password", "-w", "-s", service],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip() or None


def _anthropic_api_key() -> str | None:
    env_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if env_key:
        return env_key
    return _keychain_api_key(os.environ.get("NINO_KEYCHAIN_SERVICE", "").strip())


def llm_config_status() -> dict[str, Any]:
    provider = os.environ.get("NINO_LLM_PROVIDER", "").strip().lower()
    keychain_service = os.environ.get("NINO_KEYCHAIN_SERVICE", "").strip()
    env_api_key_present = bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())
    keychain_api_key_present = bool(_keychain_api_key(keychain_service)) if keychain_service else False
    api_key_present = env_api_key_present or keychain_api_key_present
    model = os.environ.get("NINO_CLAUDE_MODEL", "claude-sonnet-4-5").strip() or "claude-sonnet-4-5"
    max_tokens = int(os.environ.get("NINO_LLM_MAX_TOKENS", "320"))
    timeout_seconds = float(os.environ.get("NINO_LLM_TIMEOUT", "20"))
    supported_provider = provider in {"claude", "anthropic"}
    enabled = supported_provider and api_key_present
    missing: list[str] = []
    if not provider:
        missing.append("NINO_LLM_PROVIDER")
    elif not supported_provider:
        missing.append("supported_provider")
    if supported_provider and not api_key_present:
        missing.append("ANTHROPIC_API_KEY")
    return {
        "enabled": enabled,
        "provider": provider or None,
        "supported_provider": supported_provider,
        "api_key_present": api_key_present,
        "api_key_source": "env" if env_api_key_present else "keychain" if keychain_api_key_present else None,
        "keychain_service": keychain_service or None,
        "model": model,
        "max_tokens": max_tokens,
        "timeout_seconds": timeout_seconds,
        "missing": missing,
    }


def _redact_context(value: str) -> str:
    value = re.sub(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", "[email]", value)
    value = re.sub(r"\b\d{3,}\b", "[number]", value)
    return value


def build_nino_prompt(
    *,
    agent_id: str,
    text: str,
    intent: str,
    relation_state: dict[str, Any],
    self_model: dict[str, Any],
    world_model: dict[str, Any],
    active_goals: list[str],
    memory_candidates: list[MemoryCandidate],
    recent_turns: list[dict[str, Any]] | None = None,
    cold_facts: list[Any] | None = None,
) -> dict[str, str]:
    memories = "\n".join(
        f"- {_redact_context(candidate.statement)} (confidence {candidate.confidence:.2f})"
        for candidate in memory_candidates[:5]
    ) or "- No retrieved memory."
    turns = "\n".join(
        f"- {turn.get('role', 'unknown')}: {_redact_context(str(turn.get('text', '')))}"
        for turn in (recent_turns or [])[-8:]
        if turn.get("text")
    ) or "- No recent turns."
    facts = "\n".join(
        f"- {getattr(fact, 'key', 'fact')}: {_redact_context(str(getattr(fact, 'value', '')))} "
        f"(confidence {float(getattr(fact, 'confidence', 0.0)):.2f})"
        for fact in (cold_facts or [])[:8]
        if getattr(fact, "valid_to", None) is None
    ) or "- No active cold facts."
    preferences = ", ".join(sorted(relation_state.get("preferences", {}).keys())) or "none"
    concepts = sorted(
        world_model.get("concept_counts", {}).items(),
        key=lambda item: item[1],
        reverse=True,
    )[:8]
    concept_text = ", ".join(key for key, _ in concepts) or "none"
    system = (
        "Eres NIÑO, un agente local persistente. Responde en español, con tono directo y natural. "
        "Usa la memoria dada como contexto, no inventes recuerdos. Si no sabes algo, dilo. "
        "Mantén respuestas breves, normalmente entre 1 y 4 frases. "
        "No menciones detalles internos de implementación salvo que el usuario lo pregunte."
    )
    user = (
        f"Agente: {agent_id}\n"
        f"Intent: {intent}\n"
        f"Mensaje del usuario: {_redact_context(text)}\n\n"
        f"Preferencias conocidas: {preferences}\n"
        f"Objetivos activos: {', '.join(active_goals) or 'none'}\n"
        f"Etapa de identidad: {self_model.get('identity_stage', 'unknown')}\n"
        f"Conceptos dominantes: {concept_text}\n\n"
        f"Últimos turnos:\n{turns}\n\n"
        f"Memoria recuperada:\n{memories}\n\n"
        f"Hechos fríos activos:\n{facts}\n\n"
        "Responde ahora al usuario como NIÑO."
    )
    return {"system": system, "user": user}
