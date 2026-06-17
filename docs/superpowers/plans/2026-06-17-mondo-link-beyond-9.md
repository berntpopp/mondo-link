# mondo-link → beyond 9/10 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the already-committed MCP best-practice fixes to the live server, then add acronym/fuzzy resolution, batch tools, an `ambiguous_query` regression, and a deploy-freshness guard — pushing the server beyond 9/10.

**Architecture:** Two planes (data plane returns plain dicts + raises typed exceptions; MCP plane owns `success`/`_meta` + the 7-code error taxonomy). Fuzzy resolution reuses the existing FTS (no ingest/schema change) via a pure decision function. Batch is an MCP-plane loop over existing single-item service calls (partial-success), keeping the service pure and `mondo_service.py` under the 500-line gate (via a `resolution.py` extraction).

**Tech Stack:** Python 3.12, FastMCP, SQLite FTS5, pydantic, pytest (`-n auto`), ruff, mypy --strict. Spec: `docs/superpowers/specs/2026-06-17-mondo-link-beyond-9-design.md`.

---

## File Structure

**Create**
- `scripts/check_deployed_freshness.py` — deploy-freshness comparator + CLI (F4).
- `tests/unit/test_deploy_freshness.py` — freshness comparator tests.
- `mondo_link/services/resolution.py` — extracted resolution cascade + fuzzy fallback.
- `tests/unit/test_resolution.py` — pure `decide_fuzzy` + cascade tests.
- `mondo_link/mcp/tools/batch.py` — `resolve_disease_batch`, `get_disease_batch`.
- `tests/unit/test_batch.py` — batch service/tool tests.

**Modify**
- `mondo_link/services/mondo_service.py` — delegate resolution to `resolution.py` (shrinks the file).
- `mondo_link/constants.py:25` — add `"fuzzy"` to `MATCH_TYPES`.
- `mondo_link/mcp/envelope.py` — expose `classify_exception(exc)` (public) for per-item batch errors.
- `mondo_link/mcp/schemas.py` — add `BATCH_RESOLVE_SCHEMA`, `BATCH_DISEASE_SCHEMA`.
- `mondo_link/mcp/capabilities.py:42` — add the two batch tools to `TOOLS`; add a batch workflow line.
- `mondo_link/mcp/next_commands.py` — add `after_resolve_batch` / `after_get_disease_batch`.
- `mondo_link/mcp/tools/__init__.py` — export `register_batch_tools`.
- `mondo_link/mcp/facade.py:29` — call `register_batch_tools(mcp)`.
- `mondo_link/mcp/tools/diseases.py` — note fuzzy support in `resolve_disease` description.
- `tests/fixtures/mondo.obo` — add a shared synonym to create a genuine ambiguity (F2).
- `tests/unit/test_output_schemas.py` — add batch-tool schema coverage.
- `tests/unit/test_service.py` / `test_tools_e2e.py` — fuzzy + batch e2e.
- `Makefile` — add `verify-deploy` target.
- `AGENTS.md`, `CHANGELOG.md` — document the freshness gate + the new tools.

**Conventions (verified, reuse — do not reinvent):**
- Test fixtures (`tests/conftest.py`): `service` (MondoService over fixture DB), `facade` (FastMCP with fixture service injected), `structured`, and the `tool_map` pattern from `test_output_schemas.py`.
- Fixture terms: `MONDO:0008426` Shprintzen-Goldberg syndrome (synonym `SGS`, `Marfanoid craniosynostosis syndrome`, related `marfanoid disorder`); obsolete `MONDO:0099999` (replaced_by `MONDO:0008426`); missing `MONDO:0000000`.
- `repo.search(query, *, limit, include_obsolete, offset=0) -> (hits, total)`; each hit `{mondo_id, name, definition, score}` where **higher `score` = more relevant**, returned best-first.
- Exceptions: `AmbiguousQueryError(msg, candidates=[{mondo_id,name,label_type}])`, `NotFoundError(msg, suggestions=[...])`, `InvalidInputError(msg, field=, allowed=, hint=)`.

---

## Phase A — Deploy hardening (F4) + ship the fixes

### Task 1: Deploy-freshness comparator + script

**Files:**
- Create: `scripts/check_deployed_freshness.py`
- Test: `tests/unit/test_deploy_freshness.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_deploy_freshness.py
"""Unit tests for the deploy-freshness comparator (pure logic; no network)."""
from __future__ import annotations

import pytest

from scripts.check_deployed_freshness import extract_git_sha, is_fresh


def test_extract_git_sha_reads_nested_build_block() -> None:
    diag = {"build": {"git_sha": "abc1234", "built_at": "2026-06-17T00:00:00Z"}}
    assert extract_git_sha(diag) == "abc1234"


def test_extract_git_sha_missing_returns_none() -> None:
    assert extract_git_sha({"build": {}}) is None
    assert extract_git_sha({}) is None


@pytest.mark.parametrize(
    ("deployed", "local", "expected"),
    [("abc1234", "abc1234", True), ("abc1234", "abc1234def", True), ("old0000", "new1111", False)],
)
def test_is_fresh_compares_short_sha_prefix(deployed: str, local: str, expected: bool) -> None:
    diag = {"build": {"git_sha": deployed}}
    assert is_fresh(diag, local) is expected


def test_is_fresh_false_when_sha_absent() -> None:
    assert is_fresh({"build": {}}, "anything") is False
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/unit/test_deploy_freshness.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.check_deployed_freshness'`.

