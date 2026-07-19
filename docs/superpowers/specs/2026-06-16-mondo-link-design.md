# mondo-link — Design Spec

**Date:** 2026-06-16
**Status:** Approved scope (brainstorming forks locked by user)

> Historical record — this document records the design as of its date. Current behavior is
> defined by implemented code, standards, release evidence, and tests.

**Author:** mondo-link contributors

## 1. Purpose

`mondo-link` is an MCP + REST server that grounds disease work in the **Mondo
Disease Ontology**. It builds a local SQLite index from the Monarch Mondo
release and exposes read-only tools for disease lookup, the `is_a` hierarchy
(ancestors/descendants via a transitive closure), and cross-ontology mapping
(OMIM ↔ Orphanet ↔ DOID ↔ NCIT ↔ UMLS ↔ MeSH ↔ MONDO …).

It joins the `-link` fleet and **mirrors the mgi-link stack and architecture
exactly**. mgi-link's Mammalian-Phenotype ontology closure (`mp_closure`,
top-systems derived from the MP OBO) is the direct analog for Mondo's `is_a`
hierarchy, so its `ingest`/`services`/`mcp` scaffolding is cloned and
re-domained, not reinvented.

Research use only; not clinical decision support. Every claim is grounded in
the local index and cites the **MONDO id + Mondo release version**.

## 2. Locked scope decisions

| Fork | Decision |
|------|----------|
| **Parse source** | **OBO primary + SSSOM.** Parse `mondo.obo` for the graph (terms, `is_a`, synonyms, defs, xrefs, obsolete/`replaced_by`) by cloning mgi-link's `parse_mp_obo` → `parse_mondo_obo`; ingest `mondo.sssom.tsv` for curated mappings. `mondo.json` deferred (YAGNI). |
| **Xref model** | **Merge OBO xrefs + SSSOM into one `xref` index**, each row tagged `origin` (`obo_xref`\|`sssom`), `predicate` (`exactMatch`\|`closeMatch`\|`broadMatch`\|`narrowMatch`\|`equivalentTo`\|`xref`), and `source` provenance. `resolve_xref` ranks by predicate. |
| **Tool surface** | **Full set (11 tools)** incl. a dedicated `resolve_disease` (text/xref → canonical MONDO with `match_type` + ambiguity), mirroring mgi-link's `resolve_marker`. |

## 3. Architecture (two planes)

```
mondo_link/
  __init__.py            __version__
  config.py              MONDO_LINK_ pydantic-settings (PURLs, data dir, refresh, cors, transport, mcp_path)
  constants.py           SCHEMA_VERSION, MONDO_ROOT, xref-prefix catalogue + aliases, citation, CC-BY-4.0 license
  identifiers.py         normalize_mondo_id (MONDO:NNNNNNN), normalize_xref (Orphanet→ORPHA), infer_xref_source
  exceptions.py          MondoError + NotFound/Withdrawn/Ambiguous/InvalidInput/DataUnavailable/RateLimit/ServiceUnavailable/Download
  logging_config.py      structlog → stderr only (stdout sacred on stdio)
  buildinfo.py           build provenance for /health
  app.py                 FastAPI: /health, / ; lifespan bootstraps index + refresh scheduler
  server_manager.py      UnifiedServerManager: unified | http | stdio   (cloned)
  ingest/
    downloader.py        conditional GET (ETag/Last-Modified) + download_cache.json   (cloned shape)
    lock.py              fcntl build lock                                              (cloned verbatim)
    parser.py            parse_mondo_obo, mondo_closure_pairs, mondo_top_groupings, parse_mondo_sssom
    builder.py           atomic build (mkstemp + os.replace), schema.sql, meta provenance
    schema.sql           full DDL (see §4)
    cli.py               typer: build | refresh | status
  data/repository.py     read-only SQLite (file:...?mode=ro), row_factory=Row
  services/
    mondo_service.py     high-level ops returning plain dicts
    shaping.py           response_mode projection (minimal|compact|standard|full)
    pagination.py        page_fields truncation block                                 (cloned)
    refresh.py           bootstrap_data + refresh scheduler                            (cloned shape)
  mcp/                   DOMAIN-AGNOSTIC SCAFFOLDING — cloned from mgi-link
    envelope.py          run_mcp_tool, _classify, _error_envelope, McpErrorContext, McpToolError  (cloned)
    annotations.py       READ_ONLY_OPEN_WORLD                                          (cloned verbatim)
    schemas.py           per-tool output_schema (permissive envelope)
    next_commands.py     next_commands builders per tool
    capabilities.py      TOOLS list + build_capabilities + resources
    middleware.py        ArgValidationMiddleware (alias normalize + arg errors)        (cloned verbatim)
    arg_help.py          ARG_ALIASES, tool_signature, did_you_mean, describe_constraints
    facade.py            create_mondo_mcp() — register tools + resources + middleware
    service_adapters.py  lazy MondoService singleton
    resources.py         server instructions + usage/reference notes
    tools/
      _common.py         ResponseMode, QueryStr, MondoIdStr, XrefIdStr annotated types
      discovery.py       get_server_capabilities, get_diagnostics
      diseases.py        resolve_disease, search_diseases, get_disease
      hierarchy.py       get_disease_ancestors/descendants/parents/children
      xref.py            resolve_xref, map_cross_ontology
server.py                argparse transport entry (unified|http|stdio)                 (cloned)
mcp_server.py            stdio entry for Claude Desktop                                (cloned)
scripts/check_file_size.py   500-line/file budget                                      (cloned)
docker/Dockerfile  Makefile  AGENTS.md  CLAUDE.md  README.md  docs/{architecture,usage,deployment}.md
```

