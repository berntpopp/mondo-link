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


def test_extract_git_sha_ignores_unknown_sentinel() -> None:
    # buildinfo emits "unknown" when no sha is resolvable -- treat as absent.
    assert extract_git_sha({"build": {"git_sha": "unknown"}}) is None
    assert extract_git_sha({"git_sha": "unknown"}) is None


def test_extract_git_sha_reads_top_level_health_shape() -> None:
    # The REST /health endpoint carries git_sha at the top level (no "build" wrapper).
    assert extract_git_sha({"status": "ok", "git_sha": "abc1234"}) == "abc1234"


@pytest.mark.parametrize(
    ("deployed", "local", "expected"),
    [
        ("abc1234", "abc1234", True),
        ("abc1234", "abc1234def", True),
        ("abc1234def", "abc1234", True),
        ("old0000", "new1111", False),
    ],
)
def test_is_fresh_compares_short_sha_prefix(deployed: str, local: str, expected: bool) -> None:
    diag = {"build": {"git_sha": deployed}}
    assert is_fresh(diag, local) is expected


def test_is_fresh_false_when_sha_absent() -> None:
    assert is_fresh({"build": {}}, "anything") is False


def test_is_fresh_false_when_local_sha_blank() -> None:
    assert is_fresh({"build": {"git_sha": "abc1234"}}, "") is False
