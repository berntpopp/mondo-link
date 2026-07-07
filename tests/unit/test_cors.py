"""Security guard for CORS: mondo-link is unauthenticated by design and holds no
cookies/session, so credentialed CORS is meaningless and a footgun if origins are
ever set to a wildcard. The app must configure ``allow_credentials=False`` and
fail closed on the ``allow_credentials=True`` + wildcard-origin combination, while
preserving its GET/POST/OPTIONS method list (it serves GET /health and root).

Research use only; not clinical decision support."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from mondo_link.app import _validate_cors, create_app


def _build_app() -> object:
    no_op = AsyncMock(return_value=None)
    with (
        patch("mondo_link.app.bootstrap_data", no_op),
        patch("mondo_link.app.start_refresh_scheduler", return_value=None),
        patch("mondo_link.app.stop_refresh_scheduler", no_op),
    ):
        return create_app()


def test_cors_credentials_disabled_and_health_ok() -> None:
    app = _build_app()

    cors = [m for m in app.user_middleware if m.cls is CORSMiddleware]
    assert cors, "CORS middleware must be configured"
    assert cors[0].kwargs["allow_credentials"] is False, (
        "unauthenticated backend must not enable credentialed CORS"
    )
    # Preserve the existing method list (serves GET /health and root).
    assert cors[0].kwargs["allow_methods"] == ["GET", "POST", "OPTIONS"]

    with TestClient(app, raise_server_exceptions=True) as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_cors_startup_guard_rejects_wildcard_with_credentials() -> None:
    # Fails closed: credentialed CORS with a wildcard origin is forbidden.
    with pytest.raises(RuntimeError):
        _validate_cors(["*"], allow_credentials=True)
    # Safe combinations must not raise.
    _validate_cors(["*"], allow_credentials=False)
    _validate_cors(["http://localhost:3000"], allow_credentials=True)