**Plane boundary (non-negotiable):** the data plane builds & reads the SQLite
index and returns **plain dicts**; the MCP plane (`mcp/`) is domain-agnostic
scaffolding shared with siblings. `run_mcp_tool` owns `success`/`_meta` and
converts exceptions to **returned** structured errors (never raised to client).

## 4. Data model (SQLite — atomic `os.replace` build under fcntl lock)

`PRAGMA journal_mode = WAL;` Built into a temp file, then `os.replace` onto the
final `mondo.sqlite`; the build holds `.build.lock`.

```sql
CREATE TABLE term (
    mondo_id     TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    name_upper   TEXT NOT NULL,
    definition   TEXT,
    is_obsolete  INTEGER NOT NULL DEFAULT 0,
    replaced_by  TEXT,                 -- MONDO:NNNNNNN successor (obsolete terms)
    consider     TEXT,                 -- JSON array of MONDO ids
    synonyms     TEXT,                 -- JSON array {text, scope, type, sources[]}
    subsets      TEXT                  -- JSON array (clingen, rare, ordo_disease, …)
);
CREATE INDEX idx_term_name_upper ON term (name_upper);

CREATE TABLE term_lookup (             -- resolve_disease text→MONDO
    lookup_label TEXT NOT NULL,        -- uppercased label/synonym
    mondo_id     TEXT NOT NULL,
    label_type   TEXT NOT NULL         -- primary | exact_synonym | related_synonym | broad_synonym | narrow_synonym
);
CREATE INDEX idx_term_lookup ON term_lookup (lookup_label);

CREATE VIRTUAL TABLE term_fts USING fts5 (
    mondo_id UNINDEXED, name, synonyms, definition,
    tokenize = 'porter unicode61'
);

CREATE TABLE mondo_parent (            -- direct is_a edges
    mondo_id  TEXT NOT NULL,
    parent_id TEXT NOT NULL
);
CREATE INDEX idx_mondo_parent ON mondo_parent (mondo_id);
CREATE INDEX idx_mondo_parent_rev ON mondo_parent (parent_id);

CREATE TABLE mondo_closure (           -- transitive is_a, incl. self-pair
    mondo_id    TEXT NOT NULL,
    ancestor_id TEXT NOT NULL
);
CREATE INDEX idx_mondo_closure ON mondo_closure (mondo_id);
CREATE INDEX idx_mondo_closure_anc ON mondo_closure (ancestor_id);

CREATE TABLE mondo_top_grouping (      -- direct children of MONDO:0000001 (orientation/roll-up)
    mondo_id      TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    display_order INTEGER
);

CREATE TABLE xref (                    -- MERGED OBO xref: + SSSOM rows
    mondo_id        TEXT NOT NULL,
    prefix          TEXT NOT NULL,     -- OMIM | ORPHA | DOID | NCIT | UMLS | MESH | MEDGEN | SCTID | GARD | ICD10CM | …
    object_id       TEXT NOT NULL,     -- normalized CURIE, e.g. OMIM:182212
    object_id_upper TEXT NOT NULL,
    predicate       TEXT NOT NULL,     -- exactMatch | closeMatch | broadMatch | narrowMatch | equivalentTo | xref
    origin          TEXT NOT NULL,     -- obo_xref | sssom
    source          TEXT               -- provenance (mapping_justification / OBO source=…)
);
CREATE INDEX idx_xref_mondo ON xref (mondo_id);
CREATE INDEX idx_xref_obj ON xref (prefix, object_id_upper);

CREATE TABLE meta (
    id                INTEGER PRIMARY KEY CHECK (id = 1),
    schema_version    INTEGER,
    mondo_version     TEXT,            -- from OBO data-version: / date:
    source_purls      TEXT,            -- JSON {obo, sssom}
    source_validators TEXT,            -- JSON per-file {etag, last_modified}
    term_count        INTEGER,
    obsolete_count    INTEGER,
    closure_count     INTEGER,
    xref_count        INTEGER,
    mapping_count     INTEGER,         -- sssom rows
    build_utc         TEXT,
    build_duration_s  REAL
);
```

