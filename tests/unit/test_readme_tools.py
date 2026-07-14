"""The README's ``## Tools`` table must match the registered tool surface.

GeneFoundry README Standard v1, Rule 6: the table is machine-verified, not
hand-maintained. Adding, renaming or removing a tool without updating the README
fails here.

The live tool list is obtained exactly as ``test_tool_names.py`` obtains it — by
registering the real tool modules onto a FastMCP instance and listing them — so
this test cannot drift from the server's actual surface.
"""

from __future__ import annotations

import re
from pathlib import Path

from fastmcp import FastMCP

from mondo_link.mcp.tools import (
    register_batch_tools,
    register_discovery_tools,
    register_disease_tools,
    register_hierarchy_tools,
    register_xref_tools,
)

README = Path(__file__).resolve().parents[2] / "README.md"

#: A table row: ``| `tool_name` | Purpose |``
_ROW_RE = re.compile(r"^\|\s*`([a-z0-9_]+)`\s*\|")


def _build_mcp() -> FastMCP:
    mcp = FastMCP(name="mondo-link-test")
    register_discovery_tools(mcp)
    register_disease_tools(mcp)
    register_hierarchy_tools(mcp)
    register_xref_tools(mcp)
    register_batch_tools(mcp)
    return mcp


def _readme_tools_table() -> list[str]:
    """Tool names listed in the README's ``## Tools`` table, in order."""
    lines = README.read_text(encoding="utf-8").splitlines()
    try:
        start = lines.index("## Tools")
    except ValueError:  # pragma: no cover - guarded by test_readme_has_tools_table
        return []
    names: list[str] = []
    for line in lines[start + 1 :]:
        if line.startswith("## "):  # next section ends the table
            break
        match = _ROW_RE.match(line)
        if match:
            names.append(match.group(1))
    return names


def test_readme_has_a_tools_table() -> None:
    assert README.exists(), "README.md is missing"
    assert _readme_tools_table(), "README '## Tools' section has no `tool` table rows"


async def test_readme_tools_table_matches_registered_tools() -> None:
    """The table must list exactly the registered tools — no more, no fewer."""
    mcp = _build_mcp()
    registered = {tool.name for tool in await mcp.list_tools()}
    documented = _readme_tools_table()

    assert len(documented) == len(set(documented)), (
        f"README '## Tools' table lists a tool twice: {sorted(documented)}"
    )
    assert set(documented) == registered, (
        "README '## Tools' table is out of sync with the registered tools.\n"
        f"  missing from README: {sorted(registered - set(documented))}\n"
        f"  not registered:      {sorted(set(documented) - registered)}"
    )
