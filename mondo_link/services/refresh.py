"""Data bootstrap + conditional-refresh scheduler.

WAVE 0 STUB — the real download/build pipeline is owned by Wave 1A. These keep
the exact public signatures the server entry points depend on so the app boots
and serves ``data_unavailable`` until the index is built. Replace the bodies in
Wave 1A.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mondo_link.config import MondoDataConfig


async def bootstrap_data(config: MondoDataConfig, logger: Any) -> None:
    """Ensure the local Mondo index exists (no-op until Wave 1A builds it)."""
    if config.db_path.exists():
        logger.info("mondo_index_present", path=str(config.db_path))
        return
    logger.warning(
        "mondo_index_missing",
        path=str(config.db_path),
        hint="Run `mondo-link-data build` (ingest pipeline lands in Wave 1A).",
    )


def start_refresh_scheduler(config: MondoDataConfig, logger: Any) -> asyncio.Task[None] | None:
    """Start the in-process refresh loop when enabled (no-op stub for now)."""
    if not config.refresh_enabled:
        return None
    logger.info("mondo_refresh_scheduler_disabled_stub")
    return None


async def stop_refresh_scheduler(task: asyncio.Task[None] | None) -> None:
    """Cancel the refresh loop, if any."""
    if task is None:
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
