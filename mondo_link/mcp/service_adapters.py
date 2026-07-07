"""Lazily-constructed singleton MondoService for MCP tools.

The repository is opened against the already-built SQLite index (the server
lifespan bootstraps it; see ``mondo_link.app``). If the index is not present yet,
the service is built without a repository — tools then return ``data_unavailable``.
mondo-link has no live API, so there is no fallback client.
"""

from __future__ import annotations

import logging

from mondo_link.config import settings
from mondo_link.data.repository import MondoRepository
from mondo_link.exceptions import DataUnavailableError
from mondo_link.services.mondo_service import MondoService

logger = logging.getLogger(__name__)

_service: MondoService | None = None


def _build_service() -> MondoService:
    repo: MondoRepository | None = None
    db_path = settings.data.db_path
    if db_path.exists():
        try:
            repo = MondoRepository(db_path)
        except DataUnavailableError as exc:  # pragma: no cover - corrupt db
            # Filename only — never the absolute path (avoids leaking the
            # deployment's filesystem layout into logs).
            logger.warning("mondo_repo_open_failed file=%s err=%s", db_path.name, exc)
    return MondoService(repo)


def get_mondo_service() -> MondoService:
    """Return a process-wide :class:`MondoService` (built on first use)."""
    global _service
    if _service is None:
        _service = _build_service()
    return _service


def reset_mondo_service() -> None:
    """Drop the cached service so the next call re-opens the repository."""
    global _service
    _service = None


def set_mondo_service(service: MondoService | None) -> None:
    """Override the singleton (used by tests)."""
    global _service
    _service = service
