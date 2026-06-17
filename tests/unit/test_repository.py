"""Unit tests for the read-only Mondo repository.

The test database is built directly from the frozen ``schema.sql`` plus row
inserts — self-contained and fast, with no dependency on the ingest builder or
shared fixtures.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from mondo_link.data.repository import MondoRepository
from mondo_link.exceptions import DataUnavailableError
from mondo_link.ingest.schema import load_schema_sql

# -- fixture data -------------------------------------------------------------

ROOT = "MONDO:0000001"
NERVOUS = "MONDO:0005071"
NEURODEGEN = "MONDO:0005559"
HD = "MONDO:0007739"
RARE = "MONDO:0019262"
OBSOLETE = "MONDO:0099999"


def _syn(text: str, scope: str, sources: list[str] | None = None) -> dict:
    return {"text": text, "scope": scope, "type": None, "sources": sources or []}


def _build_db(path: Path) -> None:
    """Create a small Mondo index covering every query path under test."""
    conn = sqlite3.connect(path)
    try:
        conn.executescript(load_schema_sql())
        _insert_terms(conn)
        _insert_lookup(conn)
        _insert_fts(conn)
        _insert_graph(conn)
        _insert_groupings(conn)
        _insert_xrefs(conn)
        _insert_meta(conn)
        conn.commit()
    finally:
        conn.close()


def _insert_terms(conn: sqlite3.Connection) -> None:
    rows = [
        (ROOT, "disease or disorder", None, 0, None, [], [], []),
        (NERVOUS, "nervous system disorder", None, 0, None, [], [], []),
        (
            NEURODEGEN,
            "neurodegenerative disease",
            "A disease of progressive neuron loss.",
            0,
            None,
            [],
            [],
            [],
        ),
        (
            HD,
            "Huntington disease",
            "A neurodegenerative disorder (CAG repeat).",
            0,
            None,
            [_syn("HD", "EXACT", ["OMIM"]), _syn("chorea major", "RELATED")],
            ["gard_rare"],
            [],
        ),
        (RARE, "rare disease", "A disease affecting few people.", 0, None, [], [], []),
        (
            OBSOLETE,
            "obsolete huntingtons (legacy)",
            None,
            1,
            HD,
            [],
            [],
            ["MONDO:0099998"],
        ),
    ]
    conn.executemany(
        "INSERT INTO term (mondo_id, name, name_upper, definition, is_obsolete, replaced_by, "
        "synonyms, subsets, consider) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                mid,
                name,
                name.upper(),
                definition,
                obs,
                repl,
                json.dumps(syns),
                json.dumps(subsets),
                json.dumps(consider),
            )
            for mid, name, definition, obs, repl, syns, subsets, consider in rows
        ],
    )


def _insert_lookup(conn: sqlite3.Connection) -> None:
    rows = [
        ("DISEASE OR DISORDER", ROOT, "primary"),
        ("NERVOUS SYSTEM DISORDER", NERVOUS, "primary"),
        ("NEURODEGENERATIVE DISEASE", NEURODEGEN, "primary"),
        ("HUNTINGTON DISEASE", HD, "primary"),
        ("HD", HD, "exact_synonym"),
        ("CHOREA MAJOR", HD, "related_synonym"),
        ("RARE DISEASE", RARE, "primary"),
        # ambiguous label: two distinct ids share "shared label"
        ("SHARED LABEL", HD, "exact_synonym"),
        ("SHARED LABEL", NEURODEGEN, "related_synonym"),
    ]
    conn.executemany(
        "INSERT INTO term_lookup (lookup_label, mondo_id, label_type) VALUES (?, ?, ?)", rows
    )


def _insert_fts(conn: sqlite3.Connection) -> None:
    rows = [
        (ROOT, "disease or disorder", "", ""),
        (NERVOUS, "nervous system disorder", "", ""),
        (NEURODEGEN, "neurodegenerative disease", "", "progressive neuron loss"),
        (HD, "Huntington disease", "HD chorea major", "CAG repeat neurodegenerative"),
        (RARE, "rare disease", "", "few people"),
    ]
    conn.executemany(
        "INSERT INTO term_fts (mondo_id, name, synonyms, definition) VALUES (?, ?, ?, ?)", rows
    )


def _insert_graph(conn: sqlite3.Connection) -> None:
    # mondo_parent: HD has multiple parents (multi-parent) incl. RARE.
    parents = [
        (NERVOUS, ROOT),
        (NEURODEGEN, NERVOUS),
        (HD, NEURODEGEN),
        (HD, NERVOUS),
        (HD, RARE),
        (RARE, ROOT),
    ]
    conn.executemany("INSERT INTO mondo_parent (mondo_id, parent_id) VALUES (?, ?)", parents)
    # mondo_closure incl. self-pairs.
    closure = [
        (ROOT, ROOT),
        (NERVOUS, NERVOUS),
        (NERVOUS, ROOT),
        (NEURODEGEN, NEURODEGEN),
        (NEURODEGEN, NERVOUS),
        (NEURODEGEN, ROOT),
        (HD, HD),
        (HD, NEURODEGEN),
        (HD, NERVOUS),
        (HD, RARE),
        (HD, ROOT),
        (RARE, RARE),
        (RARE, ROOT),
    ]
    conn.executemany("INSERT INTO mondo_closure (mondo_id, ancestor_id) VALUES (?, ?)", closure)


def _insert_groupings(conn: sqlite3.Connection) -> None:
    # RARE and NERVOUS are top-level groupings; both are ancestors of HD.
    conn.executemany(
        "INSERT INTO mondo_top_grouping (mondo_id, name, display_order) VALUES (?, ?, ?)",
        [(NERVOUS, "nervous system disorder", 1), (RARE, "rare disease", 2)],
    )


def _insert_xrefs(conn: sqlite3.Connection) -> None:
    # HD carries OBO + SSSOM xrefs with assorted predicates to exercise ordering.
    # The builder/parser store ``object_id`` as the full normalised CURIE.
    rows = [
        # (mondo_id, prefix, object_id, predicate, origin, source)
        (HD, "OMIM", "OMIM:143100", "exactMatch", "sssom", "MONDO:equivalentTo"),
        (HD, "DOID", "DOID:12858", "xref", "obo_xref", None),
        (HD, "ORPHA", "ORPHA:399", "closeMatch", "sssom", "MONDO:relatedMatch"),
        (HD, "NCIT", "NCIT:C82697", "narrowMatch", "sssom", "MONDO:narrowMatch"),
        # OMIM:143100 also referenced (closeMatch) by NEURODEGEN -> exercises
        # mondo_for_xref ordering: exactMatch (HD) must precede closeMatch (NEURODEGEN).
        (NEURODEGEN, "OMIM", "OMIM:143100", "closeMatch", "sssom", "MONDO:relatedMatch"),
        # OMIM:609300 maps to ONE term (NEURODEGEN) via TWO rows: an OBO xref
        # (equivalentTo) and an SSSOM mapping (exactMatch). resolve_xref must collapse
        # these to a single row (strongest predicate) so returned never exceeds the
        # distinct-term total.
        (NEURODEGEN, "OMIM", "OMIM:609300", "equivalentTo", "obo_xref", None),
        (NEURODEGEN, "OMIM", "OMIM:609300", "exactMatch", "sssom", "MONDO:exactMatch"),
    ]
    conn.executemany(
        "INSERT INTO xref (mondo_id, prefix, object_id, object_id_upper, predicate, origin, "
        "source) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [(m, p, o, o.upper(), pred, origin, src) for m, p, o, pred, origin, src in rows],
    )


def _insert_meta(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO meta (id, schema_version, mondo_version, term_count, obsolete_count, "
        "closure_count, xref_count, mapping_count, build_utc) "
        "VALUES (1, 1, '2026-06-01', 6, 1, 12, 5, 4, '2026-06-01T00:00:00+00:00')"
    )


@pytest.fixture
def repo(tmp_path: Path) -> MondoRepository:
    db = tmp_path / "mondo.sqlite"
    _build_db(db)
    repository = MondoRepository(db)
    yield repository
    repository.close()


# -- constructor / provenance -------------------------------------------------


def test_missing_db_raises(tmp_path: Path) -> None:
    with pytest.raises(DataUnavailableError):
        MondoRepository(tmp_path / "nope.sqlite")


def test_read_meta(repo: MondoRepository) -> None:
    meta = repo.read_meta()
    assert meta["mondo_version"] == "2026-06-01"
    assert meta["term_count"] == 6


# -- term records -------------------------------------------------------------


def test_get_term_parses_json(repo: MondoRepository) -> None:
    term = repo.get_term(HD)
    assert term is not None
    assert term["name"] == "Huntington disease"
    assert term["is_obsolete"] is False
    assert term["synonyms"][0] == {
        "text": "HD",
        "scope": "EXACT",
        "type": None,
        "sources": ["OMIM"],
    }
    assert term["subsets"] == ["gard_rare"]
    assert term["consider"] == []


def test_get_term_obsolete(repo: MondoRepository) -> None:
    term = repo.get_term(OBSOLETE)
    assert term is not None
    assert term["is_obsolete"] is True
    assert term["replaced_by"] == HD
    assert term["consider"] == ["MONDO:0099998"]


def test_get_term_missing(repo: MondoRepository) -> None:
    assert repo.get_term("MONDO:0000000") is None


def test_resolve_label_primary_and_synonym(repo: MondoRepository) -> None:
    assert repo.resolve_label("HUNTINGTON DISEASE") == [{"mondo_id": HD, "label_type": "primary"}]
    assert repo.resolve_label("HD") == [{"mondo_id": HD, "label_type": "exact_synonym"}]


def test_resolve_label_ambiguous(repo: MondoRepository) -> None:
    cands = repo.resolve_label("SHARED LABEL")
    assert {c["mondo_id"] for c in cands} == {HD, NEURODEGEN}


def test_resolve_label_unknown(repo: MondoRepository) -> None:
    assert repo.resolve_label("NOTHING HERE") == []


# -- search -------------------------------------------------------------------


def test_search_basic(repo: MondoRepository) -> None:
    hits, total = repo.search("huntington", limit=10, include_obsolete=False)
    assert total >= 1
    assert hits[0]["mondo_id"] == HD
    assert "definition" in hits[0]


def test_search_punctuation_does_not_raise(repo: MondoRepository) -> None:
    # Parentheses / colon / hyphen would be FTS5 operators if not sanitized.
    for query in ["disease (disorder)", "OMIM:143100", "neuro-degenerative", '"unterminated', "()"]:
        hits, total = repo.search(query, limit=10, include_obsolete=False)
        assert isinstance(hits, list)
        assert isinstance(total, int)


def test_search_excludes_obsolete_by_default(repo: MondoRepository) -> None:
    hits, _ = repo.search("huntington", limit=10, include_obsolete=False)
    assert all(h["mondo_id"] != OBSOLETE for h in hits)


# -- hierarchy ----------------------------------------------------------------


def test_parents_multi(repo: MondoRepository) -> None:
    parents = repo.parents(HD)
    ids = {p["mondo_id"] for p in parents}
    assert ids == {NEURODEGEN, NERVOUS, RARE}
    assert all(p["name"] for p in parents)


def test_children(repo: MondoRepository) -> None:
    children = repo.children(NERVOUS)
    ids = {c["mondo_id"] for c in children}
    assert ids == {NEURODEGEN, HD}


def test_ancestors_excludes_self(repo: MondoRepository) -> None:
    anc = repo.ancestors(HD, limit=50)
    ids = {a["mondo_id"] for a in anc}
    assert ids == {NEURODEGEN, NERVOUS, RARE, ROOT}
    assert HD not in ids


def test_ancestors_limit(repo: MondoRepository) -> None:
    anc = repo.ancestors(HD, limit=1)
    assert len(anc) == 1


def test_ancestors_offset_pages_forward(repo: MondoRepository) -> None:
    # Page through HD's 4 ancestors two at a time; pages must be disjoint and cover all.
    page1 = repo.ancestors(HD, limit=2, offset=0)
    page2 = repo.ancestors(HD, limit=2, offset=2)
    ids1 = [a["mondo_id"] for a in page1]
    ids2 = [a["mondo_id"] for a in page2]
    assert len(ids1) == 2 and len(ids2) == 2
    assert set(ids1).isdisjoint(ids2)
    assert set(ids1) | set(ids2) == {NEURODEGEN, NERVOUS, RARE, ROOT}


def test_descendants_offset_pages_forward(repo: MondoRepository) -> None:
    page1 = repo.descendants(ROOT, limit=2, offset=0)
    page2 = repo.descendants(ROOT, limit=2, offset=2)
    assert {d["mondo_id"] for d in page1}.isdisjoint({d["mondo_id"] for d in page2})


def test_search_offset_pages_forward(repo: MondoRepository) -> None:
    hits, total = repo.search("disease", limit=1, offset=0, include_obsolete=False)
    hits2, total2 = repo.search("disease", limit=1, offset=1, include_obsolete=False)
    assert total == total2  # total is the full count, independent of the page
    if hits and hits2:
        assert hits[0]["mondo_id"] != hits2[0]["mondo_id"]


def test_descendants_excludes_self(repo: MondoRepository) -> None:
    desc = repo.descendants(NERVOUS, limit=50)
    ids = {d["mondo_id"] for d in desc}
    assert ids == {NEURODEGEN, HD}
    assert NERVOUS not in ids


def test_count_ancestors_descendants(repo: MondoRepository) -> None:
    assert repo.count_ancestors(HD) == 4
    assert repo.count_descendants(ROOT) == 4


def test_top_groupings(repo: MondoRepository) -> None:
    groupings = repo.top_groupings(HD)
    ids = {g["mondo_id"] for g in groupings}
    assert ids == {NERVOUS, RARE}
    # ordered by name
    names = [g["name"] for g in groupings]
    assert names == sorted(names)


# -- cross-references ---------------------------------------------------------


def test_xrefs_for_predicate_ordering(repo: MondoRepository) -> None:
    xrefs = repo.xrefs_for(HD)
    preds = [x["predicate"] for x in xrefs]
    # exactMatch (0) < closeMatch (2) < narrowMatch (3) < xref (5)
    assert preds == ["exactMatch", "closeMatch", "narrowMatch", "xref"]
    # carries source + origin
    omim = next(x for x in xrefs if x["prefix"] == "OMIM")
    assert omim["origin"] == "sssom"
    assert omim["source"] == "MONDO:equivalentTo"


def test_xrefs_for_prefix_filter(repo: MondoRepository) -> None:
    xrefs = repo.xrefs_for(HD, prefixes=["OMIM", "DOID"])
    prefixes = {x["prefix"] for x in xrefs}
    assert prefixes == {"OMIM", "DOID"}


def test_mondo_for_xref_orders_exact_before_close(repo: MondoRepository) -> None:
    matches = repo.mondo_for_xref("OMIM:143100", limit=10)
    assert [m["mondo_id"] for m in matches] == [HD, NEURODEGEN]
    assert matches[0]["predicate"] == "exactMatch"
    assert matches[1]["predicate"] == "closeMatch"


def test_mondo_for_xref_unknown(repo: MondoRepository) -> None:
    assert repo.mondo_for_xref("OMIM:000000", limit=10) == []


def test_mondo_for_xref_dedups_multi_predicate_rows_to_one_per_term(
    repo: MondoRepository,
) -> None:
    # OMIM:609300 maps to NEURODEGEN via both an OBO xref (equivalentTo) and an SSSOM
    # mapping (exactMatch). The reverse lookup must return ONE row (strongest predicate)
    # so the row count matches the distinct-term count it is paged against.
    matches = repo.mondo_for_xref("OMIM:609300", limit=10)
    assert [m["mondo_id"] for m in matches] == [NEURODEGEN]
    assert matches[0]["predicate"] == "exactMatch"  # strongest of the two rows
    assert len(matches) == repo.count_mondo_for_xref("OMIM:609300") == 1


def test_non_human_animal_ids_flags_root_and_descendants(tmp_path: Path) -> None:
    # The non-human-animal disease branch (root + descendants, via the closure table)
    # is the human-disease prior's signal for demoting veterinary terms in fuzzy resolve.
    from mondo_link.constants import NON_HUMAN_ANIMAL_ROOT

    human = "MONDO:0007947"
    pig = "MONDO:1011155"
    db = tmp_path / "nh.sqlite"
    conn = sqlite3.connect(db)
    try:
        conn.executescript(load_schema_sql())
        # closure carries self-pairs (mondo_id == ancestor_id); `pig` descends from the
        # non-human root while `human` does not.
        conn.executemany(
            "INSERT INTO mondo_closure (mondo_id, ancestor_id) VALUES (?, ?)",
            [
                (NON_HUMAN_ANIMAL_ROOT, NON_HUMAN_ANIMAL_ROOT),
                (pig, pig),
                (pig, NON_HUMAN_ANIMAL_ROOT),
                (human, human),
            ],
        )
        conn.commit()
    finally:
        conn.close()
    repository = MondoRepository(db)
    try:
        assert repository.non_human_animal_ids([human, pig, NON_HUMAN_ANIMAL_ROOT]) == {
            pig,
            NON_HUMAN_ANIMAL_ROOT,
        }
        assert repository.non_human_animal_ids([human]) == set()
        assert repository.non_human_animal_ids([]) == set()
    finally:
        repository.close()


def test_counts(repo: MondoRepository) -> None:
    counts = repo.counts()
    assert counts["terms"] == 6
    assert counts["obsolete"] == 1
    assert counts["xrefs"] == 7  # +2 dual-mapping rows for OMIM:609300
    assert counts["closure"] == 13
    assert counts["top_groupings"] == 2
