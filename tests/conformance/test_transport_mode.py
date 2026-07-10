"""Stateless-tier construction guard (in-process, no server needed)."""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from mondo_link import server_manager
from mondo_link.app import create_app


def test_unified_server_builds_stateless_json_mcp_app() -> None:
    src = inspect.getsource(server_manager.create_unified_app)
    assert "stateless_http=True" in src, "MCP app must be built stateless"
    assert "json_response=True" in src, "MCP app must return JSON responses"
    assert "host_origin_protection=True" in src, "MCP app must use the native strict guard"
    assert 'mount("/"' in src, "MCP ASGI app must mount at root (no 307)"


def test_health_returns_status_version_transport() -> None:
    """GET /health must include status, version, and transport (Transport Standard v1)."""
    no_op = AsyncMock(return_value=None)
    with (
        patch("mondo_link.app.bootstrap_data", no_op),
        patch("mondo_link.app.start_refresh_scheduler", return_value=None),
        patch("mondo_link.app.stop_refresh_scheduler", no_op),
    ):
        app = create_app()
        with TestClient(app, raise_server_exceptions=True) as client:
            resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "version" in body
    assert body["transport"] == "streamable-http-stateless"
