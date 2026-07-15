"""Response-mode projection for Mondo disease payloads.

``standard`` / ``full`` are the identity (the complete record, with structured
synonyms carrying scope/type/sources). ``compact`` (the default) drops null/empty
values and collapses synonyms to plain strings. ``minimal`` keeps the identity
anchors AND every populated collection -- narrowed to its rows' stable identifiers.
It NEVER deletes a collection: a mode that turned N records into zero would be a
silent-empty by another name (Response-Envelope v1: minimal is "the mandatory
envelope plus stable identifiers", identifiers explicitly retained).
"""

from __future__ import annotations

import re
from typing import Any

from mondo_link.exceptions import InvalidInputError

RESPONSE_MODES: list[str] = ["minimal", "compact", "standard", "full"]
DEFAULT_RESPONSE_MODE = "compact"

#: Default cap for the compact search snippet (chars). search_diseases is the
#: broadest-fan-out tool, so its default page must stay token-cheap: identity +
#: score + a short snippet, with the full definition reserved for standard/full.
SEARCH_SNIPPET_CHARS = 140

#: Matches a run of whitespace, used to back a truncated snippet off to the
#: last word boundary WITHOUT collapsing internal whitespace (see :func:`_snippet`).
_WS_RUN_RE = re.compile(r"\s+")

_PRESERVE_KEYS: frozenset[str] = frozenset({"_meta", "success"})

#: Identity/grounding anchors always retained (minimal + a sparse fieldset).
_ANCHORS: frozenset[str] = frozenset({"mondo_id", "name", "mondo_version"})
_FIELD_ANCHORS: frozenset[str] = _ANCHORS | _PRESERVE_KEYS

#: Stable identifier fields per collection key: ``minimal`` narrows each row in a
#: collection to these, so the collection survives but its per-row DETAIL is dropped.
_ROW_IDENTIFIERS: dict[str, tuple[str, ...]] = {
    "parents": ("mondo_id",),
    "children": ("mondo_id",),
    "top_groupings": ("mondo_id",),
    "ancestors": ("mondo_id",),
    "descendants": ("mondo_id",),
    "results": ("mondo_id",),
    "matches": ("mondo_id",),
    "xrefs": ("object_id",),  # grouped {prefix: [{object_id, ...}]}
    "mappings": ("object_id",),
}

#: Collections whose rows are bare scalars (a list of strings); kept verbatim.
_SCALAR_COLLECTIONS: frozenset[str] = frozenset({"synonyms", "subsets", "consider"})

#: Envelope STRUCTURE (counts/pagination echoes), always retained at ``minimal`` --
#: dropping ``count``/``total`` is what makes a discarded payload indistinguishable
#: from an empty one.
_STRUCTURAL_KEYS: frozenset[str] = frozenset(
    {"count", "total", "returned", "limit", "offset", "next_offset", "truncated", "prefixes_filter"}
)


def _is_empty(value: Any) -> bool:
    """True for the null/empty values compact mode drops."""
    return value is None or value == [] or value == "" or value == {}


def _is_collection(value: Any) -> bool:
    """True for the two collection shapes: a list, or an object grouping lists."""
    if isinstance(value, list):
        return True
    return (
        isinstance(value, dict) and bool(value) and all(isinstance(v, list) for v in value.values())
    )


def _project_row(row: Any, identifiers: tuple[str, ...] | None) -> Any:
    """Narrow ONE record to its stable identifiers (fails open when unregistered)."""
    if not isinstance(row, dict) or identifiers is None:
        return row
    return {k: row[k] for k in identifiers if k in row and not _is_empty(row[k])}


def _project_records(key: str, value: Any) -> Any:
    """Narrow every record in a collection; the collection itself always survives.

    Handles a plain list of rows and a grouped object (``{"OMIM": [row, …]}``).
    """
    if key in _SCALAR_COLLECTIONS:
        return value
    identifiers = _ROW_IDENTIFIERS.get(key)
    if isinstance(value, list):
        return [_project_row(row, identifiers) for row in value]
    if isinstance(value, dict) and all(isinstance(v, list) for v in value.values()):
        return {g: [_project_row(row, identifiers) for row in rows] for g, rows in value.items()}
    return value


def _plain_synonyms(synonyms: Any) -> list[str]:
    """Collapse a structured-synonym list to de-duplicated plain strings."""
    out: list[str] = []
    seen: set[str] = set()
    for syn in synonyms or []:
        text = syn.get("text") if isinstance(syn, dict) else syn
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def _shape_minimal(record: dict[str, Any]) -> dict[str, Any]:
    """Anchors + every count + every POPULATED collection (rows narrowed to ids).

    Drops optional record-detail scalars (``definition``, ``obsolete``, …) and empty
    collections, so ``minimal`` is a strict subset of the default -- but it can never
    turn a record's collection into nothing.
    """
    keep = _ANCHORS | _PRESERVE_KEYS | _STRUCTURAL_KEYS
    shaped: dict[str, Any] = {}
    for key, value in record.items():
        if key in keep:
            shaped[key] = value
        elif _is_collection(value) and not _is_empty(value):
            shaped[key] = _project_records(key, value)
    return shaped


