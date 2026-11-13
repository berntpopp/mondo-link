"""Configuration management for mondo-link.

Settings load from environment variables with the ``MONDO_LINK_`` prefix (nested
models use ``__``, e.g. ``MONDO_LINK_DATA__DB_FILENAME=mondo.sqlite``) and an
optional ``.env`` file.

mondo-link has no live API: the local Mondo index, built from the OBO + SSSOM
releases served on the Monarch PURLs, is the only data source.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from mondo_link import __version__

# Project root: <repo>/mondo_link/config.py -> <repo>
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_DATA_DIR = _PROJECT_ROOT / "data"

#: Monarch publishes the Mondo OBO release at this PURL.
DEFAULT_OBO_URL = "https://purl.obolibrary.org/obo/mondo.obo"

#: Mondo publishes the consolidated SSSOM cross-ontology mappings in-repo. (The
#: ``obo/mondo.sssom.tsv`` PURL 404s; the OBO already carries dbxrefs, so SSSOM
#: is treated as a supplementary, optional source — see ``ingest.downloader``.)
DEFAULT_SSSOM_URL = (
    "https://raw.githubusercontent.com/monarch-initiative/mondo/master/"
    "src/ontology/mappings/mondo.sssom.tsv"
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MUTABLE_TAGS = frozenset({"latest", "main", "master", "head", "stable", "current"})

# The one reviewed data release the init sidecar restores (a published
# `data-mondo-*` release of this repository). Change it only in a reviewed PR,
# together with container-release.json (tests/unit/test_data_release_identity.py).
DATA_RELEASE_TAG = "data-mondo-16469cc957537704"
DATA_COMPRESSED_SHA256 = "407194bc603260bc760b78a7d8c391192b77f5511b8e3bec4fcba09d604cd597"
DATA_EXPANDED_SHA256 = "16469cc95753770428fb3f97fbf42099719ce671f9a739e2522c85256ef4b96c"


class ImmutableDataRequirement(BaseModel):
    """The exact published Mondo data release that the init sidecar may materialize."""

    model_config = ConfigDict(frozen=True)

    reference_root: Path = Path("/data")
    release_tag: str = DATA_RELEASE_TAG
    bundle_url: AnyHttpUrl = AnyHttpUrl(
        "https://github.com/berntpopp/mondo-link/releases/download/"
        f"{DATA_RELEASE_TAG}/mondo.sqlite.gz"
    )
    compressed_sha256: str = DATA_COMPRESSED_SHA256
    expanded_sha256: str = DATA_EXPANDED_SHA256
    max_compressed_bytes: int = Field(default=128 * 1024 * 1024, gt=0)
    max_expanded_bytes: int = Field(default=1024 * 1024 * 1024, gt=0)

    @model_validator(mode="after")
    def _immutable_pin(self) -> ImmutableDataRequirement:
        if not self.release_tag or self.release_tag.lower() in _MUTABLE_TAGS:
            raise ValueError("immutable data release_tag must not be mutable")
        if not (
            _SHA256_RE.fullmatch(self.compressed_sha256)
            and _SHA256_RE.fullmatch(self.expanded_sha256)
        ):
            raise ValueError("immutable data digests must be lowercase SHA-256")
        if self.bundle_url.scheme != "https" or self.bundle_url.host != "github.com":
            raise ValueError("immutable data bundle_url must be an HTTPS GitHub release asset")
        if not (self.bundle_url.path or "").endswith(
            f"/download/{self.release_tag}/mondo.sqlite.gz"
        ):
            raise ValueError("immutable data bundle_url must name the asset under release_tag")
        return self


class MondoDataConfig(BaseModel):
    """Local data store: Mondo OBO + SSSOM releases -> built SQLite index."""

    data_dir: Path = Field(
        default=_DEFAULT_DATA_DIR,
        description="Directory holding the built SQLite database and download cache.",
    )
    db_filename: str = Field(
        default="mondo.sqlite",
        description="SQLite database filename within data_dir.",
    )
    obo_url: str = Field(
        default=DEFAULT_OBO_URL,
        description="URL of the Mondo OBO release (Monarch PURL).",
    )
    sssom_url: str = Field(
        default=DEFAULT_SSSOM_URL,
        description="URL of the Mondo SSSOM cross-ontology mapping release (Monarch PURL).",
    )
    download_timeout: int = Field(
        default=300,
        ge=5,
        le=1800,
        description="HTTP timeout (seconds) for downloading a Mondo release file.",
    )
    max_download_bytes: int = Field(
        default=1 << 30,
        gt=0,
        description=(
            "Maximum release artifact size; measured below 512 MiB on 2026-07-10. "
            "Override for a larger approved Mondo release."
        ),
    )
    max_download_seconds: float = Field(
        default=1800.0,
        gt=0,
        description=(
            "Maximum total release transfer time; measured below 900 seconds on 2026-07-10. "
            "Override for slower approved links."
        ),
    )
    user_agent: str = Field(
        default=f"mondo-link/{__version__} (+https://github.com/berntpopp/mondo-link)",
        description="User-Agent sent to the Monarch PURLs.",
    )
    auto_bootstrap: bool = Field(
        default=True,
        description="Build the database on first use by downloading Mondo if absent.",
    )
    refresh_enabled: bool = Field(
        default=False,
        description=(
            "Run an in-process scheduler (unified/http transports only) that "
            "conditionally refreshes the database on an interval. Default OFF: Mondo "
            "releases are best refreshed by an external cron job (see docs/deployment.md)."
        ),
    )
    refresh_interval_hours: float = Field(
        default=168.0,
        ge=1.0,
        le=720.0,
        description=(
            "Hours between conditional refresh checks (when refresh_enabled). Mondo "
            "releases update roughly weekly; a weekly check is cheap because unchanged "
            "files 304."
        ),
    )
    refresh_jitter_seconds: int = Field(
        default=600,
        ge=0,
        le=86400,
        description="Random jitter added to each refresh to avoid thundering herds.",
    )
    build_lock_timeout: int = Field(
        default=900,
        ge=1,
        le=7200,
        description="Seconds to wait for the cross-process build lock before giving up.",
    )
    cache_size: int = Field(
        default=1024,
        ge=0,
        le=65536,
        description="Max entries in the in-process query cache (0 disables).",
    )
    cache_ttl: int = Field(
        default=3600,
        ge=0,
        le=86400,
        description="Query cache TTL in seconds.",
    )

    @property
    def db_path(self) -> Path:
        """Absolute path to the SQLite database file."""
        return self.data_dir / self.db_filename

    @field_validator("data_dir")
    @classmethod
    def _expand_data_dir(cls, v: Path) -> Path:
        return Path(v).expanduser()


class ServerSettings(BaseSettings):
    """Top-level server settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        env_prefix="MONDO_LINK_",
        env_nested_delimiter="__",
    )

    host: str = Field(default="127.0.0.1", description="Server host.")
    port: int = Field(default=8000, ge=1024, le=65535, description="Server port.")
    reload: bool = Field(default=False, description="Enable auto-reload in development.")

    transport: Literal["unified", "http", "stdio"] = Field(
        default="unified",
        description="Server transport mode.",
    )
    mcp_path: str = Field(default="/mcp", description="MCP endpoint path.")
    allowed_hosts: list[str] = Field(
        default=["localhost", "127.0.0.1", "::1"],
        description="Exact Host header values accepted by the request guard.",
    )
    allowed_origins: list[str] = Field(
        default=[],
        description="Browser Origin values accepted by the request guard.",
    )

    cors_origins: list[str] = Field(
        default=["http://localhost:3000", "http://127.0.0.1:3000"],
        description="Allowed CORS origins.",
    )

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO",
        description="Logging level.",
    )
    log_format: Literal["json", "console"] = Field(
        default="console",
        description="Log format.",
    )

    data: MondoDataConfig = Field(
        default_factory=MondoDataConfig,
        description="Local data store configuration.",
    )
    immutable_data: ImmutableDataRequirement = Field(
        default_factory=ImmutableDataRequirement,
        description="Exact published data release restored only by the init sidecar.",
    )

    @field_validator("mcp_path")
    @classmethod
    def validate_mcp_path(cls, v: str) -> str:
        """Ensure the MCP path starts with a forward slash."""
        return v if v.startswith("/") else f"/{v}"

    @field_validator("allowed_hosts", "allowed_origins", "cors_origins", mode="before")
    @classmethod
    def parse_string_list(cls, v: Any) -> list[str]:
        """Parse string lists from a comma-separated value or list."""
        if isinstance(v, str):
            return [item.strip() for item in v.split(",") if item.strip()]
        return list(v) if v else []

    @field_validator("allowed_hosts")
    @classmethod
    def reject_wildcard_host(cls, v: list[str]) -> list[str]:
        """Require exact hosts; pattern syntax makes the boundary ambiguous."""
        if any(any(marker in host for marker in "*?[]") for host in v):
            raise ValueError("wildcard patterns are not allowed in allowed_hosts")
        return v


settings = ServerSettings()


def get_data_config() -> MondoDataConfig:
    """Return the active data-store configuration (used by the ingest CLI)."""
    return settings.data
