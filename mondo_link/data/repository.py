"""Read-only SQLite repository for the built Mondo index (CONTRACT BARRIER).

Wave 0 freezes the constructor (read-only connection, ``DataUnavailableError`` on
a missing file) and the method-signature surface downstream depends on. Query
bodies raise ``NotImplementedError``; Wave 1B fills them in against the frozen
``schema.sql``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from mondo_link.exceptions import DataUnavailableError


class MondoRepository:
    """Read-only access to the built Mondo SQLite index."""

    def __init__(self, db_path: Path | str) -> None:
        """Open a read-only connection to the Mondo database."""
        self._path = Path(db_path)
        if not self._path.exists():
            raise DataUnavailableError(
                f"Mondo database not found at {self._path}. Build it with `mondo-link-data build`."
            )
        try:
            self._conn = sqlite3.connect(
                f"file:{self._path}?mode=ro",
                uri=True,
                check_same_thread=False,
            )
        except sqlite3.Error as exc:  # pragma: no cover - rare OS-level failure
            raise DataUnavailableError(
                f"Cannot open Mondo database at {self._path}: {exc}."
            ) from exc
        self._conn.row_factory = sqlite3.Row

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        self._conn.close()

    # -- provenance ------------------------------------------------------------

    def read_meta(self) -> dict[str, Any]:
        """Return build provenance from the ``meta`` table."""
        raise NotImplementedError

    # -- term records ----------------------------------------------------------

    def get_term(self, mondo_id: str) -> dict[str, Any] | None:
        """Return the ``term`` row for a canonical MONDO id, or ``None``."""
        raise NotImplementedError

    def resolve_label(self, label: str) -> list[dict[str, Any]]:
        """Resolve a label/synonym to candidate ``(mondo_id, label_type)`` rows."""
        raise NotImplementedError

    def search(self, query: str, *, limit: int, include_obsolete: bool) -> list[dict[str, Any]]:
        """Full-text search over name/synonyms/definition."""
        raise NotImplementedError

    # -- hierarchy -------------------------------------------------------------

    def parents(self, mondo_id: str) -> list[dict[str, Any]]:
        """Immediate parent terms of ``mondo_id``."""
        raise NotImplementedError

    def children(self, mondo_id: str) -> list[dict[str, Any]]:
        """Immediate child terms of ``mondo_id``."""
        raise NotImplementedError

    def ancestors(self, mondo_id: str, *, limit: int) -> list[dict[str, Any]]:
        """Transitive ancestors of ``mondo_id`` (via the closure table)."""
        raise NotImplementedError

    def descendants(self, mondo_id: str, *, limit: int) -> list[dict[str, Any]]:
        """Transitive descendants of ``mondo_id`` (via the closure table)."""
        raise NotImplementedError

    def top_groupings(self) -> list[dict[str, Any]]:
        """The top-level disease groupings (the Mondo grouping grid)."""
        raise NotImplementedError

    # -- cross-references ------------------------------------------------------

    def xrefs_for(self, mondo_id: str, prefixes: list[str] | None = None) -> list[dict[str, Any]]:
        """Cross-references for ``mondo_id``, optionally filtered by prefix."""
        raise NotImplementedError

    def mondo_for_xref(self, xref_id: str, *, limit: int) -> list[dict[str, Any]]:
        """MONDO terms that carry a mapping to the external ``xref_id`` CURIE."""
        raise NotImplementedError
