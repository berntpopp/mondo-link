"""Unit tests for the next_commands chaining builders.

Every step a chainer emits must reference a tool name in capabilities.TOOLS, and
every step must be the ``{tool, arguments}`` shape consumed by the envelope.
"""

from __future__ import annotations

from typing import Any

from mondo_link.mcp import next_commands as nc
from mondo_link.mcp.capabilities import TOOLS

_VALID = set(TOOLS)


def _assert_steps(steps: list[dict[str, Any]]) -> None:
    assert isinstance(steps, list)
    assert steps, "chainer must always emit at least one step"
    for step in steps:
        assert set(step) == {"tool", "arguments"}, step
        assert step["tool"] in _VALID, f"unknown tool in chain: {step['tool']}"
        assert isinstance(step["arguments"], dict)


def test_cmd_shape() -> None:
    assert nc.cmd("get_disease", term="MONDO:1") == {
        "tool": "get_disease",
        "arguments": {"term": "MONDO:1"},
    }
    assert nc.cmd("get_disease", term="MONDO:1")["tool"] in _VALID


def test_after_capabilities() -> None:
    # The discovery root chains into the canonical resolve->record workflow.
    steps = nc.after_capabilities()
    _assert_steps(steps)
    assert steps[0]["tool"] == "resolve_disease"
    assert {s["tool"] for s in steps} <= _VALID


def test_after_resolve_disease() -> None:
    _assert_steps(nc.after_resolve_disease({"mondo_id": "MONDO:1", "query": "x"}))
    assert nc.after_resolve_disease({"mondo_id": "MONDO:1"})[0]["tool"] == "get_disease"
    _assert_steps(nc.after_resolve_disease({"mondo_id": None, "query": "x"}))
    assert nc.after_resolve_disease({"query": "x"})[0]["tool"] == "search_diseases"


def test_after_search() -> None:
    _assert_steps(nc.after_search("x", {"results": [{"mondo_id": "MONDO:1"}]}))
    _assert_steps(nc.after_search("x", {"results": []}))
    widened = nc.after_search(
        "x", {"results": [{"mondo_id": "MONDO:1"}], "truncated": True, "total": 90}
    )
    _assert_steps(widened)
    assert any(c["tool"] == "search_diseases" and c["arguments"].get("limit") for c in widened)


def test_page_cmd_and_forward_paging() -> None:
    assert nc.page_cmd("search_diseases", {"query": "x"}, 25) == {
        "tool": "search_diseases",
        "arguments": {"query": "x", "offset": 25},
    }
    paged = nc.after_search(
        "x",
        {
            "results": [{"mondo_id": "MONDO:1"}],
            "truncated": True,
            "total": 90,
            "next_offset": 25,
        },
    )
    _assert_steps(paged)
    # forward-page step (offset) AND widen step (limit) are both offered
    assert any(c["arguments"].get("offset") == 25 for c in paged)
    assert any(c["arguments"].get("limit") for c in paged)


def test_forward_paging_on_closures_and_xref() -> None:
    anc = nc.after_ancestors(
        {"mondo_id": "MONDO:1", "truncated": True, "total": 500, "next_offset": 200}
    )
    assert any(c["arguments"].get("offset") == 200 for c in anc)
    xref = nc.after_resolve_xref(
        {
            "matches": [{"mondo_id": "MONDO:1"}],
            "xref_id": "OMIM:1",
            "truncated": True,
            "total": 90,
            "next_offset": 50,
        }
    )
    assert any(c["arguments"].get("offset") == 50 for c in xref)


def test_after_get_disease() -> None:
    _assert_steps(nc.after_get_disease({"mondo_id": "MONDO:1"}))
    _assert_steps(nc.after_get_disease({}))
    tools = {c["tool"] for c in nc.after_get_disease({"mondo_id": "MONDO:1"})}
    assert "get_disease_ancestors" in tools
    assert "map_cross_ontology" in tools


