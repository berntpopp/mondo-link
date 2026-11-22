"""Disease lookup tools: resolve_disease, search_diseases, get_disease."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

from fastmcp.tools import ToolResult
from pydantic import Field

from mondo_link.mcp.annotations import READ_ONLY_OPEN_WORLD
from mondo_link.mcp.envelope import McpErrorContext, run_mcp_tool
from mondo_link.mcp.next_commands import (
    NO_GENE_NOTE,
    NO_GENE_NOTE_WITH_STEP,
    after_get_disease,
    after_resolve_disease,
    after_search,
    no_gene_steps,
)
from mondo_link.mcp.service_adapters import get_mondo_service
from mondo_link.mcp.tools._common import (
    FieldsArg,
    QueryStr,
    ResponseMode,
    TermStr,
    needs_absence_note,
)
from mondo_link.services.mondo_service import DESCENDANT_GENE_CAP

if TYPE_CHECKING:
    from fastmcp import FastMCP


#: Honest scope of ``causal_genes`` (issue #127), shared by both tool descriptions.
_GENES_NOTE = (
    "causal_genes [{hgnc_id, symbol}] are Mondo's has_material_basis_in_germline_"
    "mutation_in assertions for mostly monogenic diseases, not a gene-disease validity "
    "curation (use GenCC/ClinGen for that); next_commands chain to hgnc_resolve_symbol "
    "for the gene's band and details. A term with no gene of its own lists its "
    f"descendants' genes (at most {DESCENDANT_GENE_CAP}), each with "
    "from_descendant=<child MONDO id>. "
    "causal_genes: [] means Mondo asserts none; the "
    "field is absent only when the loaded data predates the gene table."
)


def _no_gene_followup(payload: dict[str, Any], response_mode: str) -> list[dict[str, Any]]:
    """``causal_genes: []``: add the typed absence note; return the gene-lookup steps."""
    if not needs_absence_note(payload, response_mode):
        return []
    steps = no_gene_steps(get_mondo_service().gene_lookup_ids(payload["mondo_id"]))
    payload["absence_note"] = NO_GENE_NOTE_WITH_STEP if steps else NO_GENE_NOTE
    return steps


def register_disease_tools(mcp: FastMCP) -> None:
    """Register the disease lookup/search tools on a FastMCP instance."""

    @mcp.tool(
        name="resolve_disease",
        title="Resolve Disease",
        annotations=READ_ONLY_OPEN_WORLD,
        output_schema=None,  # Tool-Surface Budget v1 B2 (see tools/__init__.py)
        tags={"disease", "resolve"},
        description=(
            "Resolve a disease label, synonym, MONDO id, or external cross-reference "
            "CURIE (OMIM/Orphanet/DOID/...) to the canonical Mondo term "
            "{mondo_id, name, match_type} and germline causal genes (Mondo assertion). "
            f"{_GENES_NOTE} A near-miss or acronym-like label falls "
            "back to a conservative fuzzy match (match_type='fuzzy'). The strongest "
            "match type wins (primary label > exact synonym > related synonym); only "
            "a tie returns ambiguous_query with candidates, each carrying match_type "
            "and score; an obsolete id returns "
            "not_found with its successor. "
            "Signature: resolve_disease(query, response_mode=)."
        ),
    )
    async def resolve_disease(
        query: QueryStr, response_mode: ResponseMode = "compact"
    ) -> dict[str, Any] | ToolResult:
        async def call() -> dict[str, Any]:
            payload = get_mondo_service().resolve_disease(query, response_mode=response_mode)
            payload.setdefault("_meta", {})["next_commands"] = [
                *after_resolve_disease(payload),
                *_no_gene_followup(payload, response_mode),
            ]
            return payload

        return await run_mcp_tool(
            "resolve_disease",
            call,
            context=McpErrorContext(
                "resolve_disease", arguments={"query": query}, response_mode=response_mode
            ),
        )

    @mcp.tool(
        name="search_diseases",
        title="Search Diseases",
        annotations=READ_ONLY_OPEN_WORLD,
        output_schema=None,  # Tool-Surface Budget v1 B2 (see tools/__init__.py)
        tags={"disease", "search"},
        description=(
            "Full-text search over Mondo disease names, synonyms, and definitions "
            "(FTS, relevance-ranked); returns {mondo_id, name, score} -- compact adds "
            "a short definition_snippet; standard/full add the complete definition -- "
            "plus a pagination block {total, returned, limit, offset, truncated, "
            "next_offset}. When truncated, next_commands carries a forward-page step "
            "(offset advanced) and a widen step. Obsolete terms are excluded unless "
            "include_obsolete=true. "
            "Signature: search_diseases(query, limit=, offset=, include_obsolete=, response_mode=)."
        ),
    )
    async def search_diseases(
        query: QueryStr,
        limit: Annotated[int, Field(ge=1, le=200, description="Max hits (default 25).")] = 25,
        offset: Annotated[
            int, Field(ge=0, description="Rows to skip for forward paging (default 0).")
        ] = 0,
        include_obsolete: Annotated[
            bool, Field(description="Include obsolete terms (default false).")
        ] = False,
        response_mode: ResponseMode = "compact",
    ) -> dict[str, Any] | ToolResult:
        async def call() -> dict[str, Any]:
            payload = get_mondo_service().search_diseases(
                query,
                limit=limit,
                offset=offset,
                include_obsolete=include_obsolete,
                response_mode=response_mode,
            )
            payload.setdefault("_meta", {})["next_commands"] = after_search(query, payload)
            return payload

        return await run_mcp_tool(
            "search_diseases",
            call,
            context=McpErrorContext(
                "search_diseases", arguments={"query": query}, response_mode=response_mode
            ),
        )

    @mcp.tool(
        name="get_disease",
        title="Get Disease",
        annotations=READ_ONLY_OPEN_WORLD,
        output_schema=None,  # Tool-Surface Budget v1 B2 (see tools/__init__.py)
        tags={"disease"},
        description=(
            "Return a Mondo disease term: definition, synonyms, grouped "
            "cross-references, direct parents and children, top-level groupings, "
            "subsets, obsolescence (replaced_by/consider), and germline causal genes "
            f"(Mondo assertion). {_GENES_NOTE} The term accepts a "
            "MONDO id, a label/synonym, or an external xref CURIE (resolved first). "
            "Pass fields=['xrefs.OMIM', ...] for a sparse projection. "
            "Signature: get_disease(term, response_mode=, fields=)."
        ),
    )
    async def get_disease(
        term: TermStr,
        response_mode: ResponseMode = "compact",
        fields: FieldsArg = None,
    ) -> dict[str, Any] | ToolResult:
        async def call() -> dict[str, Any]:
            payload = get_mondo_service().get_disease(
                term, response_mode=response_mode, fields=fields
            )
            payload.setdefault("_meta", {})["next_commands"] = [
                *after_get_disease(payload),
                *_no_gene_followup(payload, response_mode),
            ]
            return payload

        return await run_mcp_tool(
            "get_disease",
            call,
            context=McpErrorContext(
                "get_disease", arguments={"term": term}, response_mode=response_mode
            ),
        )
