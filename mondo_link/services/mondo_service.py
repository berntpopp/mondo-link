"""Frozen MondoService interface (CONTRACT BARRIER).

Wave 0 freezes the exact class + method signatures downstream waves depend on.
Bodies raise ``NotImplementedError``; Wave 1B fills them in.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mondo_link.data.repository import MondoRepository


class MondoService:
    def __init__(self, repository: MondoRepository | None) -> None:
        self._repo = repository

    @property
    def repo(self) -> MondoRepository:
        from mondo_link.exceptions import DataUnavailableError

        if self._repo is None:
            raise DataUnavailableError("The Mondo index is not built. Run `mondo-link-data build`.")
        return self._repo

    def get_diagnostics(self) -> dict[str, Any]:
        raise NotImplementedError

    def resolve_disease(self, query: str, *, response_mode: str = "compact") -> dict[str, Any]:
        raise NotImplementedError

    def search_diseases(
        self,
        query: str,
        *,
        limit: int = 25,
        include_obsolete: bool = False,
        response_mode: str = "compact",
    ) -> dict[str, Any]:
        raise NotImplementedError

    def get_disease(self, term: str, *, response_mode: str = "compact") -> dict[str, Any]:
        raise NotImplementedError

    def get_ancestors(
        self, term: str, *, limit: int = 200, response_mode: str = "compact"
    ) -> dict[str, Any]:
        raise NotImplementedError

    def get_descendants(
        self, term: str, *, limit: int = 200, response_mode: str = "compact"
    ) -> dict[str, Any]:
        raise NotImplementedError

    def get_parents(self, term: str, *, response_mode: str = "compact") -> dict[str, Any]:
        raise NotImplementedError

    def get_children(self, term: str, *, response_mode: str = "compact") -> dict[str, Any]:
        raise NotImplementedError

    def resolve_xref(
        self, xref_id: str, *, limit: int = 50, response_mode: str = "compact"
    ) -> dict[str, Any]:
        raise NotImplementedError

    def map_cross_ontology(
        self, term: str, *, prefixes: list[str] | None = None, response_mode: str = "compact"
    ) -> dict[str, Any]:
        raise NotImplementedError

    def _resolve_term_id(self, term: str) -> str:
        raise NotImplementedError
