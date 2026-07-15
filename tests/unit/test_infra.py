"""Pure-unit coverage for the envelope taxonomy, arg_help, logging, buildinfo."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, cast

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
    describe_type_expectation,
    did_you_mean,
    normalize_alias_args,
    tool_signature,
)
from mondo_link.mcp.envelope import (
    _FIXED_MESSAGES,
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
    result = await run_mcp_tool(
        "get_disease", _raiser(exc), context=McpErrorContext("get_disease", arguments={"term": "x"})
    )
    # The error path now returns a ToolResult (carrying MCP isError:true, Response-
    # Envelope v1); read its structured envelope. The success path still returns a dict.
    if isinstance(result, dict):
        return result
    assert result.is_error is True
    return cast("dict[str, Any]", result.structured_content)


# --- envelope: success ------------------------------------------------------


async def test_success_meta_tiers_by_response_mode() -> None:
    async def call() -> dict[str, Any]:
        return {"mondo_id": "MONDO:0008426"}

    # compact (default): lean _meta -- request_id kept, but the elapsed_ms
    # observability echo is dropped from the hot path (available via diagnostics).
    compact = await run_mcp_tool("get_disease", call, context=McpErrorContext("get_disease"))
    assert compact["success"] is True
    assert compact["_meta"]["tool"] == "get_disease"
    assert "request_id" in compact["_meta"]
    assert "elapsed_ms" not in compact["_meta"]

    # standard: full observability echo, incl. elapsed_ms.
    std = await run_mcp_tool(
        "get_disease", call, context=McpErrorContext("get_disease", response_mode="standard")
    )
    assert isinstance(std["_meta"]["elapsed_ms"], int)

    # minimal: only the trace essentials plus the untiered disclaimer (caller
    # explicitly opted out of guidance, but not out of the clinical-use notice).
    minimal = await run_mcp_tool(
        "get_disease", call, context=McpErrorContext("get_disease", response_mode="minimal")
    )
    assert set(minimal["_meta"]) == {"tool", "request_id", "unsafe_for_clinical_use"}
    assert minimal["_meta"]["unsafe_for_clinical_use"] is True


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
    # Candidates carry a grammar-validated MONDO id AND the trusted DB ``name`` (the
    # same term.name every success payload returns) so the model can disambiguate from
    # the error alone; a bad id is dropped and the name is code-point scrubbed.
    exc = AmbiguousQueryError(
        "ambiguous",
        candidates=[
            {"mondo_id": "MONDO:0000001", "name": "Alpha syndrome"},
            {"mondo_id": "MONDO:0000002", "name": "Beta syndrome"},
        ],
    )
    result = await _run(exc)
    assert result["error_code"] == "ambiguous_query"
    assert len(result["candidates"]) == 2
    assert result["candidates"][0] == {"mondo_id": "MONDO:0000001", "name": "Alpha syndrome"}
    assert result["_meta"]["next_commands"][0]["tool"] == "get_disease"


async def test_invalid_input_surfaces_field_and_allowed() -> None:
    exc = InvalidInputError("bad", "query", allowed=["a", "b"], hint="get_disease(term)")
    result = await _run(exc)
    assert result["error_code"] == "invalid_input"
    # `field` is surfaced only because it is a grammar-valid identifier; the
    # allowed values are grammar-valid too. `hint` (free-form prose) is NEVER
    # surfaced from the exception.
    assert result["field"] == "query"
    assert result["allowed_values"] == ["a", "b"]
    assert "hint" not in result


async def test_data_unavailable_maps_to_upstream_unavailable_and_is_retryable() -> None:
    # The local Mondo index is this server's only upstream; a missing/building index is
    # ``upstream_unavailable`` (the closed enum has no ``data_unavailable``).
    result = await _run(DataUnavailableError())
    assert result["error_code"] == "upstream_unavailable"
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
    assert result["error_code"] == "internal"
    assert result["recovery_action"] == "switch_tool"


async def test_mcp_tool_error_custom_code() -> None:
    # The error_code is preserved, but the public message is a FIXED string keyed
    # on the code -- an arbitrary author/caller-influenced message is not surfaced
    # (prose is unsafe even code-point-stripped).
    result = await _run(McpToolError(error_code="upstream_unavailable", message="cold"))
    assert result["error_code"] == "upstream_unavailable"
    assert result["message"] != "cold"
    assert "cold" not in result["message"]
    assert result["message"] == _FIXED_MESSAGES["upstream_unavailable"]


def test_classify_exception_maps_typed_errors() -> None:
    # Public per-item classifier used by the batch tools (error_code, safe message).
    assert classify_exception(NotFoundError("x"))[0] == "not_found"
    assert classify_exception(AmbiguousQueryError("y"))[0] == "ambiguous_query"
    assert classify_exception(InvalidInputError("z", "field"))[0] == "invalid_input"
    code, message = classify_exception(ValueError("boom"))
    assert code == "internal"
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


def test_describe_type_expectation_array_surfaces_example() -> None:
    # A wrong *type* on a known arg must yield the expected type + a concrete
    # example (so the message says "expects an array, e.g. [...]"), never the
    # list of argument *names*.
    schema = {
        "anyOf": [{"type": "array", "items": {"type": "string"}}, {"type": "null"}],
        "examples": [["OMIM", "ORPHA"]],
    }
    result = describe_type_expectation(schema)
    assert result is not None
    allowed, human = result
    assert "array" in human
    assert '["OMIM", "ORPHA"]' in human  # the example is shown
    assert allowed == ['["OMIM", "ORPHA"]']  # allowed carries the shape, not arg names


def test_describe_type_expectation_scalar_without_example() -> None:
    assert describe_type_expectation({"type": "string"}) == (["string"], "expects a string")
    assert describe_type_expectation({"type": "integer"}) == (["integer"], "expects an integer")


def test_describe_type_expectation_array_without_example_names_item_type() -> None:
    # No example -> still names the array's item type, allowed carries the JSON type.
    schema = {"anyOf": [{"type": "array", "items": {"type": "string"}}, {"type": "null"}]}
    assert describe_type_expectation(schema) == (["array"], "expects an array of strings")


def test_describe_type_expectation_example_from_anyof_branch() -> None:
    # The concrete example may live on an anyOf branch rather than the outer schema.
    schema = {"anyOf": [{"type": "string", "examples": ["MONDO:0008426"]}, {"type": "null"}]}
    allowed, human = describe_type_expectation(schema)  # type: ignore[misc]
    assert allowed == ['"MONDO:0008426"']
    assert human == 'expects a string, e.g. "MONDO:0008426"'


def test_describe_type_expectation_none_when_typeless() -> None:
    # No determinable type (and no constraint) -> caller falls back to a name error.
    assert describe_type_expectation({}) is None
    assert describe_type_expectation({"description": "x"}) is None


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
