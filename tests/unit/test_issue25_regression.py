"""Regression tests for issue #25 (live MCP audit): the two HIGH defects and friends.

Written BEFORE the fix and watched to fail:

- D1 search_diseases ranks veterinary (non-human-animal) terms above the canonical
  human disease -> the exact human primary-label match must lead.
- D2 resolver candidates (ambiguous / replaced_by / suggestions) carry bare MONDO ids
  with NO names -> every candidate must carry its trusted DB ``name``.
- D9 a malformed MONDO id is reported as ``not_found`` -> it must be ``invalid_input``
  with ``field: "term"``, distinct from a well-formed-but-absent id.
- fleet: an error envelope must carry MCP ``isError: true`` (raised OR returned); the
  error_code is coerced onto the closed enum; a blank/unknown prefix is invalid_input.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastmcp.tools.tool import ToolResult

from mondo_link.constants import NON_HUMAN_ANIMAL_ROOT
from mondo_link.data.repository import MondoRepository
from mondo_link.exceptions import InvalidInputError, NotFoundError
from mondo_link.ingest.schema import load_schema_sql
from mondo_link.mcp.envelope import (
    McpErrorContext,
    McpToolError,
    canon_error_code,
    classify_exception,
    run_mcp_tool,
)

# -- D1: search ranking (veterinary terms outrank the human disease) ----------

_HUMAN_CF = "MONDO:0009061"  # cystic fibrosis (human) -- rich: synonyms + long definition
_PIG_CF = "MONDO:1010544"  # cystic fibrosis, pig -- light: name only, descends from vet root


def _build_cf_db(path: Path) -> None:
    """A DB where the human 'cystic fibrosis' is a heavier FTS doc than the pig variant.

    That length asymmetry is exactly what inverts raw bm25: the well-annotated human
    term (three synonyms + a paragraph definition) sinks below the bare veterinary term
    for the query "cystic fibrosis", which is the defect this fixture reproduces.
    """
    conn = sqlite3.connect(path)
    try:
        conn.executescript(load_schema_sql())
        long_def = (
            "Cystic fibrosis is an autosomal recessive multisystem disease affecting the "
            "lungs, pancreas, liver, intestine and reproductive tract, caused by pathogenic "
            "variants in the CFTR chloride channel gene and characterised by chronic "
            "respiratory infection, pancreatic insufficiency and elevated sweat chloride."
        )
        terms = [
            (NON_HUMAN_ANIMAL_ROOT, "non-human animal disease", None, [], []),
            (
                _HUMAN_CF,
                "cystic fibrosis",
                long_def,
                [
                    {"text": "mucoviscidosis", "scope": "EXACT", "type": None, "sources": []},
                    {"text": "CF", "scope": "EXACT", "type": None, "sources": []},
                    {
                        "text": "cystic fibrosis of pancreas",
                        "scope": "RELATED",
                        "type": None,
                        "sources": [],
                    },
                ],
                [],
            ),
            (_PIG_CF, "cystic fibrosis, pig", None, [], []),
        ]
        conn.executemany(
            "INSERT INTO term (mondo_id, name, name_upper, definition, is_obsolete, "
            "replaced_by, synonyms, subsets, consider) VALUES (?, ?, ?, ?, 0, NULL, ?, ?, ?)",
            [
                (mid, name, name.upper(), d, json.dumps(syn), json.dumps(sub), json.dumps([]))
                for mid, name, d, syn, sub in terms
            ],
        )
        conn.executemany(
            "INSERT INTO term_fts (mondo_id, name, synonyms, definition) VALUES (?, ?, ?, ?)",
            [
                (NON_HUMAN_ANIMAL_ROOT, "non-human animal disease", "", ""),
                (
                    _HUMAN_CF,
                    "cystic fibrosis",
                    "mucoviscidosis CF cystic fibrosis of pancreas",
                    long_def,
                ),
                (_PIG_CF, "cystic fibrosis, pig", "", ""),
            ],
        )
        # closure carries self-pairs; the pig term descends from the non-human root.
        conn.executemany(
            "INSERT INTO mondo_closure (mondo_id, ancestor_id) VALUES (?, ?)",
            [
                (NON_HUMAN_ANIMAL_ROOT, NON_HUMAN_ANIMAL_ROOT),
                (_HUMAN_CF, _HUMAN_CF),
                (_PIG_CF, _PIG_CF),
                (_PIG_CF, NON_HUMAN_ANIMAL_ROOT),
            ],
        )
        conn.execute(
            "INSERT INTO meta (id, schema_version, mondo_version, build_utc) "
            "VALUES (1, 2, '2026-06-01', '2026-06-01T00:00:00+00:00')"
        )
        conn.commit()
    finally:
        conn.close()


def test_search_ranks_exact_human_match_above_veterinary_term(tmp_path: Path) -> None:
    db = tmp_path / "cf.sqlite"
    _build_cf_db(db)
    repo = MondoRepository(db)
    try:
        hits, _total = repo.search("cystic fibrosis", limit=10, include_obsolete=False)
        ids = [h["mondo_id"] for h in hits]
        assert _HUMAN_CF in ids and _PIG_CF in ids
        # The exact human primary-label match must lead; the veterinary variant must
        # rank below it (the defect had the pig term at rank 0 and the human at rank 9).
        assert ids[0] == _HUMAN_CF, f"human term must rank first, got order {ids}"
        assert ids.index(_HUMAN_CF) < ids.index(_PIG_CF)
    finally:
        repo.close()


# -- D2 + isError: resolver candidates carry names; errors carry isError -------
# These drive the REAL DB-backed facade (conftest ``facade``). That is essential: a
# candidate ``name`` is re-derived from the DB by the validated id, NEVER copied from the
# exception (see the security test in test_error_leak_fencing.py), so only a real repo
# can supply the trusted label.


async def test_ambiguous_candidates_carry_trusted_db_name(facade: Any) -> None:
    # "shared ambiguous disorder" is an EXACT synonym of two fixture terms.
    result = await facade.call_tool("resolve_disease", {"query": "shared ambiguous disorder"})
    assert result.is_error is True  # fleet: an error envelope MUST carry MCP isError:true
    env = result.structured_content
    assert env["error_code"] == "ambiguous_query"
    assert env["candidates"], "candidates must be present"
    names = {c.get("name") for c in env["candidates"]}
    # every candidate carries its trusted DB label, re-derived from the id
    assert all(c.get("name") for c in env["candidates"]), env["candidates"]
    assert names == {"cardiovascular disorder", "nervous system disorder"}
    # the caller's query text is NOT echoed as a candidate name
    assert "shared ambiguous disorder" not in names


async def test_obsolete_replaced_by_carries_name(facade: Any) -> None:
    # MONDO:0099999 is obsolete in the fixture, replaced_by MONDO:0008426.
    result = await facade.call_tool("resolve_disease", {"query": "MONDO:0099999"})
    assert result.is_error is True
    env = result.structured_content
    assert env["error_code"] == "not_found"
    assert env["replaced_by"], "replaced_by must be present"
    assert env["replaced_by"][0]["mondo_id"] == "MONDO:0008426"
    assert env["replaced_by"][0].get("name") == "Shprintzen-Goldberg syndrome"


# -- D9: malformed MONDO id -> invalid_input (not not_found) -------------------


async def test_malformed_mondo_id_is_invalid_input_not_not_found(facade: Any) -> None:
    result = await facade.call_tool("get_disease", {"term": "MONDO:abcxyz"})
    assert result.is_error is True
    env = result.structured_content
    assert env["error_code"] == "invalid_input", (
        f"a syntactically invalid MONDO id must be invalid_input, got {env['error_code']!r}"
    )
    assert env.get("field") == "term"


async def test_wellformed_but_absent_mondo_id_stays_not_found(facade: Any) -> None:
    # The contrast case: a grammar-valid id that simply does not exist is not_found,
    # so the two repairs (fix the syntax vs pick a real id) stay distinguishable.
    result = await facade.call_tool("get_disease", {"term": "MONDO:9999999"})
    assert result.is_error is True
    assert result.structured_content["error_code"] == "not_found"


async def test_missing_mondo_id_not_found_is_actually_notfound() -> None:
    # Guard the classifier itself: NotFoundError stays not_found (no accidental remap).
    assert classify_exception(NotFoundError("x"))[0] == "not_found"


# -- Codex review #2: error_code is coerced onto the CLOSED enum at the emit point ----


def test_canon_error_code_folds_legacy_codes_onto_the_closed_enum() -> None:
    assert canon_error_code("data_unavailable") == "upstream_unavailable"
    assert canon_error_code("internal_error") == "internal"
    assert canon_error_code("something_made_up") == "internal"
    for code in ("invalid_input", "not_found", "ambiguous_query", "rate_limited"):
        assert canon_error_code(code) == code


async def test_mcp_tool_error_with_legacy_code_is_coerced_not_leaked() -> None:
    # A McpToolError raised at runtime with a NON-canonical code must not reach the wire:
    # it is folded onto the closed enum (data_unavailable -> upstream_unavailable) with the
    # matching recovery, and classify_exception (the batch path) agrees.
    exc = McpToolError(error_code="internal", message="x")
    object.__setattr__(exc, "error_code", "data_unavailable")  # simulate a runtime leak
    assert classify_exception(exc)[0] == "upstream_unavailable"
    result = await run_mcp_tool("get_disease", _raise(exc), context=McpErrorContext("get_disease"))
    assert isinstance(result, ToolResult) and result.is_error is True
    assert result.structured_content["error_code"] == "upstream_unavailable"


# -- Codex review #3: a RETURNED error envelope (not raised) must carry isError -------


async def test_returned_error_envelope_gets_iserror_at_the_chokepoint() -> None:
    async def returns_error() -> dict[str, Any]:
        # a body that RETURNS (does not raise) a success:false envelope
        return {"success": False, "error_code": "not_found", "message": "nope"}

    result = await run_mcp_tool(
        "get_disease", returns_error, context=McpErrorContext("get_disease")
    )
    assert isinstance(result, ToolResult), "a returned error envelope must become a ToolResult"
    assert result.is_error is True
    assert result.structured_content["success"] is False


async def test_returned_success_envelope_stays_a_plain_dict() -> None:
    async def returns_ok() -> dict[str, Any]:
        return {"mondo_id": "MONDO:0008426"}

    result = await run_mcp_tool("get_disease", returns_ok, context=McpErrorContext("get_disease"))
    assert isinstance(result, dict) and result["success"] is True


def _raise(exc: BaseException) -> Any:
    async def call() -> dict[str, Any]:
        raise exc

    return call


# -- Codex review #5: a blank/unknown prefix is invalid_input, never a no-op ----------


async def test_blank_prefix_is_invalid_input_not_all_mappings(facade: Any) -> None:
    # prefixes=[" "] must NOT strip to [] and silently return every source.
    result = await facade.call_tool(
        "map_cross_ontology", {"term": "MONDO:0008426", "prefixes": [" "]}
    )
    assert result.is_error is True
    assert result.structured_content["error_code"] == "invalid_input"
    # the schema-level enum rejects the blank item; the field names the prefixes param
    # (the loc carries the failing item index, e.g. "prefixes.0")
    assert str(result.structured_content.get("field")).startswith("prefixes")


async def test_unknown_prefix_is_invalid_input(facade: Any) -> None:
    result = await facade.call_tool(
        "map_cross_ontology", {"term": "MONDO:0008426", "prefixes": ["__BOGUS__"]}
    )
    assert result.is_error is True
    assert result.structured_content["error_code"] == "invalid_input"


def test_service_blank_prefix_validated_before_stripping(service: Any) -> None:
    # Direct service call (bypasses the schema enum): the guard must validate the RAW
    # values before stripping, so a whitespace prefix raises rather than becoming a no-op.
    with pytest.raises(InvalidInputError) as exc:
        service.map_cross_ontology("MONDO:0008426", prefixes=[" "])
    assert exc.value.field == "prefixes"


def test_service_valid_first_class_prefix_still_filters(service: Any) -> None:
    # A first-class prefix still filters (the service returns a bare payload, no envelope).
    out = service.map_cross_ontology("MONDO:0008426", prefixes=["OMIM"])
    assert set(out["mappings"]) <= {"OMIM"}
    assert out["count"] >= 1


# -- gate: response_mode=minimal narrows a record's collections, never deletes them ---
# and fields=[unknown] is rejected (the 791363c gate now sees get_disease's grouped
# xrefs as a collection, so both would otherwise read as payload destruction).


async def test_get_disease_minimal_preserves_the_xrefs_collection(facade: Any) -> None:
    result = await facade.call_tool(
        "get_disease", {"term": "MONDO:0008426", "response_mode": "minimal"}
    )
    env = result.structured_content
    assert env["success"] is True
    # the grouped xrefs collection survives (narrowed to object_id), not deleted
    assert env["xrefs"], "minimal must keep the xrefs collection"
    for rows in env["xrefs"].values():
        for entry in rows:
            assert set(entry) <= {"object_id"}


async def test_get_disease_unknown_field_is_invalid_input(facade: Any) -> None:
    result = await facade.call_tool(
        "get_disease", {"term": "MONDO:0008426", "fields": ["__bogus__"]}
    )
    assert result.is_error is True
    env = result.structured_content
    assert env["error_code"] == "invalid_input"
    assert env.get("field") == "fields"
