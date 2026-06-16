"""Unit tests for the data bootstrap + refresh scheduler.

The ingest builder is monkeypatched so these stay fast and self-contained — no
real download or build is performed.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from mondo_link.config import MondoDataConfig
from mondo_link.exceptions import DownloadError
from mondo_link.services import refresh


class _FakeLogger:
    """structlog-style logger: accepts a message + arbitrary keyword context."""

    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict[str, Any]]] = []

    def _record(self, level: str, event: str, **kw: Any) -> None:
        self.events.append((level, event, kw))

    def info(self, event: str, **kw: Any) -> None:
        self._record("info", event, **kw)

    def warning(self, event: str, **kw: Any) -> None:
        self._record("warning", event, **kw)

    def debug(self, event: str, **kw: Any) -> None:
        self._record("debug", event, **kw)


def _config(tmp_path: Path, **overrides: Any) -> MondoDataConfig:
    return MondoDataConfig(data_dir=tmp_path, db_filename="mondo.sqlite", **overrides)


@pytest.mark.asyncio
async def test_bootstrap_data_builds_and_resets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    calls: dict[str, Any] = {"ensure": 0, "reset": 0}

    def fake_ensure(_cfg: Any) -> Path:
        calls["ensure"] += 1
        return config.db_path

    def fake_reset() -> None:
        calls["reset"] += 1

    monkeypatch.setattr("mondo_link.ingest.builder.ensure_database", fake_ensure)
    monkeypatch.setattr("mondo_link.mcp.service_adapters.reset_mondo_service", fake_reset)

    await refresh.bootstrap_data(config, _FakeLogger())

    assert calls["ensure"] == 1
    assert calls["reset"] == 1


@pytest.mark.asyncio
async def test_bootstrap_data_swallows_download_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)

    def boom(_cfg: Any) -> Path:
        raise DownloadError("network down")

    reset_called = {"n": 0}
    monkeypatch.setattr("mondo_link.ingest.builder.ensure_database", boom)
    monkeypatch.setattr(
        "mondo_link.mcp.service_adapters.reset_mondo_service",
        lambda: reset_called.__setitem__("n", reset_called["n"] + 1),
    )

    # Must not raise — bootstrap failures are non-fatal.
    await refresh.bootstrap_data(config, _FakeLogger())
    # Service is NOT reset when the build failed.
    assert reset_called["n"] == 0


@pytest.mark.asyncio
async def test_bootstrap_data_swallows_os_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)

    def boom(_cfg: Any) -> Path:
        raise OSError("disk full")

    monkeypatch.setattr("mondo_link.ingest.builder.ensure_database", boom)
    monkeypatch.setattr("mondo_link.mcp.service_adapters.reset_mondo_service", lambda: None)

    await refresh.bootstrap_data(config, _FakeLogger())  # no raise


def test_scheduler_none_when_disabled(tmp_path: Path) -> None:
    config = _config(tmp_path, refresh_enabled=False)
    task = refresh.start_refresh_scheduler(config, _FakeLogger())
    assert task is None


@pytest.mark.asyncio
async def test_scheduler_starts_when_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Avoid touching the real ServerSettings wrap / builder when enabled.
    config = _config(tmp_path, refresh_enabled=True)
    task = refresh.start_refresh_scheduler(config, _FakeLogger())
    assert isinstance(task, asyncio.Task)
    await refresh.stop_refresh_scheduler(task)
    assert task.cancelled()


@pytest.mark.asyncio
async def test_stop_scheduler_none_is_noop() -> None:
    await refresh.stop_refresh_scheduler(None)  # must not raise
