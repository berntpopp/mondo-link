"""MCP envelope boundary: success/_meta injection and structured errors.

Tools return a plain dict; :func:`run_mcp_tool` injects ``success`` and ``_meta``
on success, and converts any exception into a structured error dict (returned,
never raised) so the LLM sees a typed failure rather than an opaque masked
message.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, cast

from fastmcp.tools.tool import ToolResult
from mcp.types import TextContent
from pydantic import ValidationError as PydanticValidationError

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
from mondo_link.mcp import metrics
from mondo_link.mcp.next_commands import cmd, default_error_next_commands
from mondo_link.mcp.untrusted_content import UntrustedTextLimitError, sanitize_message
from mondo_link.services.shaping import DEFAULT_RESPONSE_MODE

logger = logging.getLogger(__name__)

# Per-call _meta is kept lean: static provenance (citation, Mondo release) lives
# ONLY in get_server_capabilities. Per-call _meta carries dynamic fields: tool,
# request_id, unsafe_for_clinical_use, [next_commands, capabilities_version,
# elapsed_ms] -- the bracketed three are tiered by response_mode (see
# _shape_meta), but `unsafe_for_clinical_use` is a fleet-wide disclaimer
# standard (Response-Envelope Standard v1) and is therefore untiered: it is
# emitted on every call, success or error, at every response_mode -- including
# `minimal`.
_RETRYABLE = {"rate_limited", "upstream_unavailable"}
#: Fleet-wide disclaimer emitted verbatim in every per-call `_meta` (all
#: response_modes, success and error paths). See Response-Envelope Standard v1.
_UNSAFE_FOR_CLINICAL_USE = True


@dataclass
class McpErrorContext:
    """Per-call context so envelopes can name the failing tool and recovery."""

    tool_name: str
    fallback: dict[str, Any] | None = field(default=None)
    arguments: dict[str, Any] = field(default_factory=dict)
    #: The caller's verbosity, used to tier _meta (see :func:`_shape_meta`).
    response_mode: str = DEFAULT_RESPONSE_MODE


class McpToolError(Exception):
    """Raised inside a tool body to emit a specific error code/message."""

    def __init__(self, *, error_code: str, message: str) -> None:
        """Store an error code and client-safe message."""
        super().__init__(message)
        self.error_code = error_code
        self.message = message


def _request_id() -> str:
    return uuid.uuid4().hex[:12]


def _capabilities_version() -> str | None:
    """Cached discovery-contract hash for the ``_meta`` echo (never raises)."""
    try:
        from mondo_link.mcp.capabilities import capabilities_version

        return capabilities_version()
    except Exception:  # pragma: no cover - the _meta echo must never break a tool
        return None


# FIXED, error-code-specific public messages. Classified exceptions build their
# `str(exc)` from the caller's query/identifier or a local DB path/sqlite error,
# so the message PROSE is attacker-/environment-influenced -- code-point stripping
# alone would still leak it. We therefore NEVER interpolate `str(exc)` into a
# caller-visible message: the actionable specifics ride the structured envelope
# fields (`field`, `allowed_values`, `candidates`, `replaced_by`, ...), and the raw
# detail stays only in the chained exception cause (server-side logs, class name).
# Keyed by the CLOSED error_code enum (Response-Envelope Standard v1): invalid_input,
# not_found, ambiguous_query, upstream_unavailable, rate_limited, internal. The local
# Mondo index is this server's only "upstream", so a missing/building index is an
# ``upstream_unavailable`` (retryable); ``data_unavailable``/``internal_error`` are NOT
# in the enum. ``unknown_tool``/``obsolete``/``limit_exceeded`` are message keys only.
_FIXED_MESSAGES: dict[str, str] = {
    "not_found": "No matching Mondo record was found for the request.",
    "unknown_tool": "Unknown tool. Call get_server_capabilities to list the valid tools.",
    "obsolete": "The requested Mondo term is obsolete; see replaced_by / candidates.",
    "ambiguous_query": "The query matched multiple Mondo terms; pick one from candidates.",
    "invalid_input": "The request arguments were invalid; check the field and retry.",
    "limit_exceeded": "Response exceeded the untrusted-text size/count limit.",
    "rate_limited": "Upstream rate limit hit. Retry shortly.",
    "upstream_unavailable": (
        "The Mondo data source is temporarily unavailable (it may be rebuilding). "
        "Retry shortly or call get_diagnostics."
    ),
    "internal": "An internal error occurred. The request was not completed.",
}

#: An argument NAME is echoed back to the caller ONLY when it is a plain
#: identifier: a name matching this grammar provably cannot carry spaces,
#: injection prose, or forbidden code points, so it is safe to surface. Any other
#: (caller-controlled, unknown) argument name is redacted, never echoed verbatim.
_SAFE_ARG_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,63}$")

#: Canonical MONDO id grammar. Caller-visible identifier echoes (candidates,
#: replaced_by, next_commands arguments) are surfaced ONLY when they match this,
#: so an upstream/exception label can never smuggle prose through them.
_MONDO_ID_RE = re.compile(r"^MONDO:\d{7}$")
#: Closed enum for the withdrawn/obsolete status surfaced on the envelope.
_WITHDRAWN_STATUSES = frozenset({"obsolete", "deprecated", "merged", "retired"})


def _valid_id_entries(entries: Any) -> list[dict[str, Any]]:
    """Project candidate/suggestion/replacement entries to ``{mondo_id, name[, score]}``.

    The ``mondo_id`` must match the canonical grammar (a non-conforming entry is
    dropped entirely). The ``name`` is the term's TRUSTED DB label -- the same
    ``term.name`` column every success-path payload returns verbatim -- so surfacing it
    is exactly as safe, and it is what makes the candidate list actionable from the
    error alone. Only DB DEFINITIONS (long free-text) are untrusted/fenced; a disease
    NAME is not. The name is still ``sanitize_message``-scrubbed (whole envelope by
    ``_sanitize_tree``) so code points never survive. A fuzzy ``score`` rides along too.
    """
    out: list[dict[str, Any]] = []
    if not isinstance(entries, list):
        return out
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        mondo_id = entry.get("mondo_id")
        if not (isinstance(mondo_id, str) and _MONDO_ID_RE.match(mondo_id)):
            continue
        item: dict[str, Any] = {"mondo_id": mondo_id}
        name = entry.get("name")
        if isinstance(name, str) and name:
            item["name"] = sanitize_message(name)
        score = entry.get("score")
        if isinstance(score, int | float) and not isinstance(score, bool):
            item["score"] = score
        out.append(item)
    return out


def _sanitize_tree(value: Any) -> Any:
    """Recursively code-point-strip every string leaf of a built error envelope.

    A last-step backstop ON TOP OF the fixed-message/redaction discipline: it
    strips the forbidden control/zero-width/bidi/NUL code points from every string
    (message, field, allowed_values, hint, candidates[*].name, replaced_by,
    ``_meta.next_commands[*].arguments.*`` -- the caller's own query echoed into a
    recovery step) without reshaping the structure. It does not make prose safe;
    prose is kept safe by never interpolating attacker-influenced text above.
    """
    if isinstance(value, str):
        return sanitize_message(value)
    if isinstance(value, dict):
        return {key: _sanitize_tree(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize_tree(item) for item in value]
    return value


def _classify(exc: BaseException) -> tuple[str, str]:
    """Return ``(error_code, FIXED client-safe message)`` for an exception.

    The message is a fixed, error-code-specific string -- ``str(exc)`` (which may
    embed the caller's query, an identifier, or a local filesystem path) is never
    interpolated. ``McpToolError`` carries a server-authored explicit message, so
    it is passed through the code-point backstop only.
    """
    if isinstance(exc, McpToolError):
        # Map the code to a FIXED public message: an arbitrary author-supplied
        # (or caller-influenced) message is not safe even code-point-stripped.
        return exc.error_code, _FIXED_MESSAGES.get(exc.error_code, _FIXED_MESSAGES["internal"])
    if isinstance(exc, UntrustedTextLimitError):
        # v1.1 response-limit breach: an explicit, typed limit error (the standard
        # forbids silently omitting fenced content over a ceiling), not a masked
        # internal error -- the caller retries with a narrower request.
        return "invalid_input", _FIXED_MESSAGES["limit_exceeded"]
    if isinstance(exc, WithdrawnEntryError):  # NotFoundError subclass; obsolete term
        return "not_found", _FIXED_MESSAGES["obsolete"]
    if isinstance(exc, NotFoundError):
        return "not_found", _FIXED_MESSAGES["not_found"]
    if isinstance(exc, AmbiguousQueryError):
        return "ambiguous_query", _FIXED_MESSAGES["ambiguous_query"]
    if isinstance(exc, InvalidInputError):
        return "invalid_input", _FIXED_MESSAGES["invalid_input"]
    if isinstance(exc, DataUnavailableError | ServiceUnavailableError | DownloadError):
        # The local index is this server's only upstream -> upstream_unavailable.
        return "upstream_unavailable", _FIXED_MESSAGES["upstream_unavailable"]
    if isinstance(exc, RateLimitError):
        return "rate_limited", _FIXED_MESSAGES["rate_limited"]
    if isinstance(exc, PydanticValidationError):
        # Map to a fixed reason; the pydantic `msg` can echo the rejected input
        # and the `loc`/field name is caller-controlled -- neither is interpolated.
        return "invalid_input", _FIXED_MESSAGES["invalid_input"]
    return "internal", _FIXED_MESSAGES["internal"]


def classify_exception(exc: BaseException) -> tuple[str, str]:
    """Public per-item classifier: ``(error_code, client-safe message)``.

    Batch tools catch typed exceptions per item and need the same taxonomy the
    error envelope applies, without building a whole envelope. Delegates to the
    shared classifier so single-item and batch error shaping never diverge.
    """
    return _classify(exc)


def _recovery_action(error_code: str) -> str:
    if error_code in _RETRYABLE:
        return "retry_backoff"
    if error_code in {"invalid_input", "not_found", "ambiguous_query"}:
        return "reformulate_input"
    return "switch_tool"


def _error_envelope(exc: BaseException, context: McpErrorContext) -> dict[str, Any]:
    """Build the structured error envelope, then run the recursive code-point pass."""
    return cast(dict[str, Any], _sanitize_tree(_build_error_envelope(exc, context)))


def _build_error_envelope(exc: BaseException, context: McpErrorContext) -> dict[str, Any]:
    error_code, message = _classify(exc)
    envelope: dict[str, Any] = {
        "success": False,
        "error_code": error_code,
        "message": message,
        "retryable": error_code in _RETRYABLE,
        "recovery_action": _recovery_action(error_code),
        "_meta": {
            "tool": context.tool_name,
            "request_id": _request_id(),
            "unsafe_for_clinical_use": _UNSAFE_FOR_CLINICAL_USE,
        },
    }
    # Every caller-visible structured field below is built ONLY from a FIXED
    # string, a CLOSED enum, or a grammar-validated identifier -- an exception's
    # free-text attributes (field/allowed/hint, candidate names, the caller's
    # query) are NEVER copied verbatim, because injection prose survives
    # code-point stripping. `_sanitize_tree` (applied by `_error_envelope`) is a
    # code-point backstop on top of this structural exclusion, not a substitute.
    if isinstance(exc, InvalidInputError):
        if exc.field is not None and _SAFE_ARG_NAME.match(str(exc.field)):
            envelope["field"] = str(exc.field)
        if isinstance(exc.allowed, list):
            allowed = [a for a in exc.allowed if isinstance(a, str) and _SAFE_ARG_NAME.match(a)]
            if allowed:
                envelope["allowed_values"] = allowed
        # exc.hint is free-form prose and is never surfaced from the exception.
    if isinstance(exc, AmbiguousQueryError) and exc.candidates:
        candidates = _valid_id_entries(exc.candidates)
        if candidates:
            envelope["candidates"] = candidates
        envelope["_meta"]["next_commands"] = [
            cmd("get_disease", term=c["mondo_id"]) for c in candidates[:3]
        ] or [cmd("get_server_capabilities")]
        return envelope
    if isinstance(exc, WithdrawnEntryError):
        replaced_by = _valid_id_entries(exc.replaced_by)
        envelope["obsolete"] = True
        envelope["withdrawn_status"] = (
            exc.withdrawn_status if exc.withdrawn_status in _WITHDRAWN_STATUSES else "obsolete"
        )
        envelope["replaced_by"] = replaced_by
        envelope["_meta"]["next_commands"] = [
            cmd("get_disease", term=r["mondo_id"]) for r in replaced_by[:2]
        ] or [cmd("get_server_capabilities")]
        return envelope
    if isinstance(exc, NotFoundError) and exc.suggestions:
        candidates = _valid_id_entries(exc.suggestions)
        if candidates:
            envelope["candidates"] = candidates
        envelope["_meta"]["next_commands"] = [
            cmd("get_disease", term=c["mondo_id"]) for c in candidates[:3]
        ] or [cmd("get_server_capabilities")]
        return envelope
    # No caller value is echoed into recovery steps (prose would survive there);
    # they resolve to fixed, argument-free discovery commands.
    envelope["_meta"]["next_commands"] = default_error_next_commands(
        context.tool_name, error_code, context.arguments
    )
    return envelope


def build_arg_error_envelope(
    *,
    tool_name: str,
    loc: str,
    error_type: str,
    valid_params: list[str],
    signature: str,
    suggestion: str | None,
    constraints: tuple[list[str], str] | None = None,
) -> dict[str, Any]:
    """Standard invalid-input envelope for an argument-binding failure.

    When ``constraints`` is supplied the failure is an invalid *value* on a known
    argument, so ``allowed_values`` carries the valid range/enum (not the list of
    argument *names*) and the message states the constraint.

    The ``loc`` (argument name) is caller-controlled for an *unknown* argument, so
    it is echoed only when it is a plain identifier (``_SAFE_ARG_NAME``, provably
    prose-free); otherwise it is redacted -- never surfaced verbatim in the message
    or ``field``. The final envelope is run through the recursive code-point pass.
    """
    safe_loc = loc if _SAFE_ARG_NAME.match(loc) else None
    field_value = safe_loc or "unknown_argument"
    name_ref = f"`{safe_loc}`" if safe_loc else "the supplied argument"
    meta = {
        "tool": tool_name,
        "request_id": _request_id(),
        "unsafe_for_clinical_use": _UNSAFE_FOR_CLINICAL_USE,
        "next_commands": [cmd("get_server_capabilities")],
    }
    if constraints is not None:
        allowed, human = constraints
        message = f"Invalid value for argument {name_ref} of {tool_name}: {human}."
        return cast(
            dict[str, Any],
            _sanitize_tree(
                {
                    "success": False,
                    "error_code": "invalid_input",
                    "message": message[:280],
                    "retryable": False,
                    "recovery_action": "reformulate_input",
                    "field": field_value,
                    "allowed_values": allowed,
                    "hint": signature,
                    "_meta": meta,
                }
            ),
        )
    if error_type == "missing_argument":
        head = f"Missing required argument {name_ref} for {tool_name}."
    elif error_type == "unexpected_keyword_argument":
        head = f"Unknown argument {name_ref} for {tool_name}."
    else:
        head = f"Invalid value for argument {name_ref} of {tool_name}."
    dym = f" Did you mean `{suggestion}`?" if suggestion else ""
    message = f"{head}{dym} Valid argument names are listed in allowed_values."
    return cast(
        dict[str, Any],
        _sanitize_tree(
            {
                "success": False,
                "error_code": "invalid_input",
                "message": message[:280],
                "retryable": False,
                "recovery_action": "reformulate_input",
                "field": field_value,
                "allowed_values": valid_params,
                "hint": signature,
                "_meta": meta,
            }
        ),
    )


def build_unknown_tool_envelope() -> dict[str, Any]:
    """Fixed, identifier-free error envelope for an unknown tool NAME.

    The requested tool name is caller-controlled; it is NEVER echoed. FastMCP core
    would otherwise surface it verbatim (``Unknown tool: '<name>'`` -- with any
    control/zero-width/bidi/NUL code points or injection prose) in an ``isError``
    result. The middleware returns this BEFORE core dispatch (see the preflight in
    ``ArgValidationMiddleware.on_call_tool``).
    """
    return cast(
        dict[str, Any],
        _sanitize_tree(
            {
                "success": False,
                "error_code": "not_found",
                "message": _FIXED_MESSAGES["unknown_tool"],
                "retryable": False,
                "recovery_action": "switch_tool",
                "field": "tool_name",
                "_meta": {
                    "tool": "unknown",
                    "request_id": _request_id(),
                    "unsafe_for_clinical_use": _UNSAFE_FOR_CLINICAL_USE,
                    "next_commands": [cmd("get_server_capabilities")],
                },
            }
        ),
    )


def _stamp_capabilities_version(meta: dict[str, Any]) -> None:
    """Add the cached capabilities_version to a ``_meta`` block when available."""
    version = _capabilities_version()
    if version:
        meta["capabilities_version"] = version


def _shape_meta(meta: dict[str, Any], response_mode: str) -> dict[str, Any]:
    """Tier ``_meta`` verbosity by ``response_mode`` to control the per-call token tax.

    - ``minimal``: the trace essentials plus the disclaimer -- ``{tool, request_id,
      unsafe_for_clinical_use}``. The caller explicitly opted out of guidance, so
      ``next_commands`` / ``capabilities_version`` / ``elapsed_ms`` are dropped.
    - ``compact`` (default): keep ``next_commands`` (workflow guidance) and
      ``capabilities_version`` (the warm-client cache key the discovery contract leans
      on), but drop the ``elapsed_ms`` observability echo from the hot path -- it is
      still recorded server-side and surfaced by ``get_diagnostics``.
    - ``standard`` / ``full``: the complete ``_meta``, including ``elapsed_ms``.

    The universal ``next_commands`` invariant therefore holds for ``compact`` and
    richer (every default response still chains); ``minimal`` is the documented opt-out.
    ``unsafe_for_clinical_use`` is never tiered away -- it is present at every
    ``response_mode`` (Response-Envelope Standard v1).
    """
    if response_mode == "minimal":
        return {
            "tool": meta["tool"],
            "request_id": meta["request_id"],
            "unsafe_for_clinical_use": meta["unsafe_for_clinical_use"],
        }
    if response_mode in ("standard", "full"):
        return meta
    return {k: v for k, v in meta.items() if k != "elapsed_ms"}


def error_result(envelope: dict[str, Any]) -> ToolResult:
    """Wrap an error envelope so it carries BOTH the structure AND MCP ``isError``.

    Response-Envelope v1: *"isError: true is REQUIRED so clients surface the error to the
    model."* A tool that RETURNS a dict can never set it (so every error was delivered as
    a SUCCESSFUL call carrying ``success: false``); raising sets ``isError`` but discards
    ``structuredContent``. A ``ToolResult`` is the only shape giving both -- the
    TextContent mirror is kept in step with ``structured_content``.
    """
    return ToolResult(
        structured_content=envelope,
        content=[TextContent(type="text", text=json.dumps(envelope))],
        is_error=True,
    )


async def run_mcp_tool(
    tool_name: str,
    call: Callable[[], Awaitable[dict[str, Any]]],
    *,
    context: McpErrorContext | None = None,
) -> dict[str, Any] | ToolResult:
    """Execute a tool body: the result dict on success, or an ``isError`` ToolResult."""
    ctx = context or McpErrorContext(tool_name=tool_name)
    start = time.perf_counter()
    try:
        result = await call()
        elapsed = int((time.perf_counter() - start) * 1000)
        if isinstance(result, dict):
            existing_meta: dict[str, Any] = result.get("_meta") or {}
            success = bool(result.setdefault("success", True))
            meta = {
                **existing_meta,
                "tool": tool_name,
                "request_id": _request_id(),
                "unsafe_for_clinical_use": _UNSAFE_FOR_CLINICAL_USE,
                "elapsed_ms": elapsed,
            }
            _stamp_capabilities_version(meta)
            result["_meta"] = _shape_meta(meta, ctx.response_mode)
            metrics.record(tool_name, elapsed, ok=success)
        return result
    except Exception as exc:  # broad catch is the error-boundary contract
        elapsed = int((time.perf_counter() - start) * 1000)
        envelope = _error_envelope(exc, ctx)
        envelope["_meta"]["elapsed_ms"] = elapsed
        _stamp_capabilities_version(envelope["_meta"])
        envelope["_meta"] = _shape_meta(envelope["_meta"], ctx.response_mode)
        metrics.record(tool_name, elapsed, ok=False)
        logger.warning(
            "mcp_tool_error tool=%s code=%s exc=%s",
            tool_name,
            envelope["error_code"],
            exc.__class__.__name__,
        )
        return error_result(envelope)
