"""Unit tests for the in-process runtime metrics collector."""

from __future__ import annotations

from mondo_link.mcp import metrics


def test_empty_snapshot_is_zeroed() -> None:
    metrics.reset()
    snap = metrics.snapshot()
    assert snap["requests"] == 0
    assert snap["errors"] == 0
    assert snap["error_rate"] == 0.0
    assert snap["latency_ms"]["p95"] == 0
    assert snap["per_tool"] == {}


def test_percentiles_and_counts() -> None:
    metrics.reset()
    for i in range(1, 101):  # latencies 1..100 ms, all successful
        metrics.record("get_disease", i, ok=True)
    snap = metrics.snapshot()
    assert snap["requests"] == 100
    assert snap["errors"] == 0
    lat = snap["latency_ms"]
    assert lat["p50"] == 50
    assert lat["p95"] == 95
    assert lat["p99"] == 99
    assert lat["max"] == 100
    assert lat["sampled"] == 100
    assert snap["per_tool"]["get_disease"]["requests"] == 100


def test_error_rate_and_per_tool_errors() -> None:
    metrics.reset()
    metrics.record("resolve_xref", 5, ok=True)
    metrics.record("resolve_xref", 7, ok=False)
    snap = metrics.snapshot()
    assert snap["requests"] == 2
    assert snap["errors"] == 1
    assert snap["error_rate"] == 0.5
    assert snap["per_tool"]["resolve_xref"] == {"requests": 2, "errors": 1}


def test_latency_window_is_bounded() -> None:
    metrics.reset()
    for _ in range(2000):
        metrics.record("search_diseases", 3, ok=True)
    snap = metrics.snapshot()
    assert snap["requests"] == 2000  # counters are cumulative
    assert snap["latency_ms"]["sampled"] <= 1024  # window is bounded
