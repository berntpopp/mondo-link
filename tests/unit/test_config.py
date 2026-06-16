"""Tests for mondo_link.config defaults and invariants."""

from __future__ import annotations

import pytest


def test_data_defaults() -> None:
    from mondo_link.config import MondoDataConfig

    cfg = MondoDataConfig()
    assert cfg.db_filename == "mondo.sqlite"
    assert cfg.obo_url == "http://purl.obolibrary.org/obo/mondo.obo"
    assert cfg.sssom_url.endswith("mondo.sssom.tsv")
    assert cfg.sssom_url.startswith("https://")
    assert cfg.auto_bootstrap is True
    assert cfg.refresh_enabled is False
    assert cfg.user_agent.startswith("mondo-link/")
    assert cfg.db_path == cfg.data_dir / "mondo.sqlite"


def test_env_prefix_override(monkeypatch: pytest.MonkeyPatch) -> None:
    from mondo_link.config import ServerSettings

    monkeypatch.setenv("MONDO_LINK_PORT", "9001")
    monkeypatch.setenv("MONDO_LINK_DATA__DB_FILENAME", "custom.sqlite")
    settings = ServerSettings()
    assert settings.port == 9001
    assert settings.data.db_filename == "custom.sqlite"


@pytest.mark.parametrize(
    ("given", "expected"),
    [("mcp", "/mcp"), ("/mcp", "/mcp"), ("api/mcp", "/api/mcp")],
)
def test_mcp_path_leading_slash(given: str, expected: str) -> None:
    from mondo_link.config import ServerSettings

    assert ServerSettings(mcp_path=given).mcp_path == expected


def test_cors_origins_parsed_from_csv() -> None:
    from mondo_link.config import ServerSettings

    settings = ServerSettings(cors_origins="http://a.test, http://b.test")
    assert settings.cors_origins == ["http://a.test", "http://b.test"]
