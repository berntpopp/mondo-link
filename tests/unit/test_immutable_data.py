"""Init-sidecar restore of the pinned data release + Runtime Data Identity v1."""

from __future__ import annotations

import gzip
import hashlib
from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from pydantic import ValidationError

from mondo_link.config import ImmutableDataRequirement, MondoDataConfig, ServerSettings
from mondo_link.exceptions import DataUnavailableError
from mondo_link.immutable_data import materialize_immutable_data, verify_runtime_identity
from mondo_link.ingest.builder import build_database

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
TAG = "data-mondo-0123456789abcdef"
URL = f"https://github.com/berntpopp/mondo-link/releases/download/{TAG}/mondo.sqlite.gz"
ASSET_HOST = "https://release-assets.githubusercontent.com/asset/1"


def _release(tmp_path: Path) -> tuple[ImmutableDataRequirement, bytes]:
    settings = ServerSettings()
    settings.data.data_dir = tmp_path / "build"
    settings.data.db_filename = "mondo.sqlite"
    build_database(
        settings,
        paths={"obo": FIXTURES / "mondo.obo", "sssom": FIXTURES / "mondo.sssom.tsv"},
        validators={"obo": {"etag": None, "last_modified": None}},
        build_utc="x",
    )
    cfg = settings.data
    raw = cfg.db_path.read_bytes()
    bundle = gzip.compress(raw, mtime=0)
    requirement = ImmutableDataRequirement(
        reference_root=tmp_path / "reference",
        release_tag=TAG,
        bundle_url=URL,
        compressed_sha256=hashlib.sha256(bundle).hexdigest(),
        expanded_sha256=hashlib.sha256(raw).hexdigest(),
    )
    return requirement, bundle


def _serve(bundle: bytes) -> None:
    respx.get(URL).mock(return_value=httpx.Response(302, headers={"location": ASSET_HOST}))
    respx.get(ASSET_HOST).mock(return_value=httpx.Response(200, content=bundle))


@respx.mock
def test_materialize_selects_verified_snapshot_and_runtime_proves_it(tmp_path: Path) -> None:
    requirement, bundle = _release(tmp_path)
    _serve(bundle)

    selected = materialize_immutable_data(requirement)

    root = tmp_path / "reference"
    assert selected == root / requirement.compressed_sha256 / "mondo.sqlite"
    assert (root / "current").resolve() == selected.parent
    assert selected.stat().st_mode & 0o777 == 0o444
    assert verify_runtime_identity(root / "current" / "mondo.sqlite", requirement) == {
        "release_tag": TAG,
        "digest": f"sha256:{requirement.compressed_sha256}",
    }
    # A restart re-verifies the existing snapshot without downloading again.
    respx.reset()
    assert materialize_immutable_data(requirement) == selected
    assert not respx.calls


@respx.mock
def test_digest_mismatch_never_replaces_current(tmp_path: Path) -> None:
    requirement, bundle = _release(tmp_path)
    _serve(bundle)
    good = materialize_immutable_data(requirement)
    wrong = requirement.model_copy(update={"compressed_sha256": "f" * 64})

    with pytest.raises(DataUnavailableError, match="digest"):
        materialize_immutable_data(wrong)

    assert (tmp_path / "reference" / "current").resolve() == good.parent
    assert not list((tmp_path / "reference").glob(".*staging*"))


@respx.mock
def test_redirect_off_github_is_refused(tmp_path: Path) -> None:
    requirement, _bundle = _release(tmp_path)
    respx.get(URL).mock(
        return_value=httpx.Response(302, headers={"location": "https://evil.example/x"})
    )

    with pytest.raises(DataUnavailableError, match="allowed hosts"):
        materialize_immutable_data(requirement)


def test_runtime_identity_rejects_a_local_build(tmp_path: Path) -> None:
    requirement, _bundle = _release(tmp_path)

    with pytest.raises(DataUnavailableError):
        verify_runtime_identity(tmp_path / "build" / "mondo.sqlite", requirement)


@respx.mock
def test_runtime_identity_rejects_tampered_bytes(tmp_path: Path) -> None:
    requirement, bundle = _release(tmp_path)
    _serve(bundle)
    selected = materialize_immutable_data(requirement)
    selected.chmod(0o644)
    with selected.open("ab") as handle:
        handle.write(b"x")

    with pytest.raises(DataUnavailableError, match="expanded digest"):
        verify_runtime_identity(selected, requirement)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("release_tag", "latest"),
        ("compressed_sha256", "bad"),
        ("bundle_url", "https://example.org/download/data-mondo-0123456789abcdef/mondo.sqlite.gz"),
        ("bundle_url", URL.replace(TAG, "data-mondo-ffffffffffffffff")),
    ],
)
def test_requirement_rejects_mutable_or_mismatched_pins(field: str, value: str) -> None:
    values = {
        "release_tag": TAG,
        "bundle_url": URL,
        "compressed_sha256": "a" * 64,
        "expanded_sha256": "b" * 64,
        field: value,
    }
    with pytest.raises(ValidationError):
        ImmutableDataRequirement(**values)


@respx.mock
def test_health_reports_runtime_v1_identity_only_for_the_restored_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import mondo_link.app as app_mod
    import mondo_link.config as config_mod

    requirement, bundle = _release(tmp_path)
    _serve(bundle)
    materialize_immutable_data(requirement)
    monkeypatch.setattr(app_mod.settings, "immutable_data", requirement)
    served = MondoDataConfig(data_dir=tmp_path / "reference", db_filename="current/mondo.sqlite")
    monkeypatch.setattr(config_mod.settings, "data", served)

    body = TestClient(app_mod.create_app()).get("/health").json()
    identity = {"release_tag": TAG, "digest": f"sha256:{requirement.compressed_sha256}"}
    assert body["release_identity"] == {
        "schema_version": 1,
        "data_identity": {"expected": identity, "actual": identity},
    }

    local = MondoDataConfig(data_dir=tmp_path / "build", db_filename="mondo.sqlite")
    monkeypatch.setattr(config_mod.settings, "data", local)
    assert "release_identity" not in TestClient(app_mod.create_app()).get("/health").json()
