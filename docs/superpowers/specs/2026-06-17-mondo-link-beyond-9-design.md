# mondo-link → beyond 9/10 — Design Spec

- **Date:** 2026-06-17
- **Status:** Approved (brainstorming complete) → ready for implementation planning

> Historical record — this document records the design as of its date. Current behavior is
> defined by implemented code, standards, release evidence, and tests.

- **Branch:** `mondo-link-beyond-9` (based on `c57f5e4`)
- **Author:** senior MCP engineering pass (Claude Code)

## 1. Context

A black-box MCP-consumer review of the **deployed** `mondo-link` server (commit
`7ce6da8`) scored it ~6/10 and surfaced five issues:

1. **P0** — `get_disease` and `map_cross_ontology` leaked a raw output-schema
   validation error (grouped xrefs declared `array`, serialized as an object).
   Deterministic across all response modes; broke the canonical
   `resolve_disease → get_disease` workflow at step 2.
2. Search responses were verbose (full definitions in `compact`).
3. No `capabilities_version` for warm-client caching.
4. Pagination was limit-widen only (no offset; closures > 1000 untraversable).
5. Acronym / `not_found` queries dead-ended (e.g. `ADPKD 1`).

A source audit then found the working tree **already contained fixes for all
five**, plus runtime metrics and sparse fieldsets — and the regression test
(`tests/unit/test_output_schemas.py`) that enforces the P0 invariant. `make
ci-local` was green (mypy --strict, 232 tests).

**During this session that work was committed and merged out-of-band:**

- `60a2c14` — `feat(mcp): output-schema integrity, token-lean responses, pagination, observability`
- `c57f5e4` — merge commit (current HEAD of `main` and this branch)

`main` is **ahead of `origin/main` by 2 and not yet pushed or deployed**, so the
**live server is still the broken `7ce6da8`**. The fixes are real and landed; the
gap is now *delivery* (push + redeploy) plus a *beyond-parity* delta.

## 2. Current state (verified)

| Concern | Deployed `7ce6da8` | Source (HEAD `c57f5e4`) |
|---|---|---|
| Grouped-xref schema | `array` → leak | `_GROUPED_XREFS` object (`schemas.py:56`) ✅ |
| Search verbosity | full definitions | 140-char `definition_snippet` (`shaping.py:114`) ✅ |
| `capabilities_version` | absent | stamped in every `_meta` (`envelope.py:216`, `capabilities.py:105`) ✅ |
| Pagination | limit-widen | offset + `next_offset` (`pagination.py`) ✅ |
| Runtime metrics | none | p50/p95/p99 + per-tool in `get_diagnostics.runtime` (`metrics.py`, `discovery.py:75`) ✅ |
| Sparse fieldsets | none | `select_fields` (`xrefs.OMIM`) (`shaping.py:79`) ✅ |
| `not_found` suggestions | route to search | embedded candidate `next_commands` (`envelope.py:138`, `mondo_service.py:108`) ✅ |
| **Acronym/fuzzy resolve** | 404 | **still 404** (`resolve_label` is exact-only) ❌ |

## 3. Goals / Non-goals

**Goals**

- G1 — Get the committed fixes **live** (push + redeploy) and prove it.
- G2 — Make `mondo-link` measurably exceed 9/10 by closing the one functional
  gap (acronym/fuzzy resolve) and adding batch resolution.
- G3 — Prevent recurrence of the "stale broken build" class of failure.
- G4 — Preserve every AGENTS.md invariant; keep `make ci-local` green.

**Non-goals**

- No rewrite of the data/ingest plane or SQLite schema (fuzzy reuses FTS).
- No new external network dependencies; server stays offline/local-index.
- No clinical-decision features. Research-use-only contract unchanged.
- No history rewrite of `60a2c14`/`c57f5e4` (the granular-commit idea is moot —
  the work already landed as one feat + merge).

## 4. Phase 1 — Harden & Ship *(delivery only; no new behavior)*

The fixes are committed. Remaining work:

