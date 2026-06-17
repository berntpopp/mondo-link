# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- **Output-schema leak (P0):** `get_disease.xrefs` and `map_cross_ontology.mappings`
  are grouped-by-prefix objects but were declared as `array` in their
  `output_schema`, so FastMCP rejected the tool's own valid output and surfaced a
  raw `{...} is not of type 'array'` string instead of an envelope. Schemas now
  declare the grouped-object shape. A new `tests/unit/test_output_schemas.py`
  round-trips every tool's real output (all response modes + error cases) through
  its declared `output_schema`, so this class of drift fails CI.
- `resolve_xref.total` reported only the returned page size (never the full match
  count), so it could silently truncate without setting `truncated`; it now uses a
  true distinct-term count.

### Added

- **Forward pagination (P2):** `search_diseases`, `get_disease_ancestors`,
  `get_disease_descendants`, and `resolve_xref` accept `offset=` and return
  `offset` + `next_offset`; when truncated, `_meta.next_commands` includes a
  ready-to-call forward-page step that advances `offset` without re-sending rows
  (alongside the existing widen step).
- **`capabilities_version` (P2):** a content hash of the discovery contract is
  echoed in every `_meta` (and in `get_server_capabilities`); a warm client diffs
  it to skip re-fetching capabilities while unchanged.
- **Sparse fieldsets (P2):** `get_disease` and `map_cross_ontology` accept
  `fields=[...]` (top-level keys, or dotted into a group e.g. `xrefs.OMIM`);
  identity anchors are always returned.
- **Runtime metrics (P3):** `get_diagnostics` now returns a `runtime` block with
  request/error counts and latency percentiles (p50/p95/p99) from an in-process
  collector.

### Changed

- **Slimmer `search_diseases` (P1):** compact (default) now returns
  `mondo_id + name + score + definition_snippet` (≤140 chars); the full definition
  is reserved for `standard`/`full`, cutting tokens on the broadest-fan-out tool.
- **Answer-embedding `not_found` (P1):** a free-text label miss now attaches the
  closest search hits as `candidates` and chains `_meta.next_commands` straight to
  `get_disease` on the top hit, instead of merely routing back to the search tool.

## [0.1.0] - 2026-06-16

### Added

- Initial release of `mondo-link`, an MCP + REST server grounding disease work
  in the Mondo Disease Ontology.
- **Data plane:** conditional-GET downloader (ETag / Last-Modified) for the
  Mondo OBO + SSSOM Monarch PURLs; `fcntl` build lock; OBO + SSSOM parser with
  transitive `is_a` closure and top-grouping derivation; atomic SQLite builder
  (temp + `os.replace`) with a `meta` provenance table; `mondo-link-data`
  CLI (`build` / `refresh` / `status`).
- **Index:** terms, labels, synonyms, definitions, `is_a` closure, top
  groupings, and a merged OBO + SSSOM cross-reference table with provenance and
  mapping predicate (OMIM / Orphanet / DOID / NCIT / UMLS / MeSH / MedGen /
  SNOMED / GARD), plus deprecated / `replaced_by` handling.
- **MCP plane:** 11 read-only tools — `get_server_capabilities`,
  `get_diagnostics`, `resolve_disease`, `search_diseases`, `get_disease`,
  `get_disease_ancestors`, `get_disease_descendants`, `get_disease_parents`,
  `get_disease_children`, `resolve_xref`, `map_cross_ontology` — each with
  `output_schema`, `READ_ONLY_OPEN_WORLD` annotations, `response_mode`, and
  `_meta.next_commands`. 7-code structured error taxonomy returned via the
  envelope; `mondo://` discovery resources.
- **Server:** FastAPI + uvicorn unified server (`/health` + `/mcp`) and a stdio
  entry point (`mcp_server.py`) for Claude Desktop.
- Docs, Docker, Makefile, and a `make ci-local` gate (ruff format/lint, 500-line
  budget, mypy strict, pytest with ≥80% coverage).
