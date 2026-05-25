from __future__ import annotations

import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nino.api import create_app  # noqa: E402


db_path = Path(os.environ.get("NINO_DB_PATH", "/tmp/nino-vercel.db"))
app = create_app(db_path)
