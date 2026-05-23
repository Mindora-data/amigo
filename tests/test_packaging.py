from __future__ import annotations

import tomllib
from pathlib import Path


def test_package_metadata_exposes_server_console_script() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    assert pyproject["project"]["name"] == "nino-local"
    assert pyproject["project"]["requires-python"] == ">=3.11"
    assert pyproject["project"]["scripts"]["nino-server"] == "nino.server:main"
    assert pyproject["project"]["scripts"]["nino-smoke"] == "nino.smoke:main"
    assert pyproject["project"]["scripts"]["nino-claude-live"] == "nino.claude_live:main"
    assert pyproject["project"]["scripts"]["nino-product-audit"] == "nino.product_audit:main"
    assert pyproject["tool"]["setuptools"]["package-dir"] == {"": "src"}
