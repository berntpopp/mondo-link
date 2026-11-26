"""Response-Envelope Standard v1.2: every data tool's success validates against the schema.

The schema is vendored from berntpopp/genefoundry-envelope, tag v1.2.0
(https://raw.githubusercontent.com/berntpopp/genefoundry-envelope/v1.2.0/envelope-v1.2.schema.json).
"""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from mondo_link.config import DATA_RETRIEVED_AT
from mondo_link.mcp import envelope_frame
from mondo_link.mcp.service_adapters import set_mondo_service
from mondo_link.services.mondo_service import MondoService

SCHEMA_PATH = Path(__file__).with_name("envelope-v1.2.schema.json")
SCHEMA_SHA256 = "c3c47e20d7dd189aac2568fc963b5514d7a5ac2c3e5f21d126f42c96d1ad1ae3"
SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
OPS_TOOLS = {"get_server_capabilities", "get_diagnostics"}
MODES = ("minimal", "compact", "standard", "full")


async def _data_calls(facade: Any) -> dict[str, dict[str, Any]]:
    """One call per data tool, built from each required parameter's own ``examples``."""
    calls = {}
    for tool in await facade.list_tools():
        if tool.name in OPS_TOOLS:
            continue
        props = tool.parameters["properties"]
        calls[tool.name] = {k: props[k]["examples"][0] for k in tool.parameters["required"]}
    return calls


def test_vendored_schema_is_the_v1_2_0_release() -> None:
    assert sha256(SCHEMA_PATH.read_bytes()).hexdigest() == SCHEMA_SHA256


@pytest.mark.parametrize("mode", MODES)
async def test_every_data_tool_validates_and_carries_provenance(
    facade: Any, structured: Any, mode: str
) -> None:
    calls = await _data_calls(facade)
    assert len(calls) == 11
    for tool, arguments in calls.items():
        envelope = structured(await facade.call_tool(tool, {**arguments, "response_mode": mode}))
        assert envelope["success"] is True, tool
        jsonschema.validate(envelope, SCHEMA)
        meta = envelope["_meta"]
        assert meta["source"] == "mondo", tool
        assert "2026-06-01" in meta["data_version"], tool  # the fixture's Mondo release
        assert meta["retrieved_at"].endswith("Z"), tool


def test_retrieved_at_is_the_download_time(
    service: MondoService, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_mondo_service(service)
    try:
        # A locally built index: downloaded and built in one step.
        meta = service.repo.read_meta()
        assert meta["build_duration_s"] is not None
        assert envelope_frame.provenance()["retrieved_at"][:19] == meta["build_utc"][:19]
        # A reproducible release build pins build_utc to the OBO's Last-Modified, which is
        # not a download time: it reports the pinned data release's.
        monkeypatch.setattr(service.repo, "read_meta", lambda: {**meta, "build_duration_s": None})
        assert envelope_frame.provenance()["retrieved_at"] == DATA_RETRIEVED_AT
    finally:
        set_mondo_service(None)


@pytest.mark.parametrize("tool", sorted(OPS_TOOLS))
async def test_ops_tools_keep_their_shape(facade: Any, structured: Any, tool: str) -> None:
    envelope = structured(await facade.call_tool(tool, {}))
    assert "result" not in envelope and "retrieved_at" not in envelope["_meta"]


@pytest.mark.parametrize("mode", MODES)
async def test_error_envelope_validates_too(facade: Any, structured: Any, mode: str) -> None:
    result = await facade.call_tool("get_disease", {"term": "MONDO:9999999", "response_mode": mode})
    envelope = structured(result)
    assert envelope["success"] is False
    jsonschema.validate(envelope, SCHEMA)
