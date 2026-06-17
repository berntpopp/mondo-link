"""Tool registration coverage and Tool-Naming Standard v1 compliance.

The four ``register_*`` functions together must register EXACTLY the frozen
TOOLS set, and every name must be unprefixed snake_case starting with a canonical
verb so it composes cleanly behind a namespacing gateway (mounts under ``mondo``).
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
_CANONICAL_VERBS = frozenset({"get", "search", "list", "resolve", "find", "map", "compare"})
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


async def test_tool_names_conform_to_standard_v1() -> None:
    mcp = _build_mcp()
    names = sorted(t.name for t in await mcp.list_tools())
    assert names, "no tools registered"
    for name in names:
        assert _NAME_RE.match(name), f"{name!r} must match ^[a-z0-9_]{{1,50}}$"
        assert name.split("_", 1)[0] in _CANONICAL_VERBS, (
            f"{name!r} must start with a canonical verb {sorted(_CANONICAL_VERBS)}"
        )
        assert not name.startswith(f"{_NAMESPACE}_"), (
            f"{name!r} must not self-prefix the '{_NAMESPACE}' namespace token"
        )
