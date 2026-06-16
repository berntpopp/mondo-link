"""Discovery tools: get_server_capabilities, get_diagnostics."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import Field

from mondo_link.buildinfo import build_info
from mondo_link.mcp.annotations import READ_ONLY_OPEN_WORLD
from mondo_link.mcp.capabilities import collect_tool_signatures, project_capabilities
from mondo_link.mcp.envelope import McpErrorContext, run_mcp_tool
from mondo_link.mcp.next_commands import cmd
from mondo_link.mcp.schemas import CAPABILITIES_SCHEMA, DIAGNOSTICS_SCHEMA
from mondo_link.mcp.service_adapters import get_mondo_service

if TYPE_CHECKING:
    from fastmcp import FastMCP


def register_discovery_tools(mcp: FastMCP) -> None:
    """Register the discovery tools on a FastMCP instance."""

    @mcp.tool(
        name="get_server_capabilities",
        title="Get Server Capabilities",
        annotations=READ_ONLY_OPEN_WORLD,
        output_schema=CAPABILITIES_SCHEMA,
        tags={"discovery"},
        description=(
            "Return the mondo-link discovery surface: identity/build/Mondo release, "
            "the tool list WITH call signatures, response modes, recommended "
            "workflows, the cross-reference predicate ranking, the error taxonomy, and "
            "limits. detail='full' adds the full policy notes. Call this first in a "
            "cold session, or read mondo://tools / mondo://capabilities. "
            "Signature: get_server_capabilities(detail=)."
        ),
    )
    async def get_server_capabilities(
        detail: Annotated[
            Literal["summary", "full"],
            Field(description="summary (default, light) or full (adds policy notes)."),
        ] = "summary",
    ) -> dict[str, Any]:
        async def call() -> dict[str, Any]:
            signatures = await collect_tool_signatures(mcp)
            return project_capabilities(detail, signatures)

        return await run_mcp_tool(
            "get_server_capabilities",
            call,
            context=McpErrorContext("get_server_capabilities"),
        )

    @mcp.tool(
        name="get_diagnostics",
        title="Get Mondo Diagnostics",
        annotations=READ_ONLY_OPEN_WORLD,
        output_schema=DIAGNOSTICS_SCHEMA,
        tags={"discovery"},
        description=(
            "Report the local Mondo index status: whether the data is built, the "
            "loaded Mondo release version, term/obsolete/closure/xref/mapping counts, "
            "schema version, and when it was built. Use this to confirm freshness or "
            "diagnose a data_unavailable error. "
            "Signature: get_diagnostics()."
        ),
    )
    async def get_diagnostics() -> dict[str, Any]:
        async def call() -> dict[str, Any]:
            payload = get_mondo_service().get_diagnostics()
            payload["build"] = build_info()
            payload.setdefault("_meta", {})["next_commands"] = (
                [cmd("resolve_disease", query="Marfan syndrome")]
                if payload.get("index_built")
                else [cmd("get_server_capabilities")]
            )
            return payload

        return await run_mcp_tool(
            "get_diagnostics",
            call,
            context=McpErrorContext("get_diagnostics"),
        )