- [ ] **Step 3: Write the script**

```python
# scripts/check_deployed_freshness.py
"""Post-deploy guard: fail if the live server's build sha != local HEAD.

The deployed sha is read from a `get_diagnostics` payload (JSON on stdin or a
file). The operator obtains that payload from the running server (REST
`/diagnostics` or an MCP `get_diagnostics` call) and pipes it in. Keeping the
fetch out of this script makes the comparison pure and unit-testable; the I/O
shell is a thin `main`.
"""
from __future__ import annotations

import json
import subprocess
import sys
from typing import Any


def extract_git_sha(diagnostics: dict[str, Any]) -> str | None:
    """Return the deployed build git sha from a diagnostics payload, or None."""
    build = diagnostics.get("build")
    if isinstance(build, dict):
        sha = build.get("git_sha")
        return str(sha) if sha else None
    return None


def local_head_sha() -> str:
    """Return the local repository's short HEAD sha."""
    out = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True
    )
    return out.stdout.strip()


def is_fresh(diagnostics: dict[str, Any], local_sha: str) -> bool:
    """True iff the deployed sha matches the local HEAD (prefix-compatible)."""
    deployed = extract_git_sha(diagnostics)
    if not deployed or not local_sha:
        return False
    return local_sha.startswith(deployed) or deployed.startswith(local_sha)


def main(argv: list[str]) -> int:
    """Read a diagnostics JSON (stdin or argv[1]) and compare to local HEAD."""
    raw = open(argv[1]).read() if len(argv) > 1 else sys.stdin.read()
    diagnostics = json.loads(raw)
    local = local_head_sha()
    if is_fresh(diagnostics, local):
        print(f"OK: deployed sha matches local HEAD ({local}).")
        return 0
    print(
        f"STALE: deployed sha {extract_git_sha(diagnostics)!r} != local HEAD {local!r}.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv))
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/unit/test_deploy_freshness.py -q`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/check_deployed_freshness.py tests/unit/test_deploy_freshness.py
git commit -m "feat(ops): deploy-freshness comparator + post-deploy guard script"
```

### Task 2: Makefile target + AGENTS.md note

**Files:**
- Modify: `Makefile`, `AGENTS.md`

- [ ] **Step 1: Add the `verify-deploy` target to `Makefile`**

Append (use a TAB-indented recipe; `URL` is the live server's diagnostics endpoint):

```make
.PHONY: verify-deploy
verify-deploy:  ## Fail if the deployed build sha != local HEAD (URL=... required)
	@test -n "$(URL)" || { echo "set URL=<diagnostics endpoint>"; exit 2; }
	curl -fsS "$(URL)" | uv run python scripts/check_deployed_freshness.py
```

- [ ] **Step 2: Document the gate in `AGENTS.md` "Definition of done"**

Under the Definition of done section, add a bullet:

```markdown
- After a redeploy, `make verify-deploy URL=<server>/diagnostics` must pass: the
  live `get_diagnostics.build.git_sha` equals local HEAD. `test_output_schemas.py`
  runs in `make ci-local` and is the gate against grouped-payload schema leaks.
