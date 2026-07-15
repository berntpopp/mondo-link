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
from typing import Any, cast

from fastmcp import FastMCP
from fastmcp.exceptions import ResourceError
from fastmcp.exceptions import ValidationError as FastMCPValidationError
from fastmcp.server.middleware.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.tool import ToolResult
from mcp.types import (
    CallToolRequest,
    CallToolRequestParams,
    CallToolResult,
    GetPromptRequest,
    ReadResourceRequest,
    ServerResult,
    TextContent,
)
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

#: Fixed, input-free frames for reflection surfaces that bypass the tool envelope.
#: The resource message mirrors the Layer-2 ``on_read_resource`` constant; the prompt
#: message closes the ``prompts/get`` caller echo (``Unknown prompt: '<name>'``).
_UNKNOWN_RESOURCE_MESSAGE = "Resource unavailable or not found."
_UNKNOWN_PROMPT_MESSAGE = "The requested prompt is not available."


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
        # is_error=True: Response-Envelope v1 requires an error envelope to carry MCP
        # isError so a client branching on it surfaces the failure to the model.
        return ToolResult(
            structured_content=envelope,
            content=[TextContent(type="text", text=json.dumps(envelope))],
            is_error=True,
        )

    async def on_read_resource(
        self,
        context: MiddlewareContext[Any],
        call_next: CallNext[Any, Any],
    ) -> Any:
        """Emit a FIXED, URI-free error for a resource not-found / read failure.

        The requested resource URI is caller-controlled; FastMCP core echoes it
        (``Unknown resource: '<uri>'`` / ``Error reading resource '<uri>'`` -- with
        any percent-encoded code points or injection prose) in both the direct
        ``read_resource`` exception and the protocol ``-32002`` error. Re-raise a
        fixed ``ResourceError`` so the URI never reaches the caller/protocol.
        """
        try:
            return await call_next(context)
        except Exception as exc:
            logger.warning("mcp_resource_error type=%s", type(exc).__name__)
            raise ResourceError("Resource unavailable or not found.") from None

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
        # is_error=True: an argument-binding failure is an error envelope and must set
        # MCP isError (Response-Envelope v1), else a client sees a "successful" bad call.
        return ToolResult(
            structured_content=envelope,
            content=[TextContent(type="text", text=json.dumps(envelope))],
            is_error=True,
        )


# ---------------------------------------------------------------------------
# Layer 3 -- protocol-handler backstop (clinvar pattern)
# ---------------------------------------------------------------------------
# FastMCP's CORE dispatch reflects the caller-controlled component name/URI
# verbatim when it is unknown -- notably ``Unknown prompt: '<name>'`` (raised by
# the low-level prompts/get handler, which mcp turns into ``ErrorData(code=0,
# message=str(exc))``, echoing the name to the caller BEFORE any FastMCP
# middleware can intervene; confirmed by the probe on this stack). This wraps the
# raw ``_mcp_server.request_handlers`` for CallTool / ReadResource / GetPrompt as
# the OUTERMOST layer so no requested name/URI (nor its code points) can reach the
# JSON-RPC error frame. All messages are fixed server-authored constants.


class _ProtocolError(Exception):
    """A dispatch-level failure re-raised with a FIXED, input-free message."""


def _is_structured_envelope(result: CallToolResult) -> bool:
    """True if an isError CallToolResult carries one of OUR JSON envelopes.

    Distinguishes a structured mondo-link error (already name-free, e.g. the Layer-1
    unknown-tool frame) from a RAW FastMCP dispatch error whose plain text echoes
    the caller-supplied tool name.
    """
    if not result.content:
        return False
    text = getattr(result.content[0], "text", None)
    if not isinstance(text, str):
        return False
    try:
        obj = json.loads(text)
    except (ValueError, TypeError):
        return False
    return isinstance(obj, dict) and "error_code" in obj


def _fixed_tool_not_found_result() -> ServerResult:
    """A fixed, name-free CallToolResult for an unknown/failed tool dispatch."""
    envelope = build_unknown_tool_envelope()
    return ServerResult(
        CallToolResult(
            content=[TextContent(type="text", text=json.dumps(envelope))],
            structuredContent=envelope,
            isError=True,
        )
    )


def install_protocol_error_handler(mcp: FastMCP) -> None:
    """Wrap the raw tool/resource/prompt request handlers so a FastMCP-core
    not-found (or read) error can never reflect the caller-supplied name/URI.

    Install AFTER all tools/resources are registered (so the handlers exist) and
    as the OUTERMOST wrapper on ``CallToolRequest``.
    """
    handlers = mcp._mcp_server.request_handlers

    call_tool = handlers.get(CallToolRequest)
    if call_tool is not None:

        async def wrapped_call_tool(
            request: CallToolRequest,
            *,
            _orig: Any = call_tool,
        ) -> ServerResult:
            try:
                result = cast(ServerResult, await _orig(request))
            except Exception:
                # A registered tool never raises here (run_mcp_tool returns an
                # envelope); any exception is a dispatch-level failure whose
                # message would echo the caller name -- mask it.
                logger.warning("mcp_protocol_error kind=tool")
                return _fixed_tool_not_found_result()
            root = getattr(result, "root", None)
            if (
                isinstance(root, CallToolResult)
                and root.isError
                and not _is_structured_envelope(root)
            ):
                # FastMCP RETURNS an isError result echoing "Unknown tool: '<name>'"
                # for the return-path; replace any non-structured isError frame.
                logger.warning("mcp_protocol_error kind=tool")
                return _fixed_tool_not_found_result()
            return result

        handlers[CallToolRequest] = wrapped_call_tool

    for request_type, message, kind in (
        (ReadResourceRequest, _UNKNOWN_RESOURCE_MESSAGE, "resource"),
        (GetPromptRequest, _UNKNOWN_PROMPT_MESSAGE, "prompt"),
    ):
        orig = handlers.get(request_type)
        if orig is None:
            continue

        async def wrapped(
            request: Any,
            *,
            _orig: Any = orig,
            _message: str = message,
            _kind: str = kind,
        ) -> Any:
            try:
                return await _orig(request)
            except Exception as exc:
                # Re-raise with a FIXED, input-free message so no requested
                # name/URI (or its code points) reaches the JSON-RPC error frame.
                # Log the exception CLASS only (never the caller-controlled value).
                logger.warning("mcp_protocol_error kind=%s type=%s", _kind, type(exc).__name__)
                raise _ProtocolError(_message) from None

        handlers[request_type] = wrapped