**Closure** is generated by the cloned recursive generator with cycle guard
(`mondo_closure_pairs`), handling Mondo's multi-parent DAG. **Top groupings**
are direct children of `MONDO_ROOT = "MONDO:0000001"` (the mp_top_system
analog), ordered by name. **Version** comes from the OBO header.

**xref predicate ranking** (for `resolve_xref` ordering and `resolve_disease`
xref redirect): `exactMatch` (SSSOM) > `equivalentTo` (OBO `{source="MONDO:equivalentTo"}`)
> `closeMatch` > `broadMatch`/`narrowMatch` > plain `xref`.

## 5. Tools (11)

Each tool: `output_schema` + `READ_ONLY_OPEN_WORLD` annotations; first
description sentence is a discovery summary ending with `Signature: tool(args…)`;
`response_mode` ∈ {minimal, compact, standard, full}; listed in
`capabilities.TOOLS`. Every response carries `_meta.next_commands`.

| # | Tool | Signature | Returns |
|---|------|-----------|---------|
| 1 | `get_server_capabilities` | `(detail=summary\|full)` | server/version/Mondo-release, tools, vocab, error codes, policies |
| 2 | `get_diagnostics` | `()` | index built?, counts, Mondo version, data paths |
| 3 | `resolve_disease` | `(query, response_mode)` | canonical MONDO + `match_type` (mondo_id\|primary\|exact_synonym\|related_synonym\|xref); `ambiguous_query` → candidates |
| 4 | `search_diseases` | `(query, limit=25, include_obsolete=false, response_mode)` | FTS-ranked `[{mondo_id, name, definition?, score}]` + truncation block |
| 5 | `get_disease` | `(term, response_mode)` | def, synonyms, grouped xrefs, direct parents+children, top groupings, subsets, obsolete/`replaced_by` |
| 6 | `get_disease_ancestors` | `(term, limit, response_mode)` | transitive `is_a` ancestors (closure) |
| 7 | `get_disease_descendants` | `(term, limit, response_mode)` | transitive `is_a` descendants (closure) |
| 8 | `get_disease_parents` | `(term, response_mode)` | direct `is_a` parents |
| 9 | `get_disease_children` | `(term, response_mode)` | direct `is_a` children |
| 10 | `resolve_xref` | `(id, limit, response_mode)` | external id (OMIM/ORPHA/DOID/NCIT/UMLS/MeSH…) → MONDO ids ranked by predicate |
| 11 | `map_cross_ontology` | `(term, prefixes?, response_mode)` | a MONDO id → all xrefs grouped by prefix |

