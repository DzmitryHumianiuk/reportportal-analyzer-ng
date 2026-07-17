# INSTALL — dropping analyzer-ng into an existing ReportPortal compose

analyzer-ng replaces ReportPortal's `service-auto-analyzer` (and removes the
OpenSearch + `analyzer-train` layer entirely). It reuses the RP `rabbitmq` and
`postgres` you already run — its own `analyzer` database, never touching
`reportportal`. This guide gives the exact edits, verified against the official
**ReportPortal 5.15** compose in `docs/G5-WALKTHROUGH.md`.

You need: the `analyzer-ng:latest` image available locally (or built — see
`Dockerfile`), your RP `docker-compose.yml`, and the overlay
`docker-compose.analyzer-ng.yml` shipped in this repo.

---

## 0. TL;DR

1. **Edit the base RP compose** (§1): delete `opensearch` (+ its volume) and
   `analyzer-train` if present; remove `opensearch` from the `analyzer` service's
   `depends_on`; give the `postgres` superuser a password and mount the pgvector
   init script.
2. **Apply the overlay** and bring the stack up:
   ```bash
   docker compose -f docker-compose.yml -f docker-compose.analyzer-ng.yml \
     --profile core --profile infra --profile "" up -d
   ```
   (Profiles are how the RP 5.15 compose selects services; older single-profile RP
   composes just use `up -d`.)
3. Verify (§4).

---

## 1. Base-file edits (the primary, supported path)

These are edits the overlay **cannot** do for you (docker-compose merges/append
across `-f` files — it never *removes* keys, and it *appends* profile lists).

### 1a. Remove the `analyzer`→`opensearch` dependency  *(required)*

In the base `analyzer` service, delete the `opensearch` entry from `depends_on`
(keep `rabbitmq`):

```yaml
  analyzer:
    depends_on:
      # opensearch: { condition: service_healthy }   # <-- DELETE
      rabbitmq:
        condition: service_healthy
```

If you skip this, `up` fails with *"service analyzer depends on undefined/inactive
service opensearch"* once the overlay redefines `analyzer`.

### 1b. Delete the `opensearch` service and its volume  *(required)*

Delete the whole `opensearch:` service block and the `opensearch:` entry under
top-level `volumes:`.

> **Why the overlay's `legacy-disabled` profile is not enough (T5.2 finding):**
> docker-compose **appends** `profiles` across `-f` files. A stock RP
> `opensearch` with `profiles: ['']` merges to `['', 'legacy-disabled']`, and the
> empty default profile *still activates it*. Confirm with
> `docker compose ... config --services` — if `opensearch` is listed, it will
> start. Deleting it in the base file is the reliable fix. (Also frees ~0.5–2 GiB.)

Same applies to a legacy `analyzer-train` service if your RP version has one —
delete it.

### 1c. pgvector needs a PostgreSQL **superuser**  *(required on stock RP postgres)*

The RP bitnami `postgres` runs its app user (`POSTGRES_USER`, e.g. `rpuser`) as a
**non-superuser**. It has `CREATEDB` — so analyzer-ng *can* create the `analyzer`
database — but it **cannot** `CREATE EXTENSION vector` (pgvector is untrusted →
superuser-only). A fully-automatic start therefore fails at extension creation.

Fix: pre-create pgvector as the `postgres` superuser via the shipped init script.
Edit the base `postgres` service:

```yaml
  postgres:
    environment:
      POSTGRES_USER: ${POSTGRES_USER-rpuser}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD-rppass}
      POSTGRES_DB: ${POSTGRES_DB-reportportal}
      # T5.2: give the postgres SUPERUSER a known password so pgvector can be installed
      POSTGRESQL_POSTGRES_PASSWORD: ${POSTGRESQL_POSTGRES_PASSWORD}
    volumes:
      - postgres:/bitnami/postgresql
      # T5.2: create the `analyzer` DB + pgvector + schema as superuser, once, at init
      - ./docker/pg-init/01-analyzer-db.sh:/docker-entrypoint-initdb.d/01-analyzer-db.sh:ro
```

Add `POSTGRESQL_POSTGRES_PASSWORD=<a-strong-password>` to your `.env`.

The script (`docker/pg-init/01-analyzer-db.sh`) runs **only on first cluster init**
(empty data volume). For an already-initialized cluster, run the equivalent once by
hand as the superuser (this is exactly what G5 did):

```bash
docker exec -e PGPASSWORD=<superpw> <postgres-container> \
  psql -U postgres -d postgres -c "CREATE DATABASE analyzer OWNER ${POSTGRES_USER};"
docker exec -e PGPASSWORD=<superpw> <postgres-container> \
  psql -U postgres -d analyzer \
  -c "CREATE EXTENSION IF NOT EXISTS vector;
      CREATE EXTENSION IF NOT EXISTS pg_trgm;
      CREATE SCHEMA IF NOT EXISTS analyzer AUTHORIZATION ${POSTGRES_USER};"
```

