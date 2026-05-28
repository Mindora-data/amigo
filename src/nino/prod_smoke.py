from __future__ import annotations

import argparse
from contextlib import contextmanager
from io import BytesIO
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Iterator
from urllib.parse import urlsplit

from .api import create_app
from .auth import hash_password


@contextmanager
def _temporary_env(values: dict[str, str | None]) -> Iterator[None]:
    previous = {key: os.environ.get(key) for key in values}
    for key, value in values.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _request(
    app: Any,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[str, dict[str, Any], dict[str, str]]:
    data = b"" if payload is None else json.dumps(payload).encode("utf-8")
    captured: dict[str, Any] = {}
    split = urlsplit(path)

    def start_response(status: str, response_headers: list[tuple[str, str]]) -> None:
        captured["status"] = status
        captured["headers"] = dict(response_headers)

    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": split.path,
        "QUERY_STRING": split.query,
        "CONTENT_LENGTH": str(len(data)),
        "wsgi.input": BytesIO(data),
        "REMOTE_ADDR": "203.0.113.10",
    }
    for key, value in (headers or {}).items():
        environ[f"HTTP_{key.upper().replace('-', '_')}"] = value
    body = b"".join(app(environ, start_response))
    return str(captured["status"]), json.loads(body.decode("utf-8")), dict(captured["headers"])


def _status_ok(status: str) -> bool:
    return status.startswith("2")


def run_prod_smoke(db_path: Path | None = None) -> dict[str, Any]:
    password = "correct horse battery staple"
    checks: list[dict[str, Any]] = []

    with _temporary_env(
        {
            "NINO_ENV": "prod",
            "NINO_REQUIRE_SESSION": None,
            "NINO_PASSWORD_HASH": hash_password(password),
            "NINO_SESSION_PEPPER": "prod-smoke-session-pepper",
        }
    ):
        try:
            create_app(db_path or Path(tempfile.mkdtemp(prefix="nino-prod-startup-")) / "nino.db")
            startup_closed = False
        except RuntimeError:
            startup_closed = True
    checks.append({"name": "prod_refuses_open_session_mode", "ok": startup_closed})

    with _temporary_env(
        {
            "NINO_ENV": "prod",
            "NINO_REQUIRE_SESSION": "true",
            "NINO_PASSWORD_HASH": hash_password(password),
            "NINO_SESSION_PEPPER": "prod-smoke-session-pepper",
        }
    ):
        tmp = None
        if db_path is None:
            tmp = tempfile.TemporaryDirectory(prefix="nino-prod-smoke-")
            db = Path(tmp.name) / "nino.db"
        else:
            db = db_path
        try:
            app = create_app(db)
            insecure_status, insecure_body, _ = _request(app, "GET", "/health")
            checks.append(
                {
                    "name": "prod_rejects_non_https",
                    "ok": insecure_status.startswith("403") and insecure_body.get("error") == "https_required",
                }
            )

            https = {"X-Forwarded-Proto": "https"}
            app_status, app_body, _ = _request(app, "GET", "/app", headers=https)
            checks.append(
                {
                    "name": "prod_disables_internal_app",
                    "ok": app_status.startswith("404") and app_body.get("error") == "app_disabled_in_prod",
                }
            )

            missing_status, missing_body, _ = _request(app, "GET", "/users/ana/agents/nino/conversation", headers=https)
            checks.append(
                {
                    "name": "private_routes_require_session",
                    "ok": missing_status.startswith("401") and missing_body.get("error") == "session_required",
                }
            )

            bad_status, bad_body, _ = _request(
                app,
                "POST",
                "/session/login",
                {"user_id": "Ana", "agent_id": "nino", "password": "wrong"},
                headers=https,
            )
            checks.append({"name": "bad_password_rejected", "ok": bad_status.startswith("401") and bad_body.get("error") == "bad_credentials"})

            login_status, login_body, login_headers = _request(
                app,
                "POST",
                "/session/login",
                {"user_id": "Ana", "agent_id": "nino", "password": password},
                headers=https,
            )
            cookie = login_headers.get("Set-Cookie", "")
            checks.append(
                {
                    "name": "good_password_sets_secure_cookie",
                    "ok": _status_ok(login_status)
                    and bool(login_body.get("session_token"))
                    and "HttpOnly" in cookie
                    and "Secure" in cookie
                    and "SameSite=Strict" in cookie,
                }
            )

            cookie_header = {"X-Forwarded-Proto": "https", "Cookie": cookie}
            session_status, session_body, _ = _request(app, "GET", "/session/status", headers=cookie_header)
            checks.append({"name": "cookie_authenticates_session", "ok": _status_ok(session_status) and session_body.get("authenticated") is True})

            route_status, route_body, _ = _request(app, "GET", "/users/ana/agents/nino/conversation", headers=cookie_header)
            checks.append({"name": "cookie_authorizes_private_route", "ok": _status_ok(route_status) and "turns" in route_body})

            logout_status, logout_body, logout_headers = _request(app, "POST", "/session/logout", {}, headers=cookie_header)
            checks.append(
                {
                    "name": "logout_clears_cookie",
                    "ok": _status_ok(logout_status) and logout_body.get("logged_out") is True and "Max-Age=0" in logout_headers.get("Set-Cookie", ""),
                }
            )

            after_status, after_body, _ = _request(app, "GET", "/session/status", headers=cookie_header)
            checks.append({"name": "logout_invalidates_session", "ok": _status_ok(after_status) and after_body.get("authenticated") is False})
        finally:
            if tmp is not None:
                tmp.cleanup()

    return {"ok": all(check["ok"] for check in checks), "checks": checks}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a local production-mode auth smoke test.")
    parser.add_argument("--json", action="store_true", help="Print JSON output.")
    parser.add_argument("--db", type=Path, help="Optional SQLite path. Defaults to a temporary database.")
    args = parser.parse_args(argv)

    result = run_prod_smoke(args.db)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"prod_smoke: {'ok' if result['ok'] else 'blocked'}")
        for check in result["checks"]:
            print(f"- {check['name']}: {'ok' if check['ok'] else 'blocked'}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