```

- [ ] **Step 3: Verify the Makefile target parses**

Run: `make verify-deploy` (no URL)
Expected: prints `set URL=<diagnostics endpoint>` and exits non-zero (2).

- [ ] **Step 4: Commit**

```bash
git add Makefile AGENTS.md
git commit -m "feat(ops): make verify-deploy target + document freshness gate"
```

### Task 3: Ship Phase 1 (push + redeploy + verify) — operator step

> This task performs an outward-facing deploy. It needs push + container-rebuild
> access. If the executing agent lacks that, STOP after Step 1 and hand back to
> the user with the exact commands.

**Files:** none (delivery only; the fixes are already in `60a2c14`).

- [ ] **Step 1: Confirm the branch is current and green**

Run: `git log --oneline -3 && make ci-local`
Expected: history shows `60a2c14 feat(mcp): output-schema integrity...`; gate green.

- [ ] **Step 2: Push and integrate to the deploy branch**

```bash
git push -u origin mondo-link-beyond-9
# open a PR into main, or fast-forward main if that is the deploy source
```

- [ ] **Step 3: Redeploy** the container so it rebuilds from the new HEAD (project-specific: CI/CD or `docker build` + restart). No data rebuild needed — code only.

- [ ] **Step 4: Verify the live server is fresh and unbroken**

```bash
make verify-deploy URL=https://<live-host>/diagnostics
```
Expected: `OK: deployed sha matches local HEAD (<sha>)`. Then spot-check via an MCP client: `get_disease(term="OMIM:173900", response_mode="full")` returns a valid envelope with `xrefs` as an object (no `is not of type 'array'`), and every `_meta` carries `capabilities_version`.

- [ ] **Step 5: No commit** (delivery only). Record the deployed sha in the PR description.

---

## Phase B — Refactor (enables F1/F3 under the 500-line gate)

### Task 4: Extract the resolution cascade into `services/resolution.py`

> Pure move + re-wire, **no behavior change**. Success = the existing suite stays
> green. `mondo_service.py` is 457/500; this moves ~120 lines out.

**Files:**
- Create: `mondo_link/services/resolution.py`
- Modify: `mondo_link/services/mondo_service.py`
- Test: existing `tests/unit/test_service.py` (must stay green)

- [ ] **Step 1: Create `resolution.py` with a `Resolver` over the repository**

Move these members of `MondoService` verbatim into a new `Resolver` class that
takes a `MondoRepository`: `_resolve_term_id`, `_classify_resolution`,
`_label_not_found`, `_search_suggestions`, `_label_candidates`,
`_replacement_records`, and the `_LABEL_MATCH_TYPE` constant.

```python
# mondo_link/services/resolution.py
"""Resolution cascade: id / xref / label -> canonical MONDO id (+ match provenance).

Extracted from MondoService to keep that file within the 500-line gate and to
isolate the fuzzy fallback (Task 6). Returns plain data / raises typed
exceptions; the MCP envelope owns error shaping.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mondo_link.exceptions import AmbiguousQueryError, NotFoundError, WithdrawnEntryError
from mondo_link.identifiers import infer_xref_source, normalize_mondo_id, normalize_xref

if TYPE_CHECKING:
    from mondo_link.data.repository import MondoRepository

_LABEL_MATCH_TYPE = {
    "primary": "primary",
    "exact_synonym": "exact_synonym",
    "related_synonym": "related_synonym",
    "broad_synonym": "related_synonym",
    "narrow_synonym": "related_synonym",
}


class Resolver:
    """Resolve any id/label/xref to a canonical MONDO id with provenance."""

    def __init__(self, repo: MondoRepository) -> None:
        self._repo = repo

    # (paste _resolve_term_id, _classify_resolution, _label_not_found,
    #  _search_suggestions, _label_candidates, _replacement_records here verbatim,
    #  replacing every `self.repo` with `self._repo`.)
```

- [ ] **Step 2: Re-wire `MondoService` to delegate**

In `mondo_service.py`: construct `self._resolver = Resolver(repository)` when a repo
is present, drop the moved methods, and replace internal calls
`self._resolve_term_id(term)` → `self._resolver.resolve_term_id(term)` and
`self._classify_resolution(raw)` → `self._resolver.classify_resolution(raw)`.
(Rename the two entry points to public — no leading underscore — since they now
cross a module boundary.)

- [ ] **Step 3: Run the full suite to verify no behavior change**

Run: `uv run pytest tests -q -m "not integration"`
Expected: PASS, same count as before (232).

- [ ] **Step 4: Verify the file-size gate**

Run: `uv run python scripts/check_file_size.py`
Expected: `OK: all files within the 500-line budget.`

- [ ] **Step 5: Commit**

```bash
git add mondo_link/services/resolution.py mondo_link/services/mondo_service.py
git commit -m "refactor(services): extract resolution cascade into resolution.py (no behavior change)"
```

---

## Phase C — F1: Acronym / fuzzy resolution

### Task 5: Pure `decide_fuzzy` decision function

**Files:**
- Modify: `mondo_link/services/resolution.py`
- Test: `tests/unit/test_resolution.py`

- [ ] **Step 1: Write the failing test (synthetic hits — no DB)**

```python
# tests/unit/test_resolution.py
"""Unit tests for the pure fuzzy-resolution decision logic."""
from __future__ import annotations

from mondo_link.services.resolution import (
    FUZZY_DOMINANCE,
    FUZZY_MIN_SCORE,
    decide_fuzzy,
)


def _hit(mid: str, name: str, score: float) -> dict[str, object]:
    return {"mondo_id": mid, "name": name, "score": score}


def test_empty_hits_resolve_to_none() -> None:
    assert decide_fuzzy([]) == ("none", None)


def test_below_floor_resolves_to_none() -> None:
    assert decide_fuzzy([_hit("MONDO:1", "x", FUZZY_MIN_SCORE - 0.01)]) == ("none", None)


def test_single_strong_hit_resolves() -> None:
    kind, payload = decide_fuzzy([_hit("MONDO:0008263", "pkd 1", FUZZY_MIN_SCORE + 1.0)])
    assert kind == "resolve"
    assert payload["mondo_id"] == "MONDO:0008263"


def test_dominant_top_resolves() -> None:
    hits = [_hit("MONDO:1", "a", 3.0), _hit("MONDO:2", "b", 3.0 / (FUZZY_DOMINANCE + 0.5))]
    kind, payload = decide_fuzzy(hits)
    assert kind == "resolve"
    assert payload["mondo_id"] == "MONDO:1"


def test_close_runner_up_is_ambiguous() -> None:
    hits = [_hit("MONDO:1", "a", 3.0), _hit("MONDO:2", "b", 2.9)]
    kind, candidates = decide_fuzzy(hits)
    assert kind == "ambiguous"
    assert {c["mondo_id"] for c in candidates} == {"MONDO:1", "MONDO:2"}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/unit/test_resolution.py -q`
Expected: FAIL — `ImportError: cannot import name 'decide_fuzzy'`.

- [ ] **Step 3: Implement `decide_fuzzy` in `resolution.py`**

```python
# add near the top of resolution.py
FUZZY_MIN_SCORE = 0.5      # score floor (repo.search score = round(-bm25,4); higher=better)
FUZZY_DOMINANCE = 1.5      # top must beat #2 by this factor to be an unambiguous winner
FUZZY_MAX_CANDIDATES = 5


def decide_fuzzy(
    hits: list[dict[str, Any]],
) -> tuple[str, dict[str, Any] | list[dict[str, Any]] | None]:
    """Classify FTS hits into a fuzzy decision.

    Returns ("resolve", top_hit) for a clear winner, ("ambiguous", candidates) when
    the runner-up is within FUZZY_DOMINANCE, or ("none", None) when nothing clears
    FUZZY_MIN_SCORE. Conservative by design: never returns a winner on a tie.
    """
    if not hits:
        return ("none", None)
    top = hits[0]
    if float(top.get("score") or 0.0) < FUZZY_MIN_SCORE:
        return ("none", None)
    if len(hits) == 1:
        return ("resolve", top)
    second = float(hits[1].get("score") or 0.0)
    if second <= 0.0 or float(top["score"]) >= FUZZY_DOMINANCE * second:
        return ("resolve", top)
    return ("ambiguous", hits[:FUZZY_MAX_CANDIDATES])
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/unit/test_resolution.py -q`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add mondo_link/services/resolution.py tests/unit/test_resolution.py
git commit -m "feat(resolve): pure fuzzy-resolution decision function"
```

### Task 6: Wire fuzzy into the cascade + surface it

**Files:**
- Modify: `mondo_link/services/resolution.py`, `mondo_link/constants.py:25`, `mondo_link/mcp/tools/diseases.py`
- Test: `tests/unit/test_resolution.py`, `tests/unit/test_service.py`

- [ ] **Step 1: Write the failing integration test (real fixture terms)**

```python
# add to tests/unit/test_resolution.py
import pytest

from mondo_link.exceptions import NotFoundError


def test_fuzzy_resolves_near_miss_label(service) -> None:
    # "Shprintzen Goldberg" (space, no hyphen) is not an exact label but FTS-matches
    # only MONDO:0008426 -> resolves with match_type "fuzzy".
    out = service.resolve_disease("Shprintzen Goldberg")
    assert out["mondo_id"] == "MONDO:0008426"
    assert out["match_type"] == "fuzzy"


def test_gibberish_still_not_found(service) -> None:
    with pytest.raises(NotFoundError):
        service.resolve_disease("zzzzznotadiseasezzzzz")
```

(The `service` fixture comes from `tests/conftest.py`.)

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/unit/test_resolution.py -q`
Expected: FAIL — `test_fuzzy_resolves_near_miss_label` raises `NotFoundError` (no fuzzy step yet).

- [ ] **Step 3: Insert the fuzzy fallback in `classify_resolution`**

In `resolution.py`, in `classify_resolution`, replace the final
`raise self._label_not_found(raw)` branch (the `if not candidates:` path) with a
fuzzy attempt first:

```python
        candidates = self._repo.resolve_label(raw.upper())
        if not candidates:
            return self._fuzzy_or_not_found(raw)   # was: raise self._label_not_found(raw)
        distinct = {c["mondo_id"] for c in candidates}
        if len(distinct) == 1:
            best = candidates[0]
            return _LABEL_MATCH_TYPE.get(best["label_type"], "primary"), best["mondo_id"]
        raise AmbiguousQueryError(
            f"'{raw}' matches {len(distinct)} Mondo terms; pick one and call get_disease.",
            candidates=self._label_candidates(candidates),
        )

    def _fuzzy_or_not_found(self, raw: str) -> tuple[str, str]:
        """Exact-label miss: try a conservative FTS-based fuzzy resolution."""
        hits, _ = self._repo.search(raw, limit=FUZZY_MAX_CANDIDATES, include_obsolete=False)
        kind, payload = decide_fuzzy(hits)
        if kind == "resolve":
            return "fuzzy", str(payload["mondo_id"])  # type: ignore[index]
        if kind == "ambiguous":
            cands = [
                {"mondo_id": h["mondo_id"], "name": h["name"], "label_type": "fuzzy"}
                for h in payload  # type: ignore[union-attr]
            ]
            raise AmbiguousQueryError(
                f"'{raw}' has no exact match; closest terms are in candidates.",
                candidates=cands,
            )
        raise self._label_not_found(raw)
```

(`AmbiguousQueryError` is already imported in `resolution.py`.)

- [ ] **Step 4: Add `"fuzzy"` to `MATCH_TYPES`**

In `mondo_link/constants.py:25`:

```python
MATCH_TYPES = ("mondo_id", "primary", "exact_synonym", "related_synonym", "fuzzy", "xref")
```

- [ ] **Step 5: Note fuzzy support in the `resolve_disease` description**

In `mondo_link/mcp/tools/diseases.py`, extend the `resolve_disease` description sentence (keep the `Signature:` ending intact):

```python
            "{mondo_id, name, match_type}. A near-miss label falls back to a "
            "conservative fuzzy match (match_type='fuzzy'); an ambiguous label "
            "returns ambiguous_query with candidates; an obsolete id returns "
            "not_found with its successor. "
            "Signature: resolve_disease(query, response_mode=)."
```

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/unit/test_resolution.py tests/unit/test_service.py -q`
Expected: PASS (fuzzy resolves; gibberish still 404s; existing service tests green).

- [ ] **Step 7: Verify the capabilities hash test still passes**

Run: `uv run pytest tests/unit/test_capabilities.py -q`
Expected: PASS (MATCH_TYPES change re-hashes `capabilities_version`; if a test pins the old hash, update it to the new value it reports).

- [ ] **Step 8: Commit**

```bash
git add mondo_link/services/resolution.py mondo_link/constants.py mondo_link/mcp/tools/diseases.py tests/unit/test_resolution.py
git commit -m "feat(resolve): conservative FTS fuzzy fallback for near-miss/acronym labels"
```

---

## Phase D — F2: Verify `ambiguous_query`

### Task 7: Create a genuine ambiguity in the fixture + regression test

**Files:**
- Modify: `tests/fixtures/mondo.obo`
- Test: `tests/unit/test_service.py`

- [ ] **Step 1: Add a shared EXACT synonym to two fixture terms**

In `tests/fixtures/mondo.obo`, add the same synonym line under **two** different
`[Term]` stanzas (e.g. `MONDO:0000002` cardiovascular disorder and `MONDO:0000003`
nervous system disorder):

```
synonym: "shared ambiguous disorder" EXACT []
```

(Add the identical line to both stanzas.)

- [ ] **Step 2: Write the regression test**

```python
# add to tests/unit/test_service.py
import pytest

from mondo_link.exceptions import AmbiguousQueryError


def test_ambiguous_label_raises_with_candidates(service) -> None:
    with pytest.raises(AmbiguousQueryError) as exc:
        service.resolve_disease("shared ambiguous disorder")
    candidates = exc.value.candidates
    assert len({c["mondo_id"] for c in candidates}) >= 2
    assert all(c.get("name") for c in candidates)
```

- [ ] **Step 3: Run it (the session-scoped `built_db` rebuilds from the edited OBO)**

Run: `uv run pytest tests/unit/test_service.py::test_ambiguous_label_raises_with_candidates -q`
Expected: PASS. If it does not raise, the exact-label path needs the synonym indexed as a `term_lookup` row — confirm the builder maps EXACT synonyms into `term_lookup` (it does for `SGS`); if not, file a follow-up and use two terms sharing a primary-name collision instead.

- [ ] **Step 4: Verify the envelope surfaces candidates end-to-end**

```python
# add to tests/unit/test_tools_e2e.py
async def test_resolve_ambiguous_envelope(facade, structured) -> None:
    tool = {t.name: t for t in await facade.list_tools()}["resolve_disease"]
    payload = await tool.fn(query="shared ambiguous disorder")
    assert payload["success"] is False
    assert payload["error_code"] == "ambiguous_query"
    assert payload["candidates"]
    assert payload["_meta"]["next_commands"][0]["tool"] == "get_disease"
```

Run: `uv run pytest tests/unit/test_tools_e2e.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/fixtures/mondo.obo tests/unit/test_service.py tests/unit/test_tools_e2e.py
git commit -m "test(resolve): regression coverage for ambiguous_query path"
```

---

## Phase E — F3: Batch tools

### Task 8: Public `classify_exception` for per-item batch errors

**Files:**
- Modify: `mondo_link/mcp/envelope.py`
- Test: `tests/unit/test_infra.py` (envelope tests live here)

- [ ] **Step 1: Write the failing test**

```python
# add to tests/unit/test_infra.py
from mondo_link.exceptions import AmbiguousQueryError, NotFoundError
from mondo_link.mcp.envelope import classify_exception


def test_classify_exception_maps_typed_errors() -> None:
    assert classify_exception(NotFoundError("x"))[0] == "not_found"
    assert classify_exception(AmbiguousQueryError("y"))[0] == "ambiguous_query"
    assert classify_exception(ValueError("boom"))[0] == "internal_error"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/unit/test_infra.py -q -k classify_exception`
Expected: FAIL — `ImportError: cannot import name 'classify_exception'`.

- [ ] **Step 3: Expose the existing classifier publicly**

In `mondo_link/mcp/envelope.py`, add a public alias delegating to `_classify`:

```python
def classify_exception(exc: BaseException) -> tuple[str, str]:
    """Public per-item classifier (error_code, client-safe message) for batch tools."""
    return _classify(exc)
```

- [ ] **Step 4: Run it to verify it passes**

Run: `uv run pytest tests/unit/test_infra.py -q -k classify_exception`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mondo_link/mcp/envelope.py tests/unit/test_infra.py
git commit -m "feat(mcp): public classify_exception for per-item batch error shaping"
```

### Task 9: Batch output schemas

**Files:**
- Modify: `mondo_link/mcp/schemas.py`

- [ ] **Step 1: Add the two batch schemas**

Append to `mondo_link/mcp/schemas.py`:

```python
_BATCH_ITEM = {
    "type": "object",
    "additionalProperties": True,
    "properties": {
        "query": _STR,
        "term": _STR,
        "ok": _BOOL,
        "mondo_id": _STR_NULL,
        "name": _STR_NULL,
        "match_type": _STR_NULL,
        "error_code": _STR,
        "message": _STR,
    },
}

BATCH_RESOLVE_SCHEMA = _envelope(
    count=_INT,
    results={"type": "array", "items": _BATCH_ITEM},
)

BATCH_DISEASE_SCHEMA = _envelope(
    count=_INT,
    results={"type": "array", "items": _BATCH_ITEM},
)
```

- [ ] **Step 2: Type-check (no test yet; schemas are data)**

Run: `uv run mypy mondo_link/mcp/schemas.py`
Expected: `Success: no issues found`.

- [ ] **Step 3: Commit**

```bash
git add mondo_link/mcp/schemas.py
git commit -m "feat(mcp): output schemas for batch resolve/get tools"
```

### Task 10: Batch tools + registration + capabilities sync

**Files:**
- Create: `mondo_link/mcp/tools/batch.py`
- Modify: `mondo_link/mcp/tools/__init__.py`, `mondo_link/mcp/facade.py:29`, `mondo_link/mcp/capabilities.py:42`, `mondo_link/mcp/next_commands.py`
- Test: `tests/unit/test_batch.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_batch.py
"""Batch tool tests: partial success, cap enforcement, capabilities sync."""
from __future__ import annotations

from typing import Any

import pytest

pytestmark = pytest.mark.mcp


async def _tool(facade: Any, name: str) -> Any:
    return {t.name: t for t in await facade.list_tools()}[name]


async def test_resolve_batch_partial_success(facade: Any) -> None:
    tool = await _tool(facade, "resolve_disease_batch")
    payload = await tool.fn(queries=["Shprintzen-Goldberg syndrome", "MONDO:0000000"])
    assert payload["success"] is True
    assert payload["count"] == 2
    ok, bad = payload["results"]
    assert ok["ok"] is True and ok["mondo_id"] == "MONDO:0008426"
    assert bad["ok"] is False and bad["error_code"] == "not_found"


async def test_resolve_batch_rejects_oversize(facade: Any) -> None:
    tool = await _tool(facade, "resolve_disease_batch")
    payload = await tool.fn(queries=["x"] * 51)
    assert payload["success"] is False
    assert payload["error_code"] == "invalid_input"


async def test_get_disease_batch_mixed(facade: Any) -> None:
    tool = await _tool(facade, "get_disease_batch")
    payload = await tool.fn(terms=["MONDO:0008426", "MONDO:0000000"])
    assert payload["count"] == 2
    assert payload["results"][0]["ok"] is True
    assert payload["results"][1]["ok"] is False


async def test_batch_tools_in_capabilities() -> None:
    from mondo_link.mcp.capabilities import TOOLS

    assert "resolve_disease_batch" in TOOLS
    assert "get_disease_batch" in TOOLS
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/unit/test_batch.py -q`
Expected: FAIL — `KeyError: 'resolve_disease_batch'` (not registered).

- [ ] **Step 3: Implement `tools/batch.py`**

```python
# mondo_link/mcp/tools/batch.py
"""Batch tools: resolve_disease_batch, get_disease_batch (partial success)."""
from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

from pydantic import Field

from mondo_link.mcp.annotations import READ_ONLY_OPEN_WORLD
from mondo_link.mcp.envelope import McpErrorContext, classify_exception, run_mcp_tool
from mondo_link.mcp.next_commands import after_get_disease_batch, after_resolve_batch
from mondo_link.mcp.schemas import BATCH_DISEASE_SCHEMA, BATCH_RESOLVE_SCHEMA
from mondo_link.mcp.service_adapters import get_mondo_service
from mondo_link.mcp.tools._common import FieldsArg, ResponseMode

if TYPE_CHECKING:
    from fastmcp import FastMCP

MAX_BATCH = 50


def _require_batch(items: list[str], field: str) -> None:
    from mondo_link.exceptions import InvalidInputError

    if not items:
        raise InvalidInputError(f"{field} must be a non-empty list.", field=field)
    if len(items) > MAX_BATCH:
        raise InvalidInputError(
            f"{field} accepts at most {MAX_BATCH} items (got {len(items)}).", field=field
        )


def register_batch_tools(mcp: FastMCP) -> None:
    """Register the batch resolve/get tools on a FastMCP instance."""

    @mcp.tool(
        name="resolve_disease_batch",
        title="Resolve Diseases (batch)",
        annotations=READ_ONLY_OPEN_WORLD,
        output_schema=BATCH_RESOLVE_SCHEMA,
        tags={"disease", "resolve", "batch"},
        description=(
            "Resolve many labels/ids/xrefs in one call (partial success: each item "
            "returns its resolution or its own error_code/message; the call never "
            f"fails wholesale). Max {MAX_BATCH} items. "
            "Signature: resolve_disease_batch(queries, response_mode=)."
        ),
    )
    async def resolve_disease_batch(
        queries: Annotated[list[str], Field(description=f"1..{MAX_BATCH} labels/ids/xrefs.")],
        response_mode: ResponseMode = "compact",
    ) -> dict[str, Any]:
        async def call() -> dict[str, Any]:
            _require_batch(queries, "queries")
            svc = get_mondo_service()
            results: list[dict[str, Any]] = []
            for q in queries:
                try:
                    rec = svc.resolve_disease(q, response_mode=response_mode)
                    results.append({"query": q, "ok": True, **rec})
                except Exception as exc:  # per-item boundary; whole call still succeeds
                    code, msg = classify_exception(exc)
                    results.append({"query": q, "ok": False, "error_code": code, "message": msg})
            payload = {"count": len(results), "results": results}
            payload.setdefault("_meta", {})["next_commands"] = after_resolve_batch(payload)
            return payload

        return await run_mcp_tool(
            "resolve_disease_batch", call, context=McpErrorContext("resolve_disease_batch")
        )

    @mcp.tool(
        name="get_disease_batch",
        title="Get Diseases (batch)",
        annotations=READ_ONLY_OPEN_WORLD,
        output_schema=BATCH_DISEASE_SCHEMA,
        tags={"disease", "batch"},
        description=(
            "Fetch many disease records in one call (partial success per item). Each "
            "term accepts a MONDO id, label, or xref CURIE; pass fields=[...] for a "
            f"sparse projection. Max {MAX_BATCH} items. "
            "Signature: get_disease_batch(terms, response_mode=, fields=)."
        ),
    )
    async def get_disease_batch(
        terms: Annotated[list[str], Field(description=f"1..{MAX_BATCH} ids/labels/xrefs.")],
        response_mode: ResponseMode = "compact",
        fields: FieldsArg = None,
    ) -> dict[str, Any]:
        async def call() -> dict[str, Any]:
            _require_batch(terms, "terms")
            svc = get_mondo_service()
            results: list[dict[str, Any]] = []
            for term in terms:
                try:
                    rec = svc.get_disease(term, response_mode=response_mode, fields=fields)
                    results.append({"term": term, "ok": True, **rec})
                except Exception as exc:
                    code, msg = classify_exception(exc)
                    results.append({"term": term, "ok": False, "error_code": code, "message": msg})
            payload = {"count": len(results), "results": results}
            payload.setdefault("_meta", {})["next_commands"] = after_get_disease_batch(payload)
            return payload

        return await run_mcp_tool(
            "get_disease_batch", call, context=McpErrorContext("get_disease_batch")
        )
```

- [ ] **Step 2b: Add the batch next_commands chainers**

Append to `mondo_link/mcp/next_commands.py`:

```python
def after_resolve_batch(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """After resolve_disease_batch: open the first successfully resolved record."""
    for item in payload.get("results", []):
        if item.get("ok") and item.get("mondo_id"):
            return [cmd("get_disease", term=item["mondo_id"])]
    return [cmd("get_server_capabilities")]


def after_get_disease_batch(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """After get_disease_batch: map the first resolved record across ontologies."""
    for item in payload.get("results", []):
        if item.get("ok") and item.get("mondo_id"):
            return [cmd("map_cross_ontology", term=item["mondo_id"])]
    return [cmd("get_server_capabilities")]
```

- [ ] **Step 3: Register the tools**

In `mondo_link/mcp/tools/__init__.py`, import and export `register_batch_tools`
(mirror the existing `register_*` exports and `__all__`). In
`mondo_link/mcp/facade.py`, add after `register_xref_tools(mcp)`:

```python
    from mondo_link.mcp.tools import register_batch_tools
    register_batch_tools(mcp)
```

(or add `register_batch_tools` to the existing `from mondo_link.mcp.tools import (...)`).
In `mondo_link/mcp/capabilities.py:42`, add to `TOOLS`:

```python
    "resolve_disease_batch",
    "get_disease_batch",
```

and add a workflow line in `recommended_workflows`:

```python
            "many labels/ids -> resolve_disease_batch / get_disease_batch (one round trip)",
```

- [ ] **Step 4: Run the batch tests**

Run: `uv run pytest tests/unit/test_batch.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Verify the tool-name sync guard**

Run: `uv run pytest tests/unit/test_tool_names.py tests/unit/test_capabilities.py -q`
Expected: PASS (`capabilities.TOOLS` == registered set; update the pinned `capabilities_version` if asserted).

- [ ] **Step 6: Commit**

```bash
git add mondo_link/mcp/tools/batch.py mondo_link/mcp/tools/__init__.py mondo_link/mcp/facade.py mondo_link/mcp/capabilities.py mondo_link/mcp/next_commands.py tests/unit/test_batch.py
git commit -m "feat(mcp): resolve_disease_batch + get_disease_batch (partial-success batch tools)"
```

### Task 11: Batch output-schema coverage

**Files:**
- Modify: `tests/unit/test_output_schemas.py`

- [ ] **Step 1: Add schema-validation coverage for the batch tools**

```python
# add to tests/unit/test_output_schemas.py
async def test_batch_outputs_validate(tool_map: dict[str, Any]) -> None:
    ok = await _check(
        tool_map, "resolve_disease_batch", queries=["Shprintzen-Goldberg syndrome", _MISSING]
    )
    assert ok["success"] is True and ok["count"] == 2
    capped = await _check(tool_map, "resolve_disease_batch", queries=["x"] * 51)
    assert capped["success"] is False
    got = await _check(tool_map, "get_disease_batch", terms=[_SGS, _MISSING])
    assert got["results"][0]["ok"] is True
```

- [ ] **Step 2: Run it**

Run: `uv run pytest tests/unit/test_output_schemas.py -q`
Expected: PASS (success, per-item-error, and cap-error payloads all validate against the batch schemas).

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_output_schemas.py
git commit -m "test(mcp): output-schema coverage for batch tools"
```

---

## Phase F — Close-out

### Task 12: CHANGELOG, full gate, PR

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Add a CHANGELOG entry**

Under the unreleased section, list: deploy-freshness guard (`make verify-deploy`),
fuzzy/acronym resolution (`match_type: "fuzzy"`), `ambiguous_query` regression
coverage, and the two batch tools.

- [ ] **Step 2: Run the full definition-of-done gate**

Run: `make ci-local`
Expected: format-check, lint, file-size, mypy --strict, and the full test suite
all green; coverage ≥ 80%.

- [ ] **Step 3: Commit and open a PR**

```bash
git add CHANGELOG.md
git commit -m "docs: changelog for beyond-9 quality work"
git push -u origin mondo-link-beyond-9
gh pr create --fill --base main
```

- [ ] **Step 4: Redeploy and verify** (repeat Task 3 Steps 3–4) so the live server
carries F1–F4. Confirm `resolve_disease(query="ADPKD 1")` against the **real**
Mondo index now resolves (or returns ranked candidates) rather than a bare 404.

---

## Self-Review

**Spec coverage:**
- Phase 1 ship → Task 3. ✅
- F1 fuzzy → Tasks 5–6 (+ refactor Task 4). ✅
- F2 ambiguous → Task 7. ✅
- F3 batch → Tasks 8–11. ✅
- F4 freshness → Tasks 1–2 (+ used in 3/12). ✅
- LOC refactor (`resolution.py`) → Task 4; batch in own module → Task 10. ✅
- Invariants (output_schema, `Signature:`, `next_commands`, `TOOLS` sync, schema validation) → Tasks 9/10/11. ✅

**Placeholder scan:** no TBD/TODO; every code step shows complete code. One
intentional verification branch (Task 7 Step 3) tells the engineer what to do if
EXACT synonyms aren't indexed into `term_lookup` — that's a conditional with a
concrete fallback, not a placeholder.

**Type consistency:** `decide_fuzzy` returns the same tuple shape used by
`_fuzzy_or_not_found`; `classify_exception` signature matches its use in
`tools/batch.py`; batch item keys (`ok`, `error_code`, `message`, `mondo_id`)
match `_BATCH_ITEM` and the tests. `Resolver.resolve_term_id` /
`classify_resolution` (public) match the `MondoService` delegation in Task 4.

**Risk note:** the F1 `FUZZY_MIN_SCORE`/`FUZZY_DOMINANCE` constants are tuned
against bm25-derived scores; the pure tests pin the *logic*, and the fixture
integration test pins one real resolve + one real 404. If real-Mondo tuning shows
false positives post-deploy, adjust the two constants (single-file change) — the
conservative tie→ambiguous rule bounds the blast radius.