> **Bind-mount caveat (colima/lima/WSL):** if your Docker VM does not share the
> directory holding the compose file, the `01-analyzer-db.sh` bind silently mounts
> as an empty directory and the script never runs (you'll see *"database analyzer
> does not exist"*). Put the checkout on a shared path (e.g. under `$HOME`), or run
> the manual SQL above. On Docker Desktop / native Linux the mount works directly.

With pgvector pre-created, you may leave `ANALYZER_PG_CREATE_DB=true` (the service's
`CREATE … IF NOT EXISTS` calls no-op) or set it `false`.

---

## 2. The overlay

`docker-compose.analyzer-ng.yml` (shipped) redefines the `analyzer` service with the
`analyzer-ng:latest` image (same service **name** so RP healthchecks/refs still
resolve), points it at RabbitMQ vhost `analyzer` and a separate `analyzer` Postgres
DB, and adds an optional `ollama` sidecar under the `llm` profile. Its env expects
`RABBITMQ_DEFAULT_USER/PASS` and `POSTGRES_USER/PASSWORD` from your `.env`
(the RP compose already defines them).

Minimal `.env` (match your RP values):

```
RABBITMQ_DEFAULT_USER=rabbitmq
RABBITMQ_DEFAULT_PASS=rabbitmq
POSTGRES_USER=rpuser
POSTGRES_PASSWORD=rppass
POSTGRES_DB=reportportal
POSTGRESQL_POSTGRES_PASSWORD=change-me-superuser
```

Bring up:

```bash
docker compose -f docker-compose.yml -f docker-compose.analyzer-ng.yml \
  --profile core --profile infra --profile "" up -d
```

### Best-effort path (clean base files only)

If your base `analyzer` has **no** `depends_on` on opensearch/analyzer-train, the
overlay also *tries* to park those services behind a `legacy-disabled` profile. As
noted in §1b this does **not** reliably stop a service whose base profile is `''` —
prefer the deletes in §1. There is no substitute for the §1c superuser edit.

---

## 3. LLM sidecar (optional, off by default)

To enable the local LLM roles, uncomment in the overlay's `analyzer` env:

```yaml
      ANALYZER_LLM_ENABLED: "true"
      OLLAMA_URL: http://ollama:11434
```

and start with the extra profile: `... --profile llm up -d`. The model is pulled on
first use, or pre-pull: `docker compose ... exec ollama ollama pull qwen3:4b-q4_K_M`.
See `docs/OPERATIONS.md` for the kill switch and role toggles.

---

## 4. Verify

```bash
# analyzer-ng healthy, no opensearch/analyzer-train containers
docker compose ... ps

# exchange + queues (RP discovery)
docker exec <rabbitmq> rabbitmqctl list_exchanges -p analyzer name type arguments
docker exec <rabbitmq> rabbitmqctl list_queues    -p analyzer name durable consumers

# service health
docker exec <analyzer> python -c \
  "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:5001/health').read())"

# analyzer schema created (49 tables), pgvector present
docker exec -e PGPASSWORD=$POSTGRES_PASSWORD <postgres> \
  psql -U $POSTGRES_USER -d analyzer -c "\dt analyzer.*"
```

Expected: `analyzer` exchange (fanout, `durable=false auto_delete=true`) with args
`analyzer, analyzer_index, analyzer_priority, analyzer_log_search, analyzer_suggest,
analyzer_cluster, version`; queues `analyzer-ng.all` / `.train` (durable, 1 consumer)
and `analyzer-ng.dlq`; `/health` → `{"ready":true,"pg":true,"amqp":true,...}`.

For the full end-to-end scenario (import → auto-analysis → Make Decision → defect
update → label_event) see `docs/G5-WALKTHROUGH.md` and `scripts/g5/`.

---

## 5. Notes on RP-side settings

- **Auto-analysis** is a per-project setting stored in RP **project attributes**:
  `analyzer.isAutoAnalyzerEnabled=true`, `analyzer.autoAnalyzerMode` (`ALL` /
  `LAUNCH_NAME` / `CURRENT_LAUNCH`), `analyzer.minShouldMatch`,
  `analyzer.numberOfLogLines`. Set via `PUT /api/v1/project/{project}` with
  `{"configuration":{"attributes":{...}}}`, or in the UI under
  Project Settings → Auto-Analysis.
- **Analyzer priority** (`ANALYZER_PRIORITY`, advertised in the exchange args; lower
  wins when several analyzers are registered) defaults to `1`. Only relevant if you
  run more than one analyzer against the same RP.
- analyzer-ng does **not** run both alongside the legacy analyzer on the same
  exchange name — replace it (§1), don't add.