def shape_disease(record: dict[str, Any], mode: str) -> dict[str, Any]:
    """Project a disease record to the requested verbosity.

    - ``minimal``: anchors + every count + every collection (rows narrowed to ids).
    - ``compact``: drop null/empty, collapse synonyms to plain strings.
    - ``standard`` / ``full``: the complete record incl. structured synonyms.
    """
    if mode == "minimal":
        return _shape_minimal(record)
    if mode in ("standard", "full"):
        return dict(record)
    out: dict[str, Any] = {}
    for key, value in record.items():
        if key == "synonyms":
            value = _plain_synonyms(value)
        if key not in _PRESERVE_KEYS and _is_empty(value):
            continue
        out[key] = value
    return out


def validate_fields(payload: dict[str, Any], fields: list[str] | None) -> None:
    """Reject a ``fields`` projection naming a key the record does not have.

    ``fields`` is an open-world projection (its valid values are the record's own keys),
    so it cannot be a static schema ``enum``. But an unrecognised field was silently
    skipped, so ``fields=["__bogus__"]`` returned just the anchors with ``success:true``
    -- the same silent-empty class as an unrecognised filter, forbidden by
    Response-Envelope v1.1. Validate the RAW field roots against the built record's keys.
    """
    if not fields:
        return
    projectable = [k for k in payload if k not in _PRESERVE_KEYS]
    unknown = sorted({f.partition(".")[0] for f in fields} - set(projectable))
    if unknown:
        raise InvalidInputError(
            "fields references key(s) this record does not have; project its own keys only.",
            field="fields",
            allowed=sorted(projectable),
        )


def shape_hit(hit: dict[str, Any], mode: str) -> dict[str, Any]:
    """Project a search-hit row to the requested verbosity."""
    if mode == "minimal":
        return {"mondo_id": hit.get("mondo_id"), "name": hit.get("name")}
    if mode in ("standard", "full"):
        return dict(hit)
    return {k: v for k, v in hit.items() if not _is_empty(v)}


def _is_fenced(value: Any) -> bool:
    """True for a v1.1 ``untrusted_text`` object (a fenced, OPAQUE leaf).

    A fenced object is ``{kind: "untrusted_text", text, provenance, raw_sha256}``.
    The field projector must never descend into it: a projection like
    ``fields=["definition.text"]`` must return the whole wrapper, never the bare
    ``text`` stripped of ``kind``/``provenance``/``raw_sha256`` (that would defeat
    the fence). See Response-Envelope Standard v1.1.
    """
    return isinstance(value, dict) and value.get("kind") == "untrusted_text"


def select_fields(payload: dict[str, Any], fields: list[str] | None) -> dict[str, Any]:
    """Project a payload to a caller-requested sparse fieldset.

    Identity/grounding anchors (``mondo_id``, ``name``, ``mondo_version``, plus the
    preserved ``_meta``/``success``) are always retained. Supports top-level keys
    and ONE level of dotting into a grouped object -- e.g. ``"xrefs.OMIM"`` keeps
    only the OMIM group under ``xrefs``. Unknown fields are skipped (open-world).
    Returns the payload unchanged when ``fields`` is falsy.

    A fenced ``untrusted_text`` object is treated as an OPAQUE leaf: dotting into
    it (``"definition.text"``) yields the whole wrapper, never the bare ``text``
    (no fence-bypass via projection).
    """
    if not fields:
        return payload
    out: dict[str, Any] = {k: v for k, v in payload.items() if k in _FIELD_ANCHORS}
    for field in fields:
        top, _, sub = field.partition(".")
        if sub:
            container = payload.get(top)
            if _is_fenced(container):
                # Opaque leaf: never descend into a fenced object; keep the wrapper.
                out[top] = container
            elif isinstance(container, dict) and sub in container:
                nested = out.setdefault(top, {})
                if isinstance(nested, dict):
                    nested[sub] = container[sub]
        elif top in payload:
            out[top] = payload[top]
    return out


def _snippet(text: str, limit: int) -> str:
    """Truncate ``text`` to ``limit`` chars on a word boundary (adds ``…``).

    Internal whitespace (tab/LF/CR) is **preserved**, not collapsed: the snippet
    is fenced as a v1.1 ``untrusted_text`` object at the MCP boundary and its
    ``raw_sha256`` must cover the snippet's true upstream bytes. Collapsing
    whitespace first would strip tab/LF/CR the standard requires preserved and
    make the digest cover rewritten text. Only trailing whitespace at the cut is
    trimmed before the ellipsis marker.
    """
    if len(text) <= limit:
        return text
    cut = text[:limit].rstrip()
    matches = list(_WS_RUN_RE.finditer(cut))
    if matches and matches[-1].start() > 0:
        cut = cut[: matches[-1].start()]
    return cut + "…"


def shape_search_hit(
    hit: dict[str, Any], mode: str, *, snippet_chars: int = SEARCH_SNIPPET_CHARS
) -> dict[str, Any]:
    """Project a search hit, keeping the hot path token-cheap.

    - ``minimal`` / ``compact``: ``{mondo_id, name, score}`` -- compact adds a
      ``definition_snippet`` (truncated to ``snippet_chars``) when a definition
      exists, but never the full paragraph.
    - ``standard`` / ``full``: identity + score + the complete ``definition``.
    """
    out: dict[str, Any] = {
        "mondo_id": hit.get("mondo_id"),
        "name": hit.get("name"),
        "score": hit.get("score"),
    }
    definition = hit.get("definition")
    if mode in ("standard", "full"):
        if definition:
            out["definition"] = definition
    elif mode == "compact" and definition:
        out["definition_snippet"] = _snippet(definition, snippet_chars)
    return out
