"""Tool registration coverage and Tool-Naming Standard v1.1 compliance.

The five ``register_*`` functions together must register EXACTLY the frozen
TOOLS set, and every name must be unprefixed snake_case starting with a ratified
verb so it composes cleanly behind a namespacing gateway (mounts under ``mondo``).

Ratified verb canon (Tool-Naming Standard v1.1, 2026-06-30):
  Tier-1 (universal read/query): get search list resolve find compare compute map
  Tier-2 (domain action/compute): predict annotate recode liftover analyze score
                                   submit export generate download
  ops/meta tag carve-out: tools tagged ``ops`` or ``meta`` skip the verb rule
    (still must match charset/length/no-self-prefix).
"""

from __future__ import annotations

import re

from fastmcp import FastMCP

from mondo_link.mcp.capabilities import TOOLS
from mondo_link.mcp.tools import (
    register_batch_tools,
    register_discovery_tools,
    register_disease_tools,
    register_hierarchy_tools,
    register_xref_tools,
)

_NAME_RE = re.compile(r"^[a-z0-9_]{1,50}$")
# Tier-1: full ratified read/query canon (Standard v1.1)
_TIER1_VERBS = frozenset({"get", "search", "list", "resolve", "find", "compare", "compute", "map"})
# Tier-2: sanctioned domain action/compute verbs (Standard v1.1)
_TIER2_VERBS = frozenset(
    {
        "predict",
        "annotate",
        "recode",
        "liftover",
        "analyze",
        "score",
        "submit",
        "export",
        "generate",
        "download",
    }
)
_CANONICAL_VERBS = _TIER1_VERBS | _TIER2_VERBS
_NAMESPACE = "mondo"


def _build_mcp() -> FastMCP:
    mcp = FastMCP(name="mondo-link-test")
    register_discovery_tools(mcp)
    register_disease_tools(mcp)
    register_hierarchy_tools(mcp)
    register_xref_tools(mcp)
    register_batch_tools(mcp)
    return mcp


async def test_registered_tools_equal_frozen_tools() -> None:
    mcp = _build_mcp()
    names = {t.name for t in await mcp.list_tools()}
    assert names == set(TOOLS)


async def test_tool_names_conform_to_standard_v1_1() -> None:
    """Every tool name must conform to Tool-Naming Standard v1.1.

    ops/meta-tagged tools skip the verb check but still must satisfy charset,
    length, and no-self-prefix rules.
    """
    mcp = _build_mcp()
    tools = await mcp.list_tools()
    assert tools, "no tools registered"
    for tool in tools:
        name = tool.name
        tags = set(tool.tags or ())
        assert _NAME_RE.match(name), f"{name!r} must match ^[a-z0-9_]{{1,50}}$"
        assert not name.startswith(f"{_NAMESPACE}_"), (
            f"{name!r} must not self-prefix the '{_NAMESPACE}' namespace token"
        )
        # ops/meta utilities are exempt from the verb rule (fleet ops carve-out).
        if "ops" in tags or "meta" in tags:
            continue
        assert name.split("_", 1)[0] in _CANONICAL_VERBS, (
            f"{name!r} must start with a canonical verb {sorted(_CANONICAL_VERBS)}"
        )


# ponytail: copy of genefoundry-router scripts/surface.py `lead_states_return` (D1,
# Tool-Schema Documentation v1.1); re-vendor if the router's rule changes.
_LEAD_VERBS = (  # noqa: SIM905 -- kept identical to the router source
    "get fetch retrieve list look lookup search find resolve report read predict annotate "
    "compute calculate count summarize summarise map translate convert describe show check "
    "validate classify score rank query browse discover explain compare diff normalize normalise "
    "expand evaluate assess enumerate identify locate export download match suggest recommend "
    "inspect generate render estimate"
).split()
_VERB_LEAD = re.compile(
    rf"^(?:use this (?:tool )?to )?(?:{'|'.join(_LEAD_VERBS)})(?:s|es|ies)?\b", re.IGNORECASE
)
_RETURNS = re.compile(
    r"(?<!not )(?<!n't )(?<!n\u2019t )(?<!never )\breturn(?:s|ed|ing)?\b(?!\s*[~<\d])|→|->",
    re.IGNORECASE,
)
_FIELD_LIST = re.compile(r"\w+\W+\w+[^:]*:\s+[^.;:]+,")
_SENTENCE_END = re.compile(r"(?<!e\.g\.)(?<!i\.e\.)(?<=[.!?])\s+")


def _lead_states_return(description: str | None) -> bool:
    text = " ".join((description or "").split())
    lead = _SENTENCE_END.split(text, maxsplit=1)[0][:300]
    return bool(_VERB_LEAD.match(lead) or _RETURNS.search(lead) or _FIELD_LIST.search(lead))


async def test_descriptions_lead_with_what_they_return() -> None:
    """D1: every tool's first sentence says what it returns."""
    tools = await _build_mcp().list_tools()
    failing = sorted(t.name for t in tools if not _lead_states_return(t.description))
    assert not failing, f"description lead must state what the tool returns: {failing}"
