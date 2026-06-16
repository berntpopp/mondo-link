"""Unit tests for Mondo / xref identifier helpers."""

from __future__ import annotations

import pytest

from mondo_link.identifiers import (
    infer_xref_source,
    looks_like_mondo_id,
    normalize_mondo_id,
    normalize_xref,
    xref_prefix,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("MONDO:0008426", "MONDO:0008426"),
        ("mondo:0008426", "MONDO:0008426"),
        ("0008426", "MONDO:0008426"),
        ("  0008426 ", "MONDO:0008426"),
        ("MONDO:123", None),  # not 7 digits
        ("WT1", None),
        ("", None),
    ],
)
def test_normalize_mondo_id(value: str, expected: str | None) -> None:
    assert normalize_mondo_id(value) == expected


def test_looks_like_mondo_id() -> None:
    assert looks_like_mondo_id("MONDO:0008426") is True
    assert looks_like_mondo_id("0008426") is True
    assert looks_like_mondo_id("MONDO:123") is False
    assert looks_like_mondo_id("WT1") is False


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Orphanet:2462", "ORPHA:2462"),
        ("ORPHA:2462", "ORPHA:2462"),
        ("omim:182212", "OMIM:182212"),
        ("MIM:182212", "OMIM:182212"),
        ("DOID:0050776", "DOID:0050776"),
        ("SNOMEDCT:1234", "SCTID:1234"),
        ("nope", None),
        ("OMIM:", None),
        ("", None),
    ],
)
def test_normalize_xref(value: str, expected: str | None) -> None:
    assert normalize_xref(value) == expected


def test_xref_prefix() -> None:
    assert xref_prefix("Orphanet:2462") == "ORPHA"
    assert xref_prefix("omim:182212") == "OMIM"
    assert xref_prefix("WT1") is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("OMIM:182212", "OMIM"),
        ("Orphanet:2462", "ORPHA"),
        ("DOID:0050776", "DOID"),
        ("MONDO:0008426", None),  # a Mondo id is not an external xref source
        ("0008426", None),
    ],
)
def test_infer_xref_source(value: str, expected: str | None) -> None:
    assert infer_xref_source(value) == expected
