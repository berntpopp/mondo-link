"""JSON output schemas for the typed Mondo MCP tools (MCP structured output).

The schemas are deliberately **permissive** (``additionalProperties: true``,
nothing ``required``) because ``response_mode`` projects fields out and the error
envelope is returned by the same tool body and must also validate.
"""

from __future__ import annotations

from typing import Any

_META = {"type": "object", "additionalProperties": True}


def _envelope(**properties: Any) -> dict[str, Any]:
    """A permissive object schema carrying the common envelope keys + extras."""
    props: dict[str, Any] = {
        "success": {"type": "boolean"},
        "_meta": _META,
        "error_code": {"type": "string"},
        "message": {"type": "string"},
        "retryable": {"type": "boolean"},
        "recovery_action": {"type": "string"},
        "field": {"type": "string"},
        "allowed_values": {"type": "array"},
        "hint": {"type": "string"},
        "candidates": {"type": "array"},
        **properties,
    }
    return {"type": "object", "additionalProperties": True, "properties": props}


_STR = {"type": "string"}
_STR_NULL = {"type": ["string", "null"]}
_INT = {"type": "integer"}
_BOOL = {"type": "boolean"}
_ARR = {"type": "array"}
_OBJ = {"type": "object", "additionalProperties": True}

CAPABILITIES_SCHEMA = _envelope(
    server=_STR,
    server_version=_STR,
    mondo_version=_STR,
    tools=_ARR,
    response_modes=_ARR,
    error_codes=_ARR,
)

DIAGNOSTICS_SCHEMA = _envelope(
    data_available=_BOOL,
    mondo_version=_STR_NULL,
    term_count=_INT,
    obsolete_count=_INT,
    xref_count=_INT,
    mapping_count=_INT,
    schema_version=_INT,
    built_utc=_STR,
    build=_OBJ,
)

RESOLVE_DISEASE_SCHEMA = _envelope(
    query=_STR,
    mondo_id=_STR_NULL,
    name=_STR_NULL,
    definition=_STR_NULL,
    match_type=_STR_NULL,
    obsolete=_BOOL,
    mondo_version=_STR_NULL,
)

SEARCH_SCHEMA = _envelope(
    query=_STR,
    include_obsolete=_BOOL,
    total=_INT,
    returned=_INT,
    limit=_INT,
    truncated=_BOOL,
    results=_ARR,
)

DISEASE_SCHEMA = _envelope(
    mondo_id=_STR,
    name=_STR,
    definition=_STR_NULL,
    synonyms=_ARR,
    xrefs=_ARR,
    parents=_ARR,
    children=_ARR,
    obsolete=_BOOL,
    match_type=_STR_NULL,
    mondo_version=_STR_NULL,
)

ANCESTORS_SCHEMA = _envelope(
    mondo_id=_STR,
    name=_STR_NULL,
    total=_INT,
    returned=_INT,
    limit=_INT,
    truncated=_BOOL,
    ancestors=_ARR,
)

DESCENDANTS_SCHEMA = _envelope(
    mondo_id=_STR,
    name=_STR_NULL,
    total=_INT,
    returned=_INT,
    limit=_INT,
    truncated=_BOOL,
    descendants=_ARR,
)

PARENTS_SCHEMA = _envelope(
    mondo_id=_STR,
    name=_STR_NULL,
    count=_INT,
    parents=_ARR,
)

CHILDREN_SCHEMA = _envelope(
    mondo_id=_STR,
    name=_STR_NULL,
    count=_INT,
    children=_ARR,
)

RESOLVE_XREF_SCHEMA = _envelope(
    xref_id=_STR,
    prefix=_STR_NULL,
    total=_INT,
    returned=_INT,
    limit=_INT,
    truncated=_BOOL,
    matches=_ARR,
)

CROSS_ONTOLOGY_SCHEMA = _envelope(
    mondo_id=_STR,
    name=_STR_NULL,
    prefixes=_ARR,
    count=_INT,
    mappings=_ARR,
)
