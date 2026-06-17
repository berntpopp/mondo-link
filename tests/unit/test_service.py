"""Unit tests for the Mondo service orchestration over the self-built index."""

from __future__ import annotations

from pathlib import Path

import pytest

from mondo_link.data.repository import MondoRepository
from mondo_link.exceptions import (
    AmbiguousQueryError,
    InvalidInputError,
    NotFoundError,
    WithdrawnEntryError,
)
from mondo_link.services.mondo_service import MondoService
from tests.unit.test_repository import (
    HD,
    NERVOUS,
    NEURODEGEN,
    OBSOLETE,
    RARE,
    ROOT,
    _build_db,
)


@pytest.fixture
def service(tmp_path: Path) -> MondoService:
    db = tmp_path / "mondo.sqlite"
    _build_db(db)
    repo = MondoRepository(db)
    svc = MondoService(repo)
    yield svc
    repo.close()


# -- resolve ------------------------------------------------------------------


def test_resolve_by_mondo_id(service: MondoService) -> None:
    res = service.resolve_disease("MONDO:0007739")
    assert res["mondo_id"] == HD
    assert res["match_type"] == "mondo_id"
    assert res["name"] == "Huntington disease"
    assert res["mondo_version"] == "2026-06-01"


def test_resolve_by_bare_id(service: MondoService) -> None:
    assert service.resolve_disease("0007739")["mondo_id"] == HD


def test_resolve_by_primary_label(service: MondoService) -> None:
    res = service.resolve_disease("Huntington disease")
    assert res["mondo_id"] == HD
    assert res["match_type"] == "primary"


def test_resolve_by_exact_synonym(service: MondoService) -> None:
    res = service.resolve_disease("HD")
    assert res["mondo_id"] == HD
    assert res["match_type"] == "exact_synonym"


def test_resolve_by_xref(service: MondoService) -> None:
    res = service.resolve_disease("OMIM:143100")
    assert res["mondo_id"] == HD  # exactMatch wins over NEURODEGEN's closeMatch
    assert res["match_type"] == "xref"


def test_resolve_ambiguous_raises(service: MondoService) -> None:
    with pytest.raises(AmbiguousQueryError) as exc:
        service.resolve_disease("shared label")
    cands = exc.value.candidates
    assert {c["mondo_id"] for c in cands} == {HD, NEURODEGEN}
    assert all("name" in c for c in cands)


def test_resolve_obsolete_raises_withdrawn(service: MondoService) -> None:
    with pytest.raises(WithdrawnEntryError) as exc:
        service.resolve_disease(OBSOLETE)
    assert exc.value.withdrawn == OBSOLETE
    # replaced_by carries successor records (replaced_by + consider).
    ids = {r["mondo_id"] for r in exc.value.replaced_by}
    assert HD in ids


def test_resolve_not_found(service: MondoService) -> None:
    with pytest.raises(NotFoundError):
        service.resolve_disease("MONDO:0000000")


def test_resolve_label_miss_attaches_search_suggestions(service: MondoService) -> None:
    # "huntington" alone is not an exact label/synonym, but FTS finds HD -- the
    # not_found should embed that answer as a suggestion, not just route to search.
    with pytest.raises(NotFoundError) as exc:
        service.resolve_disease("huntington")
    assert any(s["mondo_id"] == HD for s in exc.value.suggestions)


def test_get_disease_label_miss_attaches_search_suggestions(service: MondoService) -> None:
    with pytest.raises(NotFoundError) as exc:
        service.get_disease("huntington")
    assert any(s["mondo_id"] == HD for s in exc.value.suggestions)


def test_unmatchable_label_has_no_suggestions(service: MondoService) -> None:
    with pytest.raises(NotFoundError) as exc:
        service.resolve_disease("zzzznotathing")
    assert exc.value.suggestions == []


def test_resolve_empty_raises(service: MondoService) -> None:
    with pytest.raises(InvalidInputError):
        service.resolve_disease("   ")


# -- search -------------------------------------------------------------------


