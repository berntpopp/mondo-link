"""Command-line interface for building and refreshing the Mondo index.

Exposed as the ``mondo-link-data`` console script and intended as the cron entry
point. Commands: ``build`` (force a download + rebuild), ``refresh`` (conditional
rebuild -- the cron job), and ``status`` (print provenance of the existing DB).
"""

from __future__ import annotations

import json
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import TYPE_CHECKING

import typer

from mondo_link.config import settings
from mondo_link.exceptions import DownloadError
from mondo_link.ingest.builder import BuildMeta, build_database, read_meta, rebuild
from mondo_link.ingest.downloader import REPORT_FILENAMES, download_bulk

if TYPE_CHECKING:
    from mondo_link.config import ServerSettings

app = typer.Typer(
    add_completion=False,
    help="Build and refresh the local Mondo SQLite index from the OBO + SSSOM releases.",
)


def get_config() -> ServerSettings:
    """Return the active server settings (data store + URLs) for the ingest CLI."""
    return settings


def _print_summary(meta: BuildMeta, *, header: str) -> None:
    """Print a compact provenance summary for a build."""
    print(header)
    print(f"  schema_version  : {meta.schema_version}")
    print(f"  mondo_version   : {meta.mondo_version}")
    print(f"  terms           : {meta.term_count}")
    print(f"  obsolete        : {meta.obsolete_count}")
    print(f"  closure rows    : {meta.closure_count}")
    print(f"  xref rows       : {meta.xref_count}")
    print(f"  mappings        : {meta.mapping_count}")
    print(f"  built_utc       : {meta.build_utc}")
    if meta.build_duration_s is not None:
        print(f"  build_seconds   : {meta.build_duration_s}")


@app.command()
def build(
    reproducible: bool = typer.Option(
        False,
        "--reproducible",
        help="Pin build_utc to the OBO's Last-Modified so the file is byte-reproducible.",
    ),
) -> None:
    """Force a download and full rebuild of the database."""
    config = get_config()
    try:
        download = download_bulk(config, force=True)
    except DownloadError as exc:
        print(f"ERROR: download failed: {exc}")
        raise typer.Exit(code=1) from exc
    paths = {key: download.path(key) for key in download.results}
    build_utc = None
    if reproducible:
        obo = download.results.get("obo")
        if obo is None or not obo.last_modified or paths.get("sssom") is None:
            print("ERROR: --reproducible needs the OBO Last-Modified header and the SSSOM file.")
            raise typer.Exit(code=1)
        build_utc = parsedate_to_datetime(obo.last_modified).isoformat()
    meta = build_database(
        config, paths=paths, validators=download.validators(), build_utc=build_utc
    )
    _print_summary(meta, header="Built Mondo database:")


@app.command()
def package(
    output: Path = typer.Option(..., help="New directory for the release assets."),
    revision: str = typer.Option(..., help="Full commit SHA of the transformation code."),
    authorization: Path = typer.Option(
        Path("release/data-authorization.json"), help="Owner redistribution authorization."
    ),
) -> None:
    """Package the reproducibly built database as data-release assets; print the manifest."""
    from mondo_link.ingest.release import package_release

    config = get_config()
    data_dir = config.data.data_dir
    manifest = package_release(
        config.data.db_path,
        obo=data_dir / REPORT_FILENAMES["obo"],
        sssom=data_dir / REPORT_FILENAMES["sssom"],
        authorization=authorization,
        output=output,
        revision=revision,
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))


@app.command()
def refresh() -> None:
    """Conditionally refresh the database; rebuild only if the releases changed."""
    config = get_config()
    try:
        result = rebuild(config, force=False)
    except DownloadError as exc:
        print(f"ERROR: download failed: {exc}")
        raise typer.Exit(code=1) from exc
    if result.not_modified or result.meta is None:
        version = result.meta.mondo_version if result.meta else "unknown"
        print(f"Mondo database is up to date (releases not modified; version {version}).")
        return
    _print_summary(result.meta, header="Mondo database refreshed:")


@app.command()
def status() -> None:
    """Print provenance of the existing database, or a hint to build it."""
    config = get_config()
    meta = read_meta(config.data.db_path)
    if meta is None:
        print(f"No Mondo database at {config.data.db_path}.")
        print("Run `mondo-link-data build` to download and build it.")
        raise typer.Exit(code=1)
    _print_summary(meta, header=f"Mondo database at {config.data.db_path}:")


def main() -> None:
    """Console-script entry point for ``mondo-link-data``."""
    app()


if __name__ == "__main__":
    main()
