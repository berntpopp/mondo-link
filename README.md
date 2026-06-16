# mondo-link

MCP/API server that grounds disease work in the [Mondo Disease Ontology](https://mondo.monarchinitiative.org/).

mondo-link mirrors the architecture of the sibling `mgi-link` server: a local
SQLite index built from the Mondo OBO + SSSOM releases (Monarch PURLs) serves a
read-only MCP + REST surface for disease term lookup, hierarchy navigation, and
cross-ontology (OMIM / Orphanet / DOID / NCIT / UMLS / MeSH / …) mapping. There
is no live API — the local index is the only source.

## Quickstart

```bash
make install      # uv sync --group dev
make data         # download Mondo and build the local index
make dev          # unified REST + MCP server on http://127.0.0.1:8000
make mcp-serve    # local stdio MCP server (Claude Desktop)
```

## Status

Wave 0 serial foundation: project scaffold, domain primitives, MCP scaffolding,
and the frozen `MondoService` / `MondoRepository` / `schema.sql` contracts.
Tool registrations and the ingest pipeline are added by later waves.

Research use only; not for clinical decision support, diagnosis, treatment, or
patient management. The Mondo Disease Ontology is distributed under CC BY 4.0.
