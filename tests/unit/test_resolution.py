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
    Resolver,
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


def test_unmatched_xref_curie_not_found(service: Any) -> None:
    # A well-formed external CURIE that no Mondo term references -> not_found
    # (the xref branch never falls through to a fuzzy label search).
    with pytest.raises(NotFoundError):
        service.resolve_disease("OMIM:000000")


def test_relaxed_suggestions_avoid_dead_end(service: Any) -> None:
    # A multi-token query whose strict AND-search matches nothing ("Marfan zzzqq")
    # must still surface ranked candidates via single-token relaxation, so a query
    # like "ADPKD 1" never dead-ends on a bare 404 with empty candidates.
    with pytest.raises(NotFoundError) as exc:
        service.resolve_disease("Marfan zzzqq")
    assert exc.value.suggestions, "relaxation should surface Marfan-family candidates"


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


# -- fuzzy fallback branches (Resolver over a scripted fake repo) --------------


class _FakeRepo:
    """Repo stub: no exact label match, scripted FTS hits -> drives the fuzzy paths."""

    def __init__(self, hits: list[dict[str, Any]], non_human: set[str] | None = None) -> None:
        self._hits = hits
        self._non_human = set(non_human or ())

    def resolve_label(self, label: str) -> list[dict[str, Any]]:
        return []

    def search(
        self, query: str, *, limit: int, include_obsolete: bool, offset: int = 0
    ) -> tuple[list[dict[str, Any]], int]:
        return self._hits[:limit], len(self._hits)

    def non_human_animal_ids(self, mondo_ids: list[str]) -> set[str]:
        return {m for m in mondo_ids if m in self._non_human}


def test_fuzzy_near_tie_raises_ambiguous_with_fuzzy_candidates() -> None:
    resolver = Resolver(_FakeRepo([_hit("MONDO:1", "a", 3.0), _hit("MONDO:2", "b", 2.9)]))
    with pytest.raises(AmbiguousQueryError) as exc:
        resolver.classify_resolution("near tie")
    assert {c["mondo_id"] for c in exc.value.candidates} == {"MONDO:1", "MONDO:2"}
    assert all(c["label_type"] == "fuzzy" for c in exc.value.candidates)


def test_fuzzy_below_floor_not_found_embeds_weak_hits() -> None:
    weak = [_hit("MONDO:9", "weak", FUZZY_MIN_SCORE - 0.1)]
    resolver = Resolver(_FakeRepo(weak))
    with pytest.raises(NotFoundError) as exc:
        resolver.classify_resolution("weak query")
    assert exc.value.suggestions[0]["mondo_id"] == "MONDO:9"


def test_fuzzy_no_hits_not_found_without_suggestions() -> None:
    resolver = Resolver(_FakeRepo([]))
    with pytest.raises(NotFoundError) as exc:
        resolver.classify_resolution("nothing here")
    assert exc.value.suggestions == []


def test_fuzzy_demotes_non_human_animal_terms_so_human_leads() -> None:
    # Veterinary terms ("..., pig"/"..., cattle") can outrank the human disease in raw
    # bm25, but the human-disease prior must sink them below human terms so the
    # canonical human term leads the candidates (resolve_disease("Marfan syndrom")).
    hits = [
        _hit("MONDO:1011155", "Marfan syndrome, pig", 100.0),
        _hit("MONDO:1011156", "Marfan syndrome, cattle", 99.0),
        _hit("MONDO:0007947", "Marfan syndrome", 3.0),
        _hit("MONDO:0017309", "neonatal Marfan syndrome", 2.9),
    ]
    resolver = Resolver(_FakeRepo(hits, non_human={"MONDO:1011155", "MONDO:1011156"}))
    with pytest.raises(AmbiguousQueryError) as exc:
        resolver.classify_resolution("Marfan syndrom")
    ids = [c["mondo_id"] for c in exc.value.candidates]
    # the two human terms lead; the livestock terms are demoted to the tail
    assert ids[:2] == ["MONDO:0007947", "MONDO:0017309"]
    assert set(ids[2:]) == {"MONDO:1011155", "MONDO:1011156"}


def test_fuzzy_demotion_lets_human_term_resolve_over_higher_scored_livestock() -> None:
    # With the livestock term demoted out of the runner-up slot, a dominant human term
    # resolves cleanly instead of being dragged into a livestock-led near-tie.
    hits = [
        _hit("MONDO:1011155", "Marfan syndrome, pig", FUZZY_MIN_SCORE + 50.0),
        _hit("MONDO:0007947", "Marfan syndrome", FUZZY_MIN_SCORE + 5.0),
        _hit("MONDO:0017309", "neonatal", (FUZZY_MIN_SCORE + 5.0) / (FUZZY_DOMINANCE + 0.5)),
    ]
    resolver = Resolver(_FakeRepo(hits, non_human={"MONDO:1011155"}))
    match_type, mondo_id = resolver.classify_resolution("Marfan syndrom")
    assert match_type == "fuzzy"
    assert mondo_id == "MONDO:0007947"


def test_strict_resolve_term_id_never_fuzzy() -> None:
    # resolve_term_id (get_disease's entry) must NOT fuzzy-resolve a strong hit.
    resolver = Resolver(_FakeRepo([_hit("MONDO:1", "a", FUZZY_MIN_SCORE + 5.0)]))
    with pytest.raises(NotFoundError):
        resolver.resolve_term_id("some label")
