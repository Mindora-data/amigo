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
    assert pyproject["project"]["scripts"]["nino-eval"] == "nino.eval_runner:main"
    assert pyproject["project"]["scripts"]["nino-status"] == "nino.product_status:main"
    assert pyproject["project"]["scripts"]["nino-completion-audit"] == "nino.completion_audit:main"
    assert pyproject["project"]["scripts"]["nino-closing-report"] == "nino.closing_report:main"
    assert pyproject["tool"]["setuptools"]["package-dir"] == {"": "src"}


def test_user_docs_do_not_recommend_inline_anthropic_api_key_assignment() -> None:
    docs = "\n".join(
        [
            Path("README.md").read_text(encoding="utf-8"),
            Path("PRODUCT_READINESS.md").read_text(encoding="utf-8"),
            Path("SPRINTS.md").read_text(encoding="utf-8"),
        ]
    )

    assert "ANTHROPIC_API_KEY=..." not in docs
    assert "read -rsp 'ANTHROPIC_API_KEY: '" in docs
