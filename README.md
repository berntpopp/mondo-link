# mondo-link

[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![CI](https://github.com/berntpopp/mondo-link/actions/workflows/ci.yml/badge.svg)](https://github.com/berntpopp/mondo-link/actions/workflows/ci.yml)
[![Conformance](https://github.com/berntpopp/mondo-link/actions/workflows/conformance.yml/badge.svg)](https://github.com/berntpopp/mondo-link/actions/workflows/conformance.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

MCP server that grounds disease work in the [Mondo Disease Ontology](https://mondo.monarchinitiative.org/).
It builds a local SQLite index from the Mondo OBO + SSSOM releases and serves read-only
Streamable-HTTP tools for disease-term lookup, the `is_a` hierarchy, and cross-ontology
mapping (OMIM ↔ Orphanet ↔ DOID ↔ NCIT ↔ UMLS ↔ MeSH ↔ MONDO …).

> [!IMPORTANT]
> Research use only. Not clinical decision support. Do not use for diagnosis,
> treatment, triage, or patient management.

## Why

Mondo ships as an **ontology release file, not a queryable API**. There is no upstream
service to ask "which MONDO term is OMIM:182212?" or "what are the descendants of this
disease?" — a consumer must download the OBO, parse it, compute the transitive `is_a`
closure itself, and reconcile the OBO's `xref:` lines against the separately published
SSSOM mapping set (whose `obo/` PURL 404s).

`mondo-link` does that once, at build time: an atomic SQLite index with full-text search,
a precomputed closure, and a merged, predicate-ranked cross-reference table. There is no
live API behind it, so lookups are offline, fast, and no upstream can rate-limit or
throttle you.

## Quick start

The server is hosted — no install required:

```bash
claude mcp add --transport http mondo-link --scope user https://mondo-link.genefoundry.org/mcp
```

To run it yourself (Python 3.12+, [uv](https://github.com/astral-sh/uv)):

```bash
make install        # uv sync --group dev
make data           # REQUIRED: download Mondo (OBO + SSSOM) and build the local index
make data-status    # print the loaded Mondo release + counts
make dev            # unified REST + MCP server on http://127.0.0.1:8000 (MCP at /mcp)
curl -s http://127.0.0.1:8000/health
```

`make data` is mandatory — with no index every tool returns `upstream_unavailable`. For
Claude Desktop and other stdio hosts, `make mcp-serve` runs the stdio server instead.
HTTP deployments enforce exact Host/Origin allowlists; see
[deployment.md](docs/deployment.md#host--origin-allowlists) before putting one behind a
proxy.

## Tools

| Tool | Purpose |
|------|---------|
| `get_server_capabilities` | Discovery surface: tools, workflows, error taxonomy, limits |
| `get_diagnostics` | Index status, loaded Mondo release, counts, latency percentiles |
| `resolve_disease` | Label, synonym, MONDO id or external CURIE → one canonical term |
| `resolve_disease_batch` | Resolve many queries in one call; per-item errors, never fails wholesale |
| `search_diseases` | Full-text search over name, synonyms and definition |
| `get_disease` | The record: definition, synonyms, grouped xrefs, parents/children, obsolescence |
| `get_disease_batch` | Fetch many records in one call; per-item errors, sparse projection |
| `get_disease_ancestors` | Transitive `is_a` ancestors (precomputed closure) |
| `get_disease_descendants` | Transitive `is_a` descendants |
| `get_disease_parents` | Direct `is_a` parents |
| `get_disease_children` | Direct `is_a` children |
| `resolve_xref` | External CURIE → MONDO ids, ranked by mapping predicate |
| `map_cross_ontology` | A MONDO term → its mappings grouped by target prefix |

Leaf names are **unprefixed** here (`serverInfo.name` = `mondo-link`) per Tool-Naming
Standard v1. The GeneFoundry router applies the canonical gateway namespace token `mondo`
at mount time, so `resolve_disease` surfaces as `mondo_resolve_disease` behind the
federated endpoint.

Every response carries `_meta.next_commands` (ready-to-call follow-ups); ids are normalised
to `MONDO:NNNNNNN`; `response_mode` ∈ `minimal | compact | standard | full` (default
`compact`). Worked examples: [usage.md](docs/usage.md).

## Data & provenance

The index is built from the Mondo **OBO** release (`purl.obolibrary.org/obo/mondo.obo`)
plus the consolidated **SSSOM** cross-ontology mappings from the Mondo repository. SSSOM is
*supplementary and optional*: the OBO already carries dbxrefs, so if SSSOM is unavailable
the index still builds from the OBO alone — cross-references present, curated SSSOM
predicates omitted.

Downloads are conditional (ETag / Last-Modified; `304` → no rebuild) and the build is
atomic under a lock, recording provenance — Mondo release, source validators, counts — in a
`meta` table. `get_diagnostics` and `make data-status` report the loaded release.

Ground every claim in the index and cite the **MONDO id + Mondo release version**. Mondo
data are CC BY 4.0 (Monarch Initiative); cite:

> Vasilevsky NA, Matentzoglu NA, Toro S, et al. *Mondo: Unifying diseases for the world,
> by the world.* medRxiv 2022.04.13.22273750. doi:10.1101/2022.04.13.22273750.

Sources, refresh mechanics and the full citation contract: [data.md](docs/data.md).

## Documentation

- [Data & provenance](docs/data.md) — sources, the mandatory build, refresh model, licence, citation.
- [Usage](docs/usage.md) — per-tool examples, response modes, error taxonomy, workflows.
- [Architecture](docs/architecture.md) — the two planes, ingest pipeline, SQLite schema, request lifecycle.
- [Deployment](docs/deployment.md) — Docker, every `MONDO_LINK_*` variable, Host/Origin allowlists, refresh scheduling, health.
- [AGENTS.md](AGENTS.md) / [CLAUDE.md](CLAUDE.md) — contributor + agent guide; [CHANGELOG.md](CHANGELOG.md) — release history.

## Contributing

See [`AGENTS.md`](AGENTS.md) for engineering conventions (two-plane boundary, invariants,
layout). `make ci-local` is the definition-of-done gate: format, lint, line budget, README
standard, mypy, and tests.

## License

Code: [MIT](LICENSE) © 2026 Bernt Popp. Data: the Mondo Disease Ontology is distributed by
the Monarch Initiative under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) and
must be cited as above.
