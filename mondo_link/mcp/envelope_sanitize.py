"""Error-envelope sanitisation: trusted-name re-derivation, id projection, code-point scrub.

Split from :mod:`mondo_link.mcp.envelope` to keep that module within the 500-line budget.
These are the injection-safety primitives the error path leans on:

- :func:`valid_id_entries` projects candidate/suggestion/replacement entries to a
  grammar-validated ``mondo_id`` plus a name RE-DERIVED from the DB (never the exception's
  free-text) -- so a hostile candidate label can never reach a caller-visible field.
- :func:`sanitize_tree` is the whole-envelope code-point backstop.
"""

from __future__ import annotations

import re
from typing import Any

from mondo_link.mcp.untrusted_content import sanitize_message

#: Canonical MONDO id grammar. Caller-visible identifier echoes (candidates, replaced_by,
#: next_commands arguments) are surfaced ONLY when they match this, so an upstream/exception
#: label can never smuggle prose through them.
_MONDO_ID_RE = re.compile(r"^MONDO:\d{7}$")


def trusted_db_name(mondo_id: str) -> str | None:
    """The term's TRUSTED primary label, re-derived from the DB by a validated id.

    The candidate ``name`` is NEVER taken from the exception: an exception attribute is
    free-text that could carry injection prose surviving code-point stripping. The only
    trusted source is the same ``term.name`` DB lookup the success path uses, keyed on a
    grammar-validated ``mondo_id``. Best-effort: any failure (index unbuilt, id absent)
    yields ``None`` and the candidate degrades to id-only rather than echoing anything
    unverified.
    """
    try:
        from mondo_link.mcp.service_adapters import get_mondo_service

        record = get_mondo_service().repo.get_term(mondo_id)
    except Exception:  # index unbuilt / lookup failed -> no name, never raise
        return None
    if not isinstance(record, dict):
        return None
    name = record.get("name")
    return name if isinstance(name, str) and name else None


def valid_id_entries(entries: Any) -> list[dict[str, Any]]:
    """Project candidate/suggestion/replacement entries to ``{mondo_id, name[, score]}``.

    The ``mondo_id`` must match the canonical grammar (a non-conforming entry is dropped
    entirely). The ``name`` is RE-DERIVED from the DB for that validated id via
    :func:`trusted_db_name` -- NOT copied from the exception, whose ``name`` is free-text
    that could carry injection prose. This makes the label provably trusted (the same
    ``term.name`` every success payload returns) and is what makes the candidate list
    actionable from the error alone; when the DB cannot vouch for the id the candidate is
    id-only. The name is still ``sanitize_message``-scrubbed (whole envelope by
    :func:`sanitize_tree`). A numeric ``score`` (injection-safe) rides along when present.
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
        name = trusted_db_name(mondo_id)
        if name:
            item["name"] = sanitize_message(name)
        score = entry.get("score")
        if isinstance(score, int | float) and not isinstance(score, bool):
            item["score"] = score
        out.append(item)
    return out


def sanitize_tree(value: Any) -> Any:
    """Recursively code-point-strip every string leaf of a built error envelope.

    A last-step backstop ON TOP OF the fixed-message/redaction discipline: it strips the
    forbidden control/zero-width/bidi/NUL code points from every string (message, field,
    allowed_values, hint, candidates[*].name, replaced_by, ``_meta.next_commands[*].
    arguments.*`` -- the caller's own query echoed into a recovery step) without reshaping
    the structure. It does not make prose safe; prose is kept safe by never interpolating
    attacker-influenced text above.
    """
    if isinstance(value, str):
        return sanitize_message(value)
    if isinstance(value, dict):
        return {key: sanitize_tree(item) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize_tree(item) for item in value]
    return value
