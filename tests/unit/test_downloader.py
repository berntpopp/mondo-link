"""Unit tests for the conditional Mondo release downloader (respx-mocked)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import respx

from mondo_link.config import ServerSettings
from mondo_link.exceptions import DownloadError
from mondo_link.ingest import downloader


class _ChunkedBody(httpx.SyncByteStream):
    def __iter__(self) -> Iterator[bytes]:
        yield b"1234"
        yield b"56789"


@pytest.fixture
def config(tmp_path: Path) -> ServerSettings:
    settings = ServerSettings()
    settings.data.data_dir = tmp_path
    settings.data.db_filename = "mondo.sqlite"
    return settings


@respx.mock
def test_mondo_purl_follows_only_reviewed_chain(config: ServerSettings) -> None:
    purl = config.data.obo_url
    latest = "https://github.com/monarch-initiative/mondo/releases/latest/download/mondo.obo"
    versioned = "https://github.com/monarch-initiative/mondo/releases/download/v1/mondo.obo"
    asset = "https://release-assets.githubusercontent.com/mondo?id=1"
    respx.get(purl).mock(return_value=httpx.Response(302, headers={"Location": latest}))
    respx.get(latest).mock(return_value=httpx.Response(302, headers={"Location": versioned}))
    respx.get(versioned).mock(return_value=httpx.Response(302, headers={"Location": asset}))
    respx.get(asset).mock(return_value=httpx.Response(200, content=b"format-version: 1.2\n"))
    result = downloader.download_file(config, "obo", force=True)
    assert result.path is not None


@respx.mock
def test_mondo_purl_rejects_http_downgrade(config: ServerSettings) -> None:
    blocked_url = "http://github.com/mondo.obo"
    blocked = respx.get(blocked_url).mock(return_value=httpx.Response(200))
    respx.get(config.data.obo_url).mock(
        return_value=httpx.Response(302, headers={"Location": blocked_url})
    )
    with pytest.raises(DownloadError, match="HTTPS"):
        downloader.download_file(config, "obo", force=True)
    assert blocked.called is False


@respx.mock
def test_optional_sssom_overflow_preserves_previous_file(config: ServerSettings) -> None:
    config.data.max_download_bytes = 8
    destination = config.data.data_dir / "mondo.sssom.tsv"
    destination.write_bytes(b"old")
    respx.get(config.data.obo_url).mock(return_value=httpx.Response(200, content=b"obo"))
    respx.get(config.data.sssom_url).mock(return_value=httpx.Response(200, stream=_ChunkedBody()))
    result = downloader.download_bulk(config, force=True)
    assert result.results["sssom"].path == destination
    assert destination.read_bytes() == b"old"
    assert list(config.data.data_dir.glob("*.download.tmp")) == []


@respx.mock
def test_sssom_cannot_redirect_to_obo_hosts(config: ServerSettings) -> None:
    target_url = "https://github.com/monarch-initiative/mondo/mondo.sssom.tsv"
    target = respx.get(target_url).mock(return_value=httpx.Response(200, content=b"bad"))
    respx.get(config.data.sssom_url).mock(
        return_value=httpx.Response(302, headers={"Location": target_url})
    )
    with pytest.raises(DownloadError, match=r"host github\.com is not allowed"):
        downloader.download_file(config, "sssom", force=True)
    assert target.called is False


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
def test_download_bulk_optional_sssom_404_degrades(config: ServerSettings) -> None:
    # The OBO downloads fine; the supplementary SSSOM 404s. The bulk must NOT
    # raise — it degrades to an OBO-only build (sssom path is None).
    respx.get(config.data.obo_url).mock(
        return_value=httpx.Response(
            200, text="[Term]\nid: MONDO:0000001\n", headers={"ETag": '"a"'}
        )
    )
    respx.get(config.data.sssom_url).mock(return_value=httpx.Response(404))
    bulk = downloader.download_bulk(config)
    assert bulk.path("obo") is not None
    assert bulk.path("sssom") is None
    assert "sssom" in bulk.results


@respx.mock
def test_download_bulk_required_obo_404_raises(config: ServerSettings) -> None:
    # The OBO is required: its failure must still propagate.
    respx.get(config.data.obo_url).mock(return_value=httpx.Response(404))
    with pytest.raises(DownloadError):
        downloader.download_bulk(config)


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
