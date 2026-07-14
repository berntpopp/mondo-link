# Data & provenance

`mondo-link` has **no live API**. The local SQLite index is the only source, so
every lookup is offline and fast — and the index must be built before the server
is useful.

## Build the index

```bash
make data           # uv run mondo-link-data build    — download + build (required once)
make data-refresh   # uv run mondo-link-data refresh  — rebuild only if the release changed
make data-status    # uv run mondo-link-data status   — loaded Mondo release + counts
```

`make data` is a **mandatory pre-run step**: with no index the server answers
`data_unavailable`. In Docker the entrypoint bootstraps the index into the data
volume on first start (see [deployment.md](deployment.md)).

`make data-status` is the operator's freshness check — it prints the loaded Mondo
release and the row counts without touching the MCP surface (`get_diagnostics`
reports the same provenance to a client).

## Sources

| Source | URL | Role |
|--------|-----|------|
| Mondo OBO release | `https://purl.obolibrary.org/obo/mondo.obo` | **Primary.** Terms, labels, synonyms, definitions, `is_a` edges, `xref:` dbxrefs, obsolescence / `replaced_by`. |
| Mondo SSSOM mappings | `https://raw.githubusercontent.com/monarch-initiative/mondo/master/src/ontology/mappings/mondo.sssom.tsv` | **Supplementary, optional.** Curated cross-ontology mappings with explicit predicates. |

Both are configurable (`MONDO_LINK_DATA__OBO_URL`, `MONDO_LINK_DATA__SSSOM_URL`).

Two things about SSSOM that are easy to lose:

- **The `obo/mondo.sssom.tsv` PURL 404s.** The consolidated mapping set is served
  from the Mondo repository, not from an OBO PURL — hence the `raw.githubusercontent`
  default above.
- **SSSOM is optional.** The OBO already carries dbxrefs, so if SSSOM is
  unavailable the index still builds from the OBO alone: cross-references are
  present, only the curated SSSOM predicates are omitted. An SSSOM outage
  degrades the mapping surface; it does not break the build.

## Refresh model

Downloads are **conditional GET** (`If-None-Match` / `If-Modified-Since`, cached in
`download_cache.json`); a `304` reuses the local file and skips the rebuild. The
build is **atomic** — a temp SQLite is written via `load_schema_sql()`, populated,
then `os.replace`d onto `mondo.sqlite` under an `fcntl` build lock, so a reader
never observes a half-built index.

Scheduling options (in-process vs. external cron) are documented in
[deployment.md](deployment.md#data-refresh).

## Provenance recorded in the index

The builder writes a single-row `meta` table: schema version, **Mondo release
version**, source validators (ETag / Last-Modified), row counts and build time.
`get_diagnostics` and `get_server_capabilities` report the loaded release, and
every record payload echoes `mondo_version`.

## Cross-reference model

OBO `xref:` lines and SSSOM rows are merged into one `xref` index, each row tagged
with its `origin` (`obo_xref` | `sssom`) and a mapping **predicate**, ranked
`exactMatch > equivalentTo > closeMatch > narrowMatch > broadMatch > xref`.
First-class prefixes: OMIM, ORPHA, DOID, NCIT, UMLS, MESH, MEDGEN, SCTID, GARD.
See [architecture.md](architecture.md#cross-reference-model) for the schema.

## Citation contract

Ground every claim in the index and cite the **MONDO id** plus the **Mondo release
version** (`mondo_version`, or `get_diagnostics`). Ids are normalised to
`MONDO:NNNNNNN`; external CURIEs are case-folded and `Orphanet` is normalised to
`ORPHA`.

> Vasilevsky NA, Matentzoglu NA, Toro S, et al. *Mondo: Unifying diseases for the
> world, by the world.* medRxiv 2022.04.13.22273750.
> doi:10.1101/2022.04.13.22273750. Mondo Disease Ontology, Monarch Initiative,
> <https://mondo.monarchinitiative.org/>.

## Licence

The Mondo Disease Ontology is distributed by the Monarch Initiative under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). `mondo-link`'s own code
is MIT (see [`LICENSE`](../LICENSE)). Research use only; not clinical decision
support.
