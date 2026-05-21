from __future__ import annotations

from nino.server import build_parser


def test_server_parser_defaults() -> None:
    args = build_parser().parse_args([])

    assert args.db == "data/nino.db"
    assert args.host == "127.0.0.1"
    assert args.port == 8000
    assert args.scheduler_interval == 0.0


def test_server_parser_accepts_overrides() -> None:
    args = build_parser().parse_args([
        "--db", "tmp/nino.db",
        "--host", "0.0.0.0",
        "--port", "9000",
        "--scheduler-interval", "30",
    ])

    assert args.db == "tmp/nino.db"
    assert args.host == "0.0.0.0"
    assert args.port == 9000
    assert args.scheduler_interval == 30
