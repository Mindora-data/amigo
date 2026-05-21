from __future__ import annotations

import argparse
from pathlib import Path

from .api import run_server


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the NIÑO local API server.")
    parser.add_argument(
        "--db",
        default="data/nino.db",
        help="SQLite database path. Defaults to data/nino.db.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind.")
    parser.add_argument("--port", default=8000, type=int, help="Port to bind.")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    db_path = Path(args.db)
    print(f"NIÑO API listening on http://{args.host}:{args.port} using {db_path}")
    run_server(db_path, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
