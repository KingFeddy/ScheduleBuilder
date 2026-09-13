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

The repository provides separate API and scraper Dockerfiles. The scraper image
includes Chromium; both install the same locked Python runtime dependencies.

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

The guard holds `pg_try_advisory_xact_lock` in a dedicated connection and transaction for the entire Banner or RMP run. Saving the initial run record, committing progress, or rolling back the scraper's data transaction cannot release it. A second run of the same scraper skips while the first is active; Banner and RMP have separate lock IDs.

The lock connection closes and rolls back when the guard exits, including on failure or cancellation. A skipped run releases its extra connection before logging the overlap. The guard uses a real transaction even if the supplied engine defaults to autocommit, and disables the idle-transaction timeout only within that owned transaction. The cron pool has room for both the lock and data connections and is disposed on every exit. Real PostgreSQL regressions in `apps/api/tests/scrapers/test_lock_lifetime.py` cover these lifetimes, overlapping runs, and connection reuse.

### 5. Scraper response validation and errors

Banner search results must be HTTP 200 JSON with `success: true`, a section array, and a non-negative integer `totalCount`. Only `data: []` with `totalCount: 0` proves an empty catalog. Each page is checked before any of its rows are written: required section structure, matching subject/term, unique CRNs, expected page length, stable total counts, and pagination echoes when supplied. Failed or ambiguous responses stop subject cleanup and preserve existing sections and meetings.

Cleanup also requires every section update to succeed. One failed upsert skips all stale-section deletion for that subject; successfully refreshed sections remain saved, and a later fully successful run can resume cleanup. Each section and its meetings share one transaction. Incomplete or reversed meeting times reject the replacement instead of silently dropping a meeting, so a failed update keeps the previous schedule intact. Valid asynchronous and TBA sections remain supported. Real PostgreSQL tests in `apps/api/tests/scrapers/test_banner_write_safety.py` cover errors during writes and commits, cancellation, recovery, and what a separate reader can see during an update.

The scraper distinguishes three error classes:

- **`BannerBlockedError`** (401/403 or non-JSON content): log and continue to the next subject without retrying the blocked request.
- **`BannerSchemaError`** (unexpected response or required section structure): abort remaining subjects at the first malformed page.
- **`BannerResponseError`** (failed search, incomplete/inconsistent results, or other non-200 HTTP status): preserve the catalog. Invalid result sets abort the subject; HTTP request failures use the existing retry loop.

Network timeouts and retryable request failures use `[5, 15, 30]` second backoff. Browser-free regressions in `apps/api/tests/scrapers/test_banner_responses.py` exercise the actual response parser and scraper against synthetic HTTP responses and an isolated PostgreSQL catalog, including first-page failures, later-page failures, and verified empty results.

Prerequisite refreshes store versioned expression trees in
`courses.prerequisites_rules`. They preserve AND/OR alternatives, explicit groups,
minimum grades, academic levels, and source-provided concurrency conditions.
Corequisites are fetched separately and retain required section CRNs when supplied.
Each rule set records the observed term and representative section CRN; it does
not establish that every section or future term has identical requirements.
Missing concurrency information stays `unspecified`. Unsupported tests, wildcards,
grades, ambiguous corequisite section alternatives, or mixed AND/OR without explicit
grouping stay unresolved, with their original evidence retained.

Both prerequisite and corequisite sources must be fully understood before replacing
the retained rules. Failed lookup, extraction, resolution, or database writes keep
the previous rules and their evidence together. Only a complete recognized
`section[aria-labelledby="preReqs"]` containing the `Catalog Prerequisites` heading
alone verifies no prerequisites; blank/error HTML or an empty prerequisite table
does not. Corequisites support a recognized empty section or a complete table with
the expected headers and no rows. Subject lookup still requires successful JSON,
unambiguous entries, and fewer than its 100-entry limit; a full page stays unresolved
until pagination support is added.

`prerequisites_source` holds the subject lookup and both rule responses associated
with the retained rules. `prerequisites_latest_attempt` separately records the
latest candidate, outcome, and evidence, including failures. Evidence includes the
URL, term/CRN, fetch time, HTTP status/content type, decoded response text, and its
UTF-8 SHA-256 hash. Sources over 256 KiB retain a bounded text prefix and the full
text hash, are marked truncated, and cannot verify rules. NUL-containing text uses
reversible base64 encoding for PostgreSQL JSONB storage. Evidence is stored internally
and is not rendered by the frontend or included in the public API.

