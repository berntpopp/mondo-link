# mondo-link Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `mondo-link`, an MCP+REST server that grounds disease work in the Mondo Disease Ontology via a locally-built SQLite index, mirroring the mgi-link stack/architecture exactly.

**Architecture:** Two planes. The **data plane** (`config`/`constants`/`identifiers`/`ingest`/`data`/`services`) downloads `mondo.obo` + `mondo.sssom.tsv` via conditional GET, atomically builds a SQLite index (terms, synonyms, defs, `is_a` closure, merged OBO+SSSOM xrefs, obsolete/`replaced_by`), and returns plain dicts. The **MCP plane** (`mcp/`) is domain-agnostic scaffolding cloned verbatim from mgi-link; `run_mcp_tool` owns `success`/`_meta` and converts exceptions to returned structured errors.

**Tech Stack:** Python 3.12, uv, hatchling; fastmcp 3 + mcp[cli]; FastAPI+uvicorn; pydantic v2 + pydantic-settings; httpx, structlog, orjson, typer; ruff + mypy strict; pytest (+xdist, respx, cov).

**Clone source:** `/home/bernt-popp/development/mgi-link` (referred to below as `$MGI`). Sibling conventions: `../hgnc-link`, `../gencc-link`, `../gnomad-link`. Local Mondo data for real-ingest test: `/home/bernt-popp/development/omim-scrape/data/external/mondo.obo`.

**Naming map (apply globally when cloning):** package `mgi_link`→`mondo_link`; class `MgiService`→`MondoService`; `MgiRepository`→`MondoRepository`; env prefix `MGI_LINK_`→`MONDO_LINK_`; resource scheme `mgi://`→`mondo://`; db `mgi.sqlite`→`mondo.sqlite`; factory `create_mgi_mcp`→`create_mondo_mcp`; ids `MGI:`/`MP:`→`MONDO:`.

---

## Frozen contracts (Wave 0 produces these; Wave 1 codes against them)

### Contract A — `mondo_link/ingest/schema.sql`
The full DDL is reproduced verbatim in Task 0.5. Tables: `term`, `term_lookup`, `term_fts` (FTS5), `mondo_parent`, `mondo_closure`, `mondo_top_grouping`, `xref`, `meta`.

### Contract B — `MondoService` public methods (in `mondo_link/services/mondo_service.py`)
All return plain `dict[str, Any]`; raise typed exceptions from `mondo_link.exceptions` (never return error envelopes). Implemented in Wave 1B, consumed by Wave 1C.

```python
class MondoService:
    def __init__(self, repository: "MondoRepository | None") -> None: ...
    @property
    def repo(self) -> "MondoRepository": ...   # raises DataUnavailableError if None

    def get_diagnostics(self) -> dict[str, Any]: ...
    def resolve_disease(self, query: str, *, response_mode: str = "compact") -> dict[str, Any]: ...
    def search_diseases(self, query: str, *, limit: int = 25,
                        include_obsolete: bool = False, response_mode: str = "compact") -> dict[str, Any]: ...
    def get_disease(self, term: str, *, response_mode: str = "compact") -> dict[str, Any]: ...
    def get_ancestors(self, term: str, *, limit: int = 200, response_mode: str = "compact") -> dict[str, Any]: ...
    def get_descendants(self, term: str, *, limit: int = 200, response_mode: str = "compact") -> dict[str, Any]: ...
    def get_parents(self, term: str, *, response_mode: str = "compact") -> dict[str, Any]: ...
    def get_children(self, term: str, *, response_mode: str = "compact") -> dict[str, Any]: ...
    def resolve_xref(self, xref_id: str, *, limit: int = 50, response_mode: str = "compact") -> dict[str, Any]: ...
    def map_cross_ontology(self, term: str, *, prefixes: list[str] | None = None,
                           response_mode: str = "compact") -> dict[str, Any]: ...
    # internal helper used by term-accepting tools:
    def _resolve_term_id(self, term: str) -> str: ...   # id|label|xref -> MONDO id, raises NotFound/Withdrawn/Ambiguous
```

`MondoRepository` (in `mondo_link/data/repository.py`) opens `sqlite3.connect(f"file:{db}?mode=ro", uri=True, check_same_thread=False)` with `row_factory=sqlite3.Row` and exposes the row-level queries each service method needs (see Task 1B).

### Contract C — parser outputs (in `mondo_link/ingest/parser.py`)
```python
def parse_mondo_obo(text: str) -> dict[str, dict[str, Any]]: ...
# {mondo_id: {id, name, definition, parents:[MONDO..], synonyms:[{text,scope,type,sources}],
#             xrefs:[{prefix,object_id,predicate,source}], subsets:[str], obsolete:bool,
#             replaced_by:str|None, consider:[str]}}
def parse_obo_header(text: str) -> dict[str, str]: ...        # {data_version, date}
def mondo_closure_pairs(terms: dict[str, dict[str, Any]]) -> Iterator[tuple[str, str]]: ...   # (mondo_id, ancestor_id) incl self
def mondo_top_groupings(terms: dict[str, dict[str, Any]]) -> list[tuple[str, str, int]]: ...  # direct children of MONDO:0000001
def parse_mondo_sssom(text: str) -> Iterator[dict[str, Any]]: ...
# {subject_id(MONDO), object_id(prefix:id), predicate(exactMatch|closeMatch|broadMatch|narrowMatch), source}
```

