#!/usr/bin/env python3
"""Enforce a per-file line budget to keep modules focused and reviewable.

Run via `make lint-loc`. Fails (exit 1) if any tracked Python file exceeds the
soft cap, so a module that has grown too large gets split rather than sprawling.

VENDORED FILES ARE EXEMPT, AND THE EXEMPTION IS DERIVED
-------------------------------------------------------
The fleet conformance probes in ``tests/conformance/`` are vendored BYTE-IDENTICAL
from ``genefoundry-router``, which is their single source of truth. They are not ours
to split: splitting one would break the vendoring contract that makes every ``-link``
repo run the same gate, and the next sync would silently revert the split anyway.

The exemption is read from each file's OWN docstring ("vendored"), never from a
hardcoded list of filenames here. A hardcoded list is the same bug one level up:
whoever adds the next vendored probe forgets the list, the build breaks, and the fix
under time pressure is to shrink the budget's reach rather than the file. Derived
means a new vendored probe is exempt the day it lands, and a repo-authored file in the
same directory is still budgeted.
"""

from __future__ import annotations

import sys
from pathlib import Path

MAX_LINES = 500
ROOTS = ("mondo_link", "tests")
EXTRA_FILES = ("server.py", "mcp_server.py")

#: Only files under this directory may claim the exemption, and only by SAYING so in
#: their own docstring. Both conditions must hold.
VENDORED_DIR = "tests/conformance"
VENDORED_MARKER = "vendored"
_DOCSTRING_WINDOW = 1200


def is_vendored(path: Path, repo: Path) -> bool:
    """True when ``path`` is a fleet probe vendored verbatim from genefoundry-router."""
    relative = path.relative_to(repo).as_posix()
    if not relative.startswith(f"{VENDORED_DIR}/"):
        return False
    head = path.read_text(encoding="utf-8")[:_DOCSTRING_WINDOW].lower()
    return VENDORED_MARKER in head


def main() -> int:
    """Report files over the line budget; return non-zero if any are found."""
    repo = Path(__file__).resolve().parents[1]
    offenders: list[tuple[Path, int]] = []
    exempt = 0
    paths: list[Path] = [repo / f for f in EXTRA_FILES]
    for root in ROOTS:
        paths.extend((repo / root).rglob("*.py"))
    for path in paths:
        if not path.exists():
            continue
        lines = path.read_text(encoding="utf-8").count("\n") + 1
        if lines <= MAX_LINES:
            continue
        if is_vendored(path, repo):
            exempt += 1
            continue
        offenders.append((path.relative_to(repo), lines))
    for rel, lines in sorted(offenders):
        print(f"{rel}: {lines} lines (> {MAX_LINES})")
    if offenders:
        print(f"\n{len(offenders)} file(s) exceed the {MAX_LINES}-line budget.")
        return 1
    suffix = f" ({exempt} vendored file(s) exempt)" if exempt else ""
    print(f"OK: all files within the {MAX_LINES}-line budget{suffix}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
