"""Unit tests for the atomic Mondo SQLite builder (real fixture build)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from mondo_link.config import ServerSettings
from mondo_link.ingest import builder

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


@pytest.fixture
def config(tmp_path: Path) -> ServerSettings:
    settings = ServerSettings()
    settings.data.data_dir = tmp_path
    settings.data.db_filename = "mondo.sqlite"
    return settings


@pytest.fixture
def built(config: ServerSettings) -> builder.BuildMeta:
    paths = {"obo": FIXTURES / "mondo.obo", "sssom": FIXTURES / "mondo.sssom.tsv"}
    validators = {
        "obo": {"etag": '"v1"', "last_modified": "Mon, 01 Jun 2026 00:00:00 GMT"},
        "sssom": {"etag": '"v2"', "last_modified": None},
    }
    return builder.build_database(config, paths=paths, validators=validators)


def _conn(config: ServerSettings) -> sqlite3.Connection:
    conn = sqlite3.connect(config.data.db_path)
    conn.row_factory = sqlite3.Row
    return conn


def test_build_meta(built: builder.BuildMeta) -> None:
    assert "2026-06-01" in built.mondo_version
    assert built.term_count == 8  # 7 active + 1 obsolete = all stanzas
    assert built.obsolete_count == 1
    assert built.closure_count > 0
    assert built.mapping_count == 3


def test_db_term_count(config: ServerSettings, built: builder.BuildMeta) -> None:
    conn = _conn(config)
    try:
        n = conn.execute("SELECT COUNT(*) FROM term").fetchone()[0]
        assert n == built.term_count
    finally:
        conn.close()


def test_closure_pair_to_root(config: ServerSettings, built: builder.BuildMeta) -> None:
    conn = _conn(config)
    try:
        row = conn.execute(
            "SELECT 1 FROM mondo_closure WHERE mondo_id = ? AND ancestor_id = ?",
            ("MONDO:0008426", "MONDO:0000001"),
        ).fetchone()
        assert row is not None
    finally:
        conn.close()


def test_term_lookup_primary_and_synonym(config: ServerSettings, built: builder.BuildMeta) -> None:
    conn = _conn(config)
    try:
        types = {
            r["label_type"]
            for r in conn.execute(
                "SELECT label_type FROM term_lookup WHERE mondo_id = ?", ("MONDO:0008426",)
            ).fetchall()
        }
        assert "primary" in types
        assert "exact_synonym" in types
        assert "related_synonym" in types
    finally:
        conn.close()


def test_xref_origins(config: ServerSettings, built: builder.BuildMeta) -> None:
    conn = _conn(config)
    try:
        origins = {
            r["origin"]
            for r in conn.execute(
                "SELECT origin FROM xref WHERE mondo_id = ?", ("MONDO:0008426",)
            ).fetchall()
        }
        assert "obo_xref" in origins
        assert "sssom" in origins
        # object_id_upper populated
        row = conn.execute(
            "SELECT object_id, object_id_upper, prefix FROM xref "
            "WHERE mondo_id = ? AND prefix = 'ORPHA' LIMIT 1",
            ("MONDO:0008426",),
        ).fetchone()
        assert row is not None
        assert row["object_id_upper"] == row["object_id"].upper()
    finally:
        conn.close()


def test_top_grouping_rows(config: ServerSettings, built: builder.BuildMeta) -> None:
    conn = _conn(config)
    try:
        n = conn.execute("SELECT COUNT(*) FROM mondo_top_grouping").fetchone()[0]
        assert n == 3
    finally:
        conn.close()


def test_read_meta_roundtrip(config: ServerSettings, built: builder.BuildMeta) -> None:
    meta = builder.read_meta(config.data.db_path)
    assert meta is not None
    assert meta.mondo_version == built.mondo_version
    assert meta.obsolete_count == 1


def test_build_without_sssom(config: ServerSettings) -> None:
    paths = {"obo": FIXTURES / "mondo.obo", "sssom": None}
    meta = builder.build_database(config, paths=paths, validators={})
    assert meta.mapping_count == 0
    assert meta.term_count > 0


def test_rebuild_result_shape(config: ServerSettings, built: builder.BuildMeta) -> None:
    meta = builder.read_meta(config.data.db_path)
    result = builder.RebuildResult(changed=False, not_modified=True, meta=meta)
    assert result.changed is False
    assert result.not_modified is True
    assert result.meta is meta
