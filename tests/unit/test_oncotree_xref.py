"""ONCOTREE is a first-class xref source: resolve ONCOTREE codes and filter by them."""

from __future__ import annotations

from typing import Any

_SKCM = "MONDO:0005012"


async def test_resolve_oncotree_code(facade: Any, structured: Any) -> None:
    payload = structured(await facade.call_tool("resolve_disease", {"query": "ONCOTREE:SKCM"}))
    assert payload["success"] is True
    assert payload["result"]["mondo_id"] == _SKCM


async def test_map_cross_ontology_filters_by_oncotree(facade: Any, structured: Any) -> None:
    payload = structured(
        await facade.call_tool("map_cross_ontology", {"term": _SKCM, "prefixes": ["ONCOTREE"]})
    )
    assert payload["success"] is True
    assert set(payload["result"]) == {"ONCOTREE"}
    assert payload["result"]["ONCOTREE"][0]["object_id"] == "ONCOTREE:SKCM"