1. **Push** `mondo-link-beyond-9` (or fast-forward `main`) to origin.
2. **Redeploy** the container so it rebuilds from the new HEAD (cron/data build
   unchanged; this is a code redeploy).
3. **Live smoke-verify** (scripted, see §6.F4):
   - `get_diagnostics.build.git_sha` == new HEAD short sha, and a `runtime`
     block is present.
   - `get_disease("OMIM:173900", response_mode="full")` returns a valid envelope
     (no validation error); `xrefs` is an object keyed by prefix.
   - `map_cross_ontology` likewise.
   - Every `_meta` carries `capabilities_version`.
   - A truncated closure returns `next_offset`, and advancing `offset` pages
     forward without re-sending rows.

**Acceptance:** live `get_diagnostics` reports the new sha **and** the four checks
above pass. This alone moves the live score ~6 → ~9.

## 5. Phase 2 — Exceed *(four features; independent of each other)*

### F1 — Acronym / fuzzy resolution *(highest user-facing value)*

**Problem.** `MondoService._classify_resolution` (`mondo_service.py:220`) ends at
`repo.resolve_label(raw.upper())`, an exact label/synonym match. Acronyms and
near-misses (`ADPKD 1`) fall straight through to `not_found`.

**Design.** Insert a **conservative fuzzy fallback** *after* exact-label failure
and *before* `_label_not_found`:

1. Run the existing FTS (`repo.search(raw, limit=N)`).
2. Compute a confidence decision:
   - **Resolve** (new `match_type: "fuzzy"`) iff the top hit's score ≥ an
     absolute threshold **and** exceeds the second hit by a relative gap
     (top is unambiguous).
   - **Ambiguous** (`AmbiguousQueryError` with candidates) iff multiple hits
     cluster near the top (no clear winner).
   - **Not found** (existing path, with suggestions) iff nothing clears the bar.
3. Thresholds are module constants, tuned against a fixture set and documented.

**Why FTS reuse, not a new acronym index:** no ingest/schema change, stays in the
data plane, and the bias-to-ambiguous/not-found rule guarantees it never silently
returns a wrong term. A dedicated acronym table is a future option, not now.

**Surfaces:** `match_types` gains `fuzzy` in `constants.py` + capabilities;
`resolve_disease` description notes acronym/fuzzy support; `RESOLVE_DISEASE_SCHEMA`
already permits `match_type` (no schema change).

### F2 — Verify `ambiguous_query`

**Problem.** The path exists (`len(distinct) > 1` → `AmbiguousQueryError`,
`mondo_service.py:246`) but was never observed (`anemia` is itself a primary
term). Coverage gap, not a known bug.

**Design.** Find a label that maps to ≥2 distinct MONDO ids (a shared synonym),
add a regression test asserting `error_code == "ambiguous_query"`, populated
`candidates`, and `next_commands` to each candidate's `get_disease`. Fix only if
the path proves dead. Interacts with F1: fuzzy must defer to the exact-ambiguous
case (exact matches win before fuzzy runs).

### F3 — Batch resolve / get *(largest surface)*

**Design.** Two new tools with **partial-success** semantics:

- `resolve_disease_batch(queries: list[str], response_mode=)`
- `get_disease_batch(terms: list[str], response_mode=, fields=)`

Each returns `results: [...]` where every element is either the normal record or
a per-item `{query|term, error_code, message}` — the call **never fails
wholesale**. A batch-size cap (`MAX_BATCH = 50`) raises `invalid_input` when
exceeded. Service methods loop the existing single-item logic (catching the typed
exceptions per item); no new repository queries.

**Surfaces (invariant-critical):** new `BATCH_*` output schemas; new
`mcp/tools/batch.py` registration; **`capabilities.TOOLS` updated** (enforced by
`test_tool_names.py`); capabilities `recommended_workflows` gains a batch line;
`test_output_schemas.py` covers both new tools (success + per-item-error + cap
error); facade registers the new tool module.

### F4 — Deploy-freshness guard *(prevents recurrence)*

**Design, two parts:**

