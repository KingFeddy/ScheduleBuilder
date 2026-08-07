# NJIT Schedule Builder

A full-stack web app for NJIT students: conflict-free schedule generation, professor RMP research, and DegreeWorks PDF → semester graduation plan. No accounts, no server-side user data — just a fast, public tool that works.

**Live at:** [[njitschedule.com](https://njitschedule.com)](https://njit-schedule-builder-web.vercel.app/scheduler)

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

## Architectural decisions

Over 20 ADRs are documented in [`docs/DECISIONS.md`](docs/DECISIONS.md), covering every significant choice from the solver algorithm to the PDF parsing strategy to the color palette. A few worth reading:

- **ADR-5/6/7**: Why backtracking CSP beats brute-force product, why MRV ordering, why 800ms
- **ADR-8**: Minute-of-week integers — why two integer comparisons beats day-string set intersection on the hot path
- **ADR-9**: Why pdfplumber + regex beats Claude for DegreeWorks parsing
- **ADR-3**: Why transaction-level advisory locks instead of session-level with asyncpg
- **ADR-4**: The scraper error taxonomy and why 403s must not be retried