---

## WAVE 0 — Serial foundation (must complete before Wave 1)

### Task 0.1: Project scaffold

**Files:**
- Create: `pyproject.toml`, `Makefile`, `scripts/check_file_size.py`, `docker/Dockerfile`, `docker/docker-compose.yml`, `docker/README.md`, `mondo_link/__init__.py`

- [ ] **Step 1: Clone `$MGI/pyproject.toml`** to `pyproject.toml`. Edit: `name="mondo-link"`, `version="0.1.0"`, description "MCP/API server that grounds disease work in the Mondo Disease Ontology", keywords `["mondo","disease","ontology","mcp","api","bioinformatics","cross-reference","omim","orphanet"]`, scripts `mondo-link="server:main"`, `mondo-link-mcp="mcp_server:main"`, `mondo-link-data="mondo_link.ingest.cli:main"`, `[tool.hatch.build.targets.wheel] packages=["mondo_link"]`, `[tool.coverage.run] source=["mondo_link"]`, ruff/mypy `per-file-ignores`/format targets `mondo_link tests server.py mcp_server.py scripts`, markers reference "Mondo/Monarch" instead of MGI/MouseMine. Keep all dependency pins identical.
- [ ] **Step 2: Clone `$MGI/Makefile`** to `Makefile`. Replace `mgi_link`→`mondo_link`, `mgi-link`→`mondo-link`, data target comments to "Download Mondo and build the local index". Keep `ci-local: format-check lint-ci lint-loc typecheck test-fast`.
- [ ] **Step 3: Clone `$MGI/scripts/check_file_size.py`** verbatim, change `ROOTS = ("mondo_link", "tests")`. (MAX_LINES=500 unchanged.)
- [ ] **Step 4: Clone `$MGI/docker/*`** with `mgi`→`mondo` renames (service name, port stays 8000).
- [ ] **Step 5: Create `mondo_link/__init__.py`** with `__version__ = "0.1.0"`.
- [ ] **Step 6: `uv sync --group dev`** then commit.

Run: `uv sync --group dev` → Expected: resolves env. Run: `uv run python scripts/check_file_size.py` → Expected: exit 0.

```bash
git add -A && git commit -m "chore: project scaffold (pyproject, Makefile, docker, file-size budget)"
```

### Task 0.2: Domain primitives — config, constants, identifiers, exceptions, logging, buildinfo

**Files:**
- Create: `mondo_link/config.py`, `mondo_link/constants.py`, `mondo_link/identifiers.py`, `mondo_link/exceptions.py`, `mondo_link/logging_config.py`, `mondo_link/buildinfo.py`
- Test: `tests/unit/test_identifiers.py`, `tests/unit/test_config.py`

- [ ] **Step 1: Clone `$MGI/mondo_link/exceptions.py`** (from `$MGI/mgi_link/exceptions.py`) verbatim with rename. It defines: `MondoError` base + `NotFoundError`, `WithdrawnEntryError`, `AmbiguousQueryError`, `InvalidInputError`, `DataUnavailableError`, `RateLimitError`, `ServiceUnavailableError`, `DownloadError`. Verify `WithdrawnEntryError` carries `replaced_by`/`withdrawn_status` attributes (used by envelope); if mgi names differ, keep mgi's attribute names.
- [ ] **Step 2: Clone `$MGI/mgi_link/logging_config.py`** and `buildinfo.py` verbatim with rename (structlog→stderr; build_info reads version/git).
- [ ] **Step 3: Clone `$MGI/mgi_link/config.py`** → `config.py`. Set `env_prefix="MONDO_LINK_"`. Nested `MondoDataConfig`: `db_filename="mondo.sqlite"`, PURLs `obo_url="http://purl.obolibrary.org/obo/mondo.obo"`, `sssom_url="http://purl.obolibrary.org/obo/mondo.sssom.tsv"`, `user_agent="mondo-link/{__version__} (+https://github.com/berntpopp/mondo-link)"`, keep `download_timeout=300`, `auto_bootstrap=True`, `refresh_enabled=False`, `refresh_interval_hours=168.0`, `build_lock_timeout=900`, `cache_size`, `cache_ttl`. Drop the `mousemine` nested config entirely. Keep host/port/transport/mcp_path/cors/log fields and the `settings = ServerSettings()` singleton.
- [ ] **Step 4: Write `tests/unit/test_config.py`** — assert env prefix override (`MONDO_LINK_PORT`), default db filename, PURL defaults, mcp_path leading-slash validator.
- [ ] **Step 5: Write `mondo_link/identifiers.py`** (test-first; write `tests/unit/test_identifiers.py` first):

