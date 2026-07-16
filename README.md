# analyzer-ng

A standalone Python microservice for ReportPortal that auto-triages failed test
items. It is a drop-in replacement for `service-auto-analyzer` + OpenSearch,
storing all state in **PostgreSQL** (with `pgvector`) and optionally using a
local Ollama LLM sidecar. No OpenSearch/Elasticsearch required.

See `analyzer-ng-plan/CONTEXT.md` and `analyzer-ng-plan/specs/` for the design
brief and authoritative specifications.

## Status

Early development. This repository currently contains the project skeleton
(package layout, packaging, tooling). Functionality is implemented in later
phases per `analyzer-ng-plan/MASTER_PLAN.md`.

## Development

```sh
make install   # create venv and install the package with dev extras
make lint      # ruff check
make test      # run the test suite
```

Requires Python 3.12.

## License

Apache-2.0. See [LICENSE](LICENSE).
