"""Guard: pyproject -> installed metadata -> __version__ -> serverInfo are one value."""

from __future__ import annotations

import tomllib
from importlib.metadata import version
from pathlib import Path

import yaml  # type: ignore[import-untyped]

from mondo_link import __version__
from mondo_link.mcp.facade import create_mondo_mcp

DIST = "mondo-link"


def _pyproject_version() -> str:
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    return tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]


def test_generated_citation_tracks_current_release_metadata() -> None:
    citation = yaml.safe_load(
        (Path(__file__).resolve().parents[2] / "CITATION.cff").read_text(encoding="utf-8")
    )

    assert citation["version"] == _pyproject_version()
    assert citation["version"] == "0.4.5"
    assert citation["date-released"] == "2026-09-02"


def test_pyproject_is_the_single_source() -> None:
    assert version(DIST) == _pyproject_version()


def test_dunder_version_is_metadata_derived() -> None:
    assert __version__ == version(DIST)


def test_mcp_server_info_version_matches_package() -> None:
    assert create_mondo_mcp().version == __version__