```python
from __future__ import annotations
import re

_MONDO_ID_RE = re.compile(r"^MONDO:(\d{7})$", re.IGNORECASE)
_BARE_ID_RE = re.compile(r"^\d{7}$")
# external xref shapes for resolve_disease -> resolve_xref redirect
_XREF_PREFIX_RE = re.compile(r"^([A-Za-z][A-Za-z0-9]*):(.+)$")
_KNOWN_PREFIX_ALIASES = {"ORPHANET": "ORPHA", "ORPHA": "ORPHA", "OMIM": "OMIM",
    "MIM": "OMIM", "DOID": "DOID", "NCIT": "NCIT", "UMLS": "UMLS", "MESH": "MESH",
    "MSH": "MESH", "MEDGEN": "MEDGEN", "SCTID": "SCTID", "SNOMEDCT": "SCTID",
    "GARD": "GARD", "ICD10CM": "ICD10CM", "ICD10": "ICD10", "EFO": "EFO"}

def normalize_mondo_id(value: str) -> str | None:
    text = (value or "").strip()
    m = _MONDO_ID_RE.match(text)
    if m:
        return f"MONDO:{m.group(1)}"
    if _BARE_ID_RE.match(text):
        return f"MONDO:{text}"
    return None

def looks_like_mondo_id(value: str) -> bool:
    return normalize_mondo_id(value) is not None

def normalize_xref(value: str) -> str | None:
    """Normalize an external CURIE: case-fold prefix, Orphanet->ORPHA. Returns 'PREFIX:local' or None."""
    text = (value or "").strip()
    m = _XREF_PREFIX_RE.match(text)
    if not m:
        return None
    prefix, local = m.group(1).upper(), m.group(2).strip()
    prefix = _KNOWN_PREFIX_ALIASES.get(prefix, prefix)
    if not local:
        return None
    return f"{prefix}:{local}"

def xref_prefix(value: str) -> str | None:
    norm = normalize_xref(value)
    return norm.split(":", 1)[0] if norm else None

def infer_xref_source(value: str) -> str | None:
    """True external (non-MONDO) CURIE shape -> its normalized prefix; else None."""
    if looks_like_mondo_id(value):
        return None
    return xref_prefix(value)
```

Tests: `normalize_mondo_id("MONDO:0008426")=="MONDO:0008426"`, `"mondo:0008426"` ok, `"0008426"` ok, `"MONDO:123"` (not 7 digits) → None, `"WT1"`→None; `normalize_xref("Orphanet:2462")=="ORPHA:2462"`, `"omim:182212"=="OMIM:182212"`, `"DOID:0050776"` ok; `infer_xref_source("OMIM:182212")=="OMIM"`, `infer_xref_source("MONDO:0008426") is None`.

- [ ] **Step 6: Write `mondo_link/constants.py`**: `SCHEMA_VERSION = 1`; `MONDO_ROOT = "MONDO:0000001"`; `XREF_PREFIXES` first-class tuple `("OMIM","ORPHA","DOID","NCIT","UMLS","MESH","MEDGEN","SCTID","GARD")`; `PREDICATE_RANK = {"exactMatch":0,"equivalentTo":1,"closeMatch":2,"narrowMatch":3,"broadMatch":4,"xref":5}`; `RECOMMENDED_CITATION` (Vasilevsky NA, et al. *Mondo: Unifying diseases for the world, by the world.* medRxiv 2022. doi:10.1101/2022.04.13.22273750); `MONDO_LICENSE = "Mondo is distributed under CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/). Cite the Mondo Disease Ontology / Monarch Initiative."`; `MATCH_TYPES = ("mondo_id","primary","exact_synonym","related_synonym","xref")`.
- [ ] **Step 7: Run tests + commit.**

Run: `uv run pytest tests/unit/test_identifiers.py tests/unit/test_config.py -v` → Expected: PASS.
```bash
git commit -am "feat: config, constants, identifiers, exceptions, logging primitives"
```

### Task 0.3: Clone MCP scaffolding + server entry points (domain-agnostic)

**Files (clone from `$MGI`, apply naming map):**
- Create: `mondo_link/mcp/envelope.py`, `annotations.py`, `middleware.py`, `arg_help.py`, `service_adapters.py`, `__init__.py`; `server.py`, `mondo_link/server_manager.py`, `mcp_server.py`, `mondo_link/app.py`

