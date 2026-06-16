"""Unit tests for the conditional Mondo release downloader (respx-mocked)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from mondo_link.config import ServerSettings
from mondo_link.exceptions import DownloadError
from mondo_link.ingest import downloader


@pytest.fixture
def config(tmp_path: Path) -> ServerSettings:
    settings = ServerSettings()
    settings.data.data_dir = tmp_path
    settings.data.db_filename = "mondo.sqlite"
    return settings


@respx.mock
def test_download_file_200_then_304(config: ServerSettings) -> None:
    url = config.data.obo_url
    route = respx.get(url).mock(
        return_value=httpx.Response(
            200,
            text="[Term]\nid: MONDO:0000001\n",
            headers={"ETag": '"v1"', "Last-Modified": "Mon, 01 Jun 2026 00:00:00 GMT"},
        )
    )
    res = downloader.download_file(config, "obo")
    assert res.not_modified is False
    assert res.path is not None and res.path.exists()
    assert res.etag == '"v1"'
    assert res.path.name == "mondo.obo"

    # Second call sends conditional headers; mock returns 304, reuse local file.
    route.mock(return_value=httpx.Response(304))
    res2 = downloader.download_file(config, "obo")
    assert res2.not_modified is True
    assert res2.path is not None and res2.path.exists()


@respx.mock
def test_download_file_http_error(config: ServerSettings) -> None:
    respx.get(config.data.obo_url).mock(return_value=httpx.Response(500))
    with pytest.raises(DownloadError):
        downloader.download_file(config, "obo")


@respx.mock
def test_download_bulk(config: ServerSettings) -> None:
    respx.get(config.data.obo_url).mock(
        return_value=httpx.Response(200, text="x\n", headers={"ETag": '"a"'})
    )
    respx.get(config.data.sssom_url).mock(
        return_value=httpx.Response(200, text="y\n", headers={"ETag": '"b"'})
    )
    bulk = downloader.download_bulk(config)
    assert bulk.not_modified is False
    assert bulk.path("obo") is not None
    assert bulk.path("sssom") is not None
    assert set(bulk.validators()) == {"obo", "sssom"}


@respx.mock
def test_download_bulk_not_modified(config: ServerSettings) -> None:
    # Seed local files + cache via an initial 200 download.
    respx.get(config.data.obo_url).mock(
        return_value=httpx.Response(200, text="x\n", headers={"ETag": '"a"'})
    )
    respx.get(config.data.sssom_url).mock(
        return_value=httpx.Response(200, text="y\n", headers={"ETag": '"b"'})
    )
    downloader.download_bulk(config)
    # Now everything 304s.
    respx.get(config.data.obo_url).mock(return_value=httpx.Response(304))
    respx.get(config.data.sssom_url).mock(return_value=httpx.Response(304))
    bulk = downloader.download_bulk(config)
    assert bulk.not_modified is True
