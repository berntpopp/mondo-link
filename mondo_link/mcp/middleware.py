"""FastMCP middleware that wraps argument-binding failures in the error envelope.

FastMCP validates call arguments with pydantic inside ``FunctionTool.run()`` --
before the registered tool body executes -- so a wrong argument *name*/*type* or a
*missing required* argument raises a ``pydantic.ValidationError`` that never reaches
``run_mcp_tool``'s error boundary. This middleware catches it at ``on_call_tool``
and returns the standard ``invalid_input`` envelope (valid names + a did-you-mean).
It also normalizes curated argument aliases (e.g. ``term`` -> ``query``) before
dispatch and discloses any rewrite under ``_meta.argument_aliases_applied``.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastmcp.exceptions import ValidationError as FastMCPValidationError
from fastmcp.server.middleware.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.tool import ToolResult
from mcp.types import CallToolRequestParams, TextContent
from pydantic import ValidationError as PydanticValidationError

from mondo_link.mcp.arg_help import (
    describe_constraints,
    describe_type_expectation,
    did_you_mean,
    normalize_alias_args,
    tool_signature,
)
from mondo_link.mcp.envelope import build_arg_error_envelope, build_unknown_tool_envelope

logger = logging.getLogger(__name__)

#: FastMCP logs the FULL pydantic argument-validation detail (the caller-supplied
#: argument NAME and rejected input value, with any control/zero-width/bidi/NUL
#: code points) at WARNING from ``fastmcp.server.server`` BEFORE this middleware
#: reshapes it. That record bypasses the envelope, so scrub it at the source.
_FASTMCP_SERVER_LOGGER = "fastmcp.server.server"
_SCRUBBED_LOG_PREFIXES = ("Invalid arguments for tool", "Error calling tool")


class _ScrubValidationLogFilter(logging.Filter):
    """Replace FastMCP's arg-validation/error log records with fixed metadata.

    Clears ``args``/``exc_info``/``exc_text`` so the raw caller input (which the
    record interpolates) can never reach a log sink. Always returns ``True`` -- the
    (now caller-input-free) record is still emitted for operational visibility.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.msg if isinstance(record.msg, str) else ""
        if msg.startswith(_SCRUBBED_LOG_PREFIXES):
            record.msg = "tool call rejected: arguments failed validation"
            record.args = ()
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
        return True


_validation_log_filter_installed = False


def install_validation_log_filter() -> None:
    """Idempotently attach the scrubbing filter to FastMCP's server logger."""
    global _validation_log_filter_installed
    if _validation_log_filter_installed:
        return
    logging.getLogger(_FASTMCP_SERVER_LOGGER).addFilter(_ScrubValidationLogFilter())
    _validation_log_filter_installed = True


class _ExceptionSpanRedactor:
    """Duck-typed OpenTelemetry span processor that strips recorded exception detail.

    FastMCP's ``server_span`` calls ``span.record_exception(exc)`` +
    ``set_status(Status(ERROR, str(exc)))`` on the tools/call span when a *recording*
    tracer provider is configured -- and ``str(exc)`` of an argument-validation /
    unknown-tool error carries the caller-supplied argument NAME/value (with any
    control/zero-width/bidi/NUL code points or injection prose). This scrubs the
    ``exception`` event(s) and the ERROR status description before export.

    NB mondo-link ships only ``opentelemetry-api`` (no SDK), so spans are non-recording
    and this path is inert by default; the guard is defense-in-depth for a deployment
    that adds ``opentelemetry-sdk`` and a recording provider. Ordering across other
    processors is best-effort (a synchronous exporter registered *before* this
    redactor may export first); it is reliable with the batch processor.
    """

    def on_start(self, span: Any, parent_context: Any = None) -> None:
        """No-op: redaction happens on span end."""
        return None

    def on_end(self, span: Any) -> None:
        """Strip recorded exception events + ERROR status description from a span."""
        events = getattr(span, "_events", None)
        if events:
            kept = [ev for ev in events if getattr(ev, "name", "") != "exception"]
            if len(kept) != len(events):
                span._events = kept if isinstance(events, list) else type(events)(kept)
        status = getattr(span, "_status", None)
        if status is not None and getattr(status, "description", None):
            from opentelemetry.trace import Status

            span._status = Status(status.status_code)

    def shutdown(self) -> None:
        """No-op: the redactor holds no resources."""
        return None

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        """No buffered spans to flush."""
        return True


_span_redactor_installed = False


