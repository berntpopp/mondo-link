"""Unit tests for the mondo-link capabilities discovery surface."""

from __future__ import annotations

from mondo_link.mcp import capabilities as cap

_ERROR_CODES = [
    "invalid_input",
    "not_found",
    "ambiguous_query",
    "data_unavailable",
    "rate_limited",
    "upstream_unavailable",
    "internal_error",
]


def test_tools_list_has_11_unique_names() -> None:
    assert len(cap.TOOLS) == 11
    assert len(set(cap.TOOLS)) == 11


def test_build_capabilities_core_keys_present() -> None:
    payload = cap.build_capabilities()
    for key in (
        "server",
        "server_version",
        "mondo_version",
        "data_source",
        "recommended_citation",
        "license",
        "research_use_only",
        "research_use_notice",
        "tools",
        "tool_count",
        "response_modes",
        "default_response_mode",
        "match_types",
        "xref_prefixes",
        "predicate_rank",
        "error_codes",
        "limits",
        "read_only",
    ):
        assert key in payload, f"missing capability key: {key}"
    assert payload["server"] == "mondo-link"
    assert payload["tool_count"] == len(cap.TOOLS)
    assert payload["research_use_only"] is True
    assert payload["read_only"] is True
    assert payload["default_response_mode"] == "compact"


def test_error_codes_are_the_seven_code_taxonomy() -> None:
    assert cap.build_capabilities()["error_codes"] == _ERROR_CODES


def test_project_capabilities_summary_vs_full() -> None:
    full = cap.project_capabilities(
        "full", tool_signatures={"resolve_disease": "resolve_disease(query)"}
    )
    assert full["detail"] == "full"
    summary = cap.project_capabilities("summary", tool_signatures={})
    assert summary["detail"] == "summary"
    # summary is a subset of full
    assert set(summary) <= set(full) | {"more"}
