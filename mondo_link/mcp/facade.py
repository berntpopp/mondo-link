"""MCP facade for mondo-link: assemble the FastMCP instance with all tools."""

from __future__ import annotations

from fastmcp import FastMCP

from mondo_link.mcp.capabilities import register_capability_resources
from mondo_link.mcp.middleware import ArgValidationMiddleware
from mondo_link.mcp.resources import MONDO_SERVER_INSTRUCTIONS
from mondo_link.mcp.tools import (
    register_batch_tools,
    register_discovery_tools,
    register_disease_tools,
    register_hierarchy_tools,
    register_xref_tools,
)


def create_mondo_mcp() -> FastMCP:
    """Build a FastMCP instance with all mondo-link tools, resources, middleware."""
    mcp = FastMCP(
        name="mondo-link",
        instructions=MONDO_SERVER_INSTRUCTIONS,
        mask_error_details=True,
    )

    register_discovery_tools(mcp)
    register_disease_tools(mcp)
    register_hierarchy_tools(mcp)
    register_xref_tools(mcp)
    register_batch_tools(mcp)
    register_capability_resources(mcp)
    mcp.add_middleware(ArgValidationMiddleware())

    return mcp