- [ ] **Step 1: Clone `$MGI/mgi_link/mcp/annotations.py`** verbatim (`READ_ONLY_OPEN_WORLD`).
- [ ] **Step 2: Clone `$MGI/mgi_link/mcp/envelope.py`** verbatim with rename. Keep `run_mcp_tool`, `_classify`, `_error_envelope`, `build_arg_error_envelope`, `McpErrorContext`, `McpToolError`, the 7-code mapping, and `WithdrawnEntryError` handling (replaced_by surfacing). Update the import of `default_error_next_commands` from the local `next_commands` (created Wave 1C) — leave the import; it will resolve once 1C lands. (Envelope does not import any domain service.)
- [ ] **Step 3: Clone `middleware.py`, `arg_help.py`** verbatim with rename. In `arg_help.py` set `ARG_ALIASES` for Mondo: `{"disease":"query","term":"query","mondo":"query","mondo_id":"query","label":"query","id":"xref_id","curie":"xref_id","xref":"xref_id","max":"limit","mode":"response_mode","prefix":"prefixes"}`.
- [ ] **Step 4: Clone `service_adapters.py`** → expose `get_mondo_service()`, `reset_mondo_service()`, `set_mondo_service()`. Build `MondoService(MondoRepository(settings.data.db_path))` if the db exists, else `MondoService(None)`. Drop any MouseMine fallback branch.
- [ ] **Step 5: Clone `server.py`, `server_manager.py`, `mcp_server.py`, `app.py`** verbatim with rename. `app.py` title/description → mondo; root payload `data_source="Mondo Disease Ontology (Monarch PURL) -> local SQLite index"`. `mcp_server.py` env defaults `MONDO_LINK_TRANSPORT=stdio`.
- [ ] **Step 6: Stub `mondo_link/mcp/facade.py`** with `create_mondo_mcp()` that builds `FastMCP(name="mondo-link", instructions=MONDO_SERVER_INSTRUCTIONS, mask_error_details=True)` and (initially) registers nothing — registration calls added in Wave 1C/2. Add `MONDO_SERVER_INSTRUCTIONS` placeholder in `mcp/resources.py` (filled in 1C).
- [ ] **Step 7: Commit.** (Do not run server yet — domain tools land in Wave 1.)
```bash
git commit -am "feat: clone domain-agnostic mcp scaffolding + server entry points"
```

### Task 0.4: Freeze `MondoService` + `MondoRepository` stubs (the interface barrier)

**Files:**
- Create: `mondo_link/services/__init__.py`, `mondo_link/services/mondo_service.py` (signatures + `NotImplementedError` bodies), `mondo_link/data/__init__.py`, `mondo_link/data/repository.py` (constructor + method signatures)

- [ ] **Step 1:** Write `mondo_service.py` with the full class signature from **Contract B** (method bodies `raise NotImplementedError`). This lets Wave 1C import and wire tools against a stable interface while 1B fills bodies.
- [ ] **Step 2:** Write `repository.py` constructor (read-only connect) + method signature stubs.
- [ ] **Step 3: Commit.**
```bash
git commit -am "feat: freeze MondoService/MondoRepository interface (contract barrier)"
```

### Task 0.5: Freeze `schema.sql`

**Files:** Create `mondo_link/ingest/__init__.py`, `mondo_link/ingest/schema.sql`

- [ ] **Step 1:** Write `schema.sql` verbatim from the design spec §4 (the 8 tables: `term`, `term_lookup`, `term_fts`, `mondo_parent`, `mondo_closure`, `mondo_top_grouping`, `xref`, `meta`, with all indexes; `PRAGMA journal_mode=WAL; PRAGMA foreign_keys=OFF;` at top).
- [ ] **Step 2:** Add a `tests/unit/test_schema.py` that loads the schema into an in-memory sqlite (`conn.executescript`) and asserts all 8 tables + `term_fts` virtual table exist (`SELECT name FROM sqlite_master`).
- [ ] **Step 3: Run + commit.**

Run: `uv run pytest tests/unit/test_schema.py -v` → Expected: PASS.
```bash
git commit -am "feat: freeze SQLite schema.sql (contract barrier)"
```

---

## WAVE 1 — Parallel tasks (A, B, C, D run concurrently against frozen contracts)

### Task 1A: Ingest — parser, builder, downloader, lock, cli, fixtures

**Files:**
- Create: `mondo_link/ingest/parser.py`, `builder.py`, `downloader.py`, `lock.py`, `cli.py`
- Create: `tests/fixtures/mondo.obo`, `tests/fixtures/mondo.sssom.tsv`
- Test: `tests/unit/test_parser.py`, `test_builder.py`, `test_downloader.py`, `test_lock.py`, `test_cli.py`

