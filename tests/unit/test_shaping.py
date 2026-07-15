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


def test_minimal_keeps_anchors_and_narrows_collections() -> None:
    # minimal keeps anchors AND every populated collection, narrowing each row to its
    # stable identifiers -- it NEVER deletes a collection (that would be a silent-empty).
    out = shaping.shape_disease(_record(), "minimal")
    assert set(out) == {
        "mondo_id",
        "name",
        "mondo_version",
        "synonyms",
        "subsets",
        "parents",
        "xrefs",
    }
    assert out["mondo_id"] == "MONDO:0007739"
    # rows narrowed to identifiers (detail dropped); the collection survives
    assert out["parents"] == [{"mondo_id": "MONDO:0005559"}]
    assert out["xrefs"] == {"OMIM": [{"object_id": "143100"}]}
    # optional record-detail scalars are dropped
    assert "definition" not in out and "obsolete" not in out
    # empty collections are dropped (a strict subset of the default response)
    assert "children" not in out and "consider" not in out


def test_minimal_preserves_meta() -> None:
    rec = {**_record(), "_meta": {"x": 1}}
    out = shaping.shape_disease(rec, "minimal")
    assert out["_meta"] == {"x": 1}
    assert {"mondo_id", "name", "_meta"} <= set(out)


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


def _hit(definition: str | None = None) -> dict:
    return {
        "mondo_id": "MONDO:0007947",
        "name": "Marfan syndrome",
        "score": 13.0,
        "definition": definition,
    }


def test_search_hit_minimal_keeps_id_name_score() -> None:
    out = shaping.shape_search_hit(_hit("A disorder."), "minimal")
    assert out == {"mondo_id": "MONDO:0007947", "name": "Marfan syndrome", "score": 13.0}


def test_search_hit_compact_truncates_definition_to_snippet() -> None:
    long_def = "A disorder of the connective tissue. " * 20  # ~740 chars
    out = shaping.shape_search_hit(_hit(long_def), "compact", snippet_chars=140)
    assert "definition" not in out  # full definition is reserved for standard/full
    snippet = out["definition_snippet"]
    assert len(snippet) <= 141  # <= snippet_chars (+ trailing ellipsis char)
    assert snippet.endswith("…")  # truncated marker
    assert not snippet[:-1].endswith(" ")  # trimmed at a word boundary
    assert {"mondo_id", "name", "score", "definition_snippet"} == set(out)


def test_search_hit_compact_short_definition_not_ellipsized() -> None:
    out = shaping.shape_search_hit(_hit("A short def."), "compact", snippet_chars=140)
    assert out["definition_snippet"] == "A short def."
    assert "definition" not in out


def test_search_hit_compact_no_definition_omits_snippet() -> None:
    out = shaping.shape_search_hit(_hit(None), "compact")
    assert "definition_snippet" not in out
    assert set(out) == {"mondo_id", "name", "score"}


def test_search_hit_standard_and_full_keep_full_definition() -> None:
    long_def = "A disorder of the connective tissue. " * 20
    for mode in ("standard", "full"):
        out = shaping.shape_search_hit(_hit(long_def), mode)
        assert out["definition"] == long_def
        assert "definition_snippet" not in out


def _disease() -> dict:
    return {
        "mondo_id": "MONDO:0007739",
        "name": "Huntington disease",
        "definition": "A neurodegenerative disorder.",
        "mondo_version": "2026-06-01",
        "parents": [{"mondo_id": "MONDO:0005559"}],
        "xrefs": {
            "OMIM": [{"object_id": "OMIM:143100"}],
            "DOID": [{"object_id": "DOID:12858"}],
        },
    }


def test_select_fields_none_is_identity() -> None:
    rec = _disease()
    assert shaping.select_fields(rec, None) is rec
    assert shaping.select_fields(rec, []) is rec


def test_select_fields_keeps_anchors_plus_requested() -> None:
    out = shaping.select_fields(_disease(), ["definition"])
    assert set(out) == {"mondo_id", "name", "mondo_version", "definition"}
    assert out["definition"] == "A neurodegenerative disorder."


def test_select_fields_dotted_keeps_only_subgroup() -> None:
    out = shaping.select_fields(_disease(), ["xrefs.OMIM"])
    assert set(out) == {"mondo_id", "name", "mondo_version", "xrefs"}
    assert set(out["xrefs"]) == {"OMIM"}  # DOID dropped
    assert out["xrefs"]["OMIM"][0]["object_id"] == "OMIM:143100"


def test_select_fields_unknown_field_is_skipped() -> None:
    out = shaping.select_fields(_disease(), ["nope", "xrefs.NOTAPREFIX"])
    assert set(out) == {"mondo_id", "name", "mondo_version"}


def _fenced_definition() -> dict:
    return {
        "kind": "untrusted_text",
        "text": "A neurodegenerative disorder.",
        "provenance": {
            "source": "mondo",
            "record_id": "MONDO:0007739",
            "retrieved_at": "2026-07-11T00:00:00+00:00",
        },
        "raw_sha256": "0" * 64,
    }


def test_select_fields_treats_fenced_object_as_opaque_leaf() -> None:
    # A projection dotting into a fenced untrusted_text object must NOT strip
    # the wrapper (kind/provenance/raw_sha256) -- no fence-bypass via fields=.
    fenced = _fenced_definition()
    rec = {
        "mondo_id": "MONDO:0007739",
        "name": "Huntington disease",
        "mondo_version": "2026-06-01",
        "definition": fenced,
    }
    out = shaping.select_fields(rec, ["definition.text"])
    assert out["definition"] == fenced
    assert out["definition"]["kind"] == "untrusted_text"
    assert "raw_sha256" in out["definition"]


def test_snippet_preserves_internal_whitespace_when_under_limit() -> None:
    # The compact snippet is fenced downstream; its raw bytes (tab/LF/CR) must
    # survive, so _snippet never collapses internal whitespace.
    raw = "alpha\tbeta\ngamma delta"
    out = shaping._snippet(raw, 100)  # under the limit -> unchanged
    assert out == raw
    assert "\t" in out and "\n" in out


def test_snippet_truncates_on_word_boundary_without_collapsing() -> None:
    raw = "word one\ttwo three four five six seven eight nine ten eleven twelve"
    out = shaping._snippet(raw, 20)
    assert out.endswith("…")
    assert len(out) <= 21
    assert not out[:-1].endswith(" ")  # trimmed at a whitespace boundary
