# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Re-vendored the behaviour conformance gate from genefoundry-router `56db958`
  (`docs/conformance/behaviour.py` blob `c69801687`) so live MCP contract checks
  treat not-found example probes as inconclusive and keep empty auxiliary objects from hiding counted rows.

## [0.4.0] - 2026-07-15

MCP contract hardening in response to the live fleet audit (issue #25: 9 confirmed
defects, 2 high). Adopts Tool-Surface Budget Standard v1 and Tool-Schema Documentation
Standard v1, and closes the fleet-wide `isError`/`error_code` gaps against the vendored
Behaviour Conformance v1 gate (now run in CI).

### Fixed

- **[HIGH] search_diseases no longer ranks veterinary terms above the human disease.**
  Ranking was raw BM25, whose length-normalisation sank a well-annotated human term
  (synonyms + a long definition) below a bare veterinary variant sharing the query
  tokens — so "cystic fibrosis" returned "cystic fibrosis, pig" at rank 0 and the human
  MONDO:0009061 at rank 9. Ranking now applies, IN SQL (before the limit/offset window),
  an exact primary-label boost and a human-disease prior (Mondo's non-human-animal branch
  is demoted below human terms), then BM25 within each tier. `total` is unchanged (a COUNT
  over the same MATCH), so it stays invariant under `limit`.
- **[HIGH] resolver candidates now carry names.** `resolve_disease` (ambiguous / not_found
  / obsolete `replaced_by`) returned bare MONDO ids with no labels in every response mode,
  forcing a second call to disambiguate. Each candidate/replacement now carries its trusted
  DB `name` (plus a fuzzy `score` when present). The name is RE-DERIVED from the DB by the
  grammar-validated id — never copied from the exception, whose free-text could carry
  prompt-injection prose surviving code-point stripping; when the DB cannot vouch for an id
  the candidate stays id-only. Only long free-text definitions remain fenced as untrusted.
- **Every error envelope now carries MCP `isError: true`** (Response-Envelope v1), whether
  the tool body RAISES or RETURNS the error — both routes go through the chokepoint that
  returns a `ToolResult(structured_content=…, is_error=True)` instead of a bare dict, so a
  client branching on `isError` always sees the failure with the structured envelope intact.
- **`error_code` is the closed six-value enum** (`invalid_input`, `not_found`,
  `ambiguous_query`, `upstream_unavailable`, `rate_limited`, `internal`), typed and coerced
  at the emit point so a stray legacy code can never reach the wire. The local Mondo index
  is this server's only upstream, so `data_unavailable`→`upstream_unavailable` and
  `internal_error`→`internal`; neither legacy code is emitted.
- **A malformed MONDO id is reported as `invalid_input` (field `term`), not `not_found`.**
  `get_disease("MONDO:abcxyz")` was indistinguishable from a well-formed-but-absent id;
  the two now carry different codes so the model applies the right repair.
- **map_cross_ontology `prefixes` is a declared enum, validated before stripping.** It was a
  bare `list[str]`: a bogus source matched nothing and returned `count: 0, success: true`
  (silent omission, forbidden by Response-Envelope v1.1), and `prefixes=[" "]` stripped to
  `[]` and returned EVERY source. It is now the first-class closed set (an `enum` in the
  schema, so a validating client pre-checks and pydantic rejects an unknown/blank value with
  `invalid_input` before the body); the service revalidates raw values before stripping.
- **`response_mode=minimal` narrows a record's collections, it never deletes them.** minimal
  on `get_disease` dropped `xrefs`/`parents`/`children` entirely; it now keeps every
  populated collection, narrowing each row to its stable identifier — a record's payload can
  no longer silently vanish. Likewise an unknown `fields` projection is now `invalid_input`
  (field `fields`) naming the projectable keys, not a silent anchors-only success.
- **The batch item cap (1..50) is declared in the input schema** (`minItems`/`maxItems`)
  and an over-cap call names the constraint ("must have between 1 and 50 items") instead of
  a generic message.
- **get_diagnostics no longer promises a `mapping` count it never returned**, and its
  declared `outputSchema` (which named six properties the payload never carried) is gone.