- [ ] **Step 1: Write fixtures.** `tests/fixtures/mondo.obo` — a hand-built OBO with header (`data-version: mondo/releases/2026-06-01/mondo.owl`, `date: 01:06:2026 00:00`) and ~15 `[Term]` stanzas including: `MONDO:0000001` (root) and ≥2 direct children (top groupings); a multi-parent term (two `is_a` lines); a term with `def`, `synonym "x" EXACT [OMIM:1]`, `synonym "y" RELATED [...]`, `xref: OMIM:182212 {source="MONDO:equivalentTo"}`, `xref: Orphanet:2462 {source="MONDO:equivalentTo"}`, `xref: DOID:0050776`, `subset: clingen`; and one obsolete term (`is_obsolete: true`, `replaced_by: MONDO:0008426`, `consider: MONDO:0000003`). `tests/fixtures/mondo.sssom.tsv` — `#`-comment metadata lines + header `subject_id\tpredicate_id\tobject_id\tmapping_justification\tconfidence` + rows mapping the same terms (e.g. `MONDO:0008426\tskos:exactMatch\tOMIM:182212\tsemapv:LexicalMatching\t0.95`, a `skos:closeMatch` row, and an `ORPHA` row).
- [ ] **Step 2: Write `tests/unit/test_parser.py`** (failing) covering: `parse_obo_header` → version `mondo/releases/2026-06-01/...`; `parse_mondo_obo` extracts name/def/parents(both)/synonyms(scope+type+sources)/xrefs(prefix normalized Orphanet→ORPHA, predicate from `source="MONDO:equivalentTo"`→`equivalentTo` else `xref`)/subsets/obsolete/replaced_by/consider; `mondo_closure_pairs` includes self-pair and transitive ancestors through both parents with cycle-guard; `mondo_top_groupings` returns the direct children of `MONDO:0000001` ordered by name; `parse_mondo_sssom` yields normalized rows with predicate mapped `skos:exactMatch`→`exactMatch`, `skos:closeMatch`→`closeMatch`, `skos:broadMatch`→`broadMatch`, `skos:narrowMatch`→`narrowMatch`, object_id normalized.
- [ ] **Step 3: Implement `parser.py`** per Contract C. Base `parse_mondo_obo` / `mondo_closure_pairs` / `mondo_top_groupings` on `$MGI/mgi_link/ingest/parser.py`'s `parse_mp_obo`/`mp_closure_pairs`/`mp_top_systems` (clone the closure recursion verbatim, swap MP→MONDO). Add OBO line handling for `def:` (strip quotes + trailing `[refs]`), `synonym:` (regex `"(?P<text>.*)" (?P<scope>EXACT|RELATED|BROAD|NARROW)(?: (?P<type>\w+))? \[(?P<sources>.*)\]`), `xref:` (strip `{...}`, derive predicate from a `source="MONDO:equivalentTo"` substring, `normalize_xref` the CURIE), `subset:`, `is_obsolete:`, `replaced_by:`, `consider:`. `parse_mondo_sssom`: skip `#`/blank lines, read header, map `skos:*`→short predicate, `normalize_xref(object_id)`.
- [ ] **Step 4: Run parser tests** → PASS. Commit.
- [ ] **Step 5: Write `tests/unit/test_lock.py` + clone `lock.py`** verbatim from `$MGI/mgi_link/ingest/lock.py` (fcntl `.build.lock`, `DataUnavailableError` on timeout). Test acquire/timeout.
- [ ] **Step 6: Write `tests/unit/test_downloader.py` (respx) + implement `downloader.py`** cloned from `$MGI/mgi_link/ingest/downloader.py`: `download_cache.json` ETag/Last-Modified, `If-None-Match`/`If-Modified-Since`, 304 reuse, `DownloadError`. Two report keys: `obo`→`mondo.obo`, `sssom`→`mondo.sssom.tsv` (URLs from config). Tests: 200 writes file + caches validators; 304 returns `not_modified` reusing local file.
- [ ] **Step 7: Write `tests/unit/test_builder.py` + implement `builder.py`** cloned from `$MGI/mgi_link/ingest/builder.py`: `tempfile.mkstemp(suffix=".sqlite.tmp")`, `executescript(load_schema_sql())`, load terms→`term`+`term_lookup`+`term_fts`, edges→`mondo_parent`, closure→`mondo_closure`, top→`mondo_top_grouping`, OBO xrefs + SSSOM rows→`xref` (origin tagged), `_insert_meta` (version from header, counts, validators JSON), `os.replace` atomic swap, `unlink(missing_ok=True)` on failure. Provide `build_database(config, *, paths, validators)`, `ensure_database`, `rebuild`, `read_meta`. Test: build from fixtures → assert counts, a known closure pair, a merged xref row (one obo_xref + one sssom for same term), meta.mondo_version.
- [ ] **Step 8: Write `tests/unit/test_cli.py` + implement `cli.py`** (typer) cloned from `$MGI/mgi_link/ingest/cli.py`: `build` (force), `refresh` (conditional), `status` (print provenance/counts/version). Test via `typer.testing.CliRunner` against fixtures (monkeypatch config data_dir + download to copy fixtures).
- [ ] **Step 9: `make lint typecheck` for `mondo_link/ingest` + commit each sub-step.**

