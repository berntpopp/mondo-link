"""Shared test fixtures: a fixture-backed Mondo index, repository, service, facade."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from mondo_link.config import ServerSettings
from mondo_link.data.repository import MondoRepository
from mondo_link.ingest import builder
from mondo_link.services.mondo_service import MondoService

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def _structured(result: Any) -> dict[str, Any]:
    """Read structured_content defensively (with TextContent JSON fallback)."""
    sc = result.structured_content
    if isinstance(sc, dict):
        return sc
    return json.loads(result.content[0].text)


@pytest.fixture
def structured() -> Any:
    """Expose the structured-content reader to tests."""
    return _structured


@pytest.fixture(scope="session")
def built_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build a small Mondo index from the real fixture OBO + SSSOM once per session."""
    data_dir = tmp_path_factory.mktemp("mondo_data")
    config = ServerSettings()
    config.data.data_dir = data_dir
    config.data.db_filename = "mondo.sqlite"
    paths: dict[str, Path | None] = {
        "obo": FIXTURES_DIR / "mondo.obo",
        "sssom": FIXTURES_DIR / "mondo.sssom.tsv",
    }
    validators = {
        "obo": {"etag": '"v1"', "last_modified": "Mon, 01 Jun 2026 00:00:00 GMT"},
        "sssom": {"etag": '"v2"', "last_modified": None},
    }
    builder.build_database(config, paths=paths, validators=validators)
    return config.data.db_path


@pytest.fixture
def repo(built_db: Path) -> Any:
    """An open read-only repository over the fixture database."""
    repository = MondoRepository(built_db)
    yield repository
    repository.close()


@pytest.fixture
def service(repo: MondoRepository) -> MondoService:
    """A service backed by the fixture repository."""
    return MondoService(repo)


@pytest.fixture
def facade(service: MondoService) -> Any:
    """A FastMCP facade with the fixture service injected; cleans up after."""
    from mondo_link.mcp.facade import create_mondo_mcp
    from mondo_link.mcp.service_adapters import set_mondo_service

    set_mondo_service(service)
    mcp = create_mondo_mcp()
    yield mcp
    set_mondo_service(None)
