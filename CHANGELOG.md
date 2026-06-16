# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