`term` args accept a MONDO id, a label/synonym, or an external xref (resolved
first). An obsolete MONDO term raises `WithdrawnEntryError` carrying
`replaced_by`/`consider`, surfaced as a `not_found`-class error with recovery
`next_commands` chaining to the successor (mirrors mgi-link's withdrawn
contract).

## 6. Error handling & grounding

- **7-code taxonomy** (cloned `mcp/envelope.py`): `invalid_input`, `not_found`,
  `ambiguous_query`, `data_unavailable`, `rate_limited`,
  `upstream_unavailable`, `internal_error`. Services raise typed exceptions;
  `run_mcp_tool` converts them to **returned** error envelopes with
  `retryable` + `recovery_action` + `_meta.next_commands`.
- **Identifiers** (`identifiers.py`): `MONDO:NNNNNNN` canonicalization (accepts
  `MONDO:0008426`, `mondo:0008426`, bare `0008426`); xref normalization
  (`Orphanet`→`ORPHA`, case-fold prefixes); `infer_xref_source` redirects an
  external id thrown at `resolve_disease` into `resolve_xref`.
- **Grounding**: every payload includes the MONDO id and the Mondo release
  version (from `meta.mondo_version`). Constants carry the CC BY 4.0 license and
  the Mondo citation (Vasilevsky et al., *Mondo: Unifying diseases for the
  world, by the world*). Research-use-only notice on capabilities + resources.

## 7. Config (`MONDO_LINK_` env prefix, pydantic-settings)

Host/port/transport/mcp_path/cors/log; nested `data`: `data_dir`,
`db_filename=mondo.sqlite`, PURLs (`obo`, `sssom`), `download_timeout`,
`user_agent`, `auto_bootstrap`, `refresh_enabled`, `refresh_interval_hours`,
`build_lock_timeout`, `cache_size`, `cache_ttl`. Defaults mirror mgi-link.

PURLs:
- `http://purl.obolibrary.org/obo/mondo.obo`
- `http://purl.obolibrary.org/obo/mondo.sssom.tsv`

## 8. Testing & Definition of Done

- **Fixtures** under `tests/fixtures/`: a hand-built ~15-term `mondo.obo`
  (multi-parent term, obsolete+`replaced_by`, synonyms with scopes, OBO xrefs)
  and a matching `mondo.sssom.tsv`. Tests build a real SQLite index from these.
- **Unit tests** per module: identifiers, parser (OBO + SSSOM + closure +
  top-groupings), builder/schema, downloader (respx 200/304 conditional GET),
  lock, repository/service queries (incl. ancestors/descendants/xref ranking),
  shaping, pagination, envelope, next_commands, arg_help, each tool e2e against
  the fixture DB, tool-name ↔ `capabilities.TOOLS` sync.
- **Definition of done:** `make ci-local` green = `format-check`, `lint-ci`,
  `lint-loc` (≤500 lines/file), `typecheck` (mypy strict), `test-fast`
  (coverage ≥80%). A full ingest against the real local `mondo.obo` succeeds
  and `mondo-link-data status` prints the release.

## 9. Phased plan (waves — detailed in the implementation plan)

- **Wave 0 — serial foundation + frozen contracts.** Scaffold
  pyproject/Makefile/Docker, package skeleton, config/constants/identifiers/
  exceptions/logging/buildinfo, clone `mcp/` + server files, and freeze the two
  contracts every parallel task codes against: `schema.sql` and the
  `MondoService` method signatures.
- **Wave 1 — parallel (TDD per unit).** (A) ingest parser+builder+closure+
  downloader+cli; (B) repository + services; (C) mcp tool modules + schemas +
  next_commands + capabilities; (D) docs.
- **Wave 2 — integration.** Wire facade/app/server, full ingest against the
  real `mondo.obo`, `make ci-local` green, coverage ≥80%,
  verification-before-completion.

## 10. Non-goals (v1)

`mondo.json` ingest; logical-definition / equivalence-axiom reasoning;
write/curation endpoints; non-`is_a` relationship traversal beyond storage;
live Mondo API fallback (the local index is the single source of truth).
