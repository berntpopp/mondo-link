"""CI guard: every tool's real output validates against its declared output_schema.

This is the systemic defense against the "leaked validation error" class of bug:
a grouped-by-prefix payload (``xrefs`` / ``mappings``) declared as an ``array``
makes FastMCP reject the tool's own output and surface a raw ``{...} is not of
type 'array'`` string to the client instead of a valid envelope. Each tool is
driven against the fixture-backed service -- across every response mode and
representative error cases -- and its returned dict is round-tripped through the
tool's ``output_schema`` with a JSON Schema validator. Drift fails CI here.
"""

from __future__ import annotations

from typing import Any

import pytest
from jsonschema import Draft202012Validator

from mondo_link.services.shaping import RESPONSE_MODES

pytestmark = pytest.mark.mcp

_SGS = "MONDO:0008426"  # Shprintzen-Goldberg syndrome (has OMIM/ORPHA/DOID xrefs)
_OBSOLETE = "MONDO:0099999"  # obsolete fixture term with a replacement
_MISSING = "MONDO:0000000"


@pytest.fixture
async def tool_map(facade: Any) -> dict[str, Any]:
    """Map every registered facade tool name to its live Tool object."""
    return {t.name: t for t in await facade.list_tools()}


async def _check(tool_map: dict[str, Any], name: str, **args: Any) -> dict[str, Any]:
    """Invoke a tool body and validate its payload against its output_schema."""
    tool = tool_map[name]
    payload = await tool.fn(**args)
    assert isinstance(payload, dict), f"{name} did not return a dict"
    Draft202012Validator(tool.output_schema).validate(payload)
    return payload


async def test_discovery_outputs_validate(tool_map: dict[str, Any]) -> None:
    assert (await _check(tool_map, "get_server_capabilities"))["success"] is True
    assert (await _check(tool_map, "get_server_capabilities", detail="full"))["success"] is True
    assert (await _check(tool_map, "get_diagnostics"))["success"] is True


async def test_resolve_disease_outputs_validate(tool_map: dict[str, Any]) -> None:
    ok = await _check(tool_map, "resolve_disease", query="Shprintzen-Goldberg syndrome")
    assert ok["success"] is True
    err = await _check(tool_map, "resolve_disease", query=_MISSING)
    assert err["success"] is False


async def test_search_outputs_validate_all_modes(tool_map: dict[str, Any]) -> None:
    for mode in RESPONSE_MODES:
        ok = await _check(tool_map, "search_diseases", query="syndrome", response_mode=mode)
        assert ok["success"] is True
    err = await _check(tool_map, "search_diseases", query="   ")
    assert err["success"] is False


async def test_get_disease_outputs_validate_all_modes(tool_map: dict[str, Any]) -> None:
    # The grouped ``xrefs`` payload is the historical leak site -- check every mode.
    for mode in RESPONSE_MODES:
        ok = await _check(tool_map, "get_disease", term=_SGS, response_mode=mode)
        assert ok["success"] is True
    missing = await _check(tool_map, "get_disease", term=_MISSING)
    assert missing["success"] is False
    obsolete = await _check(tool_map, "get_disease", term=_OBSOLETE)
    assert obsolete["success"] is False
    assert obsolete.get("obsolete") is True


async def test_hierarchy_outputs_validate(tool_map: dict[str, Any]) -> None:
    for mode in RESPONSE_MODES:
        await _check(tool_map, "get_disease_ancestors", term=_SGS, limit=1, response_mode=mode)
        await _check(tool_map, "get_disease_descendants", term=_SGS, limit=1, response_mode=mode)
    await _check(tool_map, "get_disease_parents", term=_SGS)
    await _check(tool_map, "get_disease_children", term=_SGS)


async def test_xref_outputs_validate(tool_map: dict[str, Any]) -> None:
    ok = await _check(tool_map, "resolve_xref", xref_id="OMIM:182212")
    assert ok["success"] is True
    err = await _check(tool_map, "resolve_xref", xref_id="not-a-curie")
    assert err["success"] is False


async def test_map_cross_ontology_outputs_validate_all_modes(tool_map: dict[str, Any]) -> None:
    # The grouped ``mappings`` payload is the P0 leak site -- check every mode.
    for mode in RESPONSE_MODES:
        ok = await _check(tool_map, "map_cross_ontology", term=_SGS, response_mode=mode)
        assert ok["success"] is True
    err = await _check(tool_map, "map_cross_ontology", term=_MISSING)
    assert err["success"] is False


async def test_batch_outputs_validate(tool_map: dict[str, Any]) -> None:
    # Partial-success (valid + missing item), the over-cap invalid_input error, and
    # a get_disease_batch row carrying a grouped ``xrefs`` object must all validate.
    for mode in RESPONSE_MODES:
        ok = await _check(
            tool_map,
            "resolve_disease_batch",
            queries=["Shprintzen-Goldberg syndrome", _MISSING],
            response_mode=mode,
        )
        assert ok["success"] is True and ok["count"] == 2
        assert ok["results"][0]["ok"] is True and ok["results"][1]["ok"] is False
    capped = await _check(tool_map, "resolve_disease_batch", queries=["x"] * 51)
    assert capped["success"] is False and capped["error_code"] == "invalid_input"
    for mode in RESPONSE_MODES:
        got = await _check(
            tool_map, "get_disease_batch", terms=[_SGS, _MISSING], response_mode=mode
        )
        assert got["results"][0]["ok"] is True and got["results"][1]["ok"] is False


def test_untrusted_text_schema_rejects_malformed_objects() -> None:
    # The fenced-field schema must be STRICT: kind is a const and the full v1.1
    # shape is required, so a definition object missing any field (which would
    # otherwise pass under additionalProperties) fails validation.
    from mondo_link.mcp.schemas import _UNTRUSTED_TEXT, _UNTRUSTED_TEXT_NULL

    good = {
        "kind": "untrusted_text",
        "text": "A disease.",
        "provenance": {
            "source": "mondo",
            "record_id": "MONDO:0007739",
            "retrieved_at": "2026-07-11T00:00:00+00:00",
        },
        "raw_sha256": "0" * 64,
    }
    validator = Draft202012Validator(_UNTRUSTED_TEXT)
    assert validator.is_valid(good)
    assert not validator.is_valid({k: v for k, v in good.items() if k != "kind"})
    assert not validator.is_valid({**good, "kind": "trusted"})  # const enforced
    assert not validator.is_valid({k: v for k, v in good.items() if k != "raw_sha256"})
    assert not validator.is_valid({**good, "provenance": {"source": "mondo"}})

    null_validator = Draft202012Validator(_UNTRUSTED_TEXT_NULL)
    assert null_validator.is_valid(None)  # nullable variant still accepts null
    assert not null_validator.is_valid({k: v for k, v in good.items() if k != "kind"})
