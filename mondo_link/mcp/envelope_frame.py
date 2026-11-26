"""Framing of a successful result: `_meta` tiers, the payload key and provenance.

Split from :mod:`mondo_link.mcp.envelope` (line budget). Response-Envelope Standard v1 §1
(payload under ``results``/``result``), §4 (``elapsed_ms``) and v1.2 (provenance).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from mondo_link.config import DATA_RETRIEVED_AT

#: `_meta` keys no ``response_mode`` drops: the trace essentials and disclaimer, plus what
#: Response-Envelope v1 §4 (`elapsed_ms`) and v1.2 (provenance) require on every result.
_META_CORE = (
    "tool",
    "request_id",
    "unsafe_for_clinical_use",
    "elapsed_ms",
    "source",
    "data_version",
    "retrieved_at",
)


def shape_meta(meta: dict[str, Any], response_mode: str) -> dict[str, Any]:
    """Tier ``_meta`` verbosity by ``response_mode`` to control the per-call token tax.

    - ``minimal``: only :data:`_META_CORE`. The caller explicitly opted out of guidance,
      so ``next_commands`` / ``capabilities_version`` are dropped.
    - ``compact`` and richer: the complete ``_meta``.

    The universal ``next_commands`` invariant therefore holds for ``compact`` and
    richer (every default response still chains); ``minimal`` is the documented opt-out.
    """
    if response_mode == "minimal":
        return {k: meta[k] for k in _META_CORE if k in meta}
    return meta


# Ops/meta tools return no upstream data: no payload key, no provenance (v1.2 carve-out).
OPS_TOOLS = frozenset({"get_server_capabilities", "get_diagnostics"})
# Response-Envelope v1 §1: the payload key is `results` (array) or `result` (object). The
# service's own key per tool; a data tool not listed is a single record, nested under `result`.
_PAYLOAD_KEY = {
    "get_disease_ancestors": "ancestors",
    "get_disease_descendants": "descendants",
    "get_disease_parents": "parents",
    "get_disease_children": "children",
    "resolve_xref": "matches",
    "map_cross_ontology": "mappings",
}
# Stay beside the payload. `mondo_version` is deprecated there (it is `_meta.data_version`).
_FRAME_KEYS = ("success", "_meta", "mondo_version")


def frame_payload(tool_name: str, result: dict[str, Any]) -> dict[str, Any]:
    """Return ``result`` with its payload under ``results``/``result`` (a new dict)."""
    if "results" in result or "result" in result:
        return result
    key = _PAYLOAD_KEY.get(tool_name)
    if key in result:
        name = "results" if isinstance(result[key], list) else "result"
        return {(name if k == key else k): v for k, v in result.items()}
    frame = {k: v for k, v in result.items() if k in _FRAME_KEYS}
    return {"result": {k: v for k, v in result.items() if k not in frame}, **frame}


def provenance() -> dict[str, str]:
    """Response-Envelope v1.2 ``_meta`` provenance of the served index.

    Never raises, like the capabilities echo: the ``_meta`` stamp must not break a tool.
    """
    try:
        from mondo_link.mcp.service_adapters import get_mondo_service

        meta = get_mondo_service().repo.read_meta()
        # A reproducible release build pins build_utc to the OBO's Last-Modified and records
        # no duration; its download time is the pinned release's. A local build downloads
        # and builds in one step, so build_utc is its download time.
        # ponytail: any release build reports the pinned release's time; store the download
        # time in the index if more than one release build is ever served.
        if meta.get("build_duration_s") is None:
            retrieved = DATA_RETRIEVED_AT
        else:
            built = datetime.fromisoformat(meta["build_utc"]).astimezone(UTC)
            retrieved = built.isoformat(timespec="seconds").replace("+00:00", "Z")
        return {
            "source": "mondo",
            "data_version": meta.get("mondo_version") or meta["build_utc"],
            "retrieved_at": retrieved,
        }
    except Exception:
        return {}
