"""FastAPI host for mondo-link (thin: health + service info + data bootstrap)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from mondo_link import __version__
from mondo_link.buildinfo import build_info
from mondo_link.config import settings
from mondo_link.logging_config import configure_logging
from mondo_link.services.refresh import (
    bootstrap_data,
    start_refresh_scheduler,
    stop_refresh_scheduler,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


def _validate_cors(origins: list[str], *, allow_credentials: bool) -> None:
    """Fail closed on the credentialed-CORS-with-wildcard-origin footgun.

    mondo-link is unauthenticated by design and holds no cookies/session, so
    credentialed CORS is meaningless; combining it with a wildcard ``*`` origin
    is also forbidden by the CORS spec. Refuse to start rather than serve it.
    """
    if allow_credentials and "*" in origins:
        raise RuntimeError(
            "Refusing to start: allow_credentials=True with a wildcard '*' CORS "
            "origin is unsafe and forbidden by the CORS spec. mondo-link is "
            "unauthenticated by design (no cookies/session); leave credentials off."
        )


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    """Bootstrap the Mondo index and (optionally) start the refresh scheduler."""
    logger = configure_logging()
    logger.info("mondo-link starting", host=settings.host, port=settings.port)
    await bootstrap_data(settings.data, logger)
    refresh_task = start_refresh_scheduler(settings.data, logger)
    try:
        yield
    finally:
        await stop_refresh_scheduler(refresh_task)
        logger.info("mondo-link shutting down")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="mondo-link",
        description="MCP/API server grounding disease work in the Mondo Disease Ontology.",
        version=__version__,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # Unauthenticated backend: no cookies/session, so credentialed CORS is
    # meaningless (and a footgun with wildcard origins). Keep it off; preserve
    # the GET/POST/OPTIONS method list (serves GET /health and root).
    allow_credentials = False
    _validate_cors(settings.cors_origins, allow_credentials=allow_credentials)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=allow_credentials,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    @app.get("/health")
    async def health() -> dict[str, Any]:
        """Liveness probe (reports build provenance for deploy checks)."""
        return {
            "status": "ok",
            "service": "mondo-link",
            "transport": "streamable-http-stateless",
            **build_info(),
        }

    @app.get("/")
    async def root() -> dict[str, Any]:
        """Service information."""
        return {
            "name": "mondo-link",
            "version": __version__,
            "data_source": "Mondo Disease Ontology (Monarch PURL) -> local SQLite index",
            "mcp_endpoint": settings.mcp_path,
            "docs": "/docs",
            "health": "/health",
        }

    return app


app = create_app()
