# NJIT Schedule Builder

A full-stack web app for NJIT students: conflict-free schedule generation, professor RMP research, and DegreeWorks PDF → semester graduation plan. No accounts, no server-side user data — just a fast, public tool that works.

**Live at:** (https://njit-schedule-builder-web.vercel.app/scheduler)

---

## What it does

| Feature | Description |
|---|---|
| **Schedule Solver** | Select up to 8 courses → get up to 10 ranked, conflict-free schedules in under 1 second |
| **Compact Week** | Prefer schedules with fewer campus days |
| **Professor Picker** | See RMP ratings and difficulty scores inline while choosing sections |
| **Degree Planner** | Upload your DegreeWorks PDF → extract remaining requirements → generate a semester-by-semester graduation plan |

---

## Architecture

```
┌─────────────────┐     HTTPS      ┌──────────────────────┐
│   Next.js 15    │ ─────────────▶ │   FastAPI (Railway)  │
│   (Vercel)      │                │                      │
│                 │                │  /api/schedule/solve │
│  App Router     │                │  /api/courses        │
│  Zustand store  │                │  /api/plan/parse     │
│  Tailwind CSS   │                │  /api/plan/generate  │
└─────────────────┘                └──────────┬───────────┘
                                              │
                                   ┌──────────▼───────────┐
                                   │  Supabase Postgres   │
                                   │                      │
                                   │  courses             │
                                   │  sections            │
                                   │  meetings            │
                                   │  rmp_cache           │
                                   │  scraper_runs        │
                                   └──────────▲───────────┘
                                              │
                                   ┌──────────┴───────────┐
                                   │  Scraper (Railway)   │
                                   │  cron: */30 * * * *  │
                                   │                      │
                                   │  Playwright → Banner │
                                   │  httpx → RMP         │
                                   └──────────────────────┘
```

**API and scraper share one Docker image** — the scraper is a separate Railway service with a different `startCommand`. One build pipeline, zero drift between environments.

---

## Technical highlights

### 1. Backtracking CSP solver with MRV ordering

The naive approach — `itertools.product` over all section combinations — materialises up to 15⁸ ≈ 2.5 billion candidates before checking a single conflict. That's a non-starter.

The solver uses iterative backtracking with **Minimum Remaining Values (MRV) ordering**: the course with the fewest valid sections is assigned first. A course with 2 candidates at the root of the search tree prunes entire subtrees that would otherwise be explored at every level. In practice this reduces explored nodes by 10–100× on typical 3–5 course inputs.

A hard **800ms wall-clock deadline** is enforced every 500 nodes via `monotonic_ns()`. The check is amortised — calling it every 500 nodes costs ~0.2µs per 2,000 nodes instead of ~100µs for every-node checks. When the budget expires, the solver returns whatever results it found plus a truncation warning rather than a 500.

```python
SOLVE_TIME_BUDGET_MS = 800
MAX_COURSES = 8
EXPLORE_LIMIT = 25   # stop after finding this many; rank and return top 10
NODE_CHECK_INTERVAL = 500
```

### 2. Minute-of-week integers for conflict detection

On the solver's hot path, `sections_conflict(a, b)` is called millions of times on adversarial inputs. It can't afford datetime parsing or set intersection.

Each meeting is pre-expanded at load time to a `(start_mow, end_mow)` integer pair using day offsets (Monday = 0, Tuesday = 1440, Wednesday = 2880, …). Conflict detection reduces to a single predicate:

```python
a.start < b.end and b.start < a.end
```

Day separation falls out of the arithmetic for free — Monday 10:00 = minute 600, Wednesday 10:00 = minute 3480. Multi-meeting sections (TR lecture + F lab) expand to one interval per day; any overlap with any interval is detected without special-casing.

### 3. Deterministic PDF parsing with pdfplumber + regex

The DegreeWorks parser could have been an LLM call. It isn't, for three reasons:

- **Determinism**: the same PDF always produces the same output. LLM responses vary — you can't write regression tests that pin parser output to specific inputs.
- **Failure mode**: when regex misses a requirement, `validate_parsed_degree` catches the credit inconsistency and returns a 422. When an LLM hallucinates a course code, it returns a plausible-looking wrong result that passes validation and silently generates a wrong graduation plan.
- **Cost**: ~$0.01–0.05 per parse × registration-week volume is a meaningful recurring cost for a free student tool.

pdfplumber extracts text column-by-column from DegreeWorks' fixed-format PDF. Compiled regex patterns extract each field. A `ParsedDegreeValidated` subtype — only ever instantiated inside `validate_parsed_degree()` — enforces that downstream functions only receive validated data:

```python
def generate_plan(degree: ParsedDegreeValidated) -> ...:  # type error to pass raw ParsedDegree
```

### 4. PostgreSQL advisory lock for scraper concurrency

A scrape during registration week can take longer than 30 minutes (Banner slows under load). Without a guard, the next Railway cron fires while the first run is still in progress — doubling the Banner request rate and creating race conditions on the `DELETE + INSERT` in the meetings table.

The guard uses `pg_try_advisory_xact_lock` (transaction-level), not `pg_try_advisory_lock` (session-level). Session-level locks are tied to the underlying connection — asyncpg returns connections to the pool between operations, which can silently release a session lock mid-scrape. Transaction-level locks release on commit or rollback, a boundary that's explicitly controlled.

### 5. Scraper error taxonomy

The Banner scraper distinguishes two non-retriable failure classes:

- **`BannerBlockedError`** (403): log and continue to the next subject. One blocked subject doesn't mean all subjects are blocked.
- **`BannerSchemaError`** (unexpected JSON structure): abort all remaining subjects. A Banner schema change from an Ellucian upgrade affects the entire instance — processing further subjects would write corrupt data.

Network timeouts are retriable with `[5, 15, 30]` second backoff. Retrying a 403 against the same blocked IP (the old behaviour) wasted time and kept a hot connection to a system that had already flagged it.

### 6. Design system built on CSS tokens

The UI targets a specific aesthetic: Linear's layout and density, Vercel's data-heavy tables, Raycast's command-palette interaction. The design is enforced through a Tailwind token layer — no raw color classes anywhere in the codebase.

```css
/* Every color in the app lives here */
--color-bg:       #0A0A0A;
--color-surface:  #111111;
--color-njit-red: #D22630;
```

Every course code, CRN, time, and room number uses `font-mono`. This is the app's visual signature and is non-negotiable in code review.

---

## Stack

| Layer | Tech |
|---|---|
| Frontend | Next.js 15 App Router, TypeScript, Tailwind v4, Zustand, Geist |
| Backend | FastAPI, Python 3.12, SQLAlchemy 2.0 async, asyncpg |
| Scraper | Playwright (Banner), httpx (RMP) |
| Database | Supabase Postgres |
| Deploy | Vercel (frontend), Railway (API + scraper), shared Docker image |
| Monitoring | Sentry (API errors), Railway metrics |

---

## Project structure

```
apps/
  api/
    src/
      routers/          # FastAPI endpoints
      scheduler/
        solver.py       # CSP backtracking solver
        conflicts.py    # MOW interval math + commuter filters
        gap.py          # Gap minutes + campus days scoring
      services/
        dw_parser.py    # pdfplumber + regex (DegreeWorks)
        plan.py         # validate_parsed_degree, generate_plan
      scrapers/
        banner.py       # Playwright Banner scraper
        rmp.py          # httpx RMP scraper
        cron.py         # Orchestration: banner → rmp
      schemas/
      dependencies.py   # get_db (single source of truth)
      main.py           # lifespan, CORS, rate limiter, Sentry
  web/
    app/
      scheduler/        # Schedule builder page
      planner/          # Degree planner page
      courses/          # Course browser
      dashboard/        # Saved schedules
    components/
      calendar/         # ScheduleGrid, CourseBlock (pixel-accurate)
      scheduler/        # CourseSelector, ProfessorPicker, ResultNavigator
      plan/             # DegreeSummary, SemesterPlan, UploadZone
    store/scheduler.ts  # Zustand store (persisted, versioned, migrated)
    lib/api.ts          # All fetch calls
docs/
  DECISIONS.md          # 20+ architectural decision records
  API_CONTRACTS.md      # Full request/response contracts
```

---

## Running locally

**API**
```bash
cd apps/api
cp .env.example .env   # fill in DATABASE_URL, SUPABASE_URL, SUPABASE_ANON_KEY
uv sync
uv run uvicorn main:app --reload --port 8000
```

**Frontend**
```bash
cd apps/web
pnpm install
pnpm dev               # proxies /api/* to localhost:8000 via next.config.ts
```

**Environment variables required**

| Variable | Where | Description |
|---|---|---|
| `DATABASE_URL` | API | asyncpg connection string to Supabase |
| `SUPABASE_URL` | API | Supabase project URL |
| `SUPABASE_ANON_KEY` | API | Supabase anon key (read-only queries) |
| `CURRENT_TERM` | API | 6-digit NJIT term code, e.g. `202690` |
| `CORS_ORIGINS` | API | Comma-separated allowed origins |
| `NEXT_PUBLIC_API_URL` | Frontend | Empty in production (relative), `http://localhost:8000` in dev |

---

## Running backend tests safely

Database tests use a separate, disposable PostgreSQL service. They never use the
application's `DATABASE_URL` or load its `.env` file. Start Docker Desktop, then run
from the repository root:

```bash
docker compose -f compose.test.yml up -d --wait
cd apps/api
uv sync --frozen
MIGRATION_DATABASE_URL=postgresql+asyncpg://njit_test:test-only@127.0.0.1:55432/njit_test \
uv run --no-sync python -m scripts.migrate apply
APP_ENV=test \
TEST_DATABASE_URL=postgresql+asyncpg://njit_test:test-only@127.0.0.1:55432/njit_test \
uv run --no-sync pytest tests/ -q
```

The service binds only to `127.0.0.1:55432`, provisions a disposable database and
restricted test role, and keeps its data in memory. The migration command builds
the schema. The credentials above are public, test-only credentials.
Before collecting tests, pytest verifies the database's identity and disposable
marker using a read-only connection and a non-superuser test role. Remote hosts,
other database/role names, connection query parameters, and unmarked databases
are rejected before fixtures run. CI provisions and checks the same database
identity in its disposable PostgreSQL service.

Each database test creates a uniquely named schema, applies the same real migration
chain, and uses only that schema for its connections. There is no handwritten test
schema. Real commits and multiple sessions work normally. Teardown closes
the test's connections and drops only its own schema, including after a failed or
cancelled test. It never deletes shared rows by course code, professor name, or
scraper-run age. Scraper advisory lock IDs are also unique per test, so repeated
and concurrent pytest runs can share this disposable service safely.

For pure and mocked tests without Docker, run from `apps/api`:

```bash
uv run pytest tests/ -m "not database" -q
```

The `database` marker is applied automatically to tests using database fixtures.
Running the full suite without explicit test database configuration fails before
fixture setup; it does not silently skip the database tests. Test imports also
disable Sentry and substitute local dummy service settings.

To remove the disposable database, run from the repository root:

```bash
docker compose -f compose.test.yml down --volumes
```

Stopping the service discards its data. Start it again to recreate a clean schema.
If a pytest process is forcibly killed, stopping the service also removes any test
schemas whose teardown could not run. After changing the bootstrap SQL, recreate
the service with the cleanup and startup commands above to apply the new bootstrap.

---

## Database migrations

`apps/api/migrations/manifest.json` lists migrations in order. Fresh databases apply
000, 007, 009, 012, and 013. Migration 008 remains deferred until meeting backfill
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

### Verify the runtime schema before deployment

From `apps/api`, select the database explicitly and run the read-only deployment gate:

```bash
DATABASE_URL='postgresql+asyncpg://USER:PASSWORD@HOST:5432/DATABASE' \
uv run --no-sync python -m scripts.verify_migrations
```

It checks the six runtime tables and all 37 required columns, including
`sections.section_number` and every scraper-status field. It also checks column
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

---

## Architectural decisions

Over 20 ADRs are documented in [`docs/DECISIONS.md`](docs/DECISIONS.md), covering every significant choice from the solver algorithm to the PDF parsing strategy to the color palette. A few worth reading:

- **ADR-5/6/7**: Why backtracking CSP beats brute-force product, why MRV ordering, why 800ms
- **ADR-8**: Minute-of-week integers — why two integer comparisons beats day-string set intersection on the hot path
- **ADR-9**: Why pdfplumber + regex beats Claude for DegreeWorks parsing
- **ADR-3**: Why transaction-level advisory locks instead of session-level with asyncpg
- **ADR-4**: The scraper error taxonomy and why 403s must not be retried
