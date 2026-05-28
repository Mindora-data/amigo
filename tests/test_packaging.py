from __future__ import annotations

from io import BytesIO
import importlib.util
import json
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
    assert pyproject["project"]["scripts"]["nino-prod-smoke"] == "nino.prod_smoke:main"
    assert pyproject["project"]["scripts"]["nino-telegram"] == "nino.telegram:main"
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


def test_vercel_python_adapter_serves_wsgi_app(monkeypatch, tmp_path) -> None:
    root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("NINO_DB_PATH", str(tmp_path / "nino-vercel.db"))
    spec = importlib.util.spec_from_file_location("nino_vercel_index", root / "api" / "index.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    captured: dict[str, str] = {}

    def start_response(status: str, headers: list[tuple[str, str]]) -> None:
        captured["status"] = status
        captured["content_type"] = dict(headers).get("Content-Type", "")

    body = b"".join(
        module.app(
            {
                "REQUEST_METHOD": "GET",
                "PATH_INFO": "/user",
                "QUERY_STRING": "",
                "CONTENT_LENGTH": "0",
                "wsgi.input": BytesIO(b""),
            },
            start_response,
        )
    )

    assert captured["status"].startswith("200")
    assert captured["content_type"].startswith("text/html")
    assert b"minimalUserApp" in body


def test_vercel_config_routes_all_requests_to_python_adapter() -> None:
    root = Path(__file__).resolve().parents[1]
    config = json.loads((root / "vercel.json").read_text(encoding="utf-8"))

    assert config["$schema"] == "https://openapi.vercel.sh/vercel.json"
    assert config["rewrites"] == [{"source": "/(.*)", "destination": "/api/index.py"}]
