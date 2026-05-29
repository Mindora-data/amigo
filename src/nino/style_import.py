from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime
import json
import re
from pathlib import Path
from statistics import median
from typing import Any


EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
URL_RE = re.compile(r"https?://\S+|www\.\S+")
MENTION_RE = re.compile(r"@\w+")
PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d[\d\s().-]{6,}\d)(?!\w)")

PRIVATE_MARKERS = (
    "secreto",
    "no lo cuentes",
    "no se lo digas",
    "confidencial",
    "contraseña",
    "password",
    "dni",
    "tarjeta",
    "iban",
    "cancer",
    "cáncer",
    "depresion",
    "depresión",
    "ansiedad",
    "diagnostico",
    "diagnóstico",
    "medicacion",
    "medicación",
    "hospital",
)
THIRD_PARTY_MARKERS = (
    "mi madre",
    "mi padre",
    "mi hermano",
    "mi hermana",
    "mi pareja",
    "mi ex",
    "mi jefe",
    "mi amiga",
    "mi amigo",
    "su madre",
    "su padre",
    "su pareja",
    "le han dicho",
    "le dijeron",
    "le diagnosticaron",
)
SOFT_NUDGE_MARKERS = (
    "cuando puedas",
    "sin prisa",
    "tranqui",
    "si te apetece",
    "si puedes",
    "no pasa nada",
    "sin presión",
    "sin presion",
)


def _message_text(raw: Any) -> str:
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list):
        parts: list[str] = []
        for item in raw:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("text", "")))
        return "".join(parts)
    return ""


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _split_allowlist(values: list[str]) -> set[str]:
    allowed: set[str] = set()
    for value in values:
        for part in value.split(","):
            clean = part.strip()
            if clean:
                allowed.add(clean)
    return allowed


def _author_key(message: dict[str, Any]) -> str:
    return str(message.get("from_id") or message.get("from") or "").strip()


def _sanitize(text: str, role_map: dict[str, str]) -> str:
    out = text
    for real, role in sorted(role_map.items(), key=lambda item: len(item[0]), reverse=True):
        if real:
            out = re.sub(re.escape(real), role, out, flags=re.IGNORECASE)
    out = EMAIL_RE.sub("[email]", out)
    out = URL_RE.sub("[url]", out)
    out = MENTION_RE.sub("[mencion]", out)
    out = PHONE_RE.sub("[telefono]", out)
    out = re.sub(r"\s+", " ", out).strip()
    return out


def _looks_private(text: str) -> bool:
    lowered = text.casefold()
    return any(marker in lowered for marker in PRIVATE_MARKERS) or any(
        marker in lowered for marker in THIRD_PARTY_MARKERS
    )


def _is_style_example(text: str) -> bool:
    words = text.split()
    if not (2 <= len(words) <= 28):
        return False
    if len(text) > 180:
        return False
    return not _looks_private(text)


def build_style_corpus(export: dict[str, Any], allowlist: set[str]) -> dict[str, Any]:
    if not allowlist:
        raise ValueError("author_allowlist_required")

    role_map: dict[str, str] = {}
    included: list[dict[str, Any]] = []
    excluded = defaultdict(int)
    previous_dt: datetime | None = None
    gaps: list[float] = []

    for item in export.get("messages", []):
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        author_key = _author_key(item)
        author_name = str(item.get("from") or author_key).strip()
        if author_key not in allowlist and author_name not in allowlist:
            excluded["author_not_allowed"] += 1
            continue
        role = role_map.setdefault(author_name or author_key, f"amigo_{len(role_map) + 1}")
        text = _message_text(item.get("text")).strip()
        if not text:
            excluded["empty"] += 1
            continue
        if _looks_private(text):
            excluded["private_or_third_party"] += 1
            continue
        sanitized = _sanitize(text, role_map)
        if not sanitized:
            excluded["empty_after_sanitize"] += 1
            continue
        current_dt = _parse_dt(str(item.get("date") or ""))
        if current_dt and previous_dt:
            gap = max(0.0, (current_dt - previous_dt).total_seconds())
            if gap <= 24 * 3600:
                gaps.append(gap)
        if current_dt:
            previous_dt = current_dt
        included.append({"role": role, "text": sanitized, "date": item.get("date")})

    lengths = [len(item["text"]) for item in included]
    question_count = sum(1 for item in included if "?" in item["text"] or "¿" in item["text"])
    soft_nudge_count = sum(
        1 for item in included if any(marker in item["text"].casefold() for marker in SOFT_NUDGE_MARKERS)
    )
    examples = [
        {
            "label": "registro_no_recuerdo",
            "role": item["role"],
            "text": item["text"],
        }
        for item in included
        if _is_style_example(item["text"])
    ][:25]

    return {
        "kind": "telegram_style_corpus",
        "privacy": "registro_no_recuerdos",
        "source": "telegram_export_offline",
        "consent": {
            "author_allowlist_required": True,
            "allowed_author_count": len(allowlist),
            "allowed_authors_are_not_exported": True,
        },
        "runtime_memory": {
            "writes_live_database": False,
            "writes_private_user_memory": False,
            "intended_use": "prompt_style_only",
        },
        "metrics": {
            "included_messages": len(included),
            "excluded": dict(excluded),
            "average_length_chars": round(sum(lengths) / len(lengths), 2) if lengths else 0.0,
            "question_ratio": round(question_count / len(included), 4) if included else 0.0,
            "soft_nudge_ratio": round(soft_nudge_count / len(included), 4) if included else 0.0,
            "median_turn_gap_seconds": round(median(gaps), 2) if gaps else None,
        },
        "patterns": {
            "tone": "cercano, breve, con espacio para no responder",
            "soft_nudge_markers": list(SOFT_NUDGE_MARKERS),
            "roles": sorted(set(item["role"] for item in included)),
        },
        "examples": examples,
    }


def import_telegram_style(
    export_path: str | Path,
    *,
    allowlist: set[str],
    output_dir: str | Path = "data/style",
) -> Path:
    export_file = Path(export_path)
    output_root = Path(output_dir)
    corpus = build_style_corpus(json.loads(export_file.read_text(encoding="utf-8")), allowlist)
    output_root.mkdir(parents=True, exist_ok=True)
    out_path = output_root / f"{export_file.stem}.style.json"
    out_path.write_text(json.dumps(corpus, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import a Telegram JSON export as an offline anonymous style corpus.")
    parser.add_argument("export_json", help="Native Telegram JSON export.")
    parser.add_argument("--allow-author", action="append", default=[], help="Allowed Telegram author name/from_id. Repeat or comma-separate.")
    parser.add_argument("--output-dir", default="data/style", help="Output directory. Defaults to data/style.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable result.")
    args = parser.parse_args(argv)

    allowlist = _split_allowlist(args.allow_author)
    try:
        out_path = import_telegram_style(args.export_json, allowlist=allowlist, output_dir=args.output_dir)
    except ValueError as exc:
        if str(exc) == "author_allowlist_required":
            print("telegram-style-import: error: --allow-author is required and must name consenting authors.")
            return 2
        raise
    result = {"ok": True, "output": str(out_path), "privacy": "registro_no_recuerdos"}
    print(json.dumps(result, indent=2) if args.json else f"style corpus written: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
