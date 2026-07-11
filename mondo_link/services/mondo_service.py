"""Orchestration over the read-only Mondo repository.

Returns plain dicts (no envelope); the MCP layer owns ``success``/``_meta``.
Every record payload carries ``mondo_version`` (from build provenance) for
grounding. The resolution cascade (MONDO id -> primary/synonym label -> external
xref CURIE) returns the match provenance and raises typed exceptions instead of
silently collapsing ambiguity.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mondo_link.exceptions import InvalidInputError, NotFoundError
from mondo_link.identifiers import normalize_xref
from mondo_link.mcp.untrusted_content import (
    UntrustedText,
    enforce_untrusted_text_limits,
    fence_untrusted_text,
)
from mondo_link.services.pagination import page_fields
from mondo_link.services.resolution import Resolver
from mondo_link.services.shaping import (
    DEFAULT_RESPONSE_MODE,
    select_fields,
    shape_disease,
    shape_search_hit,
)

if TYPE_CHECKING:
    from mondo_link.data.repository import MondoRepository

_MAX_LIMIT = 1000

#: search_diseases' own hard pagination cap (mirrors the tool's ``limit``
#: Field constraint in ``mcp/tools/diseases.py``). Passed as the
#: ``enforce_untrusted_text_limits`` ``max_objects`` override so a legitimate
#: wide search is never rejected by the v1.1 fencing ceiling: the fleet
#: default (128) is a generic backstop, not a bespoke per-tool pagination
#: bound, and this tool's own cap was already reviewed and is unrelated to
#: the v1.1 object-count guard's intent (bounding pathological/abusive
#: payload sizes, not a normal max-page result set).
_SEARCH_MAX_OBJECTS = 200

#: Response-Envelope Standard v1.1: upstream Mondo prose (the free-text
#: ``definition``) is fenced as a typed ``untrusted_text`` object at this
#: serialization boundary, never left as a bare string. ``source`` is fixed
#: fleet-wide for this backend; ``record_id`` is the term's MONDO id.
_UNTRUSTED_SOURCE = "mondo"


def _finalize_xref_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Drop the ``predicates`` list when a target id has a single predicate (token-lean)."""
    if len(entry["predicates"]) <= 1:
        del entry["predicates"]
    return entry


def _fence_definition(
    raw: str | None, *, mondo_id: str, sink: list[UntrustedText]
) -> dict[str, Any] | None:
    """Fence an upstream Mondo definition as a v1.1 ``untrusted_text`` object.

    Returns ``None`` when there is no definition, preserving the existing
    null-when-absent contract ``shape_disease``/``shape_search_hit`` rely on
    to drop the field in compact mode. Every non-null fence is appended to
    ``sink`` so the caller can enforce the response-wide limits once.
    """
    if not raw:
        return None
    fenced = fence_untrusted_text(raw, source=_UNTRUSTED_SOURCE, record_id=mondo_id)
    sink.append(fenced)
    return fenced.model_dump(mode="json")


def _fence_search_hit(hit: dict[str, Any], mode: str, sink: list[UntrustedText]) -> dict[str, Any]:
    """Project one search hit, then fence its definition/definition_snippet.

    :func:`shape_search_hit` emits the full paragraph as ``definition`` in
    standard/full mode, or its word-boundary truncation as
    ``definition_snippet`` in compact mode -- both carry the SAME upstream
    prose, so both are fenced. Leaving ``definition_snippet`` unfenced would
    ship raw upstream text on the default (compact) hot path.
    """
    shaped = shape_search_hit(hit, mode)
    mondo_id = str(hit.get("mondo_id"))
    for key in ("definition", "definition_snippet"):
        value = shaped.get(key)
        if value:
            fenced = fence_untrusted_text(str(value), source=_UNTRUSTED_SOURCE, record_id=mondo_id)
            sink.append(fenced)
            shaped[key] = fenced.model_dump(mode="json")
    return shaped


