"""Response-mode projection for Mondo disease payloads.

``standard`` / ``full`` are the identity (the complete record, with structured
synonyms carrying scope/type/sources). ``compact`` (the default) drops null/empty
values and collapses synonyms to plain strings. ``minimal`` keeps only the
identity anchors (``mondo_id`` + ``name``).
"""

from __future__ import annotations

import re
from typing import Any

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

#: Identity anchors kept in ``minimal`` mode.
_MINIMAL_KEEP: frozenset[str] = frozenset({"mondo_id", "name", "_meta"})

#: Identity/grounding anchors a sparse fieldset always retains.
_FIELD_ANCHORS: frozenset[str] = frozenset(
    {"mondo_id", "name", "mondo_version", "_meta", "success"}
)


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