Run: `uv run pytest tests/unit/test_parser.py tests/unit/test_builder.py tests/unit/test_downloader.py tests/unit/test_lock.py tests/unit/test_cli.py -v` → Expected: PASS.

### Task 1B: Data + services — repository, mondo_service, shaping, pagination, refresh

**Files:**
- Create/Fill: `mondo_link/data/repository.py`, `mondo_link/services/mondo_service.py`, `mondo_link/services/shaping.py`, `mondo_link/services/pagination.py`, `mondo_link/services/refresh.py`
- Test: `tests/unit/test_repository.py`, `test_service.py`, `test_shaping.py`, `test_pagination.py`

> Depends on: Contract A (schema), Contract B (signatures), and a way to build a test DB. Use `mondo_link.ingest.builder.build_database` against `tests/fixtures/` in a `conftest.py` session fixture (`built_db` → path). If 1A not yet merged, build a minimal DB inline from `schema.sql` + hand-inserted rows.

- [ ] **Step 1: Clone `pagination.py`** verbatim (`page_fields(total, returned, limit) -> {total,returned,limit,truncated}`). Test.
- [ ] **Step 2: Write `shaping.py`** mirroring `$MGI/mgi_link/services/shaping.py`: `RESPONSE_MODES=["minimal","compact","standard","full"]`, `DEFAULT_RESPONSE_MODE="compact"`, `shape_disease(record, mode)` (minimal=id+name; compact=drop nulls/empty + verbose fields like full synonym objects; standard/full=full). Test each mode.
- [ ] **Step 3: Implement `repository.py`** (read-only connect from Contract B). Methods + key SQL:
  - `get_term(mondo_id)` → row from `term` or None.
  - `resolve_label(label_upper)` → `[(mondo_id,label_type)]` from `term_lookup`.
  - `search(query, *, limit, include_obsolete)` → FTS5 `SELECT mondo_id,name,definition,bm25(term_fts) AS score FROM term_fts JOIN term USING(mondo_id) WHERE term_fts MATCH ? [AND is_obsolete=0] ORDER BY score LIMIT ?` (+ a COUNT for total).
  - `parents(mondo_id)` / `children(mondo_id)` → join `mondo_parent` ↔ `term` for names.
  - `ancestors(mondo_id, limit)` → `SELECT t.mondo_id,t.name FROM mondo_closure c JOIN term t ON t.mondo_id=c.ancestor_id WHERE c.mondo_id=? AND c.ancestor_id!=? ORDER BY t.name LIMIT ?`.
  - `descendants(mondo_id, limit)` → `... WHERE c.ancestor_id=? AND c.mondo_id!=? ...`.
  - `top_groupings(mondo_id)` → `SELECT g.mondo_id,g.name FROM mondo_top_grouping g JOIN mondo_closure c ON c.ancestor_id=g.mondo_id WHERE c.mondo_id=? ORDER BY g.name`.
  - `xrefs_for(mondo_id, prefixes)` → rows from `xref` ordered by `PREDICATE_RANK` then prefix.
  - `mondo_for_xref(object_id_upper, limit)` → `SELECT DISTINCT mondo_id,prefix,object_id,predicate,origin FROM xref WHERE object_id_upper=? ORDER BY <predicate rank> LIMIT ?`.
  - `read_meta()` → `meta` row.
- [ ] **Step 4: Write `tests/unit/test_repository.py`** against `built_db` for each query (known ids from fixtures).
- [ ] **Step 5: Implement `mondo_service.py`** per Contract B, returning plain dicts:
  - `_resolve_term_id(term)`: if `normalize_mondo_id` → verify exists (raise `NotFoundError`); if exists but `is_obsolete` → raise `WithdrawnEntryError(replaced_by, consider)`; elif `infer_xref_source(term)` → `resolve_xref` best hit or `NotFoundError`; else label lookup → 1 hit ok, 0 `NotFoundError`, >1 `AmbiguousQueryError(candidates)`.
  - `resolve_disease`: returns `{query, mondo_id, name, match_type, obsolete, ...}`; multi-label → `AmbiguousQueryError`.
  - `get_disease`: full record incl. `parents`,`children`,`top_groupings`,`xrefs` (grouped), `synonyms`,`subsets`,`obsolete`,`replaced_by`; shaped by mode.
  - `get_ancestors`/`get_descendants`: `{mondo_id,name,ancestors|descendants:[...], <page_fields>}`.
  - `get_parents`/`get_children`: direct edges + names.
  - `resolve_xref`: `{xref_id, normalized, matches:[{mondo_id,name,predicate,origin}], <page_fields>}` ranked.
  - `map_cross_ontology`: `{mondo_id, name, mappings: {PREFIX:[{object_id,predicate,origin,source}]}}` filtered by `prefixes`.
  - `get_diagnostics`: index-built?, counts, `mondo_version`, db path.
  - Each method that fetches a record attaches the Mondo version into the payload (grounding).