Migration 015 adds these three JSONB fields and invalidates old verification
timestamps/statuses without changing any legacy arrays. `prerequisites_status` is
`unverified` for legacy/new data, `verified` for supported structured extraction,
`verified_empty` when both sources explicitly contain no conditions, `failed` for
request/write failures, or `unresolved` for unsupported data. Attempt time and errors
describe the latest finished attempt; the verification time remains attached to the
last successfully saved rules and survives later failures. Cancellation propagates,
and prerequisite failures allow independent section refreshes to continue.

The legacy `prerequisites` array remains an unverified compatibility projection.
Simple AND-only prerequisites can update its codes; alternatives, concurrency, or
corequisites retain its historical value instead of being flattened. The public
API and planner behavior are unchanged. Student eligibility enforcement remains
Goal 27. Tests use synthetic Banner responses; they do not certify the current NJIT
production formats. Regression coverage lives in
`apps/api/tests/scrapers/test_prerequisite_rules.py` and
`apps/api/tests/scrapers/test_prerequisite_verification.py`.

### 6. Course metadata and credit estimates

Course credits and titles refresh from Banner's section search fields after a
complete subject scrape with successful section writes. All observed sections of
a course must agree before a field is verified. Title and credit validation are
independent: a missing title can retain the previous title while valid credits
update. Incomplete pagination, failed section writes, conflicting values, or invalid
fields preserve previously saved metadata. A rejected metadata transaction rolls
back before recording its failure; cancellation preserves the prior values and
propagates.

Supported credit values include zero, one-credit labs, four-credit courses, and
fractions with at most two decimal places, from 0 through 100. `creditHours` and
`creditHourLow`/`creditHourHigh` must be consistent. Variable-credit `TO` bounds
remain a range; `OR` bounds remain discrete alternatives. Missing, contradictory,
or unsupported fields never become a verified three-credit default.

Migration 016 makes `courses.title` and `courses.credits` nullable and changes
credits to PostgreSQL `NUMERIC`. Existing values remain intact and unverified until
refreshed. New courses can have unknown credits and titles. `title_source` and
`credits_source` store each verified value with its URL, observed term, section
CRNs, timestamp, and selected Banner fields. `metadata_latest_attempt` records
missing/invalid/conflicting fields or a rejected save. Malformed evidence is bounded
and safely encoded; source evidence is internal to the database.

Course search/detail responses expose title status, credit status, variable-credit
bounds/options, and refresh warnings. The interface distinguishes fixed credits,
variable credits, missing data, and unverified legacy values. GER titles also carry
verification status. These statuses describe the retained observation; they do not
prove that all future terms will have the same metadata.

The planner uses verified fixed credits directly. For variable credits it uses the
upper supported value as an explicitly labeled estimate; unknown courses use a
labeled three-credit estimate, and legacy values stay labeled unverified estimates.
Course rows, semester totals, and the overall total identify estimated amounts.
These labels survive reloads; older saved plans without verification fields are
treated as estimates. Selecting a replacement course also marks inherited credits
as estimates until proper recalculation is implemented in Goal 46. Quantity-aware
allocation and full credit reconciliation remain Goals 24 and 26.

Tests cover extraction, actual PostgreSQL writes/rollback/cancellation, migration
upgrades, API contracts, planner use, and browser rendering. Fixtures are synthetic;
the current NJIT production formats and existing data still require the later
catalog audit and release checks.

### 7. Design system built on CSS tokens

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
uv sync --locked
uv run --no-sync uvicorn main:app --reload --port 8000
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

## Production Python dependencies and startup

Both Dockerfiles install dependencies once with `uv sync --locked --no-dev`.
`httpx` is a runtime dependency because the RMP scraper imports it. Pytest,
pytest-asyncio, and Hypothesis remain in the development group. The lockfile fixes
package versions, and `--locked` rejects inconsistent project/lockfile metadata
instead of updating the lock during a build.

