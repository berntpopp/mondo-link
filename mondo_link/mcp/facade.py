"""MCP facade for mondo-link: assemble the FastMCP instance with all tools."""

from __future__ import annotations

from fastmcp import FastMCP

from mondo_link import __version__
from mondo_link.mcp.capabilities import register_capability_resources
from mondo_link.mcp.log_filters import install_external_error_filter
from mondo_link.mcp.middleware import (
    ArgValidationMiddleware,
    install_protocol_error_handler,
    install_span_exception_redactor,
)
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
    install_span_exception_redactor()
    mcp = FastMCP(
        name="mondo-link",
        version=__version__,
        instructions=MONDO_SERVER_INSTRUCTIONS,
        mask_error_details=True,
    )
    # FastMCP configures its own non-propagating RichHandlers, which bypass a root-only
    # scrub filter -- attach the Layer-5 filter to every SOURCE logger (incl. root,
    # ``mcp.shared.session``, and FastMCP's own handlers) now that they exist, so the
    # pre-middleware reflection of the caller name/URI never reaches a log sink.
    install_external_error_filter()

    register_discovery_tools(mcp)
    register_disease_tools(mcp)
    register_hierarchy_tools(mcp)
    register_xref_tools(mcp)
    register_batch_tools(mcp)
    register_capability_resources(mcp)
    mcp.add_middleware(ArgValidationMiddleware())

    # Layer-3 protocol backstop: wrap the raw tool/resource/prompt request handlers as
    # the OUTERMOST guard so FastMCP core cannot reflect a caller-supplied name/URI/
    # prompt name (nor its code points) in a not-found JSON-RPC error frame. Installed
    # last, after all handlers exist.
    install_protocol_error_handler(mcp)

    return mcp
