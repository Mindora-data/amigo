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
    provider: str = "claude"
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


@dataclass(slots=True)
class DeepSeekClient:
    api_key: str
    provider: str = "deepseek"
    model: str = "deepseek-chat"
    max_tokens: int = 320
    base_url: str = "https://api.deepseek.com/chat/completions"
    timeout_seconds: float = 20.0

    def complete(self, prompt: dict[str, Any]) -> str:
        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": [
                {"role": "system", "content": prompt["system"]},
                {"role": "user", "content": prompt["user"]},
            ],
        }
        req = request.Request(
            self.base_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "content-type": "application/json",
                "authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        with request.urlopen(req, timeout=self.timeout_seconds) as res:
            data = json.loads(res.read().decode("utf-8"))
        choices = data.get("choices", [])
        if not choices:
            return ""
        message = choices[0].get("message", {})
        return str(message.get("content", "")).strip()


def build_configured_llm() -> LLMClient | None:
    status = llm_config_status()
    if not status["enabled"]:
        return None
    if status["provider"] == "deepseek":
        return DeepSeekClient(
            api_key=_deepseek_api_key() or "",
            model=status["model"],
            max_tokens=status["max_tokens"],
            base_url=status["base_url"],
            timeout_seconds=status["timeout_seconds"],
        )
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


def _deepseek_api_key() -> str | None:
    return os.environ.get("DEEPSEEK_API_KEY", "").strip() or os.environ.get("NINO_DEEPSEEK_API_KEY", "").strip() or None


def _parse_positive_int_env(name: str, default: int, errors: list[dict[str, str]]) -> int:
    raw = os.environ.get(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError:
        errors.append({"name": name, "error": "invalid_integer"})
        return default
    if value <= 0:
        errors.append({"name": name, "error": "must_be_positive"})
        return default
    return value


def _parse_positive_float_env(name: str, default: float, errors: list[dict[str, str]]) -> float:
    raw = os.environ.get(name, str(default)).strip()
    try:
        value = float(raw)
    except ValueError:
        errors.append({"name": name, "error": "invalid_float"})
        return default
    if value <= 0:
        errors.append({"name": name, "error": "must_be_positive"})
        return default
    return value


def llm_config_status() -> dict[str, Any]:
    provider = os.environ.get("NINO_LLM_PROVIDER", "").strip().lower()
    keychain_service = os.environ.get("NINO_KEYCHAIN_SERVICE", "").strip()
    anthropic_env_api_key_present = bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())
    keychain_api_key_present = bool(_keychain_api_key(keychain_service)) if keychain_service else False
    deepseek_env_api_key_present = bool(_deepseek_api_key())
    config_errors: list[dict[str, str]] = []
    max_tokens = _parse_positive_int_env("NINO_LLM_MAX_TOKENS", 320, config_errors)
    timeout_seconds = _parse_positive_float_env("NINO_LLM_TIMEOUT", 20.0, config_errors)
    supported_provider = provider in {"claude", "anthropic", "deepseek"}
    normalized_provider = "claude" if provider == "anthropic" else provider
    if normalized_provider == "deepseek":
        model = os.environ.get("NINO_DEEPSEEK_MODEL", "deepseek-chat").strip() or "deepseek-chat"
        base_url = os.environ.get("NINO_DEEPSEEK_BASE_URL", "https://api.deepseek.com/chat/completions").strip() or "https://api.deepseek.com/chat/completions"
        api_key_present = deepseek_env_api_key_present
        api_key_source = "env" if api_key_present else None
    else:
        model = os.environ.get("NINO_CLAUDE_MODEL", "claude-sonnet-4-5").strip() or "claude-sonnet-4-5"
        base_url = "https://api.anthropic.com/v1/messages"
        api_key_present = anthropic_env_api_key_present or keychain_api_key_present
        api_key_source = "env" if anthropic_env_api_key_present else "keychain" if keychain_api_key_present else None
    enabled = supported_provider and api_key_present and not config_errors
    missing: list[str] = []
    if not provider:
        missing.append("NINO_LLM_PROVIDER")
    elif not supported_provider:
        missing.append("supported_provider")
    if normalized_provider == "deepseek" and not api_key_present:
        missing.append("DEEPSEEK_API_KEY")
    elif supported_provider and not api_key_present:
        missing.append("ANTHROPIC_API_KEY")
    missing.extend(error["name"] for error in config_errors)
    return {
        "enabled": enabled,
        "provider": normalized_provider or None,
        "supported_provider": supported_provider,
        "api_key_present": api_key_present,
        "api_key_source": api_key_source,
        "keychain_service": keychain_service or None,
        "model": model,
        "base_url": base_url,
        "max_tokens": max_tokens,
        "timeout_seconds": timeout_seconds,
        "missing": missing,
        "config_errors": config_errors,
    }


def _redact_context(value: str) -> str:
    value = re.sub(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", "[email]", value)
    value = re.sub(r"\b\d{3,}\b", "[number]", value)
    return value


def _asks_about_continuity(text: str, intent: str) -> bool:
    haystack = f"{intent} {text}".casefold()
    markers = (
        "recuerda",
        "recuerdas",
        "recordar",
        "memoria",
        "continuidad",
        "contexto",
        "que sabes",
        "qué sabes",
        "que recuerdas",
        "qué recuerdas",
        "semana pasada",
        "mes pasado",
        "antes de ayer",
        "anteayer",
        "hace ",
        "ayer",
        "diferencia",
        "claude directo",
    )
    return any(marker in haystack for marker in markers)


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
    continuity_mode = _asks_about_continuity(text, intent)
    continuity_instruction = (
        "El usuario pregunta por memoria, continuidad o diferencia con Claude directo. "
        "Usa al menos un recuerdo, preferencia u objetivo concreto disponible y explica brevemente como lo aplicaras. "
        "No hables de endpoints, SQLite, prompts ni trazas internas salvo que te lo pida."
        if continuity_mode
        else "Si el usuario no pregunta por memoria o continuidad, responde al tema sin explicar tus mecanismos internos."
    )
    system = (
        "Eres NIÑO, un agente local persistente. Responde en español, con tono directo y natural. "
        "Usa la memoria dada como contexto, no inventes recuerdos. Si no sabes algo, dilo. "
        "Mantén respuestas breves, normalmente entre 1 y 4 frases. "
        "No menciones detalles internos de implementación salvo que el usuario lo pregunte. "
        f"{continuity_instruction}"
    )
    user = (
        f"Agente: {agent_id}\n"
        f"Intent: {intent}\n"
        f"Mensaje del usuario: {_redact_context(text)}\n\n"
        f"Modo continuidad: {'activo' if continuity_mode else 'pasivo'}\n"
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
