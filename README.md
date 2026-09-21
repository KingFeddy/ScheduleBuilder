# NJIT Schedule Builder

[![CI](https://github.com/KingFeddy/ScheduleBuilder/actions/workflows/ci.yml/badge.svg)](https://github.com/KingFeddy/ScheduleBuilder/actions/workflows/ci.yml)

Build conflict-free class schedules and draft semester-by-semester degree plans
for New Jersey Institute of Technology students. The app combines NJIT Banner
course sections, cached Rate My Professors ratings, and DegreeWorks PDF parsing
in a Next.js frontend backed by a FastAPI service.

[Try the app](https://njit-schedule-builder-web.vercel.app/scheduler) ·
[Contributing](CONTRIBUTING.md) · [API operations](apps/api/OPERATIONS.md)

## Features

- **Schedule generation:** select up to eight courses and compare ranked,
  conflict-free combinations, including sections with multiple meetings and
  asynchronous classes.
- **Commuter preferences:** set class-hour bounds, hide full sections, choose
  professors, and favor fewer campus days or fewer gaps between classes.
- **Professor research:** view cached ratings, difficulty, and review tags while
  choosing sections.
- **Degree planning:** upload a DegreeWorks audit, review extracted requirements,
  choose electives and a credit target, and generate a spring/fall semester plan.
  Supported prerequisite and corequisite rules guide course ordering; course
  replacements regenerate and validate the plan.
- **Data visibility:** inspect collected semesters, catalog coverage, seat counts,
  and refresh status, with warnings for missing or uncertain data.
- **Saved work without accounts:** scheduler choices, parsed audits, preferences,
  and generated plans persist in browser local storage.

PDFs are sent to the API for parsing with `pdfplumber`; the parsing and planning
endpoints do not persist PDFs or student plans server-side. Saved browser data is
specific to that browser and origin. Plans depend on extracted requirements and
collected catalog data: review warnings and confirm degree requirements and
registration eligibility with NJIT. Future course offerings are not guaranteed.

## Architecture

| Component | Technology | Responsibility |
| --- | --- | --- |
| Web app | Next.js 16, React 19, TypeScript, Tailwind CSS 4, Zustand | Scheduler, planner, calendar, and browser persistence |
| API | Python 3.12+, FastAPI, SQLAlchemy async, asyncpg | Schedule solving, PDF parsing, plan generation, and catalog queries |
| Data collection | Playwright, Chromium, httpx | Banner sections and prerequisite data, followed by RMP ratings |
| Database | PostgreSQL | Course catalog, sections, meetings, rating cache, and scrape history |

The browser calls `/api/*` through Next.js rewrites to FastAPI. The API and a
separate scraper process share PostgreSQL; schedule generation reads the stored
catalog rather than fetching Banner on each request.

```text
apps/
  api/
    main.py             # FastAPI app, health checks, and middleware
    src/routers/        # HTTP endpoints
    src/scheduler/      # Bounded backtracking solver and conflict detection
    src/services/       # Degree parser, planner, catalog, and data checks
    src/schemas/        # Request and response models
    src/scrapers/       # Banner, RMP, and catalog ingestion
    migrations/         # Ordered SQL migrations and manifest
    scripts/            # Migration, verification, and metadata tools
    tests/              # Backend unit, integration, and deployment tests
  web/
    app/                # Scheduler and planner pages
    components/         # Calendar, scheduler, planner, and shared UI
    hooks/              # Term discovery and planner preferences
    lib/                # API client, generated types, and planner helpers
    store/              # Persisted scheduler state
    tests/              # Frontend unit tests
    e2e/                # Browser regressions with mocked API responses
```

## Getting started

### Prerequisites

- Node.js **20.9 or newer** and **pnpm 9.15.0**; the pnpm version is pinned in [package.json](package.json).
- Python **3.12 or newer** and `uv`; CI and Docker pin **uv 0.11.8**.
- PostgreSQL **16** for the setup below, or Docker to run it locally.
- Network access for dependency installation and live Banner/RMP data collection.

The commands below use a Bash-compatible shell. Frontend browser tests can run
without an API or database; see [CONTRIBUTING.md](CONTRIBUTING.md#frontend-checks).

### 1. Clone and install dependencies

```bash
git clone https://github.com/KingFeddy/ScheduleBuilder.git
cd ScheduleBuilder
pnpm install --frozen-lockfile
(cd apps/api && uv sync --locked)
cp apps/api/.env.example apps/api/.env
```

### 2. Start a development database

If you already have an empty development database, use its connection details in
the next step. Otherwise, run this from the repository root with Docker running:

```bash
docker run --name njit-dev \
  -e POSTGRES_DB=njit_dev \
  -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD=password \
  -p 127.0.0.1:5432:5432 \
  -v njit-dev-data:/var/lib/postgresql/data \
  -d postgres:16

docker exec njit-dev pg_isready -U postgres -d njit_dev
```

Wait until `pg_isready` reports that PostgreSQL is accepting connections. These
credentials are for local development. Data persists in `njit-dev-data`; use
`docker start njit-dev` to restart the existing container on later visits.

### 3. Configure and migrate the API

Edit `apps/api/.env`, copied from [the environment example](apps/api/.env.example). Its
`DATABASE_URL` already matches the container above. For local development, the
example `SUPABASE_URL` and `SUPABASE_ANON_KEY` placeholders can remain: settings
require these fields, but the current backend queries PostgreSQL directly through
`DATABASE_URL`, so a Supabase account is not needed for this setup.

Run from the repository root, then stay in `apps/api` for steps 4 and 5:

```bash
cd apps/api
export MIGRATION_DATABASE_URL='postgresql+asyncpg://postgres:password@localhost:5432/njit_dev'
uv run --no-sync python -m scripts.migrate apply
DATABASE_URL="$MIGRATION_DATABASE_URL" uv run --no-sync python -m scripts.verify_migrations
```

If using another database, update both `.env`'s `DATABASE_URL` and the exported
`MIGRATION_DATABASE_URL`. The migration runner requires its explicit URL and does
not load `.env`. Startup does not apply migrations. Existing databases without
migration history require reviewed schema reconciliation; see the
[migration notes](apps/api/OPERATIONS.md#database-migrations).

### 4. Populate the course catalog

A fresh database contains no courses or sections. Set `CURRENT_TERM` in `.env` to
a semester published by Banner. The example `202690` means Fall 2026; term codes
end in `10` for Spring, `50` for Summer, or `90` for Fall.

For a smaller development catalog, set `CATALOG_SUBJECTS=CS,MATH` in `.env` before
running the scraper. Keep the API and scraper settings aligned. On Linux, first
install Chromium's system libraries with
`uv run --no-sync playwright install-deps chromium`. From `apps/api`:

```bash
uv run --no-sync playwright install chromium
uv run --no-sync python -m src.scrapers.cron
```

Each scrape contacts live Banner and RMP services and may take several minutes.
Review the scraper output and the selected semester's refresh status; if no
sections were collected, the scheduler cannot produce results. Official catalog metadata can also be
[imported separately](apps/api/OPERATIONS.md#importing-official-catalog-metadata).

### 5. Start the API and frontend

In the API terminal, still in `apps/api`:

```bash
uv run --no-sync uvicorn main:app --reload --port 8000
```

In a second terminal at the repository root:

```bash
pnpm --filter web dev
```

Open the [scheduler](http://localhost:3000/scheduler) or
[degree planner](http://localhost:3000/planner). The development frontend proxies
`/api/*` to `http://localhost:8000`; leave `NEXT_PUBLIC_API_URL` unset for this setup.
The API exposes [interactive documentation](http://localhost:8000/docs) and a
[health check](http://localhost:8000/health).

## Configuration

API settings load from `apps/api/.env` when commands run from `apps/api`.
Environment variables override the file. For frontend overrides, use
`apps/web/.env.local` or the deployment environment.

| Variable | Used by | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | API, scraper | Required PostgreSQL URL using `postgresql+asyncpg://` |
| `SUPABASE_URL`, `SUPABASE_ANON_KEY` | API settings | Required configuration fields; example placeholders suffice locally |
| `CURRENT_TERM` | API, scraper | Shared default semester; the frontend discovers it through `/api/terms` |
| `CATALOG_SUBJECTS` | API, scraper | Optional comma-separated subject scope; leave unset for the [shared defaults](apps/api/src/catalog.py) |
| `GER_SUBJECTS` | API | Elective browser subjects; membership alone does not establish GER eligibility |
| `CORS_ORIGINS` | API | Comma-separated allowed origins; defaults to `http://localhost:3000` |
| `APP_ENV` | API, scraper | Defaults to `development`; use `production` when deploying |
| `SENTRY_DSN`, `LOG_LEVEL` | API | Optional error tracking and API logging configuration |
| `RAILWAY_API_URL` | Frontend | Required at production build/start; API origin without `/api` |
| `NEXT_PUBLIC_API_URL` | Frontend | Optional browser-facing API origin; normally unset to use the Next.js proxy |
| `NEXT_PUBLIC_SENTRY_DSN` | Frontend | Optional client error tracking |

Database maintenance commands use separate explicit environment variables;
see the [operations guide](apps/api/OPERATIONS.md). Keep environment files and
credentials out of commits.

## Usage

**Scheduler:** choose a semester with collected sections, add courses such as
`CS100` and `MATH111`, adjust class hours or professor preferences, then select
**Solve**. Browse the returned calendars and review warnings and seat information.

**Planner:** upload a DegreeWorks PDF between 5 KiB and 5 MiB, review the extracted
audit, choose a start semester and a credit target from 3–24, and generate a plan.
The planner uses spring and fall semesters. Elective replacements are checked by
regenerating the plan; review any unresolved requirements and warnings afterward.

### API example

With the API running, inspect available semesters and search the stored catalog:

```bash
curl http://localhost:8000/api/terms
curl 'http://localhost:8000/api/courses?q=CS&limit=5'
```

Use a returned term with data and course codes collected for it in a solve request:

```bash
curl -X POST http://localhost:8000/api/schedule/solve \
  -H 'Content-Type: application/json' \
  -d '{
    "course_codes": ["CS100", "MATH111"],
    "term": "202690",
    "options": {"earliest_start": "09:00", "hide_full_sections": true},
    "compact_week": true
  }'
```

The response contains `results`, `warnings`, and `truncated`. For all request and
response models, use [Swagger UI](http://localhost:8000/docs) or the checked-in
[OpenAPI specification](apps/api/openapi.json).

## Development checks

From the repository root after installing dependencies:

```bash
pnpm --filter web typecheck
pnpm --filter web lint
pnpm --filter web test:unit
(cd apps/api && uv run --no-sync pytest tests/ -m "not database" -q)
```

[CONTRIBUTING.md](CONTRIBUTING.md) covers the disposable PostgreSQL test database,
browser tests, API type generation, and the pull request workflow. The
[operations guide](apps/api/OPERATIONS.md) covers production configuration, Docker
builds, migrations, schema verification, and catalog maintenance.

## Help and documentation

- For setup questions, bugs, or feature requests, search or open a
  [GitHub issue](https://github.com/KingFeddy/ScheduleBuilder/issues). Include the
  relevant command, environment, and reproducible steps; use synthetic audit data
  instead of posting personal DegreeWorks PDFs.
- For API behavior, see the [OpenAPI specification](apps/api/openapi.json) and
  [local interactive docs](http://localhost:8000/docs).
- For database or deployment work, start with [API operations](apps/api/OPERATIONS.md).

## Maintainers and contributing

Maintained by [KingFeddy](https://github.com/KingFeddy), with contributions tracked
in the [repository history](https://github.com/KingFeddy/ScheduleBuilder/graphs/contributors).
Bug reports, documentation improvements, and focused pull requests are welcome.
Read [CONTRIBUTING.md](CONTRIBUTING.md) for development checks and contribution guidance.

## License

No license file is currently included in this repository.
