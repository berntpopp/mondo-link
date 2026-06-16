"""Unit tests for response_mode projection of disease payloads."""

from __future__ import annotations

from mondo_link.services import shaping


def _record() -> dict:
    return {
        "mondo_id": "MONDO:0007739",
        "name": "Huntington disease",
        "definition": "A neurodegenerative disorder.",
        "synonyms": [
            {"text": "HD", "scope": "EXACT", "type": None, "sources": ["OMIM"]},
            {"text": "chorea", "scope": "RELATED", "type": None, "sources": []},
        ],
        "subsets": ["gard_rare"],
        "obsolete": False,
        "replaced_by": None,
        "consider": [],
        "parents": [{"mondo_id": "MONDO:0005559", "name": "neurodegenerative disease"}],
        "children": [],
        "top_groupings": [],
        "xrefs": {"OMIM": [{"object_id": "143100", "predicate": "exactMatch"}]},
        "mondo_version": "2026-06-01",
    }


def test_modes_constants() -> None:
    assert shaping.RESPONSE_MODES == ["minimal", "compact", "standard", "full"]
    assert shaping.DEFAULT_RESPONSE_MODE == "compact"


def test_minimal_keeps_only_identity() -> None:
    out = shaping.shape_disease(_record(), "minimal")
    assert set(out) == {"mondo_id", "name"}
    assert out["mondo_id"] == "MONDO:0007739"


def test_minimal_preserves_meta() -> None:
    rec = {**_record(), "_meta": {"x": 1}}
    out = shaping.shape_disease(rec, "minimal")
    assert "_meta" in out
    assert set(out) == {"mondo_id", "name", "_meta"}


def test_compact_drops_empty_and_collapses_synonyms() -> None:
    out = shaping.shape_disease(_record(), "compact")
    # null/empty dropped
    assert "replaced_by" not in out  # None
    assert "consider" not in out  # []
    assert "children" not in out  # []
    assert "top_groupings" not in out  # []
    # synonyms collapsed to plain strings
    assert out["synonyms"] == ["HD", "chorea"]
    # non-empty preserved
    assert out["definition"] == "A neurodegenerative disorder."
    assert out["parents"][0]["mondo_id"] == "MONDO:0005559"


def test_compact_keeps_false_obsolete_flag() -> None:
    # ``obsolete: False`` is meaningful, not "empty" — keep it.
    out = shaping.shape_disease(_record(), "compact")
    assert out["obsolete"] is False


def test_standard_is_full_record_with_structured_synonyms() -> None:
    rec = _record()
    out = shaping.shape_disease(rec, "standard")
    assert out == rec
    assert out["synonyms"][0]["scope"] == "EXACT"
    assert out["synonyms"][0]["sources"] == ["OMIM"]


def test_full_is_full_record() -> None:
    rec = _record()
    out = shaping.shape_disease(rec, "full")
    assert out == rec
    # returns a copy, not the same object
    assert out is not rec


def test_shape_hit_modes() -> None:
    hit = {"mondo_id": "MONDO:1", "name": "x", "definition": None, "score": 1.2}
    assert set(shaping.shape_hit(hit, "minimal")) == {"mondo_id", "name"}
    assert "definition" not in shaping.shape_hit(hit, "compact")  # None dropped
    assert shaping.shape_hit(hit, "full") == hit
