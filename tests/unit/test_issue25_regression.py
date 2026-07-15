"""Regression tests for issue #25 (live MCP audit): the two HIGH defects and friends.

Written BEFORE the fix and watched to fail:

- D1 search_diseases ranks veterinary (non-human-animal) terms above the canonical
  human disease -> the exact human primary-label match must lead.
- D2 resolver candidates (ambiguous / replaced_by / suggestions) carry bare MONDO ids
  with NO names -> every candidate must carry its trusted DB ``name``.
- D9 a malformed MONDO id is reported as ``not_found`` -> it must be ``invalid_input``
  with ``field: "term"``, distinct from a well-formed-but-absent id.
- fleet: an error envelope must carry MCP ``isError: true``.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from mondo_link.constants import NON_HUMAN_ANIMAL_ROOT
from mondo_link.data.repository import MondoRepository
from mondo_link.exceptions import AmbiguousQueryError, NotFoundError, WithdrawnEntryError
from mondo_link.ingest.schema import load_schema_sql
from mondo_link.mcp.facade import create_mondo_mcp
from mondo_link.mcp.service_adapters import reset_mondo_service, set_mondo_service

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


class _AmbiguousService:
    """A service stand-in whose resolve raises an ambiguity with named candidates."""

    def resolve_disease(self, *_a: Any, **_k: Any) -> dict[str, Any]:
        raise AmbiguousQueryError(
            "'hypotonia' matches 3 Mondo terms; pick one and call get_disease.",
            candidates=[
                {"mondo_id": "MONDO:0015021", "name": "hypotonia", "label_type": "primary"},
                {
                    "mondo_id": "MONDO:0013004",
                    "name": "benign congenital hypotonia",
                    "label_type": "x",
                },
                {
                    "mondo_id": "MONDO:0030025",
                    "name": "hypotonia, infantile, X-linked",
                    "label_type": "x",
                },
            ],
        )


class _ObsoleteService:
    """A service stand-in whose resolve raises a withdrawn/obsolete term with successors."""

    def resolve_disease(self, *_a: Any, **_k: Any) -> dict[str, Any]:
        raise WithdrawnEntryError(
            "MONDO:0016578",
            status="obsolete",
            replaced_by=[{"mondo_id": "MONDO:0016387", "name": "leukemia"}],
        )


@pytest.fixture
def install_service() -> Any:
    def _make(svc: Any) -> Any:
        set_mondo_service(svc)
        return create_mondo_mcp()

    yield _make
    reset_mondo_service()


async def test_ambiguous_candidates_carry_trusted_db_name(install_service: Any) -> None:
    mcp = install_service(_AmbiguousService())
    result = await mcp.call_tool("resolve_disease", {"query": "hypotonia"})
    # fleet: an error envelope MUST carry MCP isError:true.
    assert result.is_error is True
    env = result.structured_content
    assert env["error_code"] == "ambiguous_query"
    assert env["candidates"], "candidates must be present"
    for cand in env["candidates"]:
        assert cand.get("name"), f"candidate {cand} must carry a trusted DB name"
    names = {c["name"] for c in env["candidates"]}
    assert "hypotonia" in names


async def test_obsolete_replaced_by_carries_name(install_service: Any) -> None:
    mcp = install_service(_ObsoleteService())
    result = await mcp.call_tool("resolve_disease", {"query": "MONDO:0016578"})
    assert result.is_error is True
    env = result.structured_content
    assert env["error_code"] == "not_found"
    assert env["replaced_by"], "replaced_by must be present"
    assert env["replaced_by"][0].get("name") == "leukemia"


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
    from mondo_link.mcp.envelope import classify_exception

    assert classify_exception(NotFoundError("x"))[0] == "not_found"