API, scraper, browser-installation, and schema-verification commands use
`uv run --no-sync` to run the installed environment without resolving or installing
packages again. Docker pins uv 0.11.8 by image digest; CI uses the same uv version.
Update these pins together when upgrading uv. Python's base-image tag and Debian
browser libraries are separate from the Python lockfile; these builds are not
claimed to be byte-identical images.

The production dependency regressions create a separate environment containing
only runtime packages and copy only runtime sources. Installation may download
locked wheels when the uv cache is cold. Subsequent startup commands run with
uv network access disabled, and tests compare installed packages and lockfile
contents before and after startup. Scraper tests replace external scrape calls
with synthetic callbacks. API health and schema-verification checks use only the
opt-in disposable database described below; they read its migrated public schema.

To run the import and scraper startup checks without PostgreSQL, from `apps/api`:

```bash
uv sync --locked
uv run --no-sync pytest tests/deployment/test_production_dependencies.py -m "not database" -q
```

The complete backend test command below also runs the real API health and schema
verification checks from that production-only environment. This validates the
checked-in startup commands; confirming the commands selected by live Railway
services remains part of deployment configuration review.

## Docker build contexts

Build either image from the repository root with **`apps/api` as the context**:

```bash
docker build --tag schedule-builder-api:local --file apps/api/Dockerfile apps/api
docker build --tag schedule-builder-scraper:local --file apps/api/Dockerfile.scraper apps/api
```

`apps/api/.dockerignore` is shared by both images. Its allowlist admits only
`main.py`, `pyproject.toml`, `uv.lock`, Python modules under `src` and `scripts`,
numbered migration SQL, and the migration manifest. Final exclusions block
hidden files and directories, virtual environments, caches, tests, build outputs,
browser downloads, and other local tooling even when nested inside source trees.
Environment files, credential files, database dumps, PDFs, frontend dependencies,
and local documentation stay outside the context. Both Dockerfiles also copy
runtime paths explicitly; the image builds its own `.venv` and scraper browsers.
Configure deployed secrets through the service's runtime environment.

The root `.dockerignore` applies the same policy when a tool selects the entire
repository as its context. This additional filter does not change the Dockerfiles'
relative source paths: the supported build commands above still use `apps/api`.
Confirming the context and configuration selected by live Railway services remains
part of deployment configuration review.