class MondoService:
    def __init__(self, repository: MondoRepository | None) -> None:
        self._repo = repository

    @property
    def repo(self) -> MondoRepository:
        from mondo_link.exceptions import DataUnavailableError

        if self._repo is None:
            raise DataUnavailableError("The Mondo index is not built. Run `mondo-link-data build`.")
        return self._repo

    @property
    def _resolution(self) -> Resolver:
        """Resolver bound to the (guarded) repository; preserves data_unavailable."""
        return Resolver(self.repo)

    # -- provenance ------------------------------------------------------------

    def _mondo_version(self) -> str | None:
        """Return the built Mondo release string (for grounding), or ``None``."""
        meta = self.repo.read_meta()
        return meta.get("mondo_version") if meta else None

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
            # Filename only — never the absolute path. This surface is
            # unauthenticated by design; the full on-disk path would leak the
            # deployment's filesystem layout to any caller.
            "db_path": self._repo._path.name,
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
        match_type, mondo_id = self._resolution.classify_resolution(raw)
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

    # -- search ----------------------------------------------------------------

    def search_diseases(
        self,
        query: str,
        *,
        limit: int = 25,
        offset: int = 0,
        include_obsolete: bool = False,
        response_mode: str = DEFAULT_RESPONSE_MODE,
    ) -> dict[str, Any]:
        """Free-text search over disease name/synonyms/definition."""
        raw = (query or "").strip()
        if not raw:
            raise InvalidInputError("query must be a non-empty search string.", field="query")
        limit = max(1, min(limit, _SEARCH_MAX_OBJECTS))
        offset = max(0, offset)
        hits, total = self.repo.search(
            raw, limit=limit, offset=offset, include_obsolete=include_obsolete
        )
        fenced_objs: list[UntrustedText] = []
        results = [_fence_search_hit(hit, response_mode, fenced_objs) for hit in hits]
        enforce_untrusted_text_limits(fenced_objs, max_objects=_SEARCH_MAX_OBJECTS)
        return {
            "query": raw,
            "results": results,
            **page_fields(total=total, returned=len(results), limit=limit, offset=offset),
            "mondo_version": self._mondo_version(),
        }

    # -- full record -----------------------------------------------------------

    def get_disease(
        self,
        term: str,
        *,
        response_mode: str = DEFAULT_RESPONSE_MODE,
        fields: list[str] | None = None,
    ) -> dict[str, Any]:
        """Return the full disease record (hierarchy + grouped xrefs)."""
        mondo_id = self._resolution.resolve_term_id(term)
        record = self.repo.get_term(mondo_id)
        if record is None:  # pragma: no cover - defensive
            raise NotFoundError(f"No Mondo term for {mondo_id}.")
        fenced_objs: list[UntrustedText] = []
        payload: dict[str, Any] = {
            "mondo_id": mondo_id,
            "name": record["name"],
            "definition": _fence_definition(
                record["definition"], mondo_id=mondo_id, sink=fenced_objs
            ),
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
        enforce_untrusted_text_limits(fenced_objs)
        return select_fields(shape_disease(payload, response_mode), fields)

    def _grouped_xrefs(self, mondo_id: str, prefixes: list[str] | None = None) -> dict[str, Any]:
        """Group cross-references by prefix, ONE entry per target id.

        A single target id can be asserted by several rows (an OBO xref plus an SSSOM
        mapping, or two predicates). Rows arrive predicate-ranked (strongest first), so
        the first row for an id sets the primary ``predicate``/``origin``/``source`` (and
        ``name`` when the target label is known); any further predicates collect into a
        ``predicates`` list (only when there is more than one). Collapsing here keeps the
        payload token-lean -- the common case is one entry, with multiplicity surfaced
        only when it exists -- and drops the wasteful ``source: null`` of OBO xrefs.
        """
        grouped: dict[str, dict[str, dict[str, Any]]] = {}
        for xref in self.repo.xrefs_for(mondo_id, prefixes):
            bucket = grouped.setdefault(xref["prefix"], {})
            entry = bucket.get(xref["object_id"])
            if entry is None:
                entry = {"object_id": xref["object_id"], "predicate": xref["predicate"]}
                label = xref.get("object_label")
                if label:
                    entry["name"] = label
                entry["origin"] = xref["origin"]
                if xref.get("source"):
                    entry["source"] = xref["source"]
                entry["predicates"] = [xref["predicate"]]
                bucket[xref["object_id"]] = entry
            else:
                if xref["predicate"] not in entry["predicates"]:
                    entry["predicates"].append(xref["predicate"])
                if "name" not in entry and xref.get("object_label"):
                    entry["name"] = xref["object_label"]
        return {
            prefix: [_finalize_xref_entry(entry) for entry in bucket.values()]
            for prefix, bucket in grouped.items()
        }

    # -- hierarchy -------------------------------------------------------------

    def get_ancestors(
        self,
        term: str,
        *,
        limit: int = 200,
        offset: int = 0,
        response_mode: str = DEFAULT_RESPONSE_MODE,
    ) -> dict[str, Any]:
        """Return transitive ancestors of a term (closure walk)."""
        return self._closure(term, kind="ancestors", limit=limit, offset=offset)

    def get_descendants(
        self,
        term: str,
        *,
        limit: int = 200,
        offset: int = 0,
        response_mode: str = DEFAULT_RESPONSE_MODE,
    ) -> dict[str, Any]:
        """Return transitive descendants of a term (closure walk)."""
        return self._closure(term, kind="descendants", limit=limit, offset=offset)

    def _closure(self, term: str, *, kind: str, limit: int, offset: int = 0) -> dict[str, Any]:
        mondo_id = self._resolution.resolve_term_id(term)
        record = self.repo.get_term(mondo_id)
        limit = max(1, min(limit, _MAX_LIMIT))
        offset = max(0, offset)
        if kind == "ancestors":
            rows = self.repo.ancestors(mondo_id, limit=limit, offset=offset)
            total = self.repo.count_ancestors(mondo_id)
        else:
            rows = self.repo.descendants(mondo_id, limit=limit, offset=offset)
            total = self.repo.count_descendants(mondo_id)
        return {
            "mondo_id": mondo_id,
            "name": record["name"] if record else None,
            kind: rows,
            **page_fields(total=total, returned=len(rows), limit=limit, offset=offset),
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
        mondo_id = self._resolution.resolve_term_id(term)
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
        self,
        xref_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
        response_mode: str = DEFAULT_RESPONSE_MODE,
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
        offset = max(0, offset)
        key = normalized.upper()
        total = self.repo.count_mondo_for_xref(key)
        matches = self.repo.mondo_for_xref(key, limit=limit, offset=offset)
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
            **page_fields(total=total, returned=len(results), limit=limit, offset=offset),
            "mondo_version": self._mondo_version(),
        }

    def map_cross_ontology(
        self,
        term: str,
        *,
        prefixes: list[str] | None = None,
        response_mode: str = DEFAULT_RESPONSE_MODE,
        fields: list[str] | None = None,
    ) -> dict[str, Any]:
        """Return all cross-ontology mappings for a term, grouped by prefix."""
        mondo_id = self._resolution.resolve_term_id(term)
        record = self.repo.get_term(mondo_id)
        normalized = [p.strip().upper() for p in prefixes if p.strip()] if prefixes else None
        mappings = self._grouped_xrefs(mondo_id, normalized)
        payload = {
            "mondo_id": mondo_id,
            "name": record["name"] if record else None,
            "mappings": mappings,
            "count": sum(len(rows) for rows in mappings.values()),
            "prefixes_filter": normalized,
            "mondo_version": self._mondo_version(),
        }
        return select_fields(payload, fields)
