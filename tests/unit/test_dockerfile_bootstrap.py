"""Supply-chain guard: the builder must bootstrap uv from a digest-pinned image
layer, never a floating `pip install --upgrade` of pip/uv. A floating upgrade
resolves to whatever the index serves at build time, defeating byte-reproducible
rebuilds and widening the supply-chain surface (F-19). Research use only; not
clinical decision support."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # tests/unit/<file> -> repo root

_UV_PIN = (
    "ghcr.io/astral-sh/uv:0.8.7@sha256:"
    "1e26f9a868360eeb32500a35e05787ffff3402f01a8dc8168ef6aee44aef0aab"
)


def test_dockerfile_pins_uv_and_has_no_floating_pip_upgrade() -> None:
    text = (ROOT / "docker" / "Dockerfile").read_text(encoding="utf-8")
    assert "pip install --upgrade" not in text, "floating pip/uv upgrade must be removed"
    assert _UV_PIN in text, "uv must be bootstrapped via the digest-pinned COPY --from"
