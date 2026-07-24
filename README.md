# analyzer-ng

A standalone Python microservice for ReportPortal that auto-triages failed test
items. It is a drop-in replacement for `service-auto-analyzer` + OpenSearch,
storing all state in **PostgreSQL** (with `pgvector`) and optionally using a
local Ollama LLM sidecar. No OpenSearch/Elasticsearch required.

It keeps the same RabbitMQ contract as the legacy analyzer (same exchange and
queue names), so ReportPortal talks to it without any core changes. Alongside
the analyzer it ships:

- **Inspector** (`inspector/`) — a small read-only web UI to look inside the
  analyzer's state: log clusters, error signatures, the ML loop, and the
  per-item journey. Has its own backend and Dockerfile.
- **rp-migrate** (`tools/rp-migrate/`) — a tool to copy launches and defect
  labels from a source ReportPortal into a target one so the analyzer has data
  to learn from.
- **RP-side patches** (`docs/rp-patches/`) — optional ReportPortal `service-ui`
  and `service-api` changes that surface the analyzer's richer data in the
  Make Decision modal. See the `service-ui` fork branch for a pre-applied tree.

## Quick start

To build and run the whole thing locally (analyzer + Postgres/pgvector +
inspector, and how it plugs into a ReportPortal stand), follow
**[DEVELOPER.md](DEVELOPER.md)**.

To drop analyzer-ng into an existing ReportPortal docker-compose, see
**[docs/INSTALL.md](docs/INSTALL.md)**. For the full end-to-end scenario
(import, auto-analysis, Make Decision, label feedback) see
**[docs/G5-WALKTHROUGH.md](docs/G5-WALKTHROUGH.md)** and `scripts/g5/`.

## Development

```sh
make install   # create venv and install the package with dev extras
make lint      # ruff check
make test      # run the test suite
```

Requires Python 3.12. The integration tests spin up throwaway Postgres and
RabbitMQ containers via testcontainers; for interactive local work bring up the
backing services with `docker compose -f docker-compose.dev.yml up -d`.

## Operations

Runtime toggles, the LLM kill switch, and role settings are documented in
**[docs/OPERATIONS.md](docs/OPERATIONS.md)**.

## License

Apache-2.0. See [LICENSE](LICENSE).