1. **CI:** confirm `tests/unit/test_output_schemas.py` runs in `make ci-local`
   (it does, now that it's committed) — this is the gate that would have caught
   the P0. Document it in AGENTS.md "Definition of done".
2. **Post-deploy smoke gate:** `scripts/check_deployed_freshness.py` calls a live
   server's `get_diagnostics` and **exits non-zero if `build.git_sha` != local
   `git rev-parse --short HEAD`** (and asserts a `runtime` block + valid
   `get_disease` envelope). Wire it as a deploy step / Makefile target
   (`make verify-deploy URL=...`). Runtime can't know HEAD, so this lives in the
   release path, not the server.

## 6. Cross-cutting invariants (must hold for all Phase 2 work)

- **LOC budget (≤500/file, hard gate).** `mondo_service.py` is 457/500 — F1 + F3
  would breach it. **Refactor:** extract the resolution cascade
  (`_resolve_term_id`, `_classify_resolution`, `_label_not_found`,
  `_search_suggestions`, `_label_candidates`, `_replacement_records`, + fuzzy)
  into `mondo_link/services/resolution.py`; `MondoService` orchestrates. Batch
  tools live in `mcp/tools/batch.py`. Verify with `scripts/check_file_size.py`.
- Every new tool: `output_schema` + `READ_ONLY_OPEN_WORLD`; first description
  sentence ends `Signature: tool(args...)`; `_meta.next_commands` populated.
- Every tool's real output (success + error, **all** response modes) validates
  against its `output_schema` (`test_output_schemas.py`).
- `capabilities.TOOLS` == registered tool set (`test_tool_names.py`).
- Identifiers normalised only in `identifiers.py`; services return plain dicts;
  the envelope owns `success`/`_meta` and error taxonomy.
- Gate green: ruff format/check, mypy --strict, coverage ≥ 80%.

## 7. Testing strategy

- **F1:** unit tests on the resolution module — `ADPKD 1` resolves with
  `match_type: "fuzzy"`; a gibberish string still 404s; a near-tie raises
  `ambiguous_query`; exact label still beats fuzzy. Threshold table tested at
  boundaries.
- **F2:** regression test for a real ambiguous label.
- **F3:** batch happy path, mixed partial-success (valid + bogus item in one
  call), over-cap `invalid_input`; output-schema coverage for both tools.
- **F4:** unit test for the freshness comparator (sha match/mismatch); the script
  is import-testable (pure compare fn + thin I/O shell).
- **E2E:** facade integration tests exercise the new tools end-to-end.

## 8. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Fuzzy false-positive (wrong term silently returned) | Absolute threshold + score-gap; bias to `ambiguous`/`not_found`; fixture-tested boundaries |
| Batch abuse / token blowup | `MAX_BATCH=50`, `invalid_input` over cap; per-item compact default |
| LOC gate breach | `resolution.py` + `batch.py` extraction (§6) |
| Re-introducing a schema leak in new tools | `test_output_schemas.py` covers new tools; required in CI |
| Stale deploy recurs | F4 freshness gate in the release path |

## 9. Sequencing

1. **Phase 1** (push + redeploy + smoke) — independent, immediate; ship first.
2. **F4** freshness gate — small; lands with/just after Phase 1 to lock in G3.
3. **Refactor** `resolution.py` extraction (no behavior change) — precedes F1.
4. **F1** fuzzy resolve, then **F2** ambiguous verification (F2 depends on F1's
   exact-before-fuzzy ordering).
5. **F3** batch tools — largest; last.

## 10. Acceptance criteria (definition of done)

- Live `get_diagnostics` reports the new sha + `runtime`; Phase 1 smoke checks
  pass against the deployed server.
- `resolve_disease("ADPKD 1")` resolves to `MONDO:0008263` (or returns ranked
  candidates), not a bare 404.
- `ambiguous_query` has a passing regression test.
- `resolve_disease_batch` / `get_disease_batch` registered, in `capabilities.TOOLS`,
  schema-validated, partial-success + cap behavior tested.
- `scripts/check_deployed_freshness.py` exists and gates deploys.
- Every file ≤ 500 lines; `make ci-local` green; coverage ≥ 80%.
