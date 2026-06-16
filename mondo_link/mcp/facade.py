"""MCP facade for mondo-link.

Wave 0 constructs the FastMCP instance with the server instructions and the
argument-validation middleware but registers no tools/resources yet — Wave 1C/2
add the registrations.
"""

from __future__ import annotations

from fastmcp import FastMCP

from mondo_link.mcp.middleware import ArgValidationMiddleware
from mondo_link.mcp.resources import MONDO_SERVER_INSTRUCTIONS


def create_mondo_mcp() -> FastMCP:
    """Build a FastMCP instance for mondo-link (no tools registered yet)."""
    mcp = FastMCP(
        name="mondo-link",
        instructions=MONDO_SERVER_INSTRUCTIONS,
        mask_error_details=True,
    )

    mcp.add_middleware(ArgValidationMiddleware())

    return mcp
