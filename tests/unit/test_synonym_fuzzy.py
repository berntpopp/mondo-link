"""Unit tests for FTS5/trigram fuzzy matching across MONDO synonym tables.

Verifies:
- Repository synonym search and trigram query construction
- Exact and fuzzy synonym ranking
- Response envelope match_confidence and matched_synonym metadata
- Response shaping with matched_synonym in search hits
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from mondo_link.data.repository import MondoRepository
from mondo_link.ingest.schema import load_schema_sql
from mondo_link.services.mondo_service import MondoService
from mondo_link.services.resolution import (
    ResolutionDetail,
    confidence_for,
)

HD = "MONDO:0007739"
MARFAN = "MONDO:0007947"
VET_MARFAN = "MONDO:0005583"  # Non-human root / vet ancestor


def _make_test_repo(tmp_path: Path) -> MondoRepository:
    db_file = tmp_path / "test_mondo.sqlite"
    conn = sqlite3.connect(db_file)
    conn.executescript(load_schema_sql())

    # Insert terms
    terms = [
        (
            HD,
            "Huntington disease",
            "HUNTINGTON DISEASE",
            "Neurodegenerative disease",
            0,
            None,
            "[]",
            "[]",
            "[]",
        ),
        (
            MARFAN,
            "Marfan syndrome",
            "MARFAN SYNDROME",
            "Connective tissue disorder",
            0,
            None,
            "[]",
            "[]",
            "[]",
        ),
    ]
    conn.executemany(
        "INSERT INTO term (mondo_id, name, name_upper, definition, is_obsolete, replaced_by, "
        "synonyms, subsets, consider) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        terms,
    )

    # Insert term_lookup
    lookups = [
        ("HUNTINGTON DISEASE", HD, "primary", "Huntington disease"),
        ("HD", HD, "exact_synonym", "HD"),
        ("CHOREA MAJOR", HD, "related_synonym", "chorea major"),
        ("MARFAN SYNDROME", MARFAN, "primary", "Marfan syndrome"),
        ("MFS", MARFAN, "exact_synonym", "MFS"),
        ("MARFANOID SYNDROME", MARFAN, "related_synonym", "marfanoid syndrome"),
    ]
    conn.executemany(
        "INSERT INTO term_lookup (lookup_label, mondo_id, label_type, matched_label) VALUES (?, ?, ?, ?)",
        lookups,
    )

    # Insert synonym_trigram
    conn.executemany(
        "INSERT INTO synonym_trigram (mondo_id, lookup_label, label_type, matched_label) VALUES (?, ?, ?, ?)",
        [(r[1], r[3], r[2], r[3]) for r in lookups],
    )

    # Insert term_fts
    conn.executemany(
        "INSERT INTO term_fts (mondo_id, name, synonyms, definition) VALUES (?, ?, ?, ?)",
        [
            (HD, "Huntington disease", "HD chorea major", "Neurodegenerative CAG repeat"),
            (
                MARFAN,
                "Marfan syndrome",
                "MFS marfanoid syndrome",
                "Connective tissue disorder FBN1",
            ),
        ],
    )
    conn.commit()
    conn.close()
    return MondoRepository(db_file)


def test_confidence_for_mapping() -> None:
    assert confidence_for("mondo_id") == 1.0
    assert confidence_for("primary") == 1.0
    assert confidence_for("exact_synonym") == 0.95
    assert confidence_for("xref") == 0.9
    assert confidence_for("related_synonym") == 0.8
    assert confidence_for("fuzzy") == 0.6
    assert confidence_for("unknown_type") == 0.6


def test_resolution_detail_unpacking_and_indexing() -> None:
    detail = ResolutionDetail(
        "exact_synonym",
        HD,
        matched_synonym="HD",
        matched_metadata={"matched_label": "HD", "label_type": "exact_synonym"},
        confidence=0.95,
    )
    # Tuple compatibility
    match_type, mondo_id = detail
    assert match_type == "exact_synonym"
    assert mondo_id == HD
    assert detail[0] == "exact_synonym"
    assert detail[1] == HD

    # Attributes
    assert detail.matched_synonym == "HD"
    assert detail.matched_metadata == {"matched_label": "HD", "label_type": "exact_synonym"}
    assert detail.confidence == 0.95


def test_search_synonyms_exact_and_fuzzy(tmp_path: Path) -> None:
    repo = _make_test_repo(tmp_path)

    # Exact search
    hits = repo.search_synonyms("Huntington")
    assert len(hits) >= 1
    assert hits[0]["mondo_id"] == HD

    # Synonym search
    syn_hits = repo.search_synonyms("chorea major")
    assert len(syn_hits) >= 1
    assert syn_hits[0]["mondo_id"] == HD
    assert syn_hits[0]["matched_label"] == "chorea major"
    assert syn_hits[0]["label_type"] == "related_synonym"

    # Fuzzy partial / typo search
    fuzzy_hits = repo.search_synonyms("marfanoid")
    assert len(fuzzy_hits) >= 1
    assert fuzzy_hits[0]["mondo_id"] == MARFAN
    assert fuzzy_hits[0]["matched_label"] == "marfanoid syndrome"


def test_resolve_disease_synonym_metadata_and_confidence(tmp_path: Path) -> None:
    repo = _make_test_repo(tmp_path)
    service = MondoService(repo)

    # 1. Primary label
    res_primary = service.resolve_disease("Huntington disease")
    assert res_primary["match_type"] == "primary"
    assert res_primary["match_confidence"] == 1.0
    assert "matched_synonym" not in res_primary

    # 2. Exact synonym
    res_syn = service.resolve_disease("HD")
    assert res_syn["match_type"] == "exact_synonym"
    assert res_syn["match_confidence"] == 0.95
    assert res_syn["matched_synonym"] == "HD"
    assert res_syn["matched_synonym_metadata"]["label_type"] == "exact_synonym"

    # 3. Related synonym
    res_rel = service.resolve_disease("chorea major")
    assert res_rel["match_type"] == "related_synonym"
    assert res_rel["match_confidence"] == 0.8
    assert res_rel["matched_synonym"] == "chorea major"

    # 4. Fuzzy fallback on typo
    res_fuzzy = service.resolve_disease("marfanoid")
    assert res_fuzzy["match_type"] == "fuzzy"
    assert res_fuzzy["match_confidence"] == 0.6
    assert res_fuzzy["matched_synonym"] == "marfanoid syndrome"
    assert res_fuzzy["mondo_id"] == MARFAN


def test_search_diseases_exact_synonym_matched_label(tmp_path: Path) -> None:
    repo = _make_test_repo(tmp_path)
    service = MondoService(repo)

    result = service.search_diseases("HD")
    assert result["total"] >= 1
    hits = result["results"]
    assert any(h["mondo_id"] == HD for h in hits)
    hd_hit = next(h for h in hits if h["mondo_id"] == HD)
    assert hd_hit["matched_synonym"] == "HD"


def test_search_diseases_trigram_fallback_with_matched_synonym(tmp_path: Path) -> None:
    # Build database where a synonym is only present in synonym_trigram, not term_fts
    db_file = tmp_path / "trigram_only.sqlite"
    conn = sqlite3.connect(db_file)
    conn.executescript(load_schema_sql())
    conn.execute(
        "INSERT INTO term VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            MARFAN,
            "Marfan syndrome",
            "MARFAN SYNDROME",
            "Connective tissue disorder",
            0,
            None,
            "[]",
            "[]",
            "[]",
        ),
    )
    conn.execute(
        "INSERT INTO synonym_trigram VALUES (?, ?, ?, ?)",
        (MARFAN, "arachnodactyly", "related_synonym", "arachnodactyly"),
    )
    conn.commit()
    conn.close()

    repo = MondoRepository(db_file)
    service = MondoService(repo)

    result = service.search_diseases("arachnodactyly")
    assert result["total"] >= 1
    hits = result["results"]
    assert any(h["mondo_id"] == MARFAN for h in hits)
    marfan_hit = next(h for h in hits if h["mondo_id"] == MARFAN)
    assert marfan_hit["matched_synonym"] == "arachnodactyly"
