"""Mondo germline-basis genes (issue #127): parse, serve, degrade, chain to HGNC."""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from mondo_link.data.repository import MondoRepository
from mondo_link.ingest.parser import parse_mondo_obo
from mondo_link.mcp import next_commands as nc
from mondo_link.services.mondo_service import MondoService

SGS = "MONDO:0008426"  # Shprintzen-Goldberg syndrome -> SKI (fixture)
SKI = {
    "hgnc_id": "HGNC:10896",
    "symbol": "SKI",
    "source": "Mondo has_material_basis_in_germline_mutation_in",
}


def test_parser_keeps_hgnc_genes_deduplicated() -> None:
    obo = (
        "[Term]\nid: MONDO:0000208\nname: x\n"
        "intersection_of: has_material_basis_in_germline_mutation_in "
        "http://identifiers.org/hgnc/1 ! ONLYDEF\n"
        "intersection_of: has_material_basis_in_germline_mutation_in "
        "http://identifiers.org/hgnc/11771 ! TGFBI\n"
        "relationship: has_material_basis_in_germline_mutation_in "
        'http://identifiers.org/hgnc/28403 {source="OMIM:616033"} ! TRMT10A\n'
        "relationship: has_material_basis_in_germline_mutation_in "
        "http://identifiers.org/hgnc/11771 ! TGFBI\n"
        "relationship: has_material_basis_in_germline_mutation_in "
        'http://identifiers.org/ncbigene/403811 {source="OMIA:1"} ! KIT\n'
        "relationship: excluded_subClassOf MONDO:0000001 ! disease\n"
    )
    genes = parse_mondo_obo(obo)["MONDO:0000208"]["causal_genes"]
    assert genes == [("HGNC:1", "ONLYDEF"), ("HGNC:11771", "TGFBI"), ("HGNC:28403", "TRMT10A")]


@pytest.mark.parametrize("mode", ["compact", "standard", "full"])
def test_get_disease_serves_causal_genes(service: MondoService, mode: str) -> None:
    assert service.get_disease(SGS, response_mode=mode)["causal_genes"] == [SKI]


def test_minimal_keeps_gene_identifiers(service: MondoService) -> None:
    genes = service.get_disease(SGS, response_mode="minimal")["causal_genes"]
    assert genes == [{"hgnc_id": "HGNC:10896", "symbol": "SKI"}]


def test_resolve_disease_serves_causal_genes(service: MondoService) -> None:
    assert service.resolve_disease("Shprintzen-Goldberg syndrome")["causal_genes"] == [SKI]


@pytest.mark.parametrize("mode", ["minimal", "compact", "standard", "full"])
def test_no_gene_disease_returns_empty_list(service: MondoService, mode: str) -> None:
    # [] = "Mondo asserts no germline causal gene", distinct from the field being absent.
    assert service.get_disease("MONDO:0005012", response_mode=mode)["causal_genes"] == []
    assert service.resolve_disease("MONDO:0005012", response_mode=mode)["causal_genes"] == []


@pytest.mark.parametrize("term", ["MONDO:0017310", "MONDO:0015159"])
def test_umbrella_term_lists_descendant_genes(service: MondoService, term: str) -> None:
    # Issue #136: an umbrella's genes sit on its children; list them, marked by child.
    rolled = [{**SKI, "from_descendant": SGS}]
    assert service.get_disease(term)["causal_genes"] == rolled
    assert service.resolve_disease(term)["causal_genes"] == rolled
    minimal = service.get_disease(term, response_mode="minimal")["causal_genes"]
    assert minimal == [{"hgnc_id": "HGNC:10896", "symbol": "SKI", "from_descendant": SGS}]


