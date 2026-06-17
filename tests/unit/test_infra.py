"""Pure-unit coverage for the envelope taxonomy, arg_help, logging, buildinfo."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from mondo_link.buildinfo import build_info
from mondo_link.exceptions import (
    AmbiguousQueryError,
    DataUnavailableError,
    DownloadError,
    InvalidInputError,
    NotFoundError,
    RateLimitError,
    ServiceUnavailableError,
    WithdrawnEntryError,
)
from mondo_link.logging_config import configure_logging
from mondo_link.mcp.arg_help import (
    describe_constraints,
    did_you_mean,
    normalize_alias_args,
    tool_signature,
)
from mondo_link.mcp.envelope import (
    McpErrorContext,
    McpToolError,
    build_arg_error_envelope,
    classify_exception,
    run_mcp_tool,
)


def _raiser(exc: BaseException) -> Callable[[], Awaitable[dict[str, Any]]]:
    async def call() -> dict[str, Any]:
        raise exc

    return call


async def _run(exc: BaseException) -> dict[str, Any]:
    return await run_mcp_tool(
        "get_disease", _raiser(exc), context=McpErrorContext("get_disease", arguments={"term": "x"})
    )


# --- envelope: success ------------------------------------------------------


async def test_success_envelope_injects_meta() -> None:
    async def call() -> dict[str, Any]:
        return {"mondo_id": "MONDO:0008426"}

    result = await run_mcp_tool("get_disease", call, context=McpErrorContext("get_disease"))
    assert result["success"] is True
    assert result["_meta"]["tool"] == "get_disease"
    assert isinstance(result["_meta"]["elapsed_ms"], int)
    assert "request_id" in result["_meta"]


# --- envelope: full 7-code taxonomy ----------------------------------------


async def test_not_found_classification() -> None:
    result = await _run(NotFoundError("nope"))
    assert result["error_code"] == "not_found"
    assert result["recovery_action"] == "reformulate_input"
    assert result["retryable"] is False
    assert result["_meta"]["next_commands"]


async def test_withdrawn_entry_surfaces_replacement() -> None:
    exc = WithdrawnEntryError(
        "MONDO:0099999",
        status="obsolete",
        replaced_by=[{"mondo_id": "MONDO:0008426", "name": "SGS"}],
    )
    result = await _run(exc)
    assert result["error_code"] == "not_found"
    assert result["obsolete"] is True
    assert result["withdrawn_status"] == "obsolete"
    assert result["replaced_by"][0]["mondo_id"] == "MONDO:0008426"
    assert result["_meta"]["next_commands"][0]["tool"] == "get_disease"


async def test_ambiguous_query_surfaces_candidates() -> None:
    exc = AmbiguousQueryError(
        "ambiguous", candidates=[{"mondo_id": "MONDO:1"}, {"mondo_id": "MONDO:2"}]
    )
    result = await _run(exc)
    assert result["error_code"] == "ambiguous_query"
    assert len(result["candidates"]) == 2
    assert result["_meta"]["next_commands"][0]["tool"] == "get_disease"


async def test_invalid_input_surfaces_field_and_allowed() -> None:
    exc = InvalidInputError("bad", "query", allowed=["a", "b"], hint="get_disease(term)")
    result = await _run(exc)
    assert result["error_code"] == "invalid_input"
    assert result["field"] == "query"
    assert result["allowed_values"] == ["a", "b"]
    assert result["hint"] == "get_disease(term)"


async def test_data_unavailable_is_retryable() -> None:
    result = await _run(DataUnavailableError())
    assert result["error_code"] == "data_unavailable"
    assert result["retryable"] is True
    assert result["recovery_action"] == "retry_backoff"


async def test_rate_limited_classification() -> None:
    result = await _run(RateLimitError())
    assert result["error_code"] == "rate_limited"
    assert result["retryable"] is True


async def test_upstream_unavailable_from_service_and_download() -> None:
    assert (await _run(ServiceUnavailableError()))["error_code"] == "upstream_unavailable"
    assert (await _run(DownloadError("net")))["error_code"] == "upstream_unavailable"


async def test_internal_error_for_unclassified() -> None:
    result = await _run(ValueError("boom"))
    assert result["error_code"] == "internal_error"
    assert result["recovery_action"] == "switch_tool"


async def test_mcp_tool_error_custom_code() -> None:
    result = await _run(McpToolError(error_code="data_unavailable", message="cold"))
    assert result["error_code"] == "data_unavailable"
    assert result["message"] == "cold"


def test_classify_exception_maps_typed_errors() -> None:
    # Public per-item classifier used by the batch tools (error_code, safe message).
    assert classify_exception(NotFoundError("x"))[0] == "not_found"
    assert classify_exception(AmbiguousQueryError("y"))[0] == "ambiguous_query"
    assert classify_exception(InvalidInputError("z", "field"))[0] == "invalid_input"
    code, message = classify_exception(ValueError("boom"))
    assert code == "internal_error"
    assert "boom" not in message  # client-safe: internal detail is not leaked


# --- build_arg_error_envelope ----------------------------------------------


def test_arg_error_unexpected_keyword_with_suggestion() -> None:
    env = build_arg_error_envelope(
        tool_name="get_disease",
        loc="termm",
        error_type="unexpected_keyword_argument",
        valid_params=["term", "response_mode"],
        signature="get_disease(term, response_mode=)",
        suggestion="term",
    )
    assert env["error_code"] == "invalid_input"
    assert env["allowed_values"] == ["term", "response_mode"]
    assert env["hint"] == "get_disease(term, response_mode=)"
    assert "Did you mean" in env["message"]


def test_arg_error_missing_argument() -> None:
    env = build_arg_error_envelope(
        tool_name="get_disease",
        loc="term",
        error_type="missing_argument",
        valid_params=["term"],
        signature="get_disease(term)",
        suggestion=None,
    )
    assert "Missing required argument" in env["message"]


def test_arg_error_with_constraints() -> None:
    env = build_arg_error_envelope(
        tool_name="search_diseases",
        loc="limit",
        error_type="invalid_value",
        valid_params=["query", "limit"],
        signature="search_diseases(query, limit=)",
        suggestion=None,
        constraints=(["1..200"], "must be between 1 and 200"),
    )
    assert env["allowed_values"] == ["1..200"]
    assert "must be between" in env["message"]


# --- arg_help ---------------------------------------------------------------


def test_normalize_alias_rewrites_when_canonical_valid() -> None:
    args, applied = normalize_alias_args(["query"], {"disease": "SGS"})
    assert args == {"query": "SGS"}
    assert applied == [("disease", "query")]


def test_normalize_alias_explicit_canonical_wins() -> None:
    args, applied = normalize_alias_args(["query"], {"query": "A", "disease": "B"})
    assert args == {"query": "A"}
    assert applied == []


def test_normalize_alias_skips_when_canonical_not_a_param() -> None:
    # get_disease's canonical is 'term', not 'query'; 'disease'->'query' must NOT apply.
    args, applied = normalize_alias_args(["term"], {"disease": "X"})
    assert args == {"disease": "X"}
    assert applied == []


def test_did_you_mean_alias_then_fuzzy_then_none() -> None:
    assert did_you_mean("disease", ["query"]) == "query"
    assert did_you_mean("queryy", ["query"]) == "query"
    assert did_you_mean("zzz", ["query"]) is None


def test_describe_constraints_variants() -> None:
    assert describe_constraints({"enum": ["a", "b"]}) == (["a", "b"], "must be one of: a, b")
    rng = describe_constraints({"anyOf": [{"minimum": 1, "maximum": 200}]})
    assert rng is not None and rng[0] == ["1..200"]
    items = describe_constraints({"minItems": 1, "maxItems": 5})
    assert items is not None and "items" in items[1]
    assert describe_constraints({"type": "string"}) is None


def test_tool_signature_orders_required_first() -> None:
    sig = tool_signature(
        "get_disease",
        {"properties": {"term": {}, "response_mode": {}}, "required": ["term"]},
    )
    assert sig == "get_disease(term, response_mode=)"


# --- logging / buildinfo ----------------------------------------------------


def test_configure_logging_returns_usable_logger() -> None:
    logger = configure_logging()
    logger.info("infra_test_event", k="v")


def test_build_info_has_version() -> None:
    info = build_info()
    assert "version" in info
