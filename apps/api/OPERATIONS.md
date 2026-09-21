# API operations

Setup, testing, and deployment instructions for developers.
Return to the [project README](../../README.md) for an overview of the app.

- [Local setup](#local-setup)
- [Running checks](#running-checks)
- [Deployment](#deployment)
- [Database migrations](#database-migrations)

Run Python commands from `apps/api` unless a section says otherwise.

## Local setup

### Prerequisites

- Node.js **20.9+** and **pnpm 9.15.0**
- Python **3.12+** and **uv** (CI uses 0.11.8)
- Docker for the local PostgreSQL database, or your own PostgreSQL 16 instance

### 1. Install

```bash
git clone https://github.com/KingFeddy/ScheduleBuilder.git
cd ScheduleBuilder
pnpm install --frozen-lockfile
(cd apps/api && uv sync --locked)
cp apps/api/.env.example apps/api/.env
```

### 2. Start PostgreSQL

```bash
docker run --name njit-dev -d \
  -e POSTGRES_DB=njit_dev -e POSTGRES_PASSWORD=password \
  -p 127.0.0.1:5432:5432 \
  -v njit-dev-data:/var/lib/postgresql/data postgres:16

docker exec njit-dev pg_isready -U postgres -d njit_dev
```

Wait for “accepting connections” before continuing. These credentials are local
only. On later visits, restart the container with `docker start njit-dev`.

### 3. Set up the API and collect courses

The copied `apps/api/.env` already matches this database. Its Supabase placeholders
can stay for local use; the backend connects directly to PostgreSQL.
Set `CURRENT_TERM` to a semester available in Banner. For a smaller development
catalog, add `CATALOG_SUBJECTS=CS,MATH` to `.env`.

From the repository root:

```bash
cd apps/api
export MIGRATION_DATABASE_URL='postgresql+asyncpg://postgres:password@localhost:5432/njit_dev'
uv run --no-sync python -m scripts.migrate apply
DATABASE_URL="$MIGRATION_DATABASE_URL" uv run --no-sync python -m scripts.verify_migrations
```

For another database, update both `.env`'s `DATABASE_URL` and the URL above.
The migration command requires an explicit URL; it does not load `.env`.
See [migration notes](#database-migrations) for existing databases.

A fresh database has no courses. Still in `apps/api`, install Chromium and fetch
live Banner/RMP data. On Linux, first run
`uv run --no-sync playwright install-deps chromium`.

```bash
uv run --no-sync playwright install chromium
uv run --no-sync python -m src.scrapers.cron
```

Collection needs network access and may take several minutes. Check the scraper
output before trying to generate schedules.

### 4. Run

Start the API from `apps/api`:

```bash
uv run --no-sync uvicorn main:app --reload --port 8000
```

In a second terminal at the repository root:

```bash
pnpm --filter web dev
```

Open the [scheduler](http://localhost:3000/scheduler) or
[planner](http://localhost:3000/planner). The frontend proxies API requests to port
8000, so leave `NEXT_PUBLIC_API_URL` unset. Explore endpoints in
[Swagger UI](http://localhost:8000/docs).

## Running checks

From the repository root:

```bash
pnpm --filter web typecheck
pnpm --filter web lint
pnpm --filter web test:unit
(cd apps/api && uv run --no-sync pytest tests/ -m "not database" -q)
```

Run only the checks relevant to your change, using focused existing tests where
possible. CI runs backend tests, frontend type checks, unit tests, and linting;
changes limited to the README, agent instructions, `docs/`, or `assets/` skip it.

For browser tests using synthetic API responses:

```bash
pnpm --filter web exec playwright install chromium
pnpm --filter web test:e2e
# Run one browser test file when only that workflow changed:
pnpm --filter web test:e2e e2e/scheduler.spec.ts
```

For database tests, start the disposable test service with Docker running:

```bash
docker compose -f compose.test.yml up -d --wait
(cd apps/api && \
  MIGRATION_DATABASE_URL=postgresql+asyncpg://njit_test:test-only@127.0.0.1:55432/njit_test \
    uv run --no-sync python -m scripts.migrate apply && \
  APP_ENV=test \
  TEST_DATABASE_URL=postgresql+asyncpg://njit_test:test-only@127.0.0.1:55432/njit_test \
    uv run --no-sync pytest tests/ -q)
docker compose -f compose.test.yml down --volumes
```

Tests verify the disposable database before running and never use the application
database or `.env`. Its data is discarded when stopped; reapply migrations after
restarting it. The credentials above are for this test service only.

After changing API request or response models, regenerate the API contract:

```bash
(cd apps/api && uv run --no-sync python -m scripts.export_openapi)
pnpm --filter web api:generate
pnpm --filter web typecheck
```

Include the generated OpenAPI snapshot and frontend types with the model changes.

## Deployment

The deployment configuration targets Vercel for the frontend and separate Railway
services for the API and scraper, backed by PostgreSQL. Configure the API and
scraper with the same `DATABASE_URL`, `CURRENT_TERM`, and `CATALOG_SUBJECTS`.
Use the settings in [.env.example](.env.example), set `APP_ENV=production`, and
supply the production frontend origin in `CORS_ORIGINS`. Sentry is optional.

For the frontend, set `RAILWAY_API_URL` to the API origin, without `/api`, at build
and start time. Leave `NEXT_PUBLIC_API_URL` unset to use Next.js rewrites. A local
production-build check, run from the repository root, is:

```bash
RAILWAY_API_URL=http://localhost:8000 pnpm --filter web build
RAILWAY_API_URL=http://localhost:8000 pnpm --filter web start
```

Cold builds need network access to fetch the Geist fonts. The API must already be
running on port 8000 for the local production frontend to serve API requests.

[CI](../../.github/workflows/ci.yml) runs backend tests, frontend type checking,
unit tests, and linting. On pushes to `main`, deployment also requires
`PRODUCTION_DATABASE_URL` and `RAILWAY_TOKEN` GitHub Actions secrets. The production
schema verification gate runs before deploying the `api` and `scraper` services.
Migrations must be applied separately; neither startup nor deployment applies them.

The API uses [railway.toml](railway.toml); the scraper has its own
[railway.scraper.toml](railway.scraper.toml). The
[root Railway configuration](../../railway.toml) describes a 30-minute scrape
schedule. Confirm the selected service root, Dockerfile, configuration file, and
cron schedule in Railway when provisioning services. The scraper exits after one
Banner-then-RMP refresh; it does not run a scheduler loop itself.

## Docker images and runtime dependencies

Build from the repository root, with `apps/api` as the context:

```bash
docker build --tag schedule-builder-api:local --file apps/api/Dockerfile apps/api
docker build --tag schedule-builder-scraper:local --file apps/api/Dockerfile.scraper apps/api
```

Both images use Python 3.12 and install locked runtime dependencies with
`uv sync --locked --no-dev`. The scraper image also installs Chromium and its
system libraries. Runtime commands use `uv run --no-sync`. Keep the uv pin in both
Dockerfiles and CI aligned when upgrading it.

The allowlists in [the API Docker ignore file](.dockerignore) and
[the root Docker ignore file](../../.dockerignore) exclude environment files,
credentials, PDFs, local virtual environments, tests, and frontend dependencies.
Keep them aligned when adding runtime assets. The supported Docker commands above
still require `apps/api` as the context; the root allowlist does not change the
Dockerfiles' relative copy paths. Supply secrets through the runtime environment.

## Importing official catalog metadata

The [catalog importer](scripts/import_catalog_metadata.py) can add course titles
and credits from one explicitly selected undergraduate department page, even when
there are no collected sections. It is separate from the Banner/RMP cron.
Preview the page and confirm its catalog edition before applying:

```bash
uv run --no-sync python -m scripts.import_catalog_metadata \
  --url https://catalog.njit.edu/undergraduate/science-liberal-arts/physics/ \
  --subject PHYS --catalog-year 2026
```

The year is the starting year of the source catalog edition; update it when the
published edition changes. A preview returns source candidates, not a database
diff, and needs neither a database nor an environment file. Invalid course rows,
conflicting duplicates, missing subjects, and mismatched editions reject the
page. Recognized level placeholders are reported and excluded from writable rows.

After reviewing the preview and backing up a live database, load the intended
`DATABASE_URL` explicitly:

```bash
uv run --env-file .env --no-sync python -m scripts.import_catalog_metadata \
  --url https://catalog.njit.edu/undergraduate/science-liberal-arts/physics/ \
  --subject PHYS --catalog-year 2026 --apply
```

The importer verifies the runtime schema and acquires the Banner writer lock before
importing the whole page in one transaction. Official catalog titles take
precedence over Banner section titles; deploy the current Banner writer before
importing so an older writer cannot overwrite them. Catalog credits fill missing
or unverified values, while verified Banner credits remain authoritative.

The importer preserves sections, seats, and prerequisite evidence. A catalog entry
does not establish future availability or degree eligibility. Regenerate saved
plans after applying metadata to use the updated catalog.

## Database migrations

[The migration manifest](migrations/manifest.json) lists migrations in order. Fresh databases apply
000, 007, 009, 012, 013, 014, 015, 016, and 017. Migration 008 remains deferred until meeting backfill
coverage is verified and at least two weeks of production scraper data are
confirmed; its legacy section columns remain available. Numbers 010 and 011 stay
unused because their subsystems were removed.

From `apps/api`, explicitly select the database:

```bash
export MIGRATION_DATABASE_URL='postgresql+asyncpg://USER:PASSWORD@HOST:5432/DATABASE'
uv run --no-sync python -m scripts.migrate status
uv run --no-sync python -m scripts.migrate apply
```

The commands target the existing `public` schema by default; `--schema NAME`
selects another existing schema. They require `MIGRATION_DATABASE_URL` and never
fall back to application settings or dotenv files. `status` is read-only and
reports applied, pending, and deferred migrations. `apply` records each applied
version, filename, SHA-256 checksum, and timestamp in `schema_migrations`.

All pending migrations and history records commit together. A failed batch rolls
back its changes, and concurrent runners serialize per schema. Migration files
must contain transactional SQL without their own BEGIN/COMMIT commands; operations
such as CREATE INDEX CONCURRENTLY are not supported by this runner.

List every SQL file in the manifest with unique, increasing versions. Applied
files are immutable: make corrections in a new migration. Changed checksums,
unknown history versions, and gaps in applied active migrations cause a refusal.
Databases with existing relations but no migration history also require reviewed
schema reconciliation before adoption; the runner will not adopt them automatically.
Application startup and production deployment do not automatically apply migrations.

### Targeted repair for the inspected September 2026 legacy database

The legacy database inspected on 2026-09-13 predates migration history and has
`professors(id, name, department, ...)`, including duplicate names. Do not replay
baseline migrations or disable the schema verifier. The targeted repair command
below defaults to a **read-only preflight**, using the explicitly loaded `.env`:

```bash
uv run --env-file .env --no-sync python -m scripts.repair_legacy_catalog
```

Review the report and take a database backup before applying. This is a maintenance
operation: it briefly takes exclusive locks on the six runtime tables (five-second
lock timeout). It preserves the entire old professor table, UUIDs, ratings, and
duplicate names as `professors_legacy_20260913`; the new runtime professor table
contains distinct names and only an unambiguous department, otherwise null. The
archive is not the current professor-rating source; ratings use `rmp_cache`.
Existing dependent objects continue to reference the archived original table.
Current application code uses the new runtime contract; coordinate older external
consumers of the legacy professor table before applying.

The repair reconciles seat defaults/nullability, prerequisite nullability, the
course foreign key and indexes, then executes existing migrations 014–017. It
preserves courses, sections, meetings, caches, and historical run data. No legacy
meeting columns are dropped. Unknown seat/prerequisite values, invalid credits,
missing names, a pre-existing archive, or a different partially upgraded layout
are refused. Final runtime verification must pass before the transaction commits.

```bash
uv run --env-file .env --no-sync python -m scripts.repair_legacy_catalog --apply &&
uv run --env-file .env --no-sync python -m scripts.verify_migrations
```

Only after structural reconciliation passes does the repair explicitly adopt
000/007/009/012/013 and record execution of 014/015/016/017 in the migration ledger.
The table comment records this distinction; future normal migration commands can
then use the ledger. Deferred 008 remains deferred. Repeating the repair on a
compatible database makes no changes. A lost connection can make completion
uncertain: run the read-only preflight again before retrying. The preserved table
is useful for recovery but is not a substitute for an independent database backup.

After successful verification, refresh the desired term and configured subjects:

```bash
uv run --env-file .env --no-sync python -m src.scrapers.cron
```

### Verify the runtime schema before deployment

From `apps/api`, select the database explicitly and run the read-only deployment gate:

```bash
DATABASE_URL='postgresql+asyncpg://USER:PASSWORD@HOST:5432/DATABASE' \
uv run --no-sync python -m scripts.verify_migrations
```

It checks the six runtime tables and all 48 required columns, including
`sections.section_number`, course metadata sources, prerequisite rules/evidence/verification metadata, and every
scraper-status field, including recorded run scope. It also checks column
types and nullability, required defaults and generated values, primary and unique
keys used by upserts, cascading foreign keys, validated meeting/status checks, and
indexes supporting the declared access paths. Missing or incompatible items are
named in the output and return a nonzero exit code, blocking the Railway deploy.
CI runs the same command against its freshly migrated disposable database first.

The verifier uses only the explicit `DATABASE_URL`, does not load `.env` or require
other application settings, and inspects the database in a read-only transaction
with bounded connection, query, and lock waits. `--schema NAME` selects an existing
schema other than `public`; tables in other schemas cannot satisfy its checks.

When `schema_migrations` exists, recorded files/checksums must match and no active
migrations may be pending. An older database without a ledger can pass by meeting
the runtime contract, without being adopted or changed. Deferred migration 008
and legacy section time columns are not required. This schema check does not
verify meeting data coverage or authorize dropping those legacy columns.

The contract is maintained in `apps/api/scripts/runtime_schema.py`; update it and
its regressions alongside runtime SQL changes. Constraint and index names may
differ, but CHECK/default/generated expressions must match the declared PostgreSQL
definitions after whitespace normalization (with explicitly supported timestamp
and identity alternatives). An equivalent custom expression needs review and a
contract update; the verifier does not infer arbitrary SQL equivalence.

### Preview and apply meeting backfill

From `apps/api`, explicitly select the database to inspect. The default command
is read-only and emits JSON containing candidate rows and unresolved CRN/term
pairs. `--schema NAME` selects an existing schema; `--term 202690` optionally
limits a backfill to one term.

```bash
export BACKFILL_DATABASE_URL='postgresql+asyncpg://USER:PASSWORD@HOST:5432/DATABASE'
uv run --no-sync python -m scripts.backfill_meetings
```

Review the preview before applying the batch:

```bash
uv run --no-sync python -m scripts.backfill_meetings --apply
```

Only sections with no meeting rows and a complete valid legacy day/time pattern
are candidates. Existing meeting patterns are preserved. Invalid times, missing
days, unexplained empty meetings, and mismatches between existing meetings and
legacy time coverage are reported for source reconciliation. Any unresolved row
blocks the selected batch. Application rechecks under table locks, inserts in one
transaction, and rolls everything back on error. Repeat and concurrent runs do
not duplicate rows; reported insertion counts reflect committed inserts.

Flat legacy data cannot reconstruct different lecture/lab patterns. A successful
backfill preserves that legacy evidence, but source review and a correct scraper
refresh are still necessary before confirming production correctness. All-null
legacy fields alone do not prove that a section is asynchronous. The API returns
503 with a `detail` message when selected courses contain missing or invalid
meeting records, instead of returning an apparently valid untimed schedule.
Explicit all-null meeting rows remain supported.

### Deferred removal of legacy time columns

Migration 008 stays deferred in the manifest and ordinary `migrate apply` skips it.
Before cleanup, verify the meetings-based scraper against actual Banner patterns,
including lecture/lab sections, and confirm at least 14 days of stable production
data. Retain the reviewed run history and source comparisons. The timestamp and
note below attest to that review; they are not inferred from table size or the
age of an arbitrary scraper record. A database with no migration ledger requires
reviewed reconciliation before using the migration runner.

The read-only cleanup check always covers every term:

```bash
uv run --no-sync python -m scripts.backfill_meetings --check-cleanup \
  --production-verified-since '<verified ISO timestamp with timezone>' \
  --production-verification-note '<references to reviewed production and multi-pattern evidence>'
```

After the release backup/rehearsal and verified production prerequisites, use the
guarded migration command, explicitly selecting the same database that was reviewed:

```bash
MIGRATION_DATABASE_URL="$BACKFILL_DATABASE_URL" \
uv run --no-sync python -m scripts.migrate cleanup-meetings \
  --production-verified-since '<verified ISO timestamp with timezone>' \
  --production-verification-note '<references to reviewed production and multi-pattern evidence>'
```

Cleanup requires all active migrations to be recorded, nonempty meeting data,
complete coverage for every section/term, and the dated production attestation.
It locks the tables and repeats the checks in the same transaction that applies
008 and records its checksum. A stale successful preview cannot authorize an
incomplete current database. Failed checks or DDL preserve the legacy columns.
Keep 008 deferred; executing its SQL file directly bypasses the runner's safeguards.
The commands never load application `.env` files or fall back between database URLs.