def install_span_exception_redactor() -> None:
    """Idempotently attach the span-exception redactor to the active tracer provider.

    No-op when no *recording* SDK provider is configured (the default, since mondo
    depends only on ``opentelemetry-api``): the API's provider has no
    ``add_span_processor``.
    """
    global _span_redactor_installed
    if _span_redactor_installed:
        return
    try:
        from opentelemetry import trace as _otel_trace

        provider = _otel_trace.get_tracer_provider()
        add = getattr(provider, "add_span_processor", None)
        if callable(add):
            add(_ExceptionSpanRedactor())
    except Exception:  # pragma: no cover - telemetry guard must never break startup
        return
    _span_redactor_installed = True


class ArgValidationMiddleware(Middleware):
    """Reshape argument-binding errors into the envelope and apply argument aliases."""

    def __init__(self) -> None:
        """Initialise the per-tool parameter-schema cache."""
        self._schema_cache: dict[str, dict[str, Any]] = {}

    async def _schema(self, context: MiddlewareContext[Any], name: str) -> dict[str, Any]:
        if name not in self._schema_cache:
            fctx = context.fastmcp_context
            if fctx is None:
                raise RuntimeError("no fastmcp context")
            tool = await fctx.fastmcp.get_tool(name)
            self._schema_cache[name] = dict(getattr(tool, "parameters", None) or {})
        return self._schema_cache[name]

    async def _is_registered(self, context: MiddlewareContext[Any], name: str) -> bool:
        """True if ``name`` is a registered tool (used to preflight unknown names).

        ``get_tool`` returns ``None`` (it does not raise) for an unknown or disabled
        tool, so an unknown name is caught by the ``is not None`` check.
        """
        if name in self._schema_cache:
            return True
        fctx = context.fastmcp_context
        if fctx is None:
            return False
        try:
            tool = await fctx.fastmcp.get_tool(name)
        except Exception:
            return False
        return tool is not None

    @staticmethod
    def _unknown_tool_result() -> ToolResult:
        envelope = build_unknown_tool_envelope()
        return ToolResult(
            structured_content=envelope,
            content=[TextContent(type="text", text=json.dumps(envelope))],
        )

    async def on_call_tool(
        self,
        context: MiddlewareContext[CallToolRequestParams],
        call_next: CallNext[CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        """Normalize aliases, then convert binding errors into the envelope."""
        name = context.message.name
        # Preflight the tool NAME. An unknown name is caller-controlled; FastMCP
        # core would raise `Unknown tool: '<name>'` (echoing it, with any code
        # points / prose, into an isError TextContent) BEFORE our envelope. Return
        # a fixed, name-free error before core dispatch so the name never escapes.
        if context.fastmcp_context is not None and not await self._is_registered(context, name):
            return self._unknown_tool_result()
        try:
            schema = await self._schema(context, name)
        except Exception:  # registry miss with no context to preflight: defer to core
            return await call_next(context)

        valid = list(schema.get("properties", {}).keys())
        new_args, applied = normalize_alias_args(valid, context.message.arguments or {})
        context.message.arguments = new_args

        try:
            result = await call_next(context)
        except FastMCPValidationError as exc:
            cause = exc.__cause__
            if not isinstance(cause, PydanticValidationError):
                raise
            return self._error_result(name, valid, schema, cause)
        except PydanticValidationError as exc:
            return self._error_result(name, valid, schema, exc)

        if (
            applied
            and isinstance(result, ToolResult)
            and isinstance(result.structured_content, dict)
        ):
            meta = result.structured_content.setdefault("_meta", {})
            meta["argument_aliases_applied"] = [list(pair) for pair in applied]
        return result

    def _error_result(
        self,
        name: str,
        valid: list[str],
        schema: dict[str, Any],
        exc: PydanticValidationError,
    ) -> ToolResult:
        first = exc.errors(include_url=False)[0]
        loc = ".".join(str(p) for p in first.get("loc", ())) or "input"
        error_type = str(first.get("type", "value_error"))
        # A real param with a bad *value* -> surface the constraint (enum/range)
        # or, failing that, the expected type + an example -- never the list of
        # argument names (which is reserved for genuinely unknown arguments).
        constraints = None
        if loc in valid and error_type not in ("missing", "missing_argument"):
            field_schema = schema.get("properties", {}).get(loc, {})
            constraints = describe_constraints(field_schema) or describe_type_expectation(
                field_schema
            )
        suggestion = did_you_mean(loc, valid) if loc not in valid else None
        envelope = build_arg_error_envelope(
            tool_name=name,
            loc=loc,
            error_type=error_type,
            valid_params=valid,
            signature=tool_signature(name, schema),
            suggestion=suggestion,
            constraints=constraints,
        )
        # NB: never log the raw `loc` -- an unknown argument NAME is caller-
        # controlled and can carry prose / forbidden code points.
        logger.warning("mcp_arg_error tool=%s type=%s", name, error_type)
        return ToolResult(
            structured_content=envelope,
            content=[TextContent(type="text", text=json.dumps(envelope))],
        )
