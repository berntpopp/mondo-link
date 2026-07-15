"""Mondo MCP tool registration functions (one register_* per domain module).

Why every tool sets ``output_schema=None``
------------------------------------------
A tool definition is not a one-off cost paid at connect time: it sits in the model's
system-prompt prefix and is re-sent on EVERY request for the life of the session,
whether or not the tool is ever called. mondo-link advertised 13 tools costing ~7,282
tokens, of which **43% was ``outputSchema``** -- a field the MCP spec makes OPTIONAL,
that clients are only ever *recommended* to validate against, and that no model reads.
Tool-Surface Budget Standard v1 (B2) caps a server at 10,000 tokens and names
``outputSchema`` as the first thing to cut.

``output_schema=None`` SUPPRESSES it (``NotSet``, the default, would auto-infer one from
the return annotation). **``structuredContent`` is NOT lost**: with no output schema
FastMCP still emits it whenever the return value serialises to a JSON object, and every
tool here returns the dict envelope Response-Envelope Standard v1 mandates -- there is no
bare-list tool to trip the one hard constraint. The error path returns a ``ToolResult``
that carries its ``structured_content`` (and ``isError``) explicitly, unaffected either way.

Descriptions and parameter documentation are what the model actually reads and are NEVER
cut to meet the budget. Only the schema nobody reads is. Suppressing it also retired two
live defects: ``get_diagnostics``' declared ``outputSchema`` named six properties the
server never returns (``term_count``/``obsolete_count``/``xref_count``/``mapping_count``/
``data_available``/``built_utc``), and ``resolve_disease`` declared a ``definition`` it did
not return -- a schema that is not published cannot lie.
"""

from __future__ import annotations

from mondo_link.mcp.tools.batch import register_batch_tools
from mondo_link.mcp.tools.discovery import register_discovery_tools
from mondo_link.mcp.tools.diseases import register_disease_tools
from mondo_link.mcp.tools.hierarchy import register_hierarchy_tools
from mondo_link.mcp.tools.xref import register_xref_tools

__all__ = [
    "register_batch_tools",
    "register_discovery_tools",
    "register_disease_tools",
    "register_hierarchy_tools",
    "register_xref_tools",
]
