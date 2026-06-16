"""Orchestration over the read-only Mondo repository.

Returns plain dicts (no envelope); the MCP layer owns ``success``/``_meta``.
Every record payload carries ``mondo_version`` (from build provenance) for
grounding. The resolution cascade (MONDO id -> primary/synonym label -> external
xref CURIE) returns the match provenance and raises typed exceptions instead of
silently collapsing ambiguity.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mondo_link.exceptions import (
    AmbiguousQueryError,
    InvalidInputError,
    NotFoundError,
    WithdrawnEntryError,
)
from mondo_link.identifiers import infer_xref_source, normalize_mondo_id, normalize_xref
from mondo_link.services.pagination import page_fields
from mondo_link.services.shaping import DEFAULT_RESPONSE_MODE, shape_disease

if TYPE_CHECKING:
    from mondo_link.data.repository import MondoRepository

#: Maps a lookup ``label_type`` to a resolve ``match_type``.
_LABEL_MATCH_TYPE = {
    "primary": "primary",
    "exact_synonym": "exact_synonym",
    "related_synonym": "related_synonym",
    "broad_synonym": "related_synonym",
    "narrow_synonym": "related_synonym",
}
_MAX_LIMIT = 1000


class MondoService:
    def __init__(self, repository: MondoRepository | None) -> None:
        self._repo = repository

    @property
    def repo(self) -> MondoRepository:
        from mondo_link.exceptions import DataUnavailableError

        if self._repo is None:
            raise DataUnavailableError("The Mondo index is not built. Run `mondo-link-data build`.")
        return self._repo

    # -- provenance ------------------------------------------------------------

    def _mondo_version(self) -> str | None:
        """Return the built Mondo release string (for grounding), or ``None``."""
        meta = self.repo.read_meta()
        return meta.get("mondo_version") if meta else None

    # -- resolution ------------------------------------------------------------

    def _resolve_term_id(self, term: str) -> str:
        """Resolve any MONDO id / label / xref CURIE to a canonical MONDO id.

        Cascade: MONDO id (obsolete -> ``WithdrawnEntryError``) -> external xref
        CURIE -> label/synonym. Raises ``NotFoundError`` / ``AmbiguousQueryError``.
        """
        raw = (term or "").strip()
        if not raw:
            raise InvalidInputError(
                "term must be a non-empty MONDO id, label, or xref.", field="term"
            )

        mondo_id = normalize_mondo_id(raw)
        if mondo_id:
            record = self.repo.get_term(mondo_id)
            if record is None:
                raise NotFoundError(f"No Mondo term for {mondo_id}.")
            if record["is_obsolete"]:
                raise WithdrawnEntryError(
                    mondo_id,
                    status="obsolete",
                    replaced_by=self._replacement_records(record),
                )
            return mondo_id

        if infer_xref_source(raw):
            normalized = normalize_xref(raw)
            if normalized:
                matches = self.repo.mondo_for_xref(normalized.upper(), limit=2)
                if not matches:
                    raise NotFoundError(f"No Mondo term cross-references {normalized}.")
                return str(matches[0]["mondo_id"])

        candidates = self.repo.resolve_label(raw.upper())
        if not candidates:
            raise NotFoundError(
                f"No Mondo term matches '{raw}'. Try a MONDO id, a disease label, or an xref CURIE."
            )
        distinct = {c["mondo_id"] for c in candidates}
        if len(distinct) == 1:
            return str(next(iter(distinct)))
        raise AmbiguousQueryError(
            f"'{raw}' matches {len(distinct)} Mondo terms; pick one and call get_disease.",
            candidates=self._label_candidates(candidates),
        )

    def _replacement_records(self, record: dict[str, Any]) -> list[dict[str, str]]:
        """Build replacement records (replaced_by + consider) for an obsolete term."""
        out: list[dict[str, str]] = []
        seen: set[str] = set()
        targets: list[str] = []
        replaced_by = record.get("replaced_by")
        if replaced_by:
            targets.append(replaced_by)
        targets.extend(record.get("consider") or [])
        for target in targets:
            canon = normalize_mondo_id(target) or target
            if canon in seen:
                continue
            seen.add(canon)
            successor = self.repo.get_term(canon)
            out.append({"mondo_id": canon, "name": successor["name"] if successor else canon})
        return out

    def _label_candidates(self, candidates: list[dict[str, Any]]) -> list[dict[str, str]]:
        """Build de-duplicated ambiguity candidates with names."""
        out: list[dict[str, str]] = []
        seen: set[str] = set()
        for cand in candidates:
            mid = cand["mondo_id"]
            if mid in seen:
                continue
            seen.add(mid)
            term = self.repo.get_term(mid)
            out.append(
                {
                    "mondo_id": mid,
                    "name": term["name"] if term else mid,
                    "label_type": cand["label_type"],
                }
            )
        return out

    # -- diagnostics -----------------------------------------------------------

    def get_diagnostics(self) -> dict[str, Any]:
        """Return data-source provenance and freshness; never raises if unbuilt."""
        if self._repo is None:
            return {
                "index_built": False,
                "db_path": None,
                "message": "Local Mondo index not built. Run `mondo-link-data build`.",
            }
        meta = self._repo.read_meta()
        return {
            "index_built": True,
            "db_path": str(self._repo._path),
            "mondo_version": meta.get("mondo_version") if meta else None,
            "schema_version": meta.get("schema_version") if meta else None,
            "build_utc": meta.get("build_utc") if meta else None,
            "counts": self._repo.counts(),
        }

    # -- resolve ---------------------------------------------------------------

    def resolve_disease(
        self, query: str, *, response_mode: str = DEFAULT_RESPONSE_MODE
    ) -> dict[str, Any]:
        """Resolve any id/label/xref to a canonical MONDO term with provenance."""
        raw = (query or "").strip()
        if not raw:
            raise InvalidInputError(
                "query must be a non-empty MONDO id, label, or xref.", field="query"
            )
        match_type, mondo_id = self._classify_resolution(raw)
        record = self.repo.get_term(mondo_id)
        if record is None:  # pragma: no cover - defensive
            raise NotFoundError(f"No Mondo term for {mondo_id}.")
        out: dict[str, Any] = {
            "query": raw,
            "mondo_id": mondo_id,
            "name": record["name"],
            "match_type": match_type,
            "obsolete": record["is_obsolete"],
            "mondo_version": self._mondo_version(),
        }
        if record["replaced_by"]:
            out["replaced_by"] = record["replaced_by"]
        return out

    def _classify_resolution(self, raw: str) -> tuple[str, str]:
        """Resolve ``raw`` and report how the match was made (``match_type``)."""
        mondo_id = normalize_mondo_id(raw)
        if mondo_id:
            record = self.repo.get_term(mondo_id)
            if record is None:
                raise NotFoundError(f"No Mondo term for {mondo_id}.")
            if record["is_obsolete"]:
                raise WithdrawnEntryError(
                    mondo_id, status="obsolete", replaced_by=self._replacement_records(record)
                )
            return "mondo_id", mondo_id
        if infer_xref_source(raw):
            normalized = normalize_xref(raw)
            if normalized:
                matches = self.repo.mondo_for_xref(normalized.upper(), limit=2)
                if matches:
                    return "xref", matches[0]["mondo_id"]
                raise NotFoundError(f"No Mondo term cross-references {normalized}.")
        candidates = self.repo.resolve_label(raw.upper())
        if not candidates:
            raise NotFoundError(
                f"No Mondo term matches '{raw}'. Try a MONDO id, a disease label, or an xref CURIE."
            )
        distinct = {c["mondo_id"] for c in candidates}
        if len(distinct) == 1:
            best = candidates[0]
            return _LABEL_MATCH_TYPE.get(best["label_type"], "primary"), best["mondo_id"]
        raise AmbiguousQueryError(
            f"'{raw}' matches {len(distinct)} Mondo terms; pick one and call get_disease.",
            candidates=self._label_candidates(candidates),
        )

    # -- search ----------------------------------------------------------------

    def search_diseases(
        self,
        query: str,
        *,
        limit: int = 25,
        include_obsolete: bool = False,
        response_mode: str = DEFAULT_RESPONSE_MODE,
    ) -> dict[str, Any]:
        """Free-text search over disease name/synonyms/definition."""
        raw = (query or "").strip()
        if not raw:
            raise InvalidInputError("query must be a non-empty search string.", field="query")
        limit = max(1, min(limit, 200))
        hits, total = self.repo.search(raw, limit=limit, include_obsolete=include_obsolete)
        results: list[dict[str, Any]] = []
        for hit in hits:
            row: dict[str, Any] = {
                "mondo_id": hit["mondo_id"],
                "name": hit["name"],
                "score": hit["score"],
            }
            if hit.get("definition"):
                row["definition"] = hit["definition"]
            results.append(row)
        return {
            "query": raw,
            "results": results,
            **page_fields(total=total, returned=len(results), limit=limit),
            "mondo_version": self._mondo_version(),
        }

    # -- full record -----------------------------------------------------------

    def get_disease(
        self, term: str, *, response_mode: str = DEFAULT_RESPONSE_MODE
    ) -> dict[str, Any]:
        """Return the full disease record (hierarchy + grouped xrefs)."""
        mondo_id = self._resolve_term_id(term)
        record = self.repo.get_term(mondo_id)
        if record is None:  # pragma: no cover - defensive
            raise NotFoundError(f"No Mondo term for {mondo_id}.")
        payload: dict[str, Any] = {
            "mondo_id": mondo_id,
            "name": record["name"],
            "definition": record["definition"],
            "synonyms": record["synonyms"],
            "subsets": record["subsets"],
            "obsolete": record["is_obsolete"],
            "replaced_by": record["replaced_by"],
            "consider": record["consider"],
            "parents": self.repo.parents(mondo_id),
            "children": self.repo.children(mondo_id),
            "top_groupings": self.repo.top_groupings(mondo_id),
            "xrefs": self._grouped_xrefs(mondo_id),
            "mondo_version": self._mondo_version(),
        }
        return shape_disease(payload, response_mode)

    def _grouped_xrefs(self, mondo_id: str, prefixes: list[str] | None = None) -> dict[str, Any]:
        """Group cross-references by prefix (predicate-ranked within each)."""
        grouped: dict[str, list[dict[str, Any]]] = {}
        for xref in self.repo.xrefs_for(mondo_id, prefixes):
            grouped.setdefault(xref["prefix"], []).append(
                {
                    "object_id": xref["object_id"],
                    "predicate": xref["predicate"],
                    "origin": xref["origin"],
                    "source": xref["source"],
                }
            )
        return grouped

    # -- hierarchy -------------------------------------------------------------

    def get_ancestors(
        self, term: str, *, limit: int = 200, response_mode: str = DEFAULT_RESPONSE_MODE
    ) -> dict[str, Any]:
        """Return transitive ancestors of a term (closure walk)."""
        return self._closure(term, kind="ancestors", limit=limit)

    def get_descendants(
        self, term: str, *, limit: int = 200, response_mode: str = DEFAULT_RESPONSE_MODE
    ) -> dict[str, Any]:
        """Return transitive descendants of a term (closure walk)."""
        return self._closure(term, kind="descendants", limit=limit)

    def _closure(self, term: str, *, kind: str, limit: int) -> dict[str, Any]:
        mondo_id = self._resolve_term_id(term)
        record = self.repo.get_term(mondo_id)
        limit = max(1, min(limit, _MAX_LIMIT))
        if kind == "ancestors":
            rows = self.repo.ancestors(mondo_id, limit=limit)
            total = self.repo.count_ancestors(mondo_id)
        else:
            rows = self.repo.descendants(mondo_id, limit=limit)
            total = self.repo.count_descendants(mondo_id)
        return {
            "mondo_id": mondo_id,
            "name": record["name"] if record else None,
            kind: rows,
            **page_fields(total=total, returned=len(rows), limit=limit),
            "mondo_version": self._mondo_version(),
        }

    def get_parents(
        self, term: str, *, response_mode: str = DEFAULT_RESPONSE_MODE
    ) -> dict[str, Any]:
        """Return the immediate parents of a term."""
        return self._neighbours(term, kind="parents")

    def get_children(
        self, term: str, *, response_mode: str = DEFAULT_RESPONSE_MODE
    ) -> dict[str, Any]:
        """Return the immediate children of a term."""
        return self._neighbours(term, kind="children")

    def _neighbours(self, term: str, *, kind: str) -> dict[str, Any]:
        mondo_id = self._resolve_term_id(term)
        record = self.repo.get_term(mondo_id)
        rows = self.repo.parents(mondo_id) if kind == "parents" else self.repo.children(mondo_id)
        return {
            "mondo_id": mondo_id,
            "name": record["name"] if record else None,
            kind: rows,
            "count": len(rows),
            "mondo_version": self._mondo_version(),
        }

    # -- cross-ontology --------------------------------------------------------

    def resolve_xref(
        self, xref_id: str, *, limit: int = 50, response_mode: str = DEFAULT_RESPONSE_MODE
    ) -> dict[str, Any]:
        """Reverse lookup: external CURIE -> MONDO terms that cross-reference it."""
        raw = (xref_id or "").strip()
        if not raw:
            raise InvalidInputError(
                "xref_id must be a non-empty CURIE like OMIM:143100.", field="xref_id"
            )
        normalized = normalize_xref(raw)
        if normalized is None:
            raise InvalidInputError(
                f"'{raw}' is not a valid CURIE (expected PREFIX:LOCAL, e.g. OMIM:143100).",
                field="xref_id",
            )
        limit = max(1, min(limit, _MAX_LIMIT))
        matches = self.repo.mondo_for_xref(normalized.upper(), limit=limit)
        results = [
            {
                "mondo_id": m["mondo_id"],
                "name": m["name"],
                "predicate": m["predicate"],
                "origin": m["origin"],
            }
            for m in matches
        ]
        return {
            "xref_id": raw,
            "normalized": normalized,
            "matches": results,
            **page_fields(total=len(results), returned=len(results), limit=limit),
            "mondo_version": self._mondo_version(),
        }

    def map_cross_ontology(
        self,
        term: str,
        *,
        prefixes: list[str] | None = None,
        response_mode: str = DEFAULT_RESPONSE_MODE,
    ) -> dict[str, Any]:
        """Return all cross-ontology mappings for a term, grouped by prefix."""
        mondo_id = self._resolve_term_id(term)
        record = self.repo.get_term(mondo_id)
        normalized = [p.strip().upper() for p in prefixes if p.strip()] if prefixes else None
        return {
            "mondo_id": mondo_id,
            "name": record["name"] if record else None,
            "mappings": self._grouped_xrefs(mondo_id, normalized),
            "prefixes_filter": normalized,
            "mondo_version": self._mondo_version(),
        }