def test_umbrella_over_the_cap_stays_empty(
    service: MondoService, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A broad grouping is not a gene list: above the cap, [] + the absence note path.
    monkeypatch.setattr("mondo_link.services.mondo_service.DESCENDANT_GENE_CAP", 0)
    assert service.get_disease("MONDO:0017310")["causal_genes"] == []


def test_schema3_data_without_table_omits_field(built_db: Path, tmp_path: Path) -> None:
    old = tmp_path / "schema3.sqlite"
    shutil.copy(built_db, old)
    with sqlite3.connect(old) as conn:
        conn.execute("DROP TABLE causal_gene")
    repo = MondoRepository(old)
    try:
        svc = MondoService(repo)
        assert "causal_genes" not in svc.get_disease(SGS, response_mode="full")
        assert "causal_genes" not in svc.resolve_disease(SGS)
    finally:
        repo.close()


def test_next_commands_chain_to_hgnc(service: MondoService) -> None:
    step = {"tool": "hgnc_resolve_symbol", "arguments": {"query": "SKI"}}
    assert step in nc.after_get_disease(service.get_disease(SGS))
    assert step in nc.after_resolve_disease(service.resolve_disease(SGS))


def test_fleet_tool_targets_exist() -> None:
    # Guard: a cross-server step names the router-namespaced hgnc-link tool
    # (`resolve_symbol(query)` behind namespace `hgnc`) and only its real arguments.
    steps = nc.after_get_disease({"mondo_id": SGS, "causal_genes": [SKI]})
    fleet = [s for s in steps if s["tool"] in nc.FLEET_TOOLS]
    assert fleet
    for s in fleet:
        assert set(s["arguments"]) <= nc.FLEET_TOOLS[s["tool"]]
    # hpo-link: `get_genes_for_disease(disease_id)` behind namespace `hpo` (issue #134).
    fleet_surface = {
        "hgnc_resolve_symbol": frozenset({"query"}),
        "hpo_get_genes_for_disease": frozenset({"disease_id"}),
    }
    assert fleet_surface == nc.FLEET_TOOLS


def _hpo_ids(payload: dict[str, Any]) -> list[str]:
    steps = payload["_meta"].get("next_commands") or []
    return [s["arguments"]["disease_id"] for s in steps if s["tool"].startswith("hpo_")]


@pytest.mark.parametrize(("tool", "arg"), [("resolve_disease", "query"), ("get_disease", "term")])
@pytest.mark.parametrize(
    ("term", "ids"),
    [
        ("MONDO:0005012", ["OMIM:155600"]),  # the loose OMIM:155601 xref is never chained
    ],
)
async def test_no_gene_term_says_so_and_chains_to_gene_lookup(
    facade: Any, structured: Any, tool: str, arg: str, term: str, ids: list[str]
) -> None:
    # Issue #134: [] must not read as "no gene known".
    payload = structured(await facade.call_tool(tool, {arg: term}))
    assert payload["result"]["causal_genes"] == []
    assert payload["result"]["absence_note"] == nc.NO_GENE_NOTE_WITH_STEP
    assert _hpo_ids(payload) == ids


@pytest.mark.parametrize(
    ("term", "ids"),
    [
        ("MONDO:0015159", ["OMIM:900001", "OMIM:900002", "ORPHA:900003"]),  # OMIM before ORPHA
        ("MONDO:0017310", ["ORPHA:284993"]),  # no OMIM mapping: the equivalent ORPHA id
    ],
)
def test_gene_lookup_ids_order(service: MondoService, term: str, ids: list[str]) -> None:
    assert service.gene_lookup_ids(term) == ids
    assert [s["arguments"]["disease_id"] for s in nc.no_gene_steps(ids)] == ids[:2]  # cap 2


async def test_umbrella_with_descendant_genes_has_no_absence_note(
    facade: Any, structured: Any
) -> None:
    payload = structured(await facade.call_tool("resolve_disease", {"query": "MONDO:0017310"}))
    assert payload["result"]["causal_genes"][0]["from_descendant"] == SGS
    assert "absence_note" not in payload["result"]
    assert {"tool": "hgnc_resolve_symbol", "arguments": {"query": "SKI"}} in payload["_meta"][
        "next_commands"
    ]


async def test_no_gene_term_without_mapping_has_note_only(facade: Any, structured: Any) -> None:
    payload = structured(await facade.call_tool("get_disease", {"term": "MONDO:0000003"}))
    assert payload["result"]["causal_genes"] == []
    assert payload["result"]["absence_note"] == nc.NO_GENE_NOTE  # names no lookup it cannot offer
    assert _hpo_ids(payload) == []


async def test_minimal_mode_has_no_absence_note(facade: Any, structured: Any) -> None:
    args = {"term": "MONDO:0005012", "response_mode": "minimal"}
    payload = structured(await facade.call_tool("get_disease", args))
    assert payload["result"]["causal_genes"] == []
    assert "absence_note" not in payload["result"]


async def test_obsolete_term_gets_no_note_or_gene_step(facade: Any, structured: Any) -> None:
    payload = structured(await facade.call_tool("resolve_disease", {"query": "OMIM:900009"}))
    assert (payload["result"]["mondo_id"], payload["result"]["obsolete"]) == ("MONDO:0099999", True)
    assert "absence_note" not in payload["result"]
    assert _hpo_ids(payload) == []
    # A `fields` projection drops the record's own `obsolete` flag; still no note or step.
    args = {"term": "OMIM:900009", "fields": ["causal_genes"]}
    sparse = structured(await facade.call_tool("get_disease", args))
    assert "absence_note" not in sparse["result"]
    assert _hpo_ids(sparse) == []


async def test_term_with_genes_has_no_absence_note(facade: Any, structured: Any) -> None:
    payload = structured(await facade.call_tool("resolve_disease", {"query": SGS}))
    assert payload["result"]["causal_genes"]
    assert "absence_note" not in payload["result"]
    assert _hpo_ids(payload) == []


@pytest.mark.parametrize(
    ("tool", "arg"), [("resolve_disease_batch", "queries"), ("get_disease_batch", "terms")]
)
async def test_batch_carries_one_top_level_absence_note(
    facade: Any, structured: Any, tool: str, arg: str
) -> None:
    payload = structured(await facade.call_tool(tool, {arg: [SGS, "MONDO:0000003", "nope zz"]}))
    assert payload["absence_note"] == nc.NO_GENE_NOTE_BATCH
    assert all("absence_note" not in row for row in payload["results"])
    with_genes = structured(await facade.call_tool(tool, {arg: [SGS, "nope zz"]}))
    assert "absence_note" not in with_genes
    minimal = {arg: ["MONDO:0000003"], "response_mode": "minimal"}
    assert "absence_note" not in structured(await facade.call_tool(tool, minimal))