- [ ] **Step 6: Write `tests/unit/test_service.py`** for every method incl. obsolete→Withdrawn, ambiguous label, xref redirect, closure ancestors/descendants, xref predicate ranking.
- [ ] **Step 7: Clone `refresh.py`** (`bootstrap_data`, `start_refresh_scheduler`, `stop_refresh_scheduler`) with rename; resets `get_mondo_service`. Light test (bootstrap on missing db is non-fatal).
- [ ] **Step 8: Commit each sub-step.**

Run: `uv run pytest tests/unit/test_repository.py tests/unit/test_service.py tests/unit/test_shaping.py tests/unit/test_pagination.py -v` → Expected: PASS.

### Task 1C: MCP tools + schemas + next_commands + capabilities

**Files:**
- Create: `mondo_link/mcp/tools/__init__.py`, `_common.py`, `discovery.py`, `diseases.py`, `hierarchy.py`, `xref.py`; `mondo_link/mcp/schemas.py`, `next_commands.py`, `capabilities.py`, fill `resources.py`
- Test: `tests/unit/test_tools_e2e.py`, `test_next_commands.py`, `test_tool_names.py`, `test_arg_help.py`, `test_envelope.py`

> Depends on: Contract B (service signatures, available as stubs from Task 0.4). Tools call `get_mondo_service()`; e2e tests inject a real service via `set_mondo_service(MondoService(MondoRepository(built_db)))`.

- [ ] **Step 1: Write `schemas.py`** mirroring `$MGI/mgi_link/mcp/schemas.py` — permissive `_envelope(**props)` helper + one schema constant per tool: `CAPABILITIES_SCHEMA`, `DIAGNOSTICS_SCHEMA`, `RESOLVE_DISEASE_SCHEMA`, `SEARCH_SCHEMA`, `DISEASE_SCHEMA`, `ANCESTORS_SCHEMA`, `DESCENDANTS_SCHEMA`, `PARENTS_SCHEMA`, `CHILDREN_SCHEMA`, `RESOLVE_XREF_SCHEMA`, `CROSS_ONTOLOGY_SCHEMA`.
- [ ] **Step 2: Write `_common.py`** annotated types: `ResponseMode` (Literal minimal|compact|standard|full), `QueryStr` (examples `["Shprintzen-Goldberg syndrome","MONDO:0008426","OMIM:182212"]`), `MondoIdStr`, `XrefIdStr` (examples `["OMIM:182212","Orphanet:2462","DOID:0050776"]`), `TermStr`.
- [ ] **Step 3: Write `next_commands.py`** mirroring `$MGI/mgi_link/mcp/next_commands.py`: `cmd(tool, **args)`, `widen_cmd(...)`, `default_error_next_commands(tool, error_code, arguments)`, and per-tool builders `after_resolve_disease`, `after_search`, `after_get_disease` (→ ancestors + map_cross_ontology), `after_ancestors`/`after_descendants` (widen + open first), `after_resolve_xref` (→ get_disease on top hit), `after_cross_ontology`, `withdrawn_recovery(replaced_by)`. Test the shapes.
- [ ] **Step 4: Fill `resources.py`**: `RESEARCH_USE_NOTICE`, `MONDO_SERVER_INSTRUCTIONS` (workflow primer: resolve_disease → get_disease → ancestors/descendants → resolve_xref/map_cross_ontology; cite MONDO id + release; research-use-only), `MONDO_USAGE_NOTES`, `MONDO_REFERENCE_NOTES` (error codes, match_types, predicate ranking, xref prefixes, data source).
- [ ] **Step 5: Write each tool module** following `$MGI/mgi_link/mcp/tools/ontology.py` registration pattern exactly (async outer, inner `call()` attaching `_meta.next_commands`, `run_mcp_tool(name, call, context=McpErrorContext(...))`). Each `@mcp.tool` has `name`, `title`, `annotations=READ_ONLY_OPEN_WORLD`, `output_schema=...`, `tags`, and a `description` whose **first sentence ends with `Signature: tool(args...)`**. Register functions: `register_discovery_tools` (get_server_capabilities, get_diagnostics), `register_disease_tools` (resolve_disease, search_diseases, get_disease), `register_hierarchy_tools` (get_disease_ancestors/descendants/parents/children), `register_xref_tools` (resolve_xref, map_cross_ontology). `tools/__init__.py` re-exports all four `register_*`.
- [ ] **Step 6: Write `capabilities.py`** mirroring `$MGI`: `TOOLS` list (all 11 names — MUST equal the registered set), `build_capabilities()` (server/version/`mondo_version`/data_source/citation/license/research_use/response_modes/match_types/xref_prefixes/error_codes/limits/read_only), `collect_tool_signatures`, `build_tools_overview`, `project_capabilities`, `register_capability_resources(mcp)` registering `mondo://capabilities|tools|usage|reference|research-use|citation`.
- [ ] **Step 7: Write `tests/unit/test_tool_names.py`** — assert `capabilities.TOOLS` set == names registered on `create_mondo_mcp()` (after Wave 2 wiring; until then assert against the explicit registered list). `test_arg_help.py`, `test_envelope.py` cloned/adapted from `$MGI`. `test_tools_e2e.py` — call each tool through the facade with `set_mondo_service(real)` and assert `success`, `_meta.next_commands`, and key payload fields; assert an obsolete id returns a structured `not_found`-class error with `replaced_by`.
- [ ] **Step 8: Commit each sub-step.**

