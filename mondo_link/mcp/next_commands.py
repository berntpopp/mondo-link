"""Builders for `_meta.next_commands` entries: `{tool, arguments}` steps.

Wave 0 ships the envelope-facing subset (``cmd``, ``default_error_next_commands``,
``withdrawn_recovery``). Later waves add the per-tool success-path chainers as the
Mondo tools land.
"""

from __future__ import annotations

from typing import Any

from mondo_link.identifiers import infer_xref_source, looks_like_mondo_id


def cmd(tool: str, **arguments: Any) -> dict[str, Any]:
    """One ready-to-call next step."""
    return {"tool": tool, "arguments": arguments}


def widen_cmd(tool: str, base_args: dict[str, Any], total: int, ceiling: int) -> dict[str, Any]:
    """A ready-to-call step that re-runs ``tool`` with ``limit`` raised to fit."""
    return cmd(tool, **{**base_args, "limit": min(total, ceiling)})


def default_error_next_commands(
    tool: str, error_code: str, arguments: dict[str, Any]
) -> list[dict[str, Any]]:
    """A sensible recovery step for any error lacking an explicit fallback."""
    if tool in (
        "resolve_disease",
        "get_disease",
        "get_ancestors",
        "get_descendants",
        "get_parents",
        "get_children",
        "map_cross_ontology",
    ):
        value = str(arguments.get("term", "") or arguments.get("query", ""))
        source = infer_xref_source(value)
        if source:
            return [cmd("resolve_xref", xref_id=value), cmd("search_diseases", query=value)]
        if value and not looks_like_mondo_id(value):
            return [cmd("search_diseases", query=value), cmd("get_server_capabilities")]
    if tool == "resolve_xref":
        value = str(arguments.get("xref_id", ""))
        return (
            [cmd("search_diseases", query=value)] if value else [cmd("get_server_capabilities")]
        )
    if error_code == "data_unavailable":
        return [cmd("get_diagnostics")]
    return [cmd("get_server_capabilities")]


def withdrawn_recovery(replaced_by: list[dict[str, str]]) -> list[dict[str, Any]]:
    """After an obsolete-term error: chain to the successor record(s)."""
    targets = [r.get("mondo_id") for r in replaced_by if r.get("mondo_id")]
    if not targets:
        return [cmd("get_server_capabilities")]
    return [cmd("get_disease", term=t) for t in targets[:2]]
