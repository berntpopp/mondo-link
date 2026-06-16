"""Unit tests for the Mondo OBO + SSSOM parser."""

from __future__ import annotations

from pathlib import Path

import pytest

from mondo_link.constants import MONDO_ROOT
from mondo_link.ingest import parser

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


@pytest.fixture
def obo_text() -> str:
    return (FIXTURES / "mondo.obo").read_text(encoding="utf-8")


@pytest.fixture
def terms(obo_text: str) -> dict[str, dict]:
    return parser.parse_mondo_obo(obo_text)


@pytest.fixture
def sssom_text() -> str:
    return (FIXTURES / "mondo.sssom.tsv").read_text(encoding="utf-8")


def test_parse_header(obo_text: str) -> None:
    header = parser.parse_obo_header(obo_text)
    assert header["data_version"] == "mondo/releases/2026-06-01/mondo.owl"
    assert header["date"] == "01:06:2026 00:00"


def test_term_count_and_root(terms: dict[str, dict]) -> None:
    assert MONDO_ROOT in terms
    root = terms[MONDO_ROOT]
    assert root["name"] == "disease or disorder"
    assert root["parents"] == []
    assert root["obsolete"] is False


def test_term_fields_and_definition(terms: dict[str, dict]) -> None:
    sgs = terms["MONDO:0008426"]
    assert sgs["id"] == "MONDO:0008426"
    assert sgs["name"] == "Shprintzen-Goldberg syndrome"
    # def: quotes + trailing [refs] stripped
    assert sgs["definition"].startswith("Shprintzen-Goldberg syndrome (SGS)")
    assert "[Orphanet:2462]" not in sgs["definition"]
    assert not sgs["definition"].startswith('"')


def test_multi_parent(terms: dict[str, dict]) -> None:
    sgs = terms["MONDO:0008426"]
    assert set(sgs["parents"]) == {"MONDO:0015159", "MONDO:0017310"}


def test_obsolete_replaced_consider(terms: dict[str, dict]) -> None:
    obs = terms["MONDO:0099999"]
    assert obs["obsolete"] is True
    assert obs["replaced_by"] == "MONDO:0008426"
    assert obs["consider"] == ["MONDO:0000003"]


def test_synonyms_scope_type_sources(terms: dict[str, dict]) -> None:
    syns = {s["text"]: s for s in terms["MONDO:0008426"]["synonyms"]}
    sgs = syns["SGS"]
    assert sgs["scope"] == "EXACT"
    assert sgs["type"] == "ABBREVIATION"
    assert sgs["sources"] == ["OMIM:182212"]
    plain = syns["Marfanoid craniosynostosis syndrome"]
    assert plain["scope"] == "EXACT"
    assert plain["type"] is None
    rel = syns["marfanoid disorder"]
    assert rel["scope"] == "RELATED"
    assert rel["sources"] == ["GARD:1"]


def test_subsets(terms: dict[str, dict]) -> None:
    assert terms["MONDO:0008426"]["subsets"] == ["clingen"]


def test_xref_prefix_normalization_and_predicate(terms: dict[str, dict]) -> None:
    xrefs = {x["object_id"]: x for x in terms["MONDO:0008426"]["xrefs"]}
    # Orphanet -> ORPHA prefix normalization
    orpha = xrefs["ORPHA:2462"]
    assert orpha["prefix"] == "ORPHA"
    assert orpha["predicate"] == "equivalentTo"
    omim = xrefs["OMIM:182212"]
    assert omim["prefix"] == "OMIM"
    assert omim["predicate"] == "equivalentTo"
    # bare xref without {MONDO:equivalentTo} -> predicate "xref"
    doid = xrefs["DOID:0050776"]
    assert doid["prefix"] == "DOID"
    assert doid["predicate"] == "xref"


def test_only_mondo_ids_parsed(terms: dict[str, dict]) -> None:
    assert all(k.startswith("MONDO:") for k in terms)


def test_closure_self_pair_and_transitive(terms: dict[str, dict]) -> None:
    pairs = set(parser.mondo_closure_pairs(terms))
    # self-pair
    assert ("MONDO:0008426", "MONDO:0008426") in pairs
    # both direct parents
    assert ("MONDO:0008426", "MONDO:0015159") in pairs
    assert ("MONDO:0008426", "MONDO:0017310") in pairs
    # transitive to root via both parents
    assert ("MONDO:0008426", "MONDO:0000004") in pairs
    assert ("MONDO:0008426", "MONDO:0000002") in pairs
    assert ("MONDO:0008426", MONDO_ROOT) in pairs


def test_top_groupings(terms: dict[str, dict]) -> None:
    groupings = parser.mondo_top_groupings(terms)
    ids = [g[0] for g in groupings]
    names = [g[1] for g in groupings]
    assert set(ids) == {"MONDO:0000002", "MONDO:0000003", "MONDO:0000004"}
    # sorted by name
    assert names == sorted(names)
    # order index assigned
    assert [g[2] for g in groupings] == list(range(len(groupings)))


def test_sssom_predicate_and_normalization(sssom_text: str) -> None:
    rows = list(parser.parse_mondo_sssom(sssom_text))
    assert len(rows) == 3
    by_obj = {r["object_id"]: r for r in rows}
    assert by_obj["OMIM:182212"]["predicate"] == "exactMatch"
    assert by_obj["NCIT:C124840"]["predicate"] == "closeMatch"
    # Orphanet -> ORPHA normalization
    assert "ORPHA:2462" in by_obj
    assert by_obj["ORPHA:2462"]["predicate"] == "exactMatch"
    assert all(r["subject_id"].startswith("MONDO:") for r in rows)
    # source = mapping_justification
    assert by_obj["OMIM:182212"]["source"] == "semapv:LexicalMatching"