def test_search_returns_page_fields(service: MondoService) -> None:
    res = service.search_diseases("huntington", limit=10)
    assert any(r["mondo_id"] == HD for r in res["results"])
    assert {"total", "returned", "limit", "truncated"} <= set(res)
    assert res["mondo_version"] == "2026-06-01"


def test_search_blank_raises(service: MondoService) -> None:
    with pytest.raises(InvalidInputError):
        service.search_diseases("   ")


def test_search_punctuation_safe(service: MondoService) -> None:
    res = service.search_diseases("disease (disorder)")
    assert isinstance(res["results"], list)


def test_search_compact_returns_snippet_not_full_definition(service: MondoService) -> None:
    # Compact (default) is the hot path: identity + score + a short snippet only.
    res = service.search_diseases("huntington", response_mode="compact")
    hit = next(r for r in res["results"] if r["mondo_id"] == HD)
    assert "definition" not in hit  # full paragraph reserved for standard/full
    assert "score" in hit
    if hit.get("definition_snippet"):
        assert len(hit["definition_snippet"]) <= 141


def test_search_standard_returns_full_definition(service: MondoService) -> None:
    res = service.search_diseases("huntington", response_mode="standard")
    hit = next(r for r in res["results"] if r["mondo_id"] == HD)
    assert "definition_snippet" not in hit
    # the HD fixture term carries a definition, returned in full here
    assert hit.get("definition")


# -- full record --------------------------------------------------------------


def test_get_disease_grouped_xrefs(service: MondoService) -> None:
    rec = service.get_disease("MONDO:0007739", response_mode="full")
    assert rec["mondo_id"] == HD
    assert rec["name"] == "Huntington disease"
    # xrefs grouped by prefix
    assert set(rec["xrefs"]) == {"OMIM", "DOID", "ORPHA", "NCIT"}
    omim = rec["xrefs"]["OMIM"][0]
    assert omim["object_id"] == "OMIM:143100"
    assert omim["predicate"] == "exactMatch"
    assert omim["origin"] == "sssom"
    assert omim["source"] == "MONDO:equivalentTo"
    # hierarchy
    assert {p["mondo_id"] for p in rec["parents"]} == {NEURODEGEN, NERVOUS, RARE}
    assert {g["mondo_id"] for g in rec["top_groupings"]} == {NERVOUS, RARE}


def test_get_disease_compact_collapses_synonyms(service: MondoService) -> None:
    rec = service.get_disease("MONDO:0007739", response_mode="compact")
    assert rec["synonyms"] == ["HD", "chorea major"]
    # empty fields dropped in compact mode
    assert "replaced_by" not in rec  # None
    assert "consider" not in rec  # []


def test_get_disease_minimal(service: MondoService) -> None:
    rec = service.get_disease("MONDO:0007739", response_mode="minimal")
    assert set(rec) == {"mondo_id", "name"}


def test_get_disease_obsolete_raises(service: MondoService) -> None:
    with pytest.raises(WithdrawnEntryError):
        service.get_disease(OBSOLETE)


# -- hierarchy ----------------------------------------------------------------


def test_get_ancestors_page_fields(service: MondoService) -> None:
    res = service.get_ancestors("MONDO:0007739", limit=2)
    assert res["mondo_id"] == HD
    assert res["total"] == 4  # NEURODEGEN, NERVOUS, RARE, ROOT
    assert res["returned"] == 2
    assert res["truncated"] is True
    assert len(res["ancestors"]) == 2


def test_get_ancestors_offset_paging(service: MondoService) -> None:
    page1 = service.get_ancestors("MONDO:0007739", limit=2, offset=0)
    page2 = service.get_ancestors("MONDO:0007739", limit=2, offset=2)
    assert page1["offset"] == 0
    assert page1["next_offset"] == 2
    assert page1["truncated"] is True
    assert page2["offset"] == 2
    assert page2["truncated"] is False  # last page
    ids1 = {a["mondo_id"] for a in page1["ancestors"]}
    ids2 = {a["mondo_id"] for a in page2["ancestors"]}
    assert ids1.isdisjoint(ids2)
    assert len(ids1 | ids2) == 4  # full closure covered across two pages


