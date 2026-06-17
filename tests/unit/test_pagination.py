"""Unit tests for the truncation + forward-pagination helper."""

from __future__ import annotations

from mondo_link.services.pagination import page_fields


def test_page_fields_truncated_emits_next_offset() -> None:
    assert page_fields(total=126, returned=100, limit=100) == {
        "total": 126,
        "returned": 100,
        "limit": 100,
        "offset": 0,
        "truncated": True,
        "next_offset": 100,
    }


def test_page_fields_complete_has_no_next_offset() -> None:
    out = page_fields(total=5, returned=5, limit=200)
    assert out["truncated"] is False
    assert out["offset"] == 0
    assert "next_offset" not in out


def test_page_fields_zero() -> None:
    out = page_fields(total=0, returned=0, limit=50)
    assert out["truncated"] is False
    assert out["total"] == 0
    assert "next_offset" not in out


def test_page_fields_mid_page_advances_offset() -> None:
    # second page of a 10-row result, page size 4, starting at offset 4
    out = page_fields(total=10, returned=4, limit=4, offset=4)
    assert out["truncated"] is True
    assert out["offset"] == 4
    assert out["next_offset"] == 8


def test_page_fields_last_page_not_truncated() -> None:
    out = page_fields(total=10, returned=2, limit=4, offset=8)
    assert out["truncated"] is False
    assert out["offset"] == 8
    assert "next_offset" not in out