### Changed

- **Tool surface cut ~7,282t → ~4,180t** by suppressing every tool's `outputSchema`
  (`output_schema=None`; 43% of the old surface, a field no model reads and the MCP spec
  makes optional) and disabling `$ref` dereferencing. `structuredContent` is unaffected.
- **resolve_disease standard/full now return the fenced `definition`**, so `response_mode`
  meaningfully widens the payload (and a standard resolve can skip a get_disease round trip);
  the previously-declared-but-never-returned `definition` field is now real.
- **Batch rows carry `index` on every row** (success and failure) for uniform correlation;
  a failure row still omits the raw input (an unresolved value must not be echoed).
- **map_cross_ontology drops the `fields` projection parameter** (its `prefixes` filter
  covers narrowing); `get_disease`/`get_disease_batch` keep `fields`.
- Discovery surface (capabilities, reference notes, server instructions) updated to match:
  the closed error taxonomy, the new search-ranking semantics, and the field-projection scope.

### Added

- Vendored **Behaviour Conformance v1** gate (`tests/conformance/behaviour.py` +
  `test_behaviour_v1.py`, byte-identical from the router) and wired the behaviour probe into
  `conformance.yml`. The line-budget checker now exempts vendored conformance probes (derived
  from each file's own docstring marker).

## [0.3.6] - 2026-07-14

### Changed

- **The NPM deployment pulls the released image instead of building from source.**
  `docker/docker-compose.npm.yml` carried `build:`, so a deploy rebuilt the image on the
  server even though CI had already published an attested, digest-addressable image to
  GHCR. It now requires `MONDO_LINK_IMAGE` pinned to a digest and fails closed when it is
  unset. Nothing else in the overlay changed: `container_name`, the Compose project name,
  the healthcheck (including the long first-boot `start_period`), networks and volumes are
  all preserved, so the deployed topology and the persisted Mondo SQLite index are
  untouched.

## [0.3.5] - 2026-07-13

### Fixed

- Re-pin the reusable container CI and container release callers to the
  corrected GeneFoundry router release standard
  (`86b11f7ed062ed84dfddcbd309e34da88f3dae5b`), so the signed release evidence
  states the data contract this repository actually declares. The previous
  standard hardcoded a `data-independent` contract and `{"mode":"none"}` data
  requirements, which understated this `data-bound` / `upstream-live` service in
  its signed manifest and silently skipped the data-binding assertion that the
  captured data identity equals the declared artifact. Research use only.

## [0.3.4] - 2026-07-13

### Fixed

- Re-pin the reusable container CI and container release callers to the
  corrected GeneFoundry router release standard
  (`58d011d9c72efe90337244342fdec703f2b5b4b9`), which repairs seven latent
  defects in the previously pinned revision that prevented the container
  release workflow from completing. Research use only.

## [0.3.3] - 2026-07-13

### Security

- Adopt the GeneFoundry router container-release standard with SHA-pinned
  reusable CI/release callers, typed release configuration, digest-only
  production Compose, complete OCI labels, and code-only image content policy.

## [0.3.2] - 2026-07-11

### Security (defense in depth)

- Closed the last FastMCP-core not-found reflection residual. FastMCP core / the
  MCP SDK reflected the caller's own requested tool name, resource URI, or prompt
  name -- with any control/zero-width/bidi/NUL code points or injection prose --
  into log/telemetry sinks (and, for `prompts/get`, into the caller-visible error
  frame) BEFORE this repo's middleware ran. Added a Layer-3 protocol backstop that
  severs `Unknown prompt: '<name>'` (and any raw unknown-tool/resource dispatch
  error) into a fixed, input-free message, and extended the Layer-5 log-scrub
  filter from the single `fastmcp.server.server` prefix rule to a marker-based
  filter on every source logger -- root/`mcp.shared.session` (the malformed-URI
  `-32602`), `mcp.server.lowlevel.server` ("Tool cache miss"),
  `fastmcp.server.mixins.mcp_operations` ("Handler called"), the `fastmcp` parent,
  and their non-propagating Rich handlers -- at all levels (DEBUG included).
  Caller self-reflection surface (low-medium); no success schema or envelope shape
  changed. Research use only.

## [0.3.1] - 2026-07-11

### Security (defense in depth)

- Caller-visible error messages and structured fields are built from
  fixed/validated values (no exception/upstream prose) and sanitized of
  control/zero-width/bidi/NUL code points; the local DB path, decode failures,
  unknown tool names, and unknown resource URIs are no longer echoed or logged
  raw. Research use only.

## [0.3.0] - 2026-07-11

### Changed (BREAKING)

- **Upstream Mondo definitions are now fenced as Response-Envelope Standard
  v1.1 `untrusted_text` objects.** `get_disease`'s `/definition`,
  `search_diseases`'s `/results/*/definition` and `/results/*/definition_snippet`,
  and `get_disease_batch`'s `/results/*/definition` (which reuses `get_disease`)
  no longer return a bare string: each is now
  `{kind: "untrusted_text", text, provenance: {source, record_id, retrieved_at},
  raw_sha256}`. This is defense in depth against prompt injection embedded in
  upstream ontology prose -- the router already treats a `kind: untrusted_text`
  subtree as opaque. Clients reading the definition field must switch from
  `record["definition"]` to `record["definition"]["text"]`. Research use only;
  not clinical decision support.

### Added

- `mondo_link/mcp/untrusted_content.py`: the v1.1 fencing primitive
  (`fence_untrusted_text`, `UntrustedText`, `UntrustedTextProvenance`) plus an
  `enforce_untrusted_text_limits` guard (2 MiB/object, 128 objects, 8 MiB
  total per response), copied from the fleet's released PubTator reference.

### Security (defense-in-depth hardening)

- **No fence-bypass via sparse fieldset.** The `fields=` projector
  (`select_fields`) now treats a fenced `untrusted_text` object as an opaque
  leaf: `fields=["definition.text"]` returns the whole typed wrapper, never the
  bare `text` stripped of `kind`/`provenance`/`raw_sha256`.
- **Snippet digest over true raw bytes.** The compact `definition_snippet` is
  truncated from the RAW definition preserving internal tab/LF/CR (no
  whitespace collapse before fencing), so its `raw_sha256` covers the snippet's
  real pre-normalization bytes.
- **Whole-response limit enforcement in `get_disease_batch`.** Every fenced
  definition across all batch rows is aggregated into one
  `enforce_untrusted_text_limits` call; a breach surfaces as a typed
  `invalid_input` error (never a masked `internal_error`).
- **List-item schema declares the literal.** The `get_disease_batch` item
  schema now declares `definition` as the `untrusted_text` object (`kind`
  literal), not hidden behind `additionalProperties`.
- **Strict fenced-field schema.** The `untrusted_text` output schema now
  declares `kind` as `const: "untrusted_text"` and requires
  `[kind, text, provenance, raw_sha256]` (and provenance
  `[source, record_id, retrieved_at]`), so a malformed/partial object no longer
  validates under `additionalProperties`.
- **Limits enforced over the emitted response.** `get_disease` now enforces the
  v1.1 ceilings over the fenced objects actually present in the FINAL,
  post-projection payload, so `response_mode="minimal"` or a sparse fieldset
  that omits the definition never fails on a definition the caller never sees.

## [0.2.0] - 2026-07-10

### Security

- Enforce exact configurable Host and Origin allowlists across every HTTP
  route, with safe loopback defaults, wildcard rejection, explicit production
  proxy hosts, and native FastMCP protection in depth. FastMCP is upgraded to
  3.4.4 while preserving structured argument-validation error envelopes.

### Changed (BREAKING)

- Host and Origin admission is now default-deny outside the configured
  loopback values. Non-loopback and reverse-proxy deployments must list their
  exact public names in `MONDO_LINK_ALLOWED_HOSTS` and browser origins, when
  used, in `MONDO_LINK_ALLOWED_ORIGINS`.

## [0.1.4] - 2026-07-10

### Security

- Harden Mondo release acquisition with exact-host validated redirects,
  source-specific policies, configurable size and time limits, and atomic
  replacement that preserves the previous valid artifact on failure.

## [0.1.3] - 2026-07-07

### Added

- **Per-call clinical-use disclaimer (`_meta.unsafe_for_clinical_use`):** every
  MCP tool response now carries `"unsafe_for_clinical_use": true` in `_meta`,
  on both the success and error envelope paths, at every `response_mode`
  (`minimal` included, which otherwise strips `_meta` down to `{tool,
  request_id}`). Previously the research-use / not-clinical-decision-support
  notice was surfaced only once via `get_server_capabilities`
  (`research_use_only` / `research_use_notice`, still present and unchanged);
  this fleet-wide disclaimer standardization makes the notice visible on the
  response an LLM client actually reads, per call. Purely additive: no
  existing `_meta` key is renamed, removed, or restructured.

### Security

- Adopt the GeneFoundry Container & Deployment Hardening Standard v1: digest-pinned
  base image, `.dockerignore`, read-only rootfs + tmpfs scratch + writable data
  volume, `cap_drop: ALL`, `no-new-privileges`, `init`, mem/cpu/pids limits on the
  base compose, a new expose-only `docker-compose.prod.yml`, and a CI container
  scan + SBOM workflow (Trivy). Also fetch the Mondo OBO release over `https://`
  (scheme only; no checksum infra changes).
- **Inbound-boundary hardening:** the base `docker-compose.yml` now loopback-binds
  the published host port (`127.0.0.1:…`) so copying it to a server never publishes
  the unauthenticated backend on the public IP (production still fronts it via the
  prod/npm overlays, `ports: !reset []`). Credentialed CORS is disabled
  (`allow_credentials=False`) — mondo-link holds no cookies/session, so it was
  meaningless — and the app now fails closed at startup on the
  `allow_credentials=True` + wildcard-origin footgun. The unauthenticated
  diagnostics surface and the data-refresh logs now report the db **filename**
  only, never the absolute on-disk path (no filesystem-layout leak). Both compose
  and CORS guards are locked by new unit tests.

### Token-efficiency pass

- **Tiered `_meta` by `response_mode` (token tax):** the per-call `_meta` block is
  now sized to the requested verbosity instead of repeating everything on every
  call. `minimal` returns only `{tool, request_id}`; `compact` (default) keeps
  `next_commands` (workflow guidance) and `capabilities_version` (the warm-client
  cache key) but drops the `elapsed_ms` echo; `standard`/`full` add `elapsed_ms`.
  The universal `next_commands` invariant now holds for `compact` and richer;
  `minimal` is the explicit opt-out (still recorded server-side / via diagnostics).
- **Deduped `map_cross_ontology` targets:** multiple rows for the same target id
  (an OBO xref plus an SSSOM mapping, or two predicates) collapse into **one entry
  per `object_id`** carrying the strongest `predicate`/`origin` and a `predicates`
  list (only when >1) — mirroring the `resolve_xref` fix. The wasteful
  `source: null` of OBO xrefs is dropped. Fewer tokens, same information.
- **Cross-reference target labels:** cross-references now carry the target term's
  human-readable `name` when known (from the SSSOM `object_label`, persisted in a
  new `xref.object_label` column; schema v2). So `map_cross_ontology` /
  `get_disease.xrefs` answer "what *is* OMIM:182212" without a follow-up call;
  OBO-only targets simply omit `name`. The repository reads the column tolerantly,
  so an index built before v2 keeps working (label absent, no crash).
- **Value-vs-name errors disambiguated:** a wrong **type** on a known argument (e.g.
  `prefixes="OMIM"` instead of `["OMIM"]`) now reports the expected type with a
  concrete example (`expects an array, e.g. ["OMIM", "ORPHA"]`) and carries the
  shape in `allowed_values` — no longer dumping the list of valid argument *names*
  (which is reserved for genuinely unknown arguments).
- **`error_rate` noise suppressed:** `get_diagnostics.runtime.error_rate` is
  withheld (`null`) until the sample is meaningful (≥20 requests); raw
  `requests`/`errors` counts are always reported. A single early failure no longer
  reads as an alarming ratio.
- **Acronym resolution (verified + locked):** clinical acronyms that live as Mondo
  synonyms (e.g. `ADPKD`) resolve via the exact-synonym path regardless of case;
  regression coverage now guards the case-insensitive acronym path.

### Added

- **Acronym / fuzzy resolution:** `resolve_disease` now falls back to a
  conservative FTS match (`match_type: "fuzzy"`) for a near-miss or acronym-like
  label with no exact id/xref/label match. It resolves only a clear single winner
  (absolute score floor + dominance over the runner-up); a near-tie returns
  `ambiguous_query` with candidates, and anything weaker returns `not_found` with
  suggestions. `get_disease` stays strict (non-fuzzy) so record retrieval never
  silently guesses.
- **Batch tools:** `resolve_disease_batch(queries=[...])` and
  `get_disease_batch(terms=[...], fields=)` resolve/fetch up to 50 items in one
  round trip with **partial success** — each item returns its record or its own
  `ok=false`/`error_code`/`message`, and the call never fails wholesale (an
  over-cap call returns a single `invalid_input`).
- **Deploy-freshness guard:** `scripts/check_deployed_freshness.py` plus
  `make verify-deploy URL=<server>/diagnostics` fail a deploy whose live
  `build.git_sha` does not match local HEAD — the recurrence guard against
  shipping a green local tree whose fixes never reached the container.
- Regression coverage for the `ambiguous_query` path (a label shared by two
  distinct Mondo terms), end-to-end through the facade envelope.

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
- **`resolve_xref` row/total mismatch:** a term reachable via several mapping rows
  for the same external id (e.g. an OBO `equivalentTo` xref plus an SSSOM
  `exactMatch`) was returned once per row, so `returned` could exceed the
  distinct-term `total` and break a client paging off `total`. The reverse lookup
  now collapses to **one row per distinct Mondo term** (keeping its strongest
  predicate), so `returned <= total` always holds and offset-paging advances by
  whole terms.
- **`get_server_capabilities` missing `_meta.next_commands`:** the discovery root
  omitted `next_commands`, contradicting both the universal `_meta` invariant and
  its own `per_call_meta` contract (which lists `next_commands` as guaranteed). It
  now chains into the canonical `resolve_disease` → record workflow plus a
  `get_diagnostics` freshness check.
- **Human-disease prior in fuzzy resolve:** a one-character typo like
  `resolve_disease("Marfan syndrom")` could surface Mondo's veterinary terms
  ("Marfan syndrome, FBN1-related, pig/cattle") above the canonical human term,
  because those names score higher in raw FTS. Fuzzy hits are now fetched in a
  larger pool and stably re-ranked so non-human-animal terms (descendants of
  `MONDO:0005583`) sink below human terms — livestock no longer leads the
  candidates, and a dominant human term resolves cleanly. Genuinely non-human-only
  queries are unaffected (demotion is a no-op when every hit is non-human).

### Changed

- **Documented batch cap:** `capabilities.limits` now advertises
  `max_batch_items` (50) alongside the search/closure/xref limits, sourced from a
  single `constants.MAX_BATCH_ITEMS` shared by the batch tools and the discovery
  surface — previously the cap was discoverable only by tripping it.

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

## [0.1.2] - 2026-07-03

### Fixed

- **MCP `serverInfo.version` now advertises the package version, not FastMCP's.**
  `create_mondo_mcp()` built its `FastMCP(...)` instance without a `version=`
  argument, so the MCP `initialize` handshake reported the FastMCP framework
  version (e.g. `3.4.2`) as the server version. It now passes
  `version=__version__`, so `serverInfo.version` matches the `mondo-link`
  package version and the existing `/health` endpoint.

### Changed

- **Single-source versioning.** `mondo_link.__version__` is now derived from the
  installed package metadata (`importlib.metadata.version("mondo-link")`) instead
  of a hardcoded literal that had drifted to `0.1.0`. `pyproject.toml`
  `[project].version` is now the sole source of truth; a new guard test
  (`tests/unit/test_version_single_source.py`) asserts that the pyproject
  version, installed metadata, `__version__`, and `create_mondo_mcp().version`
  are all one value, preventing future drift.

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
