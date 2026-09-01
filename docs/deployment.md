# Deployment

## Docker

```bash
make docker-build
make docker-up        # starts the stack on a random free host port
make docker-url       # prints the MCP URL + a `claude mcp add` snippet
make docker-logs
make docker-down
```

The container runs the unified server (FastAPI `/health` + MCP `/mcp`). On
first start it bootstraps the Mondo index into the data volume (unless one is
already present). Mount a persistent volume at the data directory so the index
survives restarts.

Backends are unauthenticated by design and MUST be reachable only through the
GeneFoundry router / reverse proxy — never published directly.

## Fleet deploy contract (strato_v6_docker_npm)

`docker/docker-compose.npm.yml` is the overlay the fleet controller repo
(`strato_v6_docker_npm`) actually deploys and validates — it pulls the
released, attested `ghcr.io/berntpopp/mondo-link` image at a pinned digest and
never builds from source. Every service in that file must declare a numeric
`user: "<uid>:<gid>"` (currently `999:999`, this image's own uid:gid from
`docker/Dockerfile`) because the controller's runtime observer proves the
effective uid from `/proc`. The release Compose files named in
`container-release.json` (`docker/docker-compose.yml`,
`docker/docker-compose.prod.yml`) must **not** declare `user` — the shared
release gate (`container_release.py validate-compose`) forbids it there.
`tests/unit/test_deploy_overlay_user.py` guards both rules.

Release checklist enforced by this repo: bump `pyproject.toml`, run `uv lock`,
add a `CHANGELOG.md` heading `## [x.y.z] - YYYY-MM-DD`, bump `CITATION.cff`
`version:` **and** `date-released:` to the release date (unlike some sibling
repos, mondo-link updates `date-released` on every release — and
`tests/unit/test_version_single_source.py` hardcodes the current
`version`/`date-released` pair, so it must change in the same commit), tag
`vx.y.z`, then approve the `release` environment gate:
`gh api repos/berntpopp/mondo-link/actions/runs/<id>/pending_deployments`
(`status: waiting` marks the gate; may need approving twice).

Self-check that the overlay still projects cleanly for the fleet controller:

```bash
MONDO_LINK_IMAGE="ghcr.io/berntpopp/mondo-link@sha256:<64 hex>" docker compose -f docker/docker-compose.npm.yml config --format json > /tmp/r.json
# from strato_v6_docker_npm:
uv run python -c "import sys,json; sys.path.insert(0,'scripts'); from utils.deployment_preflight import canonical_projection; canonical_projection(json.load(open('/tmp/r.json')), project='mondo-link'); print('PROJECTION OK')"
```

## Configuration

Settings are read from the environment with the `MONDO_LINK_` prefix; nested
data settings use a `__` delimiter (`pydantic-settings`).

### Server

| Variable | Default | Notes |
|----------|---------|-------|
| `MONDO_LINK_HOST` | `127.0.0.1` | Bind host. |
| `MONDO_LINK_PORT` | `8000` | Bind port. |
| `MONDO_LINK_TRANSPORT` | `unified` | `unified` \| `http` \| `stdio`. |
| `MONDO_LINK_MCP_PATH` | `/mcp` | MCP mount path (must start with `/`). |
| `MONDO_LINK_ALLOWED_HOSTS` | loopback hosts | JSON list of exact accepted Host values; add the public proxy hostname. Wildcards are rejected. |
| `MONDO_LINK_ALLOWED_ORIGINS` | `[]` | JSON list of accepted browser Origins; requests without Origin remain allowed. |
| `MONDO_LINK_CORS_ORIGINS` | localhost dev origins | JSON list. |
| `MONDO_LINK_LOG_LEVEL` | `INFO` | `DEBUG`…`CRITICAL`. |
| `MONDO_LINK_LOG_FORMAT` | `console` | `console` \| `json` (logs go to stderr). |

### Data (`MONDO_LINK_DATA__*`)

| Variable | Default | Notes |
|----------|---------|-------|
| `MONDO_LINK_DATA__DATA_DIR` | `<project>/data` | Index + cache directory. |
| `MONDO_LINK_DATA__DB_FILENAME` | `mondo.sqlite` | SQLite filename. |
| `MONDO_LINK_DATA__OBO_URL` | Monarch PURL | `mondo.obo` source. |
| `MONDO_LINK_DATA__SSSOM_URL` | Monarch PURL | `mondo.sssom.tsv` source. |
| `MONDO_LINK_DATA__DOWNLOAD_TIMEOUT` | `300` | Seconds. |
| `MONDO_LINK_DATA__AUTO_BOOTSTRAP` | `true` | Build the index on first use if absent. |
| `MONDO_LINK_DATA__REFRESH_ENABLED` | `false` | In-process periodic refresh. |
| `MONDO_LINK_DATA__REFRESH_INTERVAL_HOURS` | `168` | Refresh cadence (weekly). |
| `MONDO_LINK_DATA__BUILD_LOCK_TIMEOUT` | `900` | Seconds to wait for the build lock. |

The source URLs and the SSSOM-is-optional contract are documented in
[data.md](data.md#sources).

### Host & Origin allowlists

HTTP deployments enforce **exact** Host and Origin allowlists on every route.

- `MONDO_LINK_ALLOWED_HOSTS` — a JSON list of exact accepted `Host` values.
  Configure it to contain **the public reverse-proxy hostname** in addition to the
  loopback defaults (`localhost`, `127.0.0.1`, `::1`); otherwise a proxied request
  is rejected. Wildcards are rejected.
- `MONDO_LINK_ALLOWED_ORIGINS` — the browser-origin admission gate. It defaults to
  `[]`, which **permits requests without an `Origin` header** (i.e. non-browser
  MCP clients) while admitting no browser origin.
- `MONDO_LINK_CORS_ORIGINS` — CORS *response* headers, separate from the
  request-boundary policy above. An origin you intend to serve in a browser must
  appear in **both** lists.

## Data refresh

Two options, mutually compatible:

- **In-process:** set `MONDO_LINK_DATA__REFRESH_ENABLED=true`. The server checks
  for a new Mondo release on the configured interval and atomically rebuilds.
- **External cron:** keep refresh disabled and run `make data-refresh`
  (`mondo-link-data refresh`) on a schedule. It conditionally downloads (304 →
  no-op) and rebuilds only when the release changed.

`make data-status` (`mondo-link-data status`) prints the loaded Mondo release
and counts — use it as a readiness/freshness check.

The sources, the conditional-GET / atomic-build mechanics and the citation
contract are documented in [data.md](data.md).

## Health

`GET /health` returns `{"status": "ok", "service": "mondo-link", ...build}`. The
build provenance (version, git SHA) is included for deploy verification.
Container healthchecks send `Host: localhost`, which is included in the default exact allowlist.
