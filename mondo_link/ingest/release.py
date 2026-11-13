"""Package a built Mondo SQLite into an immutable, content-addressed data release.

The assets (gzip bundle, manifest, owner authorization record, ``SHA256SUMS``) are
what the ``data-release`` workflow publishes and the init sidecar later restores.
The tag is derived from the SQLite bytes, so an unchanged upstream export yields
the same tag and is never published twice.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import shutil
import sqlite3
from pathlib import Path
from typing import Any

BUNDLE_NAME = "mondo.sqlite.gz"
_AUTHORIZATION_NAME = "data-authorization.json"


def _sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            hasher.update(chunk)
    return hasher.hexdigest()


def _meta(database: Path) -> dict[str, Any]:
    connection = sqlite3.connect(f"file:{database}?mode=ro&immutable=1", uri=True)
    try:
        connection.row_factory = sqlite3.Row
        row = connection.execute("SELECT * FROM meta WHERE id = 1").fetchone()
    finally:
        connection.close()
    if row is None:
        raise ValueError("Mondo database has no provenance (meta) row")
    return dict(row)


def package_release(
    database: Path,
    *,
    obo: Path,
    sssom: Path,
    authorization: Path,
    output: Path,
    revision: str,
) -> dict[str, Any]:
    """Write the release assets into the new directory *output*; return the manifest."""
    record = json.loads(authorization.read_text(encoding="utf-8"))
    if record.get("redistribution_allowed") is not True:
        raise ValueError("data authorization does not allow redistribution")
    meta = _meta(database)
    if meta["build_duration_s"] is not None:
        raise ValueError("database was not built reproducibly (pass a pinned build time)")
    output.mkdir(parents=True)
    bundle = output / BUNDLE_NAME
    with (
        database.open("rb") as source,
        bundle.open("xb") as raw,
        gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=9, mtime=0) as packed,
    ):
        shutil.copyfileobj(source, packed, 1 << 20)
    shutil.copyfile(authorization, output / _AUTHORIZATION_NAME)
    expanded = _sha256(database)
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "release_tag": f"data-mondo-{expanded[:16]}",
        "bundle": BUNDLE_NAME,
        "compressed_sha256": _sha256(bundle),
        "expanded_sha256": expanded,
        "expanded_bytes": database.stat().st_size,
        "data_schema_version": meta["schema_version"],
        "source": {
            "mondo_version": meta["mondo_version"],
            "urls": json.loads(meta["source_purls"]),
            "validators": json.loads(meta["source_validators"]),
            "obo_sha256": _sha256(obo),
            "sssom_sha256": _sha256(sssom),
        },
        "record_counts": {
            "terms": meta["term_count"],
            "obsolete": meta["obsolete_count"],
            "closure": meta["closure_count"],
            "xrefs": meta["xref_count"],
            "mappings": meta["mapping_count"],
        },
        "transformation": {
            "repository": "https://github.com/berntpopp/mondo-link",
            "revision": revision,
            "build_utc": meta["build_utc"],
        },
        "license": {
            "spdx": record["license"]["spdx"],
            "url": record["license"]["url"],
            "terms_url": record["license"]["terms_url"],
        },
        "attribution": record["attribution"],
        "changes": record["changes"],
        "authorization": {"reviewer": record["reviewer"], "reviewed_at": record["reviewed_at"]},
        "disclaimer": record["disclaimer"],
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    names = (BUNDLE_NAME, _AUTHORIZATION_NAME, "manifest.json")
    (output / "SHA256SUMS").write_text(
        "".join(f"{_sha256(output / name)}  {name}\n" for name in names), encoding="utf-8"
    )
    return manifest
