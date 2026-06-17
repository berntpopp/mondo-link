"""End-to-end tests through the registered MCP tool callables (envelope contract).

A FakeService stands in for the real DB-backed MondoService (injected via
set_mondo_service); each tool's underlying callable (``Tool.fn``) is invoked so
the full envelope is exercised: success + _meta.next_commands on the happy path,
and a returned (not raised) error envelope on failure.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastmcp import FastMCP

from mondo_link.exceptions import NotFoundError
from mondo_link.mcp.capabilities import TOOLS
from mondo_link.mcp.service_adapters import reset_mondo_service, set_mondo_service
from mondo_link.mcp.tools import (
    register_batch_tools,
    register_discovery_tools,
    register_disease_tools,
    register_hierarchy_tools,
    register_xref_tools,
)

_MONDO = "MONDO:0008426"
_NAME = "Shprintzen-Goldberg syndrome"
_VERSION = "2026-06-01"


class FakeService:
    """Minimal in-memory MondoService stand-in returning valid-shaped dicts."""

    raise_not_found_on_get_disease: bool = False

    def get_diagnostics(self) -> dict[str, Any]:
        return {
            "index_built": True,
            "mondo_version": _VERSION,
            "schema_version": 1,
            "counts": {"terms": 1, "obsolete": 0, "xrefs": 1, "mappings": 1},
        }

    def resolve_disease(self, query: str, *, response_mode: str = "compact") -> dict[str, Any]:
        return {
            "query": query,
            "mondo_id": _MONDO,
            "name": _NAME,
            "match_type": "primary",
            "obsolete": False,
            "mondo_version": _VERSION,
        }

    def search_diseases(
        self,
        query: str,
        *,
        limit: int = 25,
        offset: int = 0,
        include_obsolete: bool = False,
        response_mode: str = "compact",
    ) -> dict[str, Any]:
        return {
            "query": query,
            "results": [{"mondo_id": _MONDO, "name": _NAME, "score": 1.0}],
            "total": 1,
            "returned": 1,
            "limit": limit,
            "offset": offset,
            "truncated": False,
            "mondo_version": _VERSION,
        }

    def get_disease(
        self, term: str, *, response_mode: str = "compact", fields: list[str] | None = None
    ) -> dict[str, Any]:
        if self.raise_not_found_on_get_disease:
            raise NotFoundError(f"No Mondo term for {term}.")
        return {
            "mondo_id": _MONDO,
            "name": _NAME,
            "definition": "A syndrome.",
            "synonyms": [],
            "xrefs": {},
            "parents": [],
            "children": [],
            "obsolete": False,
            "mondo_version": _VERSION,
        }

    def get_ancestors(
        self, term: str, *, limit: int = 200, offset: int = 0, response_mode: str = "compact"
    ) -> dict[str, Any]:
        return {
            "mondo_id": _MONDO,
            "name": _NAME,
            "ancestors": [{"mondo_id": "MONDO:0000001", "name": "disease"}],
            "total": 1,
            "returned": 1,
            "limit": limit,
            "offset": offset,
            "truncated": False,
            "mondo_version": _VERSION,
        }

    def get_descendants(
        self, term: str, *, limit: int = 200, offset: int = 0, response_mode: str = "compact"
    ) -> dict[str, Any]:
        return {
            "mondo_id": _MONDO,
            "name": _NAME,
            "descendants": [],
            "total": 0,
            "returned": 0,
            "limit": limit,
            "offset": offset,
            "truncated": False,
            "mondo_version": _VERSION,
        }

    def get_parents(self, term: str, *, response_mode: str = "compact") -> dict[str, Any]:
        return {
            "mondo_id": _MONDO,
            "name": _NAME,
            "parents": [{"mondo_id": "MONDO:0000001", "name": "disease"}],
            "count": 1,
            "mondo_version": _VERSION,
        }

    def get_children(self, term: str, *, response_mode: str = "compact") -> dict[str, Any]:
        return {
            "mondo_id": _MONDO,
            "name": _NAME,
            "children": [],
            "count": 0,
            "mondo_version": _VERSION,
        }

    def resolve_xref(
        self, xref_id: str, *, limit: int = 50, offset: int = 0, response_mode: str = "compact"
    ) -> dict[str, Any]:
        return {
            "xref_id": xref_id,
            "normalized": xref_id.upper(),
            "matches": [{"mondo_id": _MONDO, "name": _NAME, "predicate": "exactMatch"}],
            "total": 1,
            "returned": 1,
            "limit": limit,
            "offset": offset,
            "truncated": False,
            "mondo_version": _VERSION,
        }

    def map_cross_ontology(
        self,
        term: str,
        *,
        prefixes: list[str] | None = None,
        response_mode: str = "compact",
        fields: list[str] | None = None,
    ) -> dict[str, Any]:
        return {
            "mondo_id": _MONDO,
            "name": _NAME,
            "mappings": {"OMIM": [{"object_id": "OMIM:182212", "predicate": "exactMatch"}]},
            "count": 1,
            "prefixes_filter": prefixes,
            "mondo_version": _VERSION,
        }


@pytest.fixture
def fake() -> Iterator[FakeService]:
    svc = FakeService()
    set_mondo_service(svc)  # type: ignore[arg-type]
    try:
        yield svc
    finally:
        reset_mondo_service()


@pytest.fixture
async def tools(fake: FakeService) -> dict[str, Any]:
    mcp = FastMCP(name="mondo-link-e2e")
    register_discovery_tools(mcp)
    register_disease_tools(mcp)
    register_hierarchy_tools(mcp)
    register_xref_tools(mcp)
    register_batch_tools(mcp)
    return {t.name: t for t in await mcp.list_tools()}


async def _call(tools: dict[str, Any], name: str, **kwargs: Any) -> dict[str, Any]:
    return await tools[name].fn(**kwargs)


async def test_get_diagnostics(tools: dict[str, Any]) -> None:
    result = await _call(tools, "get_diagnostics")
    assert result["success"] is True
    assert isinstance(result["_meta"]["next_commands"], list)


async def test_get_server_capabilities(tools: dict[str, Any]) -> None:
    result = await _call(tools, "get_server_capabilities")
    assert result["success"] is True
    assert result["tool_count"] == len(TOOLS)
    assert isinstance(result["_meta"], dict)
    # The discovery root must still honour the universal next_commands invariant
    # (and its own per_call_meta contract, which lists next_commands as guaranteed).
    steps = result["_meta"]["next_commands"]
    assert isinstance(steps, list) and steps, "capabilities must carry a next step"
    assert all(s["tool"] in TOOLS for s in steps)
    assert steps[0]["tool"] == "resolve_disease"


async def test_resolve_disease(tools: dict[str, Any]) -> None:
    result = await _call(tools, "resolve_disease", query="Marfan")
    assert result["success"] is True
    assert result["mondo_id"] == _MONDO
    assert isinstance(result["_meta"]["next_commands"], list)
    assert result["_meta"]["next_commands"][0]["tool"] == "get_disease"


async def test_search_diseases(tools: dict[str, Any]) -> None:
    result = await _call(tools, "search_diseases", query="Marfan")
    assert result["success"] is True
    assert isinstance(result["_meta"]["next_commands"], list)


async def test_get_disease(tools: dict[str, Any]) -> None:
    result = await _call(tools, "get_disease", term=_MONDO)
    assert result["success"] is True
    assert isinstance(result["_meta"]["next_commands"], list)


async def test_get_disease_ancestors(tools: dict[str, Any]) -> None:
    result = await _call(tools, "get_disease_ancestors", term=_MONDO)
    assert result["success"] is True
    assert isinstance(result["_meta"]["next_commands"], list)


async def test_get_disease_descendants(tools: dict[str, Any]) -> None:
    result = await _call(tools, "get_disease_descendants", term=_MONDO)
    assert result["success"] is True
    assert isinstance(result["_meta"]["next_commands"], list)


async def test_get_disease_parents(tools: dict[str, Any]) -> None:
    result = await _call(tools, "get_disease_parents", term=_MONDO)
    assert result["success"] is True
    assert isinstance(result["_meta"]["next_commands"], list)


async def test_get_disease_children(tools: dict[str, Any]) -> None:
    result = await _call(tools, "get_disease_children", term=_MONDO)
    assert result["success"] is True
    assert isinstance(result["_meta"]["next_commands"], list)


async def test_resolve_xref(tools: dict[str, Any]) -> None:
    result = await _call(tools, "resolve_xref", xref_id="OMIM:182212")
    assert result["success"] is True
    assert isinstance(result["_meta"]["next_commands"], list)


async def test_map_cross_ontology(tools: dict[str, Any]) -> None:
    result = await _call(tools, "map_cross_ontology", term=_MONDO)
    assert result["success"] is True
    assert isinstance(result["_meta"]["next_commands"], list)


async def test_not_found_error_envelope(tools: dict[str, Any], fake: FakeService) -> None:
    fake.raise_not_found_on_get_disease = True
    result = await _call(tools, "get_disease", term="MONDO:9999999")
    assert result["success"] is False
    assert result["error_code"] == "not_found"
    assert isinstance(result["_meta"]["next_commands"], list)
