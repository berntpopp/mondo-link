"""Unit tests for the mondo-link-data CLI (download mocked, real fixture build)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mondo_link.config import ServerSettings
from mondo_link.ingest import builder, cli
from mondo_link.ingest.downloader import BulkDownload, DownloadResult

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
runner = CliRunner()

_FILENAMES = {"obo": "mondo.obo", "sssom": "mondo.sssom.tsv"}


@pytest.fixture
def patched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ServerSettings:
    config = ServerSettings()
    config.data.data_dir = tmp_path
    config.data.db_filename = "mondo.sqlite"
    monkeypatch.setattr(cli, "get_config", lambda: config)

    def fake_download_bulk(
        _config: ServerSettings, *, keys: list[str] | None = None, force: bool = False
    ) -> BulkDownload:
        bulk = BulkDownload()
        for key, filename in _FILENAMES.items():
            dest = tmp_path / filename
            shutil.copy(FIXTURES / filename, dest)
            bulk.results[key] = DownloadResult(
                key=key, path=dest, last_modified="Mon, 01 Jun 2026 00:00:00 GMT"
            )
        return bulk

    monkeypatch.setattr(cli, "download_bulk", fake_download_bulk)
    # rebuild() (used by `refresh`) resolves download_bulk in the builder module.
    monkeypatch.setattr(builder, "download_bulk", fake_download_bulk)
    return config


def test_cli_build_then_status(patched: ServerSettings) -> None:
    result = runner.invoke(cli.app, ["build"])
    assert result.exit_code == 0, result.output
    assert "Built" in result.output
    assert patched.data.db_path.exists()

    status = runner.invoke(cli.app, ["status"])
    assert status.exit_code == 0
    assert "2026-06-01" in status.output
    assert "terms" in status.output.lower()


def test_cli_status_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = ServerSettings()
    config.data.data_dir = tmp_path
    config.data.db_filename = "absent.sqlite"
    monkeypatch.setattr(cli, "get_config", lambda: config)
    result = runner.invoke(cli.app, ["status"])
    assert result.exit_code == 1
    assert "No Mondo database" in result.output


def test_cli_refresh_builds(patched: ServerSettings) -> None:
    result = runner.invoke(cli.app, ["refresh"])
    assert result.exit_code == 0, result.output
    assert patched.data.db_path.exists()
