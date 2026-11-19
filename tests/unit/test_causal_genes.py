"""Mondo germline-basis genes (issue #127): parse, serve, degrade, chain to HGNC."""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

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
    assert service.get_disease("MONDO:0017310", response_mode=mode)["causal_genes"] == []
    assert service.resolve_disease("MONDO:0017310", response_mode=mode)["causal_genes"] == []


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
    hgnc_link_surface = {"hgnc_resolve_symbol": frozenset({"query"})}
    assert hgnc_link_surface == nc.FLEET_TOOLS