Run: `uv run pytest tests/unit/test_tools_e2e.py tests/unit/test_tool_names.py tests/unit/test_next_commands.py tests/unit/test_arg_help.py tests/unit/test_envelope.py -v` → Expected: PASS.

### Task 1D: Docs

**Files:** Create `README.md`, `AGENTS.md`, `CLAUDE.md`, `docs/architecture.md`, `docs/usage.md`, `docs/deployment.md`, `CHANGELOG.md`

- [ ] **Step 1:** Clone the structure of `$MGI/README.md`, `AGENTS.md`, `CLAUDE.md`, `docs/*` and re-domain to Mondo (tool list, PURLs, `make` targets, `claude mcp add` snippet, two-plane architecture, research-use notice, CC BY 4.0). AGENTS.md/CLAUDE.md must state the data-plane/MCP-plane split, the 500-line budget, mypy strict, and `make ci-local` as the gate.
- [ ] **Step 2: Commit.**

---

## WAVE 2 — Integration & verification

### Task 2.1: Wire the facade and registrations

**Files:** Modify `mondo_link/mcp/facade.py`

- [ ] **Step 1:** In `create_mondo_mcp()` call `register_discovery_tools`, `register_disease_tools`, `register_hierarchy_tools`, `register_xref_tools`, `register_capability_resources`, and `mcp.add_middleware(ArgValidationMiddleware())`.
- [ ] **Step 2:** Run `tests/unit/test_tool_names.py` asserting `TOOLS` == live registered names. Fix any drift. Commit.

### Task 2.2: Real ingest smoke + server boot

- [ ] **Step 1:** Copy the real local Mondo into place for an offline build: `mkdir -p data && cp /home/bernt-popp/development/omim-scrape/data/external/mondo.obo data/mondo.obo`. Obtain `mondo.sssom.tsv` (if not local, `uv run mondo-link-data build` will fetch via PURL; otherwise run builder directly against the obo with an empty sssom and note SSSOM as network-dependent).
- [ ] **Step 2:** `uv run mondo-link-data status` → expect release/counts printed. Assert term_count > 20000, closure_count > term_count, xref_count > 0.
- [ ] **Step 3:** Boot check: `uv run python server.py --transport unified --port 8765 &` then `curl -s localhost:8765/health` → `{"status":"ok",...}`; `curl -s -X POST localhost:8765/mcp ...` lists tools. Kill. (Use a background run + Monitor; don't block.)
- [ ] **Step 4: Commit** any wiring fixes.

### Task 2.3: Definition of Done — `make ci-local` green

- [ ] **Step 1:** `uv run ruff format mondo_link tests server.py mcp_server.py scripts`.
- [ ] **Step 2:** `make ci-local` → must pass: format-check, lint-ci, lint-loc (≤500 lines/file), typecheck (mypy strict), test-fast (coverage ≥80%).
- [ ] **Step 3:** If coverage <80%, add targeted unit tests for uncovered branches (shaping modes, error classification, cli status-missing-db). Re-run.
- [ ] **Step 4: Final commit.**

Run: `make ci-local` → Expected: all green, coverage ≥80%.
```bash
git commit -am "feat: mondo-link complete — ci-local green"
```

---

## Self-review (coverage vs spec)

- Spec §3 architecture → Tasks 0.1–0.3, 2.1. ✅
- Spec §4 data model/schema → Task 0.5 (freeze) + 1A (build) + 1B (query). ✅
- Spec §5 tools (11) → Task 1C (each registered) + 2.1 (wired) + test_tool_names sync. ✅
- Spec §6 errors/identifiers/grounding → 0.2 (identifiers), 0.3 (envelope), 1B (typed raises + version in payload). ✅
- Spec §7 config/PURLs → Task 0.2 Step 3. ✅
- Spec §8 testing/DoD → fixtures (1A Step1), per-module tests (1A/1B/1C), `make ci-local` (2.3). ✅
- Spec §2 locked forks → OBO+SSSOM parse (1A), merged xref provenance+predicate (1A builder + 1B ranking), resolve_disease in full surface (1C). ✅

**Type consistency check:** `MondoService`/`MondoRepository` method names in Contract B are used identically in 1B (impl) and 1C (callers via service). Parser output keys in Contract C match builder consumption in 1A Step7. `PREDICATE_RANK` keys (constants) match parser predicate outputs and repository ordering. No placeholders remain.
