"""Unit tests for the resolution module: pure fuzzy decision + cascade integration.

The pure ``decide_fuzzy`` tests use synthetic hits and pin the *logic* against the
threshold constants (so the constants can be tuned without rewriting the tests).
The cascade tests use the conftest ``service`` fixture (built from the real
``tests/fixtures/mondo.obo``).
"""

from __future__ import annotations

from typing import Any

import pytest

from mondo_link.exceptions import AmbiguousQueryError, NotFoundError
from mondo_link.services.resolution import (
    FUZZY_DOMINANCE,
    FUZZY_MAX_CANDIDATES,
    FUZZY_MIN_SCORE,
    decide_fuzzy,
)


def _hit(mid: str, name: str, score: float) -> dict[str, object]:
    return {"mondo_id": mid, "name": name, "score": score}


# -- pure decision logic ------------------------------------------------------


def test_empty_hits_resolve_to_none() -> None:
    assert decide_fuzzy([]) == ("none", None)


def test_below_floor_resolves_to_none() -> None:
    assert decide_fuzzy([_hit("MONDO:1", "x", FUZZY_MIN_SCORE - 0.01)]) == ("none", None)


def test_single_strong_hit_resolves() -> None:
    kind, payload = decide_fuzzy([_hit("MONDO:0008263", "pkd 1", FUZZY_MIN_SCORE + 1.0)])
    assert kind == "resolve"
    assert isinstance(payload, dict)
    assert payload["mondo_id"] == "MONDO:0008263"


def test_dominant_top_resolves() -> None:
    hits = [_hit("MONDO:1", "a", 3.0), _hit("MONDO:2", "b", 3.0 / (FUZZY_DOMINANCE + 0.5))]
    kind, payload = decide_fuzzy(hits)
    assert kind == "resolve"
    assert isinstance(payload, dict)
    assert payload["mondo_id"] == "MONDO:1"


def test_close_runner_up_is_ambiguous() -> None:
    hits = [_hit("MONDO:1", "a", 3.0), _hit("MONDO:2", "b", 2.9)]
    kind, candidates = decide_fuzzy(hits)
    assert kind == "ambiguous"
    assert isinstance(candidates, list)
    assert {c["mondo_id"] for c in candidates} == {"MONDO:1", "MONDO:2"}


def test_ambiguous_candidates_capped() -> None:
    hits = [_hit(f"MONDO:{i}", "n", 3.0) for i in range(FUZZY_MAX_CANDIDATES + 4)]
    kind, candidates = decide_fuzzy(hits)
    assert kind == "ambiguous"
    assert isinstance(candidates, list)
    assert len(candidates) == FUZZY_MAX_CANDIDATES


def test_zero_second_score_resolves_top() -> None:
    # A non-zero top over a zero-scored runner-up is an unambiguous winner.
    kind, payload = decide_fuzzy(
        [_hit("MONDO:1", "a", FUZZY_MIN_SCORE + 1.0), _hit("MONDO:2", "b", 0.0)]
    )
    assert kind == "resolve"
    assert isinstance(payload, dict)
    assert payload["mondo_id"] == "MONDO:1"


# -- cascade integration (conftest `service` over tests/fixtures/mondo.obo) ----

_SGS = "MONDO:0008426"  # Shprintzen-Goldberg syndrome


def test_fuzzy_resolves_near_miss_label(service: Any) -> None:
    # "Shprintzen Goldberg" (space, no hyphen) is not an exact label/synonym, but
    # FTS matches only MONDO:0008426 -> resolves with match_type "fuzzy".
    out = service.resolve_disease("Shprintzen Goldberg")
    assert out["mondo_id"] == _SGS
    assert out["match_type"] == "fuzzy"


def test_gibberish_still_not_found(service: Any) -> None:
    with pytest.raises(NotFoundError):
        service.resolve_disease("zzzzznotadiseasezzzzz")


def test_get_disease_stays_strict_on_near_miss(service: Any) -> None:
    # get_disease is the STRICT (non-fuzzy) entry: a near-miss returns not_found
    # with the closest hit embedded as a suggestion, rather than silently guessing.
    with pytest.raises(NotFoundError) as exc:
        service.get_disease("Shprintzen Goldberg")
    assert any(s["mondo_id"] == _SGS for s in exc.value.suggestions)


def test_exact_ambiguous_label_beats_fuzzy(service: Any) -> None:
    # "shared ambiguous disorder" is an EXACT synonym of two distinct fixture terms:
    # the exact-ambiguous path wins BEFORE fuzzy runs (F1 must defer to it).
    with pytest.raises(AmbiguousQueryError) as exc:
        service.resolve_disease("shared ambiguous disorder")
    candidates = exc.value.candidates
    assert len({c["mondo_id"] for c in candidates}) >= 2
    assert all(c.get("name") for c in candidates)