def test_after_ancestors_and_descendants() -> None:
    _assert_steps(nc.after_ancestors({"mondo_id": "MONDO:1"}))
    _assert_steps(nc.after_ancestors({"mondo_id": "MONDO:1", "truncated": True, "total": 500}))
    _assert_steps(nc.after_descendants({"mondo_id": "MONDO:1"}))
    _assert_steps(nc.after_descendants({"mondo_id": "MONDO:1", "truncated": True, "total": 500}))
    _assert_steps(nc.after_ancestors({}))
    _assert_steps(nc.after_descendants({}))


def test_after_parents_and_children() -> None:
    _assert_steps(nc.after_parents({"mondo_id": "MONDO:1", "parents": [{"mondo_id": "MONDO:2"}]}))
    _assert_steps(nc.after_children({"mondo_id": "MONDO:1", "children": [{"mondo_id": "MONDO:3"}]}))
    _assert_steps(nc.after_parents({"mondo_id": "MONDO:1", "parents": []}))
    _assert_steps(nc.after_children({"mondo_id": "MONDO:1", "children": []}))
    _assert_steps(nc.after_parents({}))
    _assert_steps(nc.after_children({}))


def test_after_resolve_xref_and_cross_ontology() -> None:
    _assert_steps(
        nc.after_resolve_xref({"matches": [{"mondo_id": "MONDO:1"}], "xref_id": "OMIM:1"})
    )
    _assert_steps(nc.after_resolve_xref({"matches": [], "xref_id": "OMIM:1"}))
    _assert_steps(
        nc.after_resolve_xref(
            {
                "matches": [{"mondo_id": "MONDO:1"}],
                "xref_id": "OMIM:1",
                "truncated": True,
                "total": 90,
            }
        )
    )
    _assert_steps(nc.after_cross_ontology({"mondo_id": "MONDO:1"}))
    _assert_steps(nc.after_cross_ontology({}))


def test_default_error_next_commands() -> None:
    _assert_steps(
        nc.default_error_next_commands("resolve_disease", "not_found", {"query": "Marfan"})
    )
    _assert_steps(
        nc.default_error_next_commands("resolve_xref", "not_found", {"xref_id": "OMIM:1"})
    )
    _assert_steps(nc.default_error_next_commands("get_disease", "data_unavailable", {}))
    _assert_steps(
        nc.default_error_next_commands("get_disease", "not_found", {"term": "OMIM:182212"})
    )


def test_after_batch_chainers() -> None:
    resolved = {"results": [{"ok": False}, {"ok": True, "mondo_id": "MONDO:1"}]}
    _assert_steps(nc.after_resolve_batch(resolved))
    assert nc.after_resolve_batch(resolved)[0] == nc.cmd("get_disease", term="MONDO:1")
    _assert_steps(nc.after_get_disease_batch(resolved))
    assert nc.after_get_disease_batch(resolved)[0] == nc.cmd("map_cross_ontology", term="MONDO:1")
    # all-failed batch falls back to a safe discovery step
    _assert_steps(nc.after_resolve_batch({"results": [{"ok": False}]}))
    _assert_steps(nc.after_get_disease_batch({"results": []}))


def test_withdrawn_recovery() -> None:
    _assert_steps(nc.withdrawn_recovery([{"mondo_id": "MONDO:2"}]))
    assert nc.withdrawn_recovery([{"mondo_id": "MONDO:2"}])[0] == nc.cmd(
        "get_disease", term="MONDO:2"
    )
    _assert_steps(nc.withdrawn_recovery([]))


def test_hierarchy_tool_error_recovery_uses_registered_names() -> None:
    # Regression: default_error_next_commands listed the bare service-method names
    # (get_ancestors/...) instead of the registered tool names (get_disease_*), so an
    # error from a hierarchy tool fell through to the generic get_server_capabilities
    # step instead of the xref/search recovery. An xref-looking term must route to
    # resolve_xref (its source is inferable) for every hierarchy tool.
    for tool in (
        "get_disease_ancestors",
        "get_disease_descendants",
        "get_disease_parents",
        "get_disease_children",
    ):
        steps = nc.default_error_next_commands(tool, "not_found", {"term": "OMIM:182212"})
        _assert_steps(steps)
        assert steps[0]["tool"] == "resolve_xref", (tool, steps)
