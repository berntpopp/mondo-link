# Deployment

## Docker

```bash
make docker-build
make docker-up        # starts the stack on a random free host port
make docker-url       # prints the MCP URL + a `claude mcp add` snippet
make docker-logs
make docker-down
```

The container runs the unified server (FastAPI `/health` + MCP `/mcp`) and
serves one **pinned, digest-verified data release** (`external-reference`,
genefoundry-router#63 option A); it never downloads from the Monarch PURLs.

- A one-shot **`mondo-data-init`** service runs `mondo-link-data materialize-data`
  before the server starts. It downloads the image's pinned `data-mondo-*` GitHub
  release asset of this repository (`github.com` /
  `release-assets.githubusercontent.com` only), verifies the compressed and
  expanded SHA-256 and the schema, writes `/data/<digest>/mondo.sqlite`, and
  atomically points `/data/current` at it. A restart re-verifies the existing
  snapshot without downloading.
- The **server mounts the volume read-only** and serves
  `/data/current/mondo.sqlite`; `/health` reports `release_identity.data_identity`
  (`expected` from the pin, `actual` re-proved from the served bytes). No in-app
  refresh and no `refresh` service.

### Operator steps (fleet host)

1. Pull the released image by digest and set `MONDO_LINK_IMAGE` as before.
2. `docker compose -f docker/docker-compose.npm.yml --env-file .env.docker up -d`.
   Compose starts `mondo-data-init` first (outbound HTTPS to `github.com` and
   `release-assets.githubusercontent.com`, ~41 MB download, ~177 MB on disk),
   waits for exit `0`, then starts the server. No manual staging is required.
3. Verify: `docker compose -f docker/docker-compose.npm.yml logs mondo-data-init`
   shows `Materialized Mondo data release data-mondo-…`, and `/health`
   `release_identity` shows `expected == actual` with the tag/digest from
   `container-release.json`.
4. Optional cleanup: the old files in the existing `mondo-data` volume
   (`mondo.sqlite*`, `mondo.obo`, `mondo.sssom.tsv`, `download_cache.json`,
   `.build.lock`) are no longer read and may be deleted; the restore writes only
   `<digest>/` and `current`.
5. Remove any host cron line that ran `docker compose ... run --rm refresh` or
   `exec ... mondo-link-data refresh`; containers no longer refresh.

### Data releases and re-pinning

`.github/workflows/data-release.yml` (weekly, or a manual dispatch on `main`)
downloads `mondo.obo` and `mondo.sssom.tsv` once and runs
`mondo-link-data build --reproducible` (`build_utc` pinned to the OBO
`Last-Modified`, no build duration, closure emitted sorted, rollback journal) and
`mondo-link-data package`, producing the content-addressed tag
`data-mondo-<first 16 hex of the SQLite SHA-256>` with `mondo.sqlite.gz`,
`manifest.json` (Mondo release version, source URLs/validators/SHA-256s, counts,
commit, licence, attribution, change notice), `data-authorization.json` and
`SHA256SUMS`. Unchanged inputs are a no-op; a new tag waits on the
`data-release` environment (owner review, `main` only), is attested, verified and
published (never as "Latest"). Re-pin in one reviewed PR: `DATA_RELEASE_TAG`,
`DATA_COMPRESSED_SHA256` and `DATA_EXPANDED_SHA256` in `mondo_link/config.py` plus
the same tag + `sha256:` digest in `container-release.json`
(`tests/unit/test_data_release_identity.py` fails if they disagree), then release
the app; the release workflow's runtime data identity check is the proof.

Licence: Mondo is CC BY 4.0. `release/data-authorization.json` records the
repository owner's 2026-11-13 redistribution decision (the owner's determination,
not a Monarch approval), the attribution and the change notice every release
carries.

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
| `MONDO_LINK_DATA__DB_FILENAME` | `mondo.sqlite` (image: `current/mondo.sqlite`) | SQLite filename. |
| `MONDO_LINK_DATA__OBO_URL` | Monarch PURL | `mondo.obo` source. |
| `MONDO_LINK_DATA__SSSOM_URL` | Monarch PURL | `mondo.sssom.tsv` source. |
| `MONDO_LINK_DATA__DOWNLOAD_TIMEOUT` | `300` | Seconds. |
| `MONDO_LINK_DATA__AUTO_BOOTSTRAP` | `true` (image: `false`) | Build the index on first use if absent (source checkout only). |
| `MONDO_LINK_DATA__REFRESH_ENABLED` | `false` | In-process periodic refresh. |
| `MONDO_LINK_DATA__REFRESH_INTERVAL_HOURS` | `168` | Refresh cadence (weekly). |
| `MONDO_LINK_DATA__BUILD_LOCK_TIMEOUT` | `900` | Seconds to wait for the build lock. |

The source URLs and the SSSOM-is-optional contract are documented in
[data.md](data.md#sources).

### Pinned data release (`MONDO_LINK_IMMUTABLE_DATA__*`, init sidecar)

Defaults are the reviewed pin in `mondo_link/config.py`; override only for a
reviewed re-pin.

| Variable | Default | Notes |
|----------|---------|-------|
| `MONDO_LINK_IMMUTABLE_DATA__REFERENCE_ROOT` | `/data` | Volume root holding `<digest>/mondo.sqlite` and `current`. |
| `MONDO_LINK_IMMUTABLE_DATA__RELEASE_TAG` | pinned `data-mondo-<sha16>` | Published data release tag of this repo. |
| `MONDO_LINK_IMMUTABLE_DATA__BUNDLE_URL` | that release's `mondo.sqlite.gz` | HTTPS `github.com` release-asset URL under the tag. |
| `MONDO_LINK_IMMUTABLE_DATA__COMPRESSED_SHA256` | pinned | SHA-256 of `mondo.sqlite.gz` (the release digest). |
| `MONDO_LINK_IMMUTABLE_DATA__EXPANDED_SHA256` | pinned | SHA-256 of the expanded `mondo.sqlite`. |
| `MONDO_LINK_IMMUTABLE_DATA__MAX_COMPRESSED_BYTES` | `134217728` | Download size cap. |
| `MONDO_LINK_IMMUTABLE_DATA__MAX_EXPANDED_BYTES` | `1073741824` | Decompressed size cap. |

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

## Data refresh (source checkout only)

Containers do not refresh (see above). For a source checkout, two options,
mutually compatible:

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
build provenance (version, git SHA) is included for deploy verification, plus
`release_identity.data_identity` when the served file is the pinned data release.
Container healthchecks send `Host: localhost`, which is included in the default exact allowlist.
