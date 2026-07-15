"""Cross-reference tools: resolve_xref (external -> Mondo), map_cross_ontology."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

from pydantic import Field

from mondo_link.mcp.annotations import READ_ONLY_OPEN_WORLD
from mondo_link.mcp.envelope import McpErrorContext, run_mcp_tool
from mondo_link.mcp.next_commands import after_cross_ontology, after_resolve_xref
from mondo_link.mcp.service_adapters import get_mondo_service
from mondo_link.mcp.tools._common import PrefixesArg, ResponseMode, TermStr, XrefIdStr

if TYPE_CHECKING:
    from fastmcp import FastMCP


def register_xref_tools(mcp: FastMCP) -> None:
    """Register the cross-reference tools on a FastMCP instance."""

    @mcp.tool(
        name="resolve_xref",
        title="Resolve Cross-Reference",
        annotations=READ_ONLY_OPEN_WORLD,
        output_schema=None,  # Tool-Surface Budget v1 B2 (see tools/__init__.py)
        tags={"xref", "resolve"},
        description=(
            "Resolve an external cross-reference CURIE (OMIM/Orphanet/DOID/NCIT/UMLS/"
            "MeSH/MedGen/SNOMED/GARD) back to the Mondo term(s) that map to it, ranked "
            "by mapping predicate (exactMatch > equivalentTo > closeMatch > ...). "
            "Returns matches[] plus a pagination block {total, returned, limit, "
            "offset, truncated, next_offset}; when truncated, next_commands carries a "
            "forward-page step (offset). "
            "Signature: resolve_xref(xref_id, limit=, offset=, response_mode=)."
        ),
    )
    async def resolve_xref(
        xref_id: XrefIdStr,
        limit: Annotated[int, Field(ge=1, le=1000, description="Max matches (default 50).")] = 50,
        offset: Annotated[
            int, Field(ge=0, description="Rows to skip for forward paging (default 0).")
        ] = 0,
        response_mode: ResponseMode = "compact",
    ) -> dict[str, Any]:
        async def call() -> dict[str, Any]:
            payload = get_mondo_service().resolve_xref(
                xref_id, limit=limit, offset=offset, response_mode=response_mode
            )
            payload.setdefault("_meta", {})["next_commands"] = after_resolve_xref(payload)
            return payload

        return await run_mcp_tool(
            "resolve_xref",
            call,
            context=McpErrorContext(
                "resolve_xref", arguments={"xref_id": xref_id}, response_mode=response_mode
            ),
        )

    @mcp.tool(
        name="map_cross_ontology",
        title="Map Cross-Ontology",
        annotations=READ_ONLY_OPEN_WORLD,
        output_schema=None,  # Tool-Surface Budget v1 B2 (see tools/__init__.py)
        tags={"xref"},
        description=(
            "List a Mondo term's cross-references to other ontologies, grouped by target "
            "prefix. get_disease surfaces every source; this tool's `prefixes` filter is "
            "the first-class set (OMIM/ORPHA/DOID/NCIT/UMLS/MESH/MEDGEN/SCTID/GARD), each "
            "with its mapping predicate and origin (obo_xref|sssom). An unrecognised prefix "
            "is rejected with invalid_input. "
            "Signature: map_cross_ontology(term, prefixes=, response_mode=)."
        ),
    )
    async def map_cross_ontology(
        term: TermStr,
        prefixes: PrefixesArg = None,
        response_mode: ResponseMode = "compact",
    ) -> dict[str, Any]:
        async def call() -> dict[str, Any]:
            payload = get_mondo_service().map_cross_ontology(
                term, prefixes=prefixes, response_mode=response_mode
            )
            payload.setdefault("_meta", {})["next_commands"] = after_cross_ontology(payload)
            return payload

        return await run_mcp_tool(
            "map_cross_ontology",
            call,
            context=McpErrorContext(
                "map_cross_ontology", arguments={"term": term}, response_mode=response_mode
            ),
        )
