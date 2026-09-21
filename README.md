# NJIT Schedule Builder

[![CI](https://github.com/KingFeddy/ScheduleBuilder/actions/workflows/ci.yml/badge.svg)](https://github.com/KingFeddy/ScheduleBuilder/actions/workflows/ci.yml)

Build conflict-free class schedules and semester-by-semester degree plans for NJIT.
Compare course sections and professors, or upload a DegreeWorks PDF to plan your
remaining semesters. No account required.

**[Try the app](https://njit-schedule-builder-web.vercel.app/scheduler)**

![NJIT Schedule Builder](assets/screenshots/scheduleBuilderScreenshot.png)

## What it does

- **Build schedules:** choose up to eight courses, set class hours, hide full
  sections, and find combinations with fewer campus days or gaps.
- **Compare professors:** view cached Rate My Professors ratings and difficulty
  scores while choosing sections.
- **Plan your degree:** upload a DegreeWorks PDF, pick electives and a credit
  target, and generate a spring/fall plan using supported prerequisite rules.
- **Save your progress:** choices, parsed audits, and plans stay in your browser's
  local storage. PDFs are processed by the API without being saved server-side.

Review the app's warnings and confirm requirements with NJIT. Catalog data and
seat counts depend on scraper refreshes; future course offerings are not guaranteed.

## How it works

```text
Next.js frontend → FastAPI → PostgreSQL
                                ↑
                        Banner + RMP scraper
```

| Component | Stack |
| --- | --- |
| Frontend | Next.js 16, React 19, TypeScript, Tailwind CSS 4, Zustand |
| API | Python, FastAPI, SQLAlchemy, pdfplumber |
| Data collection | Playwright for Banner, httpx for RMP |
| Database | PostgreSQL |

The API solves schedules from stored course data. A separate scraper refreshes
sections, prerequisites, seats, and professor ratings.

## Getting started

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
See [migration notes](apps/api/OPERATIONS.md#database-migrations) for existing databases.

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

## Configuration

Edit `apps/api/.env` for the API and scraper. Keep their term and subject settings
aligned. See [.env.example](apps/api/.env.example) for all API settings and the
[operations guide](apps/api/OPERATIONS.md) for deployment.

| Variable | Purpose |
| --- | --- |
| `DATABASE_URL` | PostgreSQL connection using `postgresql+asyncpg://` |
| `CURRENT_TERM` | Semester code, such as `202690` for Fall 2026; suffixes: `10` Spring, `50` Summer, `90` Fall |
| `CATALOG_SUBJECTS` | Optional subjects to collect, such as `CS,MATH`; omit for the shared defaults |
| `RAILWAY_API_URL` | Frontend production setting: API origin, without `/api`; required at build and start time |

## Project structure

```text
apps/web/              # Frontend, browser state, and UI tests
apps/api/src/          # Routes, schedule solver, degree planner, and scrapers
apps/api/migrations/   # Database schema changes
apps/api/scripts/      # Migration, verification, and catalog tools
apps/api/tests/        # Backend tests
```

## Running checks

From the repository root:

```bash
pnpm --filter web typecheck
pnpm --filter web lint
pnpm --filter web test:unit
(cd apps/api && uv run --no-sync pytest tests/ -m "not database" -q)
```

For database tests, browser tests, and API type generation, see
[CONTRIBUTING.md](CONTRIBUTING.md).

## Help and contributing

Maintained by [KingFeddy](https://github.com/KingFeddy). Report bugs or ask questions
in [GitHub Issues](https://github.com/KingFeddy/ScheduleBuilder/issues).
Contributions are welcome; start with [CONTRIBUTING.md](CONTRIBUTING.md).
Use synthetic examples instead of sharing personal DegreeWorks PDFs.

No license file is currently included in this repository.
