"""End-to-end tests through the real FastMCP facade over the fixture index.

These exercise the full path: ingest-built SQLite -> MondoRepository ->
MondoService -> envelope -> FastMCP facade (with the arg-validation middleware).
"""

from __future__ import annotations

from typing import Any

import pytest

from mondo_link.mcp.capabilities import TOOLS

pytestmark = pytest.mark.mcp

_SGS = "MONDO:0008426"


async def test_facade_registers_exactly_the_frozen_tools(facade: Any) -> None:
    registered = {t.name for t in await facade.list_tools()}
    assert registered == set(TOOLS)


async def test_resolve_disease_by_label(facade: Any, structured: Any) -> None:
    payload = structured(
        await facade.call_tool("resolve_disease", {"query": "Shprintzen-Goldberg syndrome"})
    )
    assert payload["success"] is True
    assert payload["mondo_id"] == _SGS
    assert payload["match_type"] == "primary"
    assert "2026-06-01" in payload["mondo_version"]
    assert payload["_meta"]["next_commands"][0]["tool"] == "get_disease"


async def test_resolve_disease_alias_rewrite(facade: Any, structured: Any) -> None:
    # 'disease' is an accepted alias for the canonical 'query' argument.
    payload = structured(
        await facade.call_tool("resolve_disease", {"disease": "Shprintzen-Goldberg syndrome"})
    )
    assert payload["success"] is True
    assert payload["mondo_id"] == _SGS


async def test_get_disease_full_record(facade: Any, structured: Any) -> None:
    payload = structured(await facade.call_tool("get_disease", {"term": _SGS}))
    assert payload["success"] is True
    assert payload["mondo_id"] == _SGS
    assert len(payload["parents"]) >= 2  # multi-parent term
    prefixes = set(payload["xrefs"])
    assert {"OMIM", "ORPHA", "DOID"} <= prefixes
    assert "2026-06-01" in payload["mondo_version"]


async def test_get_disease_by_xref(facade: Any, structured: Any) -> None:
    payload = structured(await facade.call_tool("get_disease", {"term": "OMIM:182212"}))
    assert payload["success"] is True
    assert payload["mondo_id"] == _SGS


async def test_ancestors_reach_root(facade: Any, structured: Any) -> None:
    payload = structured(await facade.call_tool("get_disease_ancestors", {"term": _SGS}))
    ids = {a["mondo_id"] for a in payload["ancestors"]}
    assert "MONDO:0000001" in ids


async def test_resolve_xref_to_mondo(facade: Any, structured: Any) -> None:
    payload = structured(await facade.call_tool("resolve_xref", {"xref_id": "OMIM:182212"}))
    assert payload["success"] is True
    assert any(m["mondo_id"] == _SGS for m in payload["matches"])


async def test_map_cross_ontology_groups_by_prefix(facade: Any, structured: Any) -> None:
    payload = structured(await facade.call_tool("map_cross_ontology", {"term": _SGS}))
    assert payload["success"] is True
    assert "OMIM" in payload["mappings"]
    assert payload["count"] == sum(len(v) for v in payload["mappings"].values())


async def test_get_disease_sparse_fieldset(facade: Any, structured: Any) -> None:
    payload = structured(
        await facade.call_tool("get_disease", {"term": _SGS, "fields": ["xrefs.OMIM"]})
    )
    assert payload["success"] is True
    # anchors retained, only the requested xref group kept, definition projected out
    assert payload["mondo_id"] == _SGS
    assert set(payload["xrefs"]) == {"OMIM"}
    assert "definition" not in payload
    assert "parents" not in payload


async def test_obsolete_term_is_withdrawn(facade: Any, structured: Any) -> None:
    payload = structured(await facade.call_tool("get_disease", {"term": "MONDO:0099999"}))
    assert payload["success"] is False
    assert payload["error_code"] == "not_found"
    assert payload.get("obsolete") is True
    assert payload["replaced_by"]
    assert payload["_meta"]["next_commands"]


async def test_unknown_id_not_found(facade: Any, structured: Any) -> None:
    payload = structured(await facade.call_tool("get_disease", {"term": "MONDO:0000000"}))
    assert payload["success"] is False
    assert payload["error_code"] == "not_found"


async def test_label_miss_embeds_top_hit_in_next_commands(facade: Any, structured: Any) -> None:
    # "Shprintzen" is a partial label (no exact lookup) but FTS finds SGS: the
    # not_found envelope must carry candidates AND chain to get_disease(top hit).
    payload = structured(await facade.call_tool("get_disease", {"term": "Shprintzen"}))
    assert payload["success"] is False
    assert payload["error_code"] == "not_found"
    assert payload["candidates"][0]["mondo_id"] == _SGS
    first = payload["_meta"]["next_commands"][0]
    assert first["tool"] == "get_disease"
    assert first["arguments"]["term"] == _SGS


async def test_capabilities_lists_all_tools(facade: Any, structured: Any) -> None:
    payload = structured(await facade.call_tool("get_server_capabilities", {}))
    assert payload["tool_count"] == len(TOOLS)
    assert "2026-06-01" in (payload["mondo_version"] or "")


async def test_every_meta_echoes_capabilities_version(facade: Any, structured: Any) -> None:
    from mondo_link.mcp.capabilities import capabilities_version

    version = capabilities_version()
    assert version
    for tool, args in (
        ("resolve_disease", {"query": "Shprintzen-Goldberg syndrome"}),
        ("get_disease", {"term": _SGS}),
        ("get_disease", {"term": "MONDO:0000000"}),  # error envelope echoes it too
    ):
        payload = structured(await facade.call_tool(tool, args))
        assert payload["_meta"]["capabilities_version"] == version


async def test_diagnostics_reports_built_index(facade: Any, structured: Any) -> None:
    payload = structured(await facade.call_tool("get_diagnostics", {}))
    assert payload["index_built"] is True
    assert "build" in payload


async def test_diagnostics_reports_runtime_metrics(facade: Any, structured: Any) -> None:
    from mondo_link.mcp import metrics

    metrics.reset()
    await facade.call_tool("resolve_disease", {"query": "Shprintzen-Goldberg syndrome"})
    await facade.call_tool("get_disease", {"term": "MONDO:0000000"})  # one error
    payload = structured(await facade.call_tool("get_diagnostics", {}))
    runtime = payload["runtime"]
    assert runtime["requests"] >= 2
    assert runtime["errors"] >= 1
    assert "p95" in runtime["latency_ms"]
    assert "resolve_disease" in runtime["per_tool"]
