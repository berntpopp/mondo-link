"""Response-mode projection for Mondo disease payloads.

``standard`` / ``full`` are the identity (the complete record, with structured
synonyms carrying scope/type/sources). ``compact`` (the default) drops null/empty
values and collapses synonyms to plain strings. ``minimal`` keeps only the
identity anchors (``mondo_id`` + ``name``).
"""

from __future__ import annotations

from typing import Any

RESPONSE_MODES: list[str] = ["minimal", "compact", "standard", "full"]
DEFAULT_RESPONSE_MODE = "compact"

_PRESERVE_KEYS: frozenset[str] = frozenset({"_meta", "success"})

#: Identity anchors kept in ``minimal`` mode.
_MINIMAL_KEEP: frozenset[str] = frozenset({"mondo_id", "name", "_meta"})


def _is_empty(value: Any) -> bool:
    """True for the null/empty values compact mode drops."""
    return value is None or value == [] or value == "" or value == {}


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


def shape_disease(record: dict[str, Any], mode: str) -> dict[str, Any]:
    """Project a disease record to the requested verbosity.

    - ``minimal``: ``mondo_id`` + ``name`` (and any preserved keys).
    - ``compact``: drop null/empty, collapse synonyms to plain strings.
    - ``standard`` / ``full``: the complete record incl. structured synonyms.
    """
    if mode == "minimal":
        return {k: v for k, v in record.items() if k in _MINIMAL_KEEP}
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


def shape_hit(hit: dict[str, Any], mode: str) -> dict[str, Any]:
    """Project a search-hit row to the requested verbosity."""
    if mode == "minimal":
        return {"mondo_id": hit.get("mondo_id"), "name": hit.get("name")}
    if mode in ("standard", "full"):
        return dict(hit)
    return {k: v for k, v in hit.items() if not _is_empty(v)}
