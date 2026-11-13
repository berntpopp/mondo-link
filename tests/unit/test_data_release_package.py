"""Reproducible data-release build + packaging (genefoundry-router#63 option A)."""

from __future__ import annotations

import gzip
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

import mondo_link.ingest.cli as cli_mod
from mondo_link.config import ServerSettings
from mondo_link.ingest.builder import build_database
from mondo_link.ingest.downloader import BulkDownload, DownloadResult
from mondo_link.ingest.release import package_release

ROOT = Path(__file__).resolve().parents[2]
OBO = ROOT / "tests" / "fixtures" / "mondo.obo"
SSSOM = ROOT / "tests" / "fixtures" / "mondo.sssom.tsv"
AUTHORIZATION = ROOT / "release" / "data-authorization.json"
LAST_MODIFIED = "Tue, 01 Sep 2026 17:32:21 GMT"
VALIDATORS = {
    "obo": {"etag": '"o"', "last_modified": LAST_MODIFIED},
    "sssom": {"etag": '"s"', "last_modified": None},
}


def _settings(directory: Path) -> ServerSettings:
    config = ServerSettings()
    config.data.data_dir = directory
    config.data.db_filename = "mondo.sqlite"
    return config


def _build(directory: Path) -> Path:
    config = _settings(directory)
    build_database(
        config,
        paths={"obo": OBO, "sssom": SSSOM},
        validators=VALIDATORS,
        build_utc="2026-09-01T17:32:21+00:00",
    )
    return config.data.db_path


def test_pinned_build_is_byte_reproducible_and_not_wal(tmp_path: Path) -> None:
    first = _build(tmp_path / "a")
    second = _build(tmp_path / "b")

    assert first.read_bytes() == second.read_bytes()
    connection = sqlite3.connect(f"file:{first}?mode=ro", uri=True)
    try:
        assert connection.execute("PRAGMA journal_mode").fetchone() == ("delete",)
        assert connection.execute("SELECT build_utc, build_duration_s FROM meta").fetchone() == (
            "2026-09-01T17:32:21+00:00",
            None,
        )
    finally:
        connection.close()


def test_package_writes_verifiable_content_addressed_assets(tmp_path: Path) -> None:
    database = _build(tmp_path / "build")
    output = tmp_path / "release"

    manifest = package_release(
        database,
        obo=OBO,
        sssom=SSSOM,
        authorization=AUTHORIZATION,
        output=output,
        revision="0" * 40,
    )

    expanded = hashlib.sha256(database.read_bytes()).hexdigest()
    assert manifest["release_tag"] == f"data-mondo-{expanded[:16]}"
    assert sorted(p.name for p in output.iterdir()) == [
        "SHA256SUMS",
        "data-authorization.json",
        "manifest.json",
        "mondo.sqlite.gz",
    ]
    bundle = output / "mondo.sqlite.gz"
    assert gzip.decompress(bundle.read_bytes()) == database.read_bytes()
    assert manifest["compressed_sha256"] == hashlib.sha256(bundle.read_bytes()).hexdigest()
    assert manifest["source"]["obo_sha256"] == hashlib.sha256(OBO.read_bytes()).hexdigest()
    assert manifest["source"]["sssom_sha256"] == hashlib.sha256(SSSOM.read_bytes()).hexdigest()
    assert manifest["source"]["mondo_version"]
    assert manifest["license"]["spdx"] == "CC-BY-4.0"
    assert manifest["changes"]
    assert json.loads((output / "manifest.json").read_text()) == manifest
    for line in (output / "SHA256SUMS").read_text().splitlines():
        digest, name = line.split("  ")
        assert hashlib.sha256((output / name).read_bytes()).hexdigest() == digest
    again = package_release(
        database,
        obo=OBO,
        sssom=SSSOM,
        authorization=AUTHORIZATION,
        output=tmp_path / "release2",
        revision="0" * 40,
    )
    assert again["compressed_sha256"] == manifest["compressed_sha256"]


def _fake_download(directory: Path, last_modified: str | None) -> BulkDownload:
    directory.mkdir(parents=True, exist_ok=True)
    obo = directory / "mondo.obo"
    sssom = directory / "mondo.sssom.tsv"
    obo.write_bytes(OBO.read_bytes())
    sssom.write_bytes(SSSOM.read_bytes())
    return BulkDownload(
        results={
            "obo": DownloadResult(key="obo", path=obo, etag="o", last_modified=last_modified),
            "sssom": DownloadResult(key="sssom", path=sssom, etag="s"),
        }
    )


def test_cli_reproducible_build_then_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _settings(tmp_path / "data")
    download = _fake_download(config.data.data_dir, LAST_MODIFIED)
    monkeypatch.setattr(cli_mod, "get_config", lambda: config)
    monkeypatch.setattr(cli_mod, "download_bulk", lambda cfg, *, force=False: download)
    runner = CliRunner()

    assert runner.invoke(cli_mod.app, ["build", "--reproducible"]).exit_code == 0
    result = runner.invoke(
        cli_mod.app,
        [
            "package",
            "--output",
            str(tmp_path / "out"),
            "--revision",
            "a" * 40,
            "--authorization",
            str(AUTHORIZATION),
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert json.loads(result.stdout)["transformation"]["build_utc"] == "2026-09-01T17:32:21+00:00"


def test_cli_reproducible_build_requires_last_modified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _settings(tmp_path)
    download = _fake_download(tmp_path, None)
    monkeypatch.setattr(cli_mod, "get_config", lambda: config)
    monkeypatch.setattr(cli_mod, "download_bulk", lambda cfg, *, force=False: download)

    result = CliRunner().invoke(cli_mod.app, ["build", "--reproducible"])

    assert result.exit_code == 1
    assert not config.data.db_path.exists()


def test_closure_pairs_are_emitted_in_a_hash_seed_independent_order() -> None:
    """Set iteration order varies with PYTHONHASHSEED; the build must not depend on it."""
    import subprocess
    import sys

    code = (
        "from pathlib import Path;"
        "from mondo_link.ingest.parser import parse_mondo_obo, mondo_closure_pairs;"
        f"t=parse_mondo_obo(Path({str(OBO)!r}).read_text());"
        "print(list(mondo_closure_pairs(t)))"
    )
    outputs = {
        subprocess.run(  # noqa: S603 - our own interpreter and literal code
            [sys.executable, "-c", code],
            env={"PYTHONHASHSEED": seed},
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        for seed in ("1", "2", "3", "4")
    }
    assert len(outputs) == 1
