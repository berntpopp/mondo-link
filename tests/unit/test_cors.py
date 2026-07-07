"""Security guard for CORS: mondo-link is unauthenticated by design and holds no
cookies/session, so credentialed CORS is meaningless and a footgun if origins are
ever set to a wildcard. The app must configure ``allow_credentials=False`` and
fail closed on the ``allow_credentials=True`` + wildcard-origin combination, while
preserving its GET/POST/OPTIONS method list (it serves GET /health and root).

Research use only; not clinical decision support."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from mondo_link.app import _validate_cors, create_app


@contextmanager
def _build_app() -> Iterator[tuple[FastAPI, AsyncMock]]:
    """Yield a configured app while the data-bootstrap hooks stay patched.

    The patches MUST stay active around the ``TestClient`` block: entering the
    client triggers the FastAPI lifespan, which awaits ``bootstrap_data``. If the
    patches were torn down first, the lifespan would run the real (slow, networky)
    Mondo download instead of the stub.
    """
    boot = AsyncMock(return_value=None)
    no_op = AsyncMock(return_value=None)
    with (
        patch("mondo_link.app.bootstrap_data", boot),
        patch("mondo_link.app.start_refresh_scheduler", return_value=None),
        patch("mondo_link.app.stop_refresh_scheduler", no_op),
    ):
        yield create_app(), boot


def test_cors_credentials_disabled_and_health_ok() -> None:
    with _build_app() as (app, boot):
        cors = [m for m in app.user_middleware if m.cls is CORSMiddleware]
        assert cors, "CORS middleware must be configured"
        assert cors[0].kwargs["allow_credentials"] is False, (
            "unauthenticated backend must not enable credentialed CORS"
        )
        # Preserve the existing method list (serves GET /health and root).
        assert cors[0].kwargs["allow_methods"] == ["GET", "POST", "OPTIONS"]

        with TestClient(app, raise_server_exceptions=True) as client:
            resp = client.get("/health")
        # The lifespan ran under the patch: the stubbed (non-networky) bootstrap
        # was invoked, not the real data download.
        boot.assert_awaited()
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


def test_cors_startup_guard_rejects_wildcard_with_credentials() -> None:
    # Fails closed: credentialed CORS with a wildcard origin is forbidden.
    with pytest.raises(RuntimeError):
        _validate_cors(["*"], allow_credentials=True)
    # Safe combinations must not raise.
    _validate_cors(["*"], allow_credentials=False)
    _validate_cors(["http://localhost:3000"], allow_credentials=True)
