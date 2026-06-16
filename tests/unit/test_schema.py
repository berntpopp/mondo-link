"""Tests for the frozen SQLite schema (CONTRACT BARRIER)."""

from __future__ import annotations

import sqlite3

from mondo_link.ingest.schema import load_schema_sql

# The 8 named tables/virtual-tables the schema must create.
EXPECTED_TABLES = {
    "term",
    "term_lookup",
    "term_fts",
    "mondo_parent",
    "mondo_closure",
    "mondo_top_grouping",
    "xref",
    "meta",
}


def test_schema_creates_all_tables() -> None:
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(load_schema_sql())
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        names = {row[0] for row in rows}
    finally:
        conn.close()
    missing = EXPECTED_TABLES - names
    assert not missing, f"schema is missing tables: {sorted(missing)}"


def test_schema_term_fts_is_virtual_fts5() -> None:
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(load_schema_sql())
        sql = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'term_fts'").fetchone()[0]
    finally:
        conn.close()
    assert "VIRTUAL TABLE" in sql
    assert "fts5" in sql.lower()