def test_resolve_xref_total_is_accurate_for_paging(service: MondoService) -> None:
    # total must be the FULL count (not just the returned page) so truncation shows.
    page = service.resolve_xref("OMIM:143100", limit=1, offset=0)
    assert page["total"] == 2
    assert page["returned"] == 1
    assert page["truncated"] is True
    assert page["next_offset"] == 1


def test_get_descendants_page_fields(service: MondoService) -> None:
    res = service.get_descendants("MONDO:0000001", limit=10)
    ids = {d["mondo_id"] for d in res["descendants"]}
    assert ids == {NERVOUS, NEURODEGEN, HD, RARE}
    assert res["total"] == 4
    assert res["truncated"] is False


def test_get_parents(service: MondoService) -> None:
    res = service.get_parents("MONDO:0007739")
    assert res["count"] == 3
    assert {p["mondo_id"] for p in res["parents"]} == {NEURODEGEN, NERVOUS, RARE}


def test_get_children(service: MondoService) -> None:
    res = service.get_children("MONDO:0005071")  # nervous system disorder
    assert {c["mondo_id"] for c in res["children"]} == {NEURODEGEN, HD}
    assert res["count"] == 2


# -- cross-ontology -----------------------------------------------------------


def test_resolve_xref_orders_exact_before_close(service: MondoService) -> None:
    res = service.resolve_xref("OMIM:143100")
    assert res["normalized"] == "OMIM:143100"
    ids = [m["mondo_id"] for m in res["matches"]]
    assert ids == [HD, NEURODEGEN]  # exactMatch precedes closeMatch
    assert res["matches"][0]["predicate"] == "exactMatch"
    assert {"total", "returned", "limit", "truncated"} <= set(res)


def test_resolve_xref_empty_is_success(service: MondoService) -> None:
    res = service.resolve_xref("OMIM:000000")
    assert res["matches"] == []
    assert res["total"] == 0


def test_resolve_xref_invalid_curie_raises(service: MondoService) -> None:
    with pytest.raises(InvalidInputError):
        service.resolve_xref("not-a-curie")


def test_resolve_xref_blank_raises(service: MondoService) -> None:
    with pytest.raises(InvalidInputError):
        service.resolve_xref("  ")


def test_map_cross_ontology_all(service: MondoService) -> None:
    res = service.map_cross_ontology("MONDO:0007739")
    assert res["mondo_id"] == HD
    assert set(res["mappings"]) == {"OMIM", "DOID", "ORPHA", "NCIT"}
    assert res["prefixes_filter"] is None
    # ``count`` is the total mappings across every prefix group.
    assert res["count"] == sum(len(rows) for rows in res["mappings"].values())
    assert res["count"] >= 4


def test_map_cross_ontology_prefix_filter(service: MondoService) -> None:
    res = service.map_cross_ontology("MONDO:0007739", prefixes=["omim", "doid"])
    assert set(res["mappings"]) == {"OMIM", "DOID"}
    assert res["prefixes_filter"] == ["OMIM", "DOID"]


# -- diagnostics --------------------------------------------------------------


def test_diagnostics_with_repo(service: MondoService) -> None:
    diag = service.get_diagnostics()
    assert diag["index_built"] is True
    assert diag["mondo_version"] == "2026-06-01"
    assert diag["counts"]["terms"] == 6
    assert diag["db_path"]


def test_diagnostics_without_repo_never_raises() -> None:
    svc = MondoService(None)
    diag = svc.get_diagnostics()  # must not raise
    assert diag["index_built"] is False
    assert "counts" not in diag
    assert "mondo_version" not in diag


def test_mondo_version_constants_present(service: MondoService) -> None:
    # Every record payload is grounded with mondo_version.
    assert service.get_parents("MONDO:0000001")["mondo_version"] == "2026-06-01"
    assert service.map_cross_ontology(ROOT)["mondo_version"] == "2026-06-01"
