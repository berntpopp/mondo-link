"""Locks the ratified GeneFoundry Response-Envelope Standard v1 (flat-banner
contract) at this repo's MCP envelope boundary: :mod:`mondo_link.mcp.envelope`.

Adapted from clingen-link (fleet exemplar, PR #20:
https://github.com/berntpopp/clingen-link/pull/20) for this repo's envelope
shape. mondo-link has no separate ``mcp/errors.py`` / ``build_meta`` split --
``run_mcp_tool`` *and* the private error-envelope builder (``_error_envelope``)
both live directly in ``mcp/envelope.py``, so this test exercises them via the
same public boundary the tools call through: ``run_mcp_tool``.

Ratified contract under test:

- SUCCESS: ``{"success": True, <tool payload>, "_meta": {...}}`` -- the
  envelope boundary injects ``success``/``_meta`` on top of whatever dict the
  tool body returns; it never reshapes or renames the tool's own payload keys.
- FAILURE: a FLAT in-band dict --
  ``{"success": False, "error_code", "message", "retryable",
  "recovery_action", "_meta": {"tool", "request_id", ...}}`` -- NEVER a bare
  raised exception, and NEVER a nested ``"error": {...}`` object.

Known drift vs the fleet-wide aspirational standard (asserted here as ground
truth, not glossed over):

- The primary payload key is **not** forced to ``results``/``result``.
  ``genefoundry-router``'s own standard doc (``RESPONSE-ENVELOPE-STANDARD-v1``)
  explicitly marks that renaming as "not yet the enforced current fleet gate"
  for Mondo-style backends, whose current router-compatible contract is a
  backend-owned domain payload alongside ``success``/``_meta``. This suite
  locks that current, real shape rather than asserting the not-yet-enforced
  future frame.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from mondo_link.exceptions import InvalidInputError, NotFoundError
from mondo_link.mcp.envelope import McpErrorContext, run_mcp_tool

# --- success envelope --------------------------------------------------------


async def test_success_envelope_is_flat_banner_over_the_tool_payload() -> None:
    """success/_meta are injected onto the tool's own payload, unwrapped/unrenamed."""

    async def call() -> dict[str, Any]:
        # A single-item tool payload shaped like get_disease's real return: flat
        # domain keys, no "result" wrapper key.
        return {"mondo_id": "MONDO:0008426", "name": "Shprintzen-Goldberg syndrome"}

    result = await run_mcp_tool("get_disease", call, context=McpErrorContext("get_disease"))

    assert result["success"] is True
    # The tool's own domain keys are preserved untouched -- no reshaping into a
    # "result" wrapper (see the module docstring's "known drift" note).
    assert result["mondo_id"] == "MONDO:0008426"
    assert result["name"] == "Shprintzen-Goldberg syndrome"
    assert "result" not in result
    assert "results" not in result
    assert "error" not in result
    assert isinstance(result["_meta"], dict)
    assert result["_meta"]["tool"] == "get_disease"
    assert isinstance(result["_meta"]["request_id"], str) and result["_meta"]["request_id"]


async def test_success_envelope_preserves_a_results_list_payload_untouched() -> None:
    """A list-returning tool (e.g. search_diseases) already carries `results`; the
    envelope boundary must not rename or nest it either."""

    async def call() -> dict[str, Any]:
        return {
            "query": "marfan",
            "results": [{"mondo_id": "MONDO:0008426", "name": "Shprintzen-Goldberg syndrome"}],
            "total": 1,
        }

    result = await run_mcp_tool("search_diseases", call, context=McpErrorContext("search_diseases"))

    assert result["success"] is True
    assert result["results"] == [
        {"mondo_id": "MONDO:0008426", "name": "Shprintzen-Goldberg syndrome"}
    ]
    assert "error" not in result


async def test_success_meta_carries_unsafe_for_clinical_use_flag() -> None:
    """Fleet disclaimer standard: per-call `_meta` carries
    `unsafe_for_clinical_use: True` on every success response, at every
    `response_mode` -- including `minimal`, which otherwise drops everything
    but the trace essentials.
    """

    async def call() -> dict[str, Any]:
        return {"mondo_id": "MONDO:0008426"}

    for response_mode in ("minimal", "compact", "standard", "full"):
        result = await run_mcp_tool(
            "get_disease",
            call,
            context=McpErrorContext("get_disease", response_mode=response_mode),
        )

        assert result["success"] is True
        assert result["_meta"]["unsafe_for_clinical_use"] is True


# --- error envelope: flat banner, never nested, never a bare exception ------


def _raiser(exc: BaseException) -> Callable[[], Awaitable[dict[str, Any]]]:
    async def call() -> dict[str, Any]:
        raise exc

    return call


async def test_error_envelope_is_a_flat_dict_never_a_nested_error_object() -> None:
    """A tool-body exception is caught at the boundary and returned (never raised)
    as a FLAT `success: false` dict -- no nested `error: {...}` object anywhere.
    """
    result = await run_mcp_tool(
        "get_disease",
        _raiser(NotFoundError("No matching Mondo record found.")),
        context=McpErrorContext("get_disease", arguments={"term": "MONDO:9999999"}),
    )

    assert result["success"] is False
    assert "error" not in result  # never a nested error object
    assert result["error_code"] == "not_found"
    # The public message is a FIXED, error-code-specific string: the exception's
    # own text (which can embed the caller's query/identifier) is never
    # interpolated into a caller-visible message.
    assert result["message"] == "No matching Mondo record was found for the request."
    assert result["retryable"] is False
    assert result["recovery_action"] == "reformulate_input"
    assert isinstance(result["_meta"], dict)
    assert result["_meta"]["tool"] == "get_disease"
    assert isinstance(result["_meta"]["request_id"], str) and result["_meta"]["request_id"]


async def test_error_envelope_retryable_true_for_upstream_style_failures() -> None:
    """`retryable`/`recovery_action` are correctly typed booleans/enums, not prose,
    for the retryable branch of the taxonomy (data_unavailable -> retry_backoff)."""
    result = await run_mcp_tool(
        "get_disease",
        _raiser(InvalidInputError("bad id", "term")),
        context=McpErrorContext("get_disease", arguments={"term": "??"}),
    )

    assert result["success"] is False
    assert result["error_code"] == "invalid_input"
    assert result["retryable"] is False
    assert result["recovery_action"] == "reformulate_input"
    assert "error" not in result


async def test_error_meta_carries_unsafe_for_clinical_use_flag() -> None:
    """Fleet disclaimer standard: the error-path `_meta` also carries
    `unsafe_for_clinical_use: True` (same guarantee as the success path), at
    every `response_mode`."""
    for response_mode in ("minimal", "compact", "standard", "full"):
        result = await run_mcp_tool(
            "get_disease",
            _raiser(NotFoundError("No matching Mondo record found.")),
            context=McpErrorContext(
                "get_disease",
                arguments={"term": "MONDO:9999999"},
                response_mode=response_mode,
            ),
        )

        assert result["success"] is False
        assert result["_meta"]["unsafe_for_clinical_use"] is True
