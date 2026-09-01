"""Guard: the deployed overlay must declare a numeric non-root user, and the
release Compose files must not -- the fleet controller's deployment contract and
the shared release gate make opposite demands on the same `user` field.
Research use only; not clinical decision support."""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]  # tests/unit/<file> -> repo root
NPM_COMPOSE = ROOT / "docker" / "docker-compose.npm.yml"
RELEASE_MANIFEST = ROOT / "container-release.json"

NUMERIC_USER = re.compile(r"^[1-9][0-9]*:[1-9][0-9]*$")


class _TagTolerantLoader(yaml.SafeLoader):
    """A SafeLoader that tolerates Compose's custom override/reset tags.

    `docker-compose.prod.yml` uses `!reset` / `!override` tags that plain
    `yaml.safe_load` cannot parse; this loader treats any unknown `!` tag as a
    plain scalar (or None for non-scalar nodes) so the file can still be read.
    """


_TagTolerantLoader.add_multi_constructor(
    "!",
    lambda loader, suffix, node: (
        loader.construct_scalar(node) if isinstance(node, yaml.ScalarNode) else None
    ),
)


def _load(path: Path) -> dict:
    # _TagTolerantLoader subclasses SafeLoader (no arbitrary object instantiation);
    # ruff's S506 only checks the loader name, not the class hierarchy.
    return yaml.load(path.read_text(encoding="utf-8"), Loader=_TagTolerantLoader)  # noqa: S506


def test_deployed_overlay_declares_a_numeric_non_root_user() -> None:
    """Every service in the deployed npm overlay declares a numeric `uid:gid`.

    The fleet controller (`strato_v6_docker_npm`,
    `scripts/utils/deployment_preflight.py`) accepts a declared user only as
    numeric non-root, and its runtime observer proves the effective uid from
    `/proc` -- a bare username (as `USER app` inspects) is rejected.
    """
    services = _load(NPM_COMPOSE)["services"]
    assert services, "docker-compose.npm.yml declares no services"
    for name, service in services.items():
        user = service.get("user")
        assert isinstance(user, str) and NUMERIC_USER.match(user), (
            f"{name} declares user={user!r}; it must match {NUMERIC_USER.pattern}"
        )


def test_release_compose_files_never_declare_user() -> None:
    """No release Compose file declares `user` -- the release gate forbids it.

    `container_release.py validate-compose` (`ALLOWED_SERVICE_KEYS`) rejects a
    `user` override on any service in the Compose files named in
    `container-release.json`, the exact opposite of the deploy contract above.
    """
    manifest = json.loads(RELEASE_MANIFEST.read_text(encoding="utf-8"))
    for compose_file in manifest["service"]["compose_files"]:
        compose = _load(ROOT / compose_file)
        for name, service in (compose.get("services") or {}).items():
            assert "user" not in service, (
                f"{compose_file}: service {name!r} declares 'user'; the release gate "
                "forbids it -- the numeric user belongs only in docker-compose.npm.yml"
            )