Keep both allowlists aligned when adding a runtime asset type, and extend the
Docker test fixture's expected inputs. Docker uses the ignore file at the context
root; a `Dockerfile.dockerignore` or `Dockerfile.scraper.dockerignore` would override
that shared policy. See [Docker's context documentation](https://docs.docker.com/build/building/context/#dockerignore-files).

Run the lightweight context checks from `apps/api`, with Docker running:

```bash
RUN_DOCKER_TESTS=1 uv run --no-sync pytest tests/deployment/test_docker_contexts.py -m "not docker_image" -q
```

These four checks use Docker itself to export each Dockerfile's filtered context
at both directory roots. Fixtures copy Git-visible runtime source files and plant
synthetic credentials, a fake host Python environment, caches, and unrelated files
at multiple depths. The exported file list must exactly match the required inputs.
They need no base-image or dependency downloads and no database.

To also build and inspect both complete Linux/amd64 images:

```bash
RUN_DOCKER_TESTS=1 uv run --no-sync pytest tests/deployment/test_docker_contexts.py -q
```

The two additional checks may download base images, locked packages, and Chromium.
Allow at least 8 GB of free host/Docker storage for a cold build and its cache.
They verify the final files, the image's Linux Python environment, production-only
imports, browser-driver integrity, and scraper browser rendering using synthetic
HTML. Inspection runs without network access and with a
read-only root filesystem. Each check removes its unique test image and container;
Docker retains reusable build cache. These six checks are explicitly opt-in and
skip during ordinary backend runs unless `RUN_DOCKER_TESTS=1` is set.

## Keeping API contracts aligned

FastAPI response models are the source of truth for JSON shapes. The checked-in
`apps/api/openapi.json` snapshot generates `apps/web/lib/api.generated.ts` using
the pinned `openapi-typescript` development dependency. `lib/api.ts` derives its
public types from each endpoint's response or request schema. Do not edit the
generated TypeScript by hand.

After changing a request or response model, regenerate both files from the
repository root:

```bash
(cd apps/api && uv run --no-sync python -m scripts.export_openapi)
pnpm --filter web api:generate
pnpm --filter web typecheck
```

The exporter overrides application settings with synthetic values, disables
dotenv and telemetry, and imports the app without starting its lifespan. It
needs installed Python dependencies, but no running API, database, or credentials.
Type generation reads only the local snapshot. Commit both generated files
alongside their model and consumer changes.

Check the contracts without rewriting files:

```bash
(cd apps/api && uv run --no-sync python -m scripts.export_openapi --check)
(cd apps/api && uv run --no-sync pytest tests/deployment/test_api_contracts.py -q)
pnpm --filter web typecheck
```

The existing backend CI suite checks that the snapshot matches the app and
exercises real route serialization with synthetic inputs. Frontend `typecheck`
checks generated-file freshness before compiling consumers, browser fixtures,
and compile-time contract regressions. These checks do not require a live API.

Contract details that previously differed between the two sides:

- `truncated` belongs on `SolveResponse`, including searches with no results.
- Section lists use `SectionResponse`; solved sections additionally carry `term`
  and `section_number` through `SolveSectionResponse`.
- Unknown titles, professor metadata, and degree credit totals remain nullable.
  The UI labels missing titles/totals and avoids calculations with unknown totals.
- Scraper status always returns `last_scrape`, `status`, `sections_upserted`, and
  `error_message`. This corrects the old `sections_updated` / `error` keys. With
  no recorded run, status is `never_run` and the other three values are null.

These checks establish response-shape consistency; they do not validate arbitrary
JSON at runtime in the browser. Planner generation inputs remain broad dictionaries
in the backend schema pending the separate input-validation work.

## Frontend API errors and cancellation

API helpers in `apps/web/lib/api.ts` reject failures with `ApiError`, which keeps:

- `kind`: `http`, `network`, or `invalid-response`.
- `status`: the received HTTP status, or null when no response was received.
- `detail`: the original validation detail, error object, or fallback text.
- `retryAfter`: the received `Retry-After` header, and `retryAfterMs`: its delay
  calculated when the error is created. Missing/invalid delays stay null.
- `cause`: the underlying transport or decoding error when available.

`getApiErrorMessage(error, fallback)` supplies display text. It formats validation
field locations/messages without stringifying submitted `input` or `ctx`, uses
HTTP status to identify rate limiting, and uses the caller's fallback for server
errors and unreadable successful responses. Keep structured details out of routine
logs because validation errors can contain submitted audit data.

Every helper accepts an optional final `{ signal }` argument. Use a fresh
`AbortController` for each request lifetime and abort it when that work is no
longer needed. Cancellation rejects with an `AbortError`, including during body
reading; `isAbortError(error)` recognizes it and `getApiErrorMessage` returns null.
Timeout signals remain failures. Callers must also guard their state updates when
work becomes obsolete. The GER dialog and scraper-status polling cancel on cleanup;
the separate upload/search/solve request-identity work remains in the backlog.

`getProfessor` resolves null only for HTTP 404. Network/server failures reject and
must not be cached as missing professors. The modal displays failed lookups
separately from a successful not-found result. No API helper automatically retries
requests. Retry metadata is available only when the server/proxy exposes the header;
the client does not invent a server delay or configure backend rate limiting.

Run the API client tests without a browser, server, or database:

```bash
pnpm --filter web test:unit
```

The 35 tests reuse the installed Playwright runner with a separate unit-test
configuration. They replace fetch before each test and restore it afterward;
every response is synthetic. The existing frontend CI job runs them after
typechecking. Cancellation and retry handling follow the browser's
[AbortSignal](https://developer.mozilla.org/en-US/docs/Web/API/AbortSignal) and
[Retry-After](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Retry-After)
contracts.

## Running frontend browser regressions

Run from the repository root (Node.js 20.9+ and pnpm 9.15):

```bash
pnpm install --frozen-lockfile
pnpm --filter web exec playwright install chromium
pnpm --filter web test:e2e         # next dev, http://127.0.0.1:3100
pnpm --filter web test:e2e:prod    # fresh next build + next start, port 3101
pnpm --filter web typecheck
pnpm --filter web test:unit
pnpm --filter web lint
```

On Linux CI, install Chromium's system dependencies with
`pnpm --filter web exec playwright install --with-deps chromium`.
The production-mode command tests a **local production build**. Both commands own
their server, refuse an occupied port, and stop it after the run. They use separate
build directories under `apps/web/.next/e2e-*`, leaving the normal `.next` build
available. Next's Geist font compilation still needs access to Google Fonts on a
cold build; browser installation also requires network access.

The 13 browser tests cover course search, solve request filters, timed and async
meeting rendering, scheduler selection persistence, empty results and retry, a
synthetic PDF upload, generated semesters and saved-plan restoration, and upload /
generation errors with retry. They also cover missing course titles, missing degree
metadata, GER search with a missing title, validation details, rate-limit retry
delays, failed versus missing professor lookups, and GER request cancellation.
All API responses are mocked. The upload bytes and
student profile in `apps/web/e2e/data.ts` are fictional; they do not validate the
real DegreeWorks parser or academic planning rules.

Tests import `test` and `expect` from `apps/web/e2e/fixtures.ts`. This installs
context-wide request interception before navigation, rejects unmocked API and
external HTTP requests, and fails on page or unexpected console errors. Chromium's
HTTP-status messages are allowed only for observed failing mock API responses.
Every test starts with
empty cookies and storage; reloads within a test retain that test's data. The
clock is fixed while timers keep running. Application API and Sentry environment
variables are overridden, and API rewrites are disabled only for the test server.
No backend, database, real PDF, or personal browser profile is needed.

Use `api.respond(method, pathname, json, status)` to override a response,
`api.reset(method, pathname)` to restore the default, or `api.handle(...)` for a
delayed response. Successful `respond` payloads and default fixtures use the
generated API types; error overrides supply an explicit error status and `detail`.
Assert submitted payloads using `api.requests(...)` alongside
visible outcomes. All mock helper paths use decoded names, including professor
names containing spaces and commas. Add focused regressions as later goals fix the remaining UI
issues; these tests do not establish full frontend or academic-rule coverage.

```bash
pnpm --filter web test:e2e e2e/scheduler.spec.ts
pnpm --filter web test:e2e --headed
pnpm --filter web exec playwright show-report playwright-report/development
```

Reports, failure screenshots, and traces are ignored by Git under
`apps/web/playwright-report/{development,production}` and
`apps/web/test-results/{development,production}`. There are no automatic retries
that could hide a failing first attempt. The test design follows Playwright's
[API mocking](https://playwright.dev/docs/mock) and
[browser isolation](https://playwright.dev/docs/browser-contexts) guidance.

## Running backend tests safely

Database tests use a separate, disposable PostgreSQL service. They never use the
application's `DATABASE_URL` or load its `.env` file. Start Docker Desktop, then run
from the repository root:

```bash
docker compose -f compose.test.yml up -d --wait
cd apps/api
uv sync --locked
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

Database tests that create records use a uniquely named schema, apply the same real migration
chain, and use only that schema for their connections. There is no handwritten test
schema. Real commits and multiple sessions work normally. Teardown closes
the test's connections and drops only its own schema, including after a failed or
cancelled test. It never deletes shared rows by course code, professor name, or
scraper-run age. Scraper advisory lock IDs are also unique per test, so repeated
and concurrent pytest runs can share this disposable service safely.

For pure and mocked tests without Docker, run from `apps/api`:

```bash
uv run --no-sync pytest tests/ -m "not database" -q
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
000, 007, 009, 012, 013, 014, 015, and 016. Migration 008 remains deferred until meeting backfill
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

It checks the six runtime tables and all 47 required columns, including
`sections.section_number`, course metadata sources, prerequisite rules/evidence/verification metadata, and every
scraper-status field. It also checks column
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

---

## Architectural decisions

Over 20 ADRs are documented in [`docs/DECISIONS.md`](docs/DECISIONS.md), covering every significant choice from the solver algorithm to the PDF parsing strategy to the color palette. A few worth reading:

- **ADR-5/6/7**: Why backtracking CSP beats brute-force product, why MRV ordering, why 800ms
- **ADR-8**: Minute-of-week integers — why two integer comparisons beats day-string set intersection on the hot path
- **ADR-9**: Why pdfplumber + regex beats Claude for DegreeWorks parsing
- **ADR-3**: Why transaction-level advisory locks instead of session-level with asyncpg
- **ADR-4**: The scraper error taxonomy and why 403s must not be retried
