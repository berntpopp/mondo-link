"""Guard: instruction/usage prose names only registered hierarchy tools.

The bare service-method names (get_parents, get_ancestors, ...) are NOT registered
MCP tools; the registered names are get_disease_* (capabilities.TOOLS). Prose that
uses the bare forms tells a model to call tools that don't exist.
"""

from __future__ import annotations

from mondo_link.mcp.resources import MONDO_SERVER_INSTRUCTIONS, MONDO_USAGE_NOTES

_BARE = ("get_ancestors", "get_descendants", "get_parents", "get_children")


def test_prose_uses_registered_hierarchy_tool_names() -> None:
    for prose in (MONDO_SERVER_INSTRUCTIONS, MONDO_USAGE_NOTES):
        for bare in _BARE:
            assert bare not in prose, f"prose references unregistered tool name {bare!r}"
    # the real registered names are present where hierarchy is described
    assert "get_disease_ancestors" in MONDO_SERVER_INSTRUCTIONS
    assert "get_disease_parents" in MONDO_USAGE_NOTES
