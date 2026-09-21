# Contributing

Thanks for helping improve NJIT Schedule Builder. Start with the
[README](README.md#getting-started) to install dependencies and run the app.
Commands below run from the repository root unless stated otherwise.

## Issues and pull requests

1. Search [existing issues](https://github.com/KingFeddy/ScheduleBuilder/issues)
   before reporting a bug or proposing a change. Include reproduction steps,
   expected behavior, actual behavior, and relevant environment details.
2. Fork the repository and create a branch for a focused change. Discuss large
   changes in an issue before investing in an implementation.
3. Follow the surrounding Python or TypeScript conventions. Add a regression
   test for behavior changes and update documentation when setup or usage changes.
4. Run the checks relevant to your change, plus type checking and linting for
   frontend changes. Explain the change and validation in your pull request;
   include screenshots for visible UI changes.

Use fictional student data and synthetic PDFs in tests and issue reports. Keep
personal audits, environment files, credentials, and database dumps out of commits.

## Frontend checks

```bash
pnpm --filter web typecheck
pnpm --filter web lint
pnpm --filter web test:unit
```

`typecheck` first checks that the generated API types match the OpenAPI snapshot,
then runs TypeScript. Unit tests use Playwright's test runner without starting a
browser, app server, or backend.

For browser regressions:

```bash
pnpm --filter web exec playwright install chromium
pnpm --filter web test:e2e
pnpm --filter web test:e2e:prod
```

On Linux, install system dependencies with
`pnpm --filter web exec playwright install-deps chromium` if needed. Browser
installation and cold Next.js font builds need network access.

The browser suites start their own servers on ports 3100 and 3101 and use separate
build directories. They intercept API requests with synthetic fixtures and do
not need the real API, PostgreSQL, or personal PDFs. They cover UI behavior, not
the correctness of live scraping or real DegreeWorks extraction. API rewrites and
Sentry are disabled by the test configuration.

Run a focused regression or inspect its report:

```bash
pnpm --filter web test:e2e e2e/scheduler.spec.ts
pnpm --filter web exec playwright show-report playwright-report/development
```

Browser tests use [e2e/fixtures.ts](apps/web/e2e/fixtures.ts) for request isolation
and mock helpers. Extend those fixtures when introducing new endpoints; unmocked
API or external HTTP requests fail the tests. Reports, traces, and screenshots
are ignored by Git.

## Backend tests

For pure and mocked tests, from `apps/api`:

```bash
uv sync --locked
uv run --no-sync pytest tests/ -m "not database" -q
```

The full suite requires the disposable test database. With Docker running, start
from the repository root:

```bash
docker compose -f compose.test.yml up -d --wait
cd apps/api
MIGRATION_DATABASE_URL=postgresql+asyncpg://njit_test:test-only@127.0.0.1:55432/njit_test \
  uv run --no-sync python -m scripts.migrate apply
APP_ENV=test \
TEST_DATABASE_URL=postgresql+asyncpg://njit_test:test-only@127.0.0.1:55432/njit_test \
  uv run --no-sync pytest tests/ -q
```

This service is separate from the development database: it binds to loopback port
55432 and stores its data in memory. The public credentials above are test-only.
Pytest verifies the database identity, restricted role, and disposable marker
before fixtures run. It does not use the application's `DATABASE_URL` or `.env`.
Database fixtures create isolated schemas using the real migration chain and
remove their own schemas afterward. Running all tests without the explicit test
database configuration fails rather than silently skipping database tests.

Production-dependency regressions also create a temporary environment containing
only locked runtime packages; a cold cache may require downloads. Docker image
checks are opt-in. From `apps/api`, with Docker running:

```bash
# Check Docker build contexts without building production images.
RUN_DOCKER_TESTS=1 uv run --no-sync pytest tests/deployment/test_docker_contexts.py -m "not docker_image" -q

# Also build and inspect the API and scraper images; may download images and Chromium.
RUN_DOCKER_TESTS=1 uv run --no-sync pytest tests/deployment/test_docker_contexts.py -q
```

From the repository root, remove the disposable service when finished:

```bash
docker compose -f compose.test.yml down --volumes
```

Stopping the service discards its database. After restarting it, apply migrations
again before testing. Recreate it after changing the test bootstrap SQL.

## API contract changes

FastAPI models define the contract. After changing a request or response model,
regenerate the checked-in OpenAPI snapshot and frontend types from the root:

```bash
(cd apps/api && uv run --no-sync python -m scripts.export_openapi)
pnpm --filter web api:generate
pnpm --filter web typecheck
```

Commit [openapi.json](apps/api/openapi.json) and
[api.generated.ts](apps/web/lib/api.generated.ts) together with the model and
consumer changes. Do not edit the generated TypeScript directly. Exporting uses
synthetic settings and does not start the API, access a database, or load secrets.

To check the backend snapshot without rewriting it:

```bash
(cd apps/api && uv run --no-sync python -m scripts.export_openapi --check)
```

## Database changes

Add new transactional SQL migrations to
[the manifest](apps/api/migrations/manifest.json) in increasing version order.
Applied migration files are immutable; make corrections in a new migration.
Update [the runtime schema contract](apps/api/scripts/runtime_schema.py) and its
regressions alongside schema changes. Follow the
[operations guide](apps/api/OPERATIONS.md#database-migrations) for migration,
verification, and deferred cleanup procedures.

[CI](.github/workflows/ci.yml) runs the backend suite, frontend type checks, unit
tests, and linting on pull requests to `main`. Browser regressions and Docker
image builds are additional local checks when the change calls for them.
