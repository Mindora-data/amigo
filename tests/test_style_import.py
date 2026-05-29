from __future__ import annotations

import json
from pathlib import Path

from nino.style_import import build_style_corpus, import_telegram_style, main


def _export() -> dict:
    return {
        "name": "Grupo cerrado",
        "messages": [
            {
                "type": "message",
                "date": "2026-05-01T10:00:00",
                "from": "Ana",
                "from_id": "user1",
                "text": "Tranqui, cuando puedas lo miramos.",
            },
            {
                "type": "message",
                "date": "2026-05-01T10:02:00",
                "from": "Bob",
                "from_id": "user2",
                "text": "Mi madre tiene ansiedad y no quiere contarlo.",
            },
            {
                "type": "message",
                "date": "2026-05-01T10:03:00",
                "from": "Eve",
                "from_id": "user3",
                "text": "Hola, soy Eve.",
            },
            {
                "type": "message",
                "date": "2026-05-01T10:04:00",
                "from": "Ana",
                "from_id": "user1",
                "text": "Ana mira https://example.com y escribe a ana@example.com",
            },
        ],
    }


def test_style_import_requires_allowlist() -> None:
    try:
        build_style_corpus(_export(), set())
    except ValueError as exc:
        assert str(exc) == "author_allowlist_required"
    else:
        raise AssertionError("allowlist should be required")


def test_style_import_filters_consent_private_and_sanitizes(tmp_path: Path) -> None:
    export_file = tmp_path / "telegram.json"
    export_file.write_text(json.dumps(_export()), encoding="utf-8")

    out_path = import_telegram_style(export_file, allowlist={"user1", "user2"}, output_dir=tmp_path / "style")
    corpus = json.loads(out_path.read_text(encoding="utf-8"))
    dumped = json.dumps(corpus, ensure_ascii=False)

    assert corpus["privacy"] == "registro_no_recuerdos"
    assert corpus["runtime_memory"]["writes_live_database"] is False
    assert corpus["metrics"]["included_messages"] == 2
    assert corpus["metrics"]["excluded"]["private_or_third_party"] == 1
    assert corpus["metrics"]["excluded"]["author_not_allowed"] == 1
    assert "Ana" not in dumped
    assert "ana@example.com" not in dumped
    assert "https://example.com" not in dumped
    assert "Mi madre" not in dumped
    assert "amigo_1" in dumped


def test_style_import_cli_refuses_without_allowlist(tmp_path: Path, capsys) -> None:
    export_file = tmp_path / "telegram.json"
    export_file.write_text(json.dumps(_export()), encoding="utf-8")

    code = main([str(export_file), "--output-dir", str(tmp_path / "style")])
    out = capsys.readouterr().out

    assert code == 2
    assert "--allow-author is required" in out
    assert not (tmp_path / "style").exists()
