"""Batch tool tests: partial success, cap enforcement, capabilities sync.

Driven through the real FastMCP facade over the fixture index (conftest ``facade``),
so the full envelope path is exercised: a batch call succeeds wholesale even when
individual items fail, and an over-cap call returns a single invalid_input error.
"""

from __future__ import annotations

from typing import Any

import pytest

pytestmark = pytest.mark.mcp

_SGS = "MONDO:0008426"
_MISSING = "MONDO:0000000"


async def _tool(facade: Any, name: str) -> Any:
    return {t.name: t for t in await facade.list_tools()}[name]


async def test_resolve_batch_partial_success(facade: Any) -> None:
    tool = await _tool(facade, "resolve_disease_batch")
    payload = await tool.fn(queries=["Shprintzen-Goldberg syndrome", _MISSING])
    assert payload["success"] is True
    assert payload["count"] == 2
    ok, bad = payload["results"]
    assert ok["ok"] is True and ok["mondo_id"] == _SGS
    assert bad["ok"] is False and bad["error_code"] == "not_found"
    assert payload["_meta"]["next_commands"][0]["tool"] == "get_disease"


async def test_resolve_batch_rejects_oversize(facade: Any) -> None:
    tool = await _tool(facade, "resolve_disease_batch")
    payload = await tool.fn(queries=["x"] * 51)
    assert payload["success"] is False
    assert payload["error_code"] == "invalid_input"


async def test_resolve_batch_rejects_empty(facade: Any) -> None:
    tool = await _tool(facade, "resolve_disease_batch")
    payload = await tool.fn(queries=[])
    assert payload["success"] is False
    assert payload["error_code"] == "invalid_input"


async def test_get_disease_batch_mixed(facade: Any) -> None:
    tool = await _tool(facade, "get_disease_batch")
    payload = await tool.fn(terms=[_SGS, _MISSING])
    assert payload["success"] is True
    assert payload["count"] == 2
    assert payload["results"][0]["ok"] is True
    assert payload["results"][1]["ok"] is False
    assert payload["_meta"]["next_commands"][0]["tool"] == "map_cross_ontology"


async def test_get_disease_batch_respects_fields(facade: Any) -> None:
    tool = await _tool(facade, "get_disease_batch")
    payload = await tool.fn(terms=[_SGS], fields=["xrefs.OMIM"], response_mode="standard")
    item = payload["results"][0]
    assert item["ok"] is True
    assert set(item["xrefs"]) == {"OMIM"}
    assert "definition" not in item


async def test_batch_tools_in_capabilities() -> None:
    from mondo_link.mcp.capabilities import TOOLS

    assert "resolve_disease_batch" in TOOLS
    assert "get_disease_batch" in TOOLS
