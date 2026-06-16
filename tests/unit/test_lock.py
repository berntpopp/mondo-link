"""Unit tests for the cross-process build lock."""

from __future__ import annotations

from pathlib import Path

import pytest

from mondo_link.exceptions import DataUnavailableError
from mondo_link.ingest.lock import build_lock


def test_build_lock_acquires(tmp_path: Path) -> None:
    with build_lock(tmp_path, timeout=5) as held:
        assert held is True
    # re-acquire after release works
    with build_lock(tmp_path, timeout=5) as held2:
        assert held2 is True


def test_build_lock_timeout_raises(tmp_path: Path) -> None:
    import mondo_link.ingest.lock as lock_mod

    if not lock_mod._HAVE_FCNTL:  # pragma: no cover - non-POSIX
        pytest.skip("fcntl not available")
    held = build_lock(tmp_path, timeout=5)
    held.__enter__()
    try:
        with pytest.raises(DataUnavailableError), build_lock(
            tmp_path, timeout=1, poll_interval=0.1
        ):
            pass
    finally:
        held.__exit__(None, None, None)
