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

DegreeWorks uploads use local `pdfplumber` text extraction and compiled regex
patterns. The parse endpoint runs structural and business consistency checks before
returning `ParsedDegreeValidated`. These checks catch supported inconsistencies;
they do not establish that every requirement or academic rule was extracted.
The planner accepts that structured type:

```python
def generate_plan(degree: ParsedDegreeValidated) -> ...:  # type error to pass raw ParsedDegree
```

Each still-needed requirement retains a stable `requirement_id`, its label and
normalized `options`, `remaining_quantity`, `quantity_unit` (`classes`, `credits`,
or `unknown`), computed `quantity_status`, and nullable `source` context. For
example, `2 Classes` remains two classes and `6 Credits` remains six credits.
Unreadable, ambiguous, or numerically unrepresentable amounts stay null and
unresolved; zero and supported fractional credits remain explicit. Recognized
blocks with unsupported options retain their raw context for later review.

Source context records the PDF's server-computed SHA-256, block occurrence,
one-based extracted-text line, and captured text through the next `Still needed:`
marker. It is extraction evidence, not a page coordinate or proof of eligibility.
The endpoint remains stateless. Context returned later by a client is untrusted.

Identities are versioned deterministic hashes. Re-parsing the same PDF preserves
IDs, while repeated requirements receive distinct IDs. Legacy JSON without IDs
gets repeatable identities without invented quantities; explicitly supplied IDs
are preserved and duplicates within an audit are rejected. Each generated course
slot has a separate `slot_id` and carries its full source requirement. Changing
the chosen course or semester keeps the slot identity; extra electives and load
fillers have their own IDs and `requirement: null`.

Planner rows show the audit amount or an unknown label and use slot IDs as React
keys. Nested metadata survives save/reload and regeneration. Older saved rows
remain readable with a temporary rendering key; they are not rewritten with
invented identities. **A known quantity does not mean it has been fulfilled.**
Goal 24 now allocates known quantities and reports unresolved remainders (see
below). Goal 25 now prevents duplicate allocation; reconciliation remains Goal 26 and
validated swaps Goal 46. Goal 20 requires no database migration;
release the updated API before its frontend consumer.

Plan generation validates browser-supplied data again. `GenerateRequest` contains
a `ParsedDegree` and `PlanPreferences`; the route runs the same
`validate_parsed_degree` business checks as PDF parsing before calling the planner.
An empty degree, missing major, inconsistent known credit totals, or an empty
requirement list without explicitly zero remaining credits returns HTTP 422.
A completed degree with no remaining requirements and zero remaining credits
still succeeds. Unknown totals and quantities remain supported when requirements
are present; validation does not certify extraction completeness or eligibility.

Preferences default to no elective courses and 15 credits per semester. Explicit
credit targets must be integers from 3 through 24; nulls, booleans, numeric strings,
and fractional values are rejected. Each elective must be one concrete ASCII
course code. Case and spaces normalize before duplicate detection; wildcards,
comma-separated entries, invalid types, and duplicate electives receive a useful
field error. Unknown preference/envelope keys are rejected to catch misspellings.
Both request objects are required, even when `preferences` is `{}`.

Plan-route validation errors contain field locations, messages, and error types,
without echoing submitted input or validator context. This also keeps malformed
numeric JSON such as `NaN` from crashing the error response. Degree totals use
strict nonnegative integers, labels/history entries must be nonblank strings, and
catalog years must have four digits. Existing transfer-code filtering still runs
at the shared business boundary. Typed frontend request aliases come from the
generated API contract; browser storage validation remains Goal 37. Goal 21 adds
no migration or dependency changes.

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
API schema remains unchanged. The planner now diagnoses structured rules after
packing (see below); automatic constraint-based rescheduling and registration
eligibility enforcement remain unfinished. Tests use synthetic Banner responses;
they do not certify the current NJIT
production formats. Regression coverage lives in
`apps/api/tests/scrapers/test_prerequisite_rules.py` and
`apps/api/tests/scrapers/test_prerequisite_verification.py`.

#### Structured prerequisite checks on generated plans

Generation performs one additional batched read for the final selected courses'
`prerequisites_status` and `prerequisites_rules`, then checks the proposed semester
assignments against both prerequisite and corequisite trees. AND requires every
condition; OR accepts a qualifying alternative without demanding the unused ones.
Results distinguish satisfied recorded conditions, conflicts with the supplied
history/schedule, and unknown evidence. They do not establish degree completion
or registration eligibility.

Minimum grades use NJIT's published A, B+, B, C+, C, D, F ordering. A D does not
satisfy a minimum C. P/S, transfer marks and imported grade variants outside that
scale do not acquire invented threshold equivalents. Every attempt is considered;
a later failed retake does not erase an earlier qualifying attempt. Passing a
course condition does not require degree-credit units: a successful zero-credit
course can still satisfy a recorded course prerequisite.

Attempt timing accepts explicit Banner term codes or unambiguous Spring/Summer/Fall
year text. Undated history and unknown timing stay unresolved, including legacy
completed-course summaries. Prior courses must precede the target semester;
concurrent courses must occur in the same semester. Planned or in-progress courses
never acquire an assumed final grade. Academic-level restrictions and required
section CRNs remain unknown because the planner has no matching evidence/selection.

Missing, malformed, failed or unverified rule data receives a grouped notice;
an empty legacy array is not evidence of no requirements. Verified-empty rules
must explicitly contain empty prerequisite and corequisite groups. Rules observed
in a different term are identified separately and any findings are conditional.
Even same-term rules describe a representative section, not every possible section.

The diagnostics use the existing generated-plan warnings, which persist with a
saved plan. Regenerate older plans against the updated API to obtain these checks.
**This pass reports problems without changing course choices or semester packing.**
The current packer still uses legacy prerequisite lists and capstone grouping;
resolving structured alternatives/concurrency during scheduling remains the next
repair. Warnings explain that unresolved conflicts need review before using the
proposed schedule. No new API fields or database migration are required.

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
as estimates until proper recalculation is implemented in Goal 46. Goal 24 counts
only verified fixed credits toward credit requirements; full credit reconciliation
remains Goal 26.

Tests cover extraction, actual PostgreSQL writes/rollback/cancellation, migration
upgrades, API contracts, planner use, and browser rendering. Fixtures are synthetic;
the current NJIT production formats and existing data still require the later
catalog audit and release checks.

#### Import official catalog metadata without section listings

The standalone importer reads one explicitly selected undergraduate department
page and subject from `catalog.njit.edu`. This fills catalog-only course rows,
such as PHYS485, even when no sections were collected. It validates the displayed
catalog edition and all relevant course headings before writing anything. An
invalid real-course row, conflicting duplicate, or missing subject aborts the page.
Recognized level placeholders such as `COM 1**` and `CS 4**` are validated and
excluded from writable courses. The preview lists their original headings in
`skipped_placeholders` and reports `skipped_placeholder_count`; apply reports the
skipped count. These placeholders never become course codes or wildcard selections.
Other malformed code formats still reject the page, and a subject containing only
placeholders cannot be imported successfully. The importer does
not discover other pages or run automatically in the Banner/RMP cron.

Preview the source candidates from `apps/api` (no database or `.env` is needed):

```bash
uv run --no-sync python -m scripts.import_catalog_metadata \
  --url https://catalog.njit.edu/undergraduate/science-liberal-arts/physics/ \
  --subject PHYS --catalog-year 2026
```

The JSON preview contains source records, **not a comparison with the database**.
The physics page was previewed against the 2026–2027 catalog and yielded 51 PHYS
courses, including PHYS485 at 3 credits. Other subject/page pairs require their
own preview. The catalog year is explicit so a new annual edition requires review.

Additional 2026–2027 department previews checked on September 14, 2026:

| Subject | Official department page | Concrete courses | Skipped placeholders |
| --- | --- | ---: | ---: |
| BME | [Biomedical Engineering](https://catalog.njit.edu/undergraduate/newark-college-engineering/biomedical/) | 41 | 0 |
| COM | [Humanities and Social Sciences](https://catalog.njit.edu/undergraduate/science-liberal-arts/humanities-and-social-sciences/) | 49 | 1 |
| HSS | [Humanities and Social Sciences](https://catalog.njit.edu/undergraduate/science-liberal-arts/humanities-and-social-sciences/) | 2 | 0 |
| CS | [Computer Science](https://catalog.njit.edu/undergraduate/computing-sciences/computer-science/) | 56 | 3 |

Use the relevant page and subject with the same preview command. These are observed
counts, not permanently expected totals or proof of complete university coverage.
The records include BME301, COM312, HSS404, CS490 and CS491; previews alone have not
updated stored titles. All source headings and credits remain reviewable in JSON.

After deploying the updated Banner writer and reviewing the preview, apply with
the intended database environment (retain a current backup for a live import):

```bash
uv run --env-file .env --no-sync python -m scripts.import_catalog_metadata \
  --url https://catalog.njit.edu/undergraduate/science-liberal-arts/physics/ \
  --subject PHYS --catalog-year 2026 --apply
```

`--apply` verifies the runtime schema, acquires the shared Banner writer lock,
and imports the entire page in one transaction. A busy writer or failed write
aborts the import; no partial page is committed. The reported count is processed
candidates, not necessarily changed rows. No course is deleted. Source evidence
records the official URL, catalog year, observation timestamp and original heading
in the existing metadata JSON fields; no schema migration is required.

Official catalog titles take precedence over Banner section topics and honors
variants. Updated Banner writers preserve that title and source; **older deployed
writers do not**, so update the scraper before applying catalog records. Catalog
credits fill missing/unverified values or refresh earlier catalog evidence.
Verified Banner credits/bounds remain authoritative and can replace catalog
fallback credits on a later successful scrape. Older catalog editions cannot
replace newer catalog evidence. Variable credits remain ranges/options rather
than an invented fixed scalar.

The importer leaves sections, seats, prerequisite evidence, and Banner attempt
history intact. New courses retain unverified prerequisites. Title-topic conflict
warnings are omitted when an official catalog title is retained; credit and save
errors still appear. Catalog presence establishes neither a future offering nor
degree eligibility. Apply reviewed pages and regenerate saved plans before expecting
the live planner to use the corrected data; a preview alone changes nothing.

### 7. Configurable catalog coverage

`CATALOG_SUBJECTS` is the comma-separated collection scope used by the scraper and
interpreted by the API. Leave it unset on both services to use the shared default,
or set identical overrides. The default covers 86 subject codes: all 78 supported
subjects checked in the September 2026 Banner refresh plus eight historical codes
retained for compatibility. Existing explicit overrides continue to take precedence;
remove or expand an old narrow override on both services when adopting this scope.
These defaults do not prove collection, future offerings, or degree eligibility.
PSY and PSYC remain distinct;
no subject aliases are guessed. Case and surrounding whitespace are normalized,
duplicates are removed, and blank entries or non-letter subject codes are rejected.

`GER_SUBJECTS` configures the broad elective browser independently. Its defaults
retain the previous browser subjects and include LIB/SSC, which already had swap
controls. Subject membership alone does not establish GER eligibility; requirement
validation and regenerated swaps remain Goals 31 and 46. The browser reports missing
data and subjects excluded from collection instead of silently hiding those gaps.

`GET /api/catalog/coverage?term=202690` returns configured subjects, elective browser
subjects, and per-subject course/section counts. Course counts span all retained
catalog records; section counts include only the requested term. It includes
configured subjects with zero records and retained subjects outside configuration.
Warnings distinguish excluded subjects, missing catalog courses, and missing term
sections. Zero collected sections does **not** prove that NJIT offers no classes.
These counts do not describe scrape completion or freshness; use the separate
semester-specific refresh status described below.

Course search/detail, elective candidates, and planned courses carry a separate
`catalog_status` and `catalog_note`. `present` means a row exists for a configured
subject; it never means degree eligibility or future availability is verified.
`subject_not_configured` warns even when an old row has verified credits.
`course_missing` identifies an absent course within the collection scope.
`unresolved` identifies TBD/FREE slots, and `unknown` means coverage has not been
checked. Missing courses can remain advisory planning options with visible warnings
and credit estimates. Unconfigured wildcard requirement subjects are also reported.

The scheduler exposes coverage details and warns about excluded/empty subjects
while searching. Course-specific warnings remain visible on required plan rows and
survive reloads. Older stored rows and replacements without a new catalog lookup
show unchecked coverage. Coverage describes configuration/data at lookup time;
regenerate a saved plan to check it again. Versioned storage validation and general
warning persistence remain Goals 37 and 40. No database migration is required for
coverage; newly configured subjects acquire data on subsequent successful scrapes.

### 8. Shared semester discovery and selection

`CURRENT_TERM` is the shared runtime default for the API, planner, and scraper.
Set the same value on API and scraper services; use six ASCII digits ending in
`10` (Spring), `50` (Summer), or `90` (Fall). Invalid configuration fails at startup.
Updating this setting changes the default without a frontend code change.

`GET /api/terms` returns `default_term` and a sorted `terms` array containing
`code`, `label`, and `has_data`. It discovers distinct valid terms from retained
section rows and always includes the configured default, even when it has no
sections. `has_data` means at least one stored section; it does not certify
completeness, freshness, or NJIT registration availability. This is the local
catalog's term list, not an upstream academic calendar.

The scheduler fetches this list before semester-specific requests. Its Semester
selector follows the server default on each visit, or remembers an explicit
choice. Older saved automatic defaults are rechecked; a removed saved choice
falls back to the current default with a notice. A semester with no collected
sections stays visible with an explanation and a disabled Solve button. Discovery
failures offer a retry and do not reuse an unverified cached semester.

Changing semesters preserves course choices and commuter filters, clears old
results and professor choices, and cancels obsolete section/solve requests.
Section lookups, coverage counts, and solves use the selected term. Course detail
also accepts `?term=...`; omitting it uses the configured default. New graduation
plans start from that same default, still skipping Summer as before. Calendar
rollover advances Fall to the following Spring without guessing a default from
the computer's date. Future course-offering validation remains Goal 30.

No database migration is required. Deploy the term-discovery API before the
updated frontend, and keep the API and scraper default settings aligned.

### 9. Semester-specific refresh status and data age

`GET /api/scraper/status?term=202690` separates `latest_attempt` from
`last_successful_refresh`. It reads overall Banner runs for the requested term;
subject diagnostic rows and other terms cannot replace that status. A full refresh
requires a completed run with zero section failures, known counts, valid start/end
timestamps, and a recorded scope covering the currently configured subjects.
Failed, partial, running, or overlap-skipped attempts never advance it.

Migration 017 records each run's requested `subjects`, supports a `partial` outcome,
and adds an index for term-specific status lookups. Existing history is preserved
with unknown scope, so old records cannot establish a full refresh under the new
contract. A subsequent successful scrape supplies that proof. Mixed legacy runs
with positive failure counts are reported as partial rather than completed.

The scraper marks a run completed only when every requested subject succeeds;
a validated empty subject is a success. Mixed subject/section outcomes are partial.
An entirely unsuccessful or empty request fails. An exception may occur after
earlier pages committed, so aggregate counts become unknown rather than reporting
incomplete totals as exact. Committed progress survives a later page failure or
cancellation, so an interrupted run is partial when any section or complete
subject was refreshed and failed otherwise. Cancellation still propagates, with
best-effort health-record finalization. Skipped attempts record a finish time but
cannot refresh data. Metadata/prerequisite verification remains separate from
these section and seat-refresh outcomes.

`data_as_of` conservatively uses the earlier of the full run's start and the oldest
retained section timestamp for the term, including older excluded-subject rows.
Missing/future timestamps, no full-refresh proof, or no section rows produce an
unknown age. This does not claim all observations happened simultaneously.
The scheduler shows the latest attempt, last full refresh, and seat-data age.
More than 45 minutes is stale; unknown ages and failed checks are visibly flagged.
Polling runs sequentially every three minutes after a response, with a retry on
failure. Age keeps advancing between responses using server time plus elapsed
browser time; semester changes/navigation cancel obsolete polling.

Apply migration 017 before the updated API/scraper and deploy the coordinated
frontend contract. The endpoint now requires `term` and replaces the ambiguous
`last_scrape` field. No live scraper or production migration is run by local tests.

### 10. Design system built on CSS tokens

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
| `CURRENT_TERM` | API + scraper | Shared default, e.g. `202690`; six ASCII digits ending in 10/50/90. Use identical values on both services; the frontend discovers it through `/api/terms`. |
| `CATALOG_SUBJECTS` | API + scraper | Optional override of the shared 86-subject default; leave unset on both services or use identical values |
| `GER_SUBJECTS` | API | Optional comma-separated elective browser subject scope; membership does not establish eligibility |
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
- Parsed audits carry `course_attempts`: course code, grade, credits, original term
  text, extracted-line source, and derived `status`/`earns_credit`. Failed,
  withdrawn, incomplete, audited, in-progress, and zero-credit attempts do not
  enter `completed_courses`. Unknown grades/credit amounts remain unresolved.
  Every repeated occurrence is retained; a previous credit-bearing pass can
  coexist with a current retake. Summary lists are recomputed from attempts when
  present, including when generation receives client JSON.
- Letter grades (including imported +/- variants), P/S, and transfer T/TR marks
  stay distinct evidence. Earning credit does not prove a minimum-grade
  prerequisite; generated-plan diagnostics now evaluate supported minimum-grade
  evidence, while automatic scheduling enforcement remains pending. The NJIT status classifications
  follow its [grading legend](https://www.njit.edu/registrar/grading-instructions);
  T/TR compatibility retains the parser's existing transfer marks.
  Legacy saved audits have `course_attempts: null`, retain their original summary
  lists, and receive no invented grades or terms. Re-upload the PDF to extract
  history with the corrected parser; this does not repair old saved audits.
- Still-needed requirements carry identity, amount/unit/status, options, and source
  context. Planned slots carry `slot_id` and a nullable full `requirement` object.
  Quantity status describes extraction certainty, not fulfillment.
- Scraper status requires a term and separates the latest attempt, last full
  refresh, and data-age cutoff. Counts and errors belong to their run objects.
  Unknown runs, timestamps, and counts remain nullable; an actual zero stays zero.

These checks establish request/response-shape consistency; they do not validate
arbitrary JSON at runtime in the browser. Generation uses a typed `ParsedDegree`
and `PlanPreferences` request plus the shared parser business checks. Python and
TypeScript model names do not establish trust in previously stored client data.

Course-history extraction accepts standalone rows or whitespace-separated PDF
columns with complete grade and credit cells. Missing terms stay null. Ambiguous
merged rows are skipped instead of assigning a neighbor's grade. Extraction is
not an audit of all PDF layouts, credit reconciliation, or repeat-replacement
policy; real-PDF acceptance remains Goal 63. No grades or PDFs are stored by the
stateless API. The browser keeps the parsed history with its existing saved audit.

DegreeWorks option lists are parsed in source order with shared department
inheritance: `CS 490 or 4@` becomes `CS490, CS4XX`, and `CS 3@ or 490` becomes
`CS3XX, CS490`. A level wildcard fills two digits; `CS @` becomes `CSXXX`, while
universal `@ @` becomes `@`. Alternatives use `or`, commas, or wrapped lines;
horizontal spaces separate subject/number cells, not arbitrary alternatives.
Duplicate choices keep their first position. The existing R510/R512 exclusion
also prevents following bare numbers from inheriting an earlier NJIT department.

Wrapped choices stop before neighboring headings or grade rows. Unsupported
tokens, qualifiers, or malformed wildcard cells leave the entire option list
unresolved rather than choosing a recognized subset or broadening eligibility.
The requirement keeps its identity, label, amount/unit, and original source text;
generation retains a TBD slot and explains that the options need review. This
does not implement a general DegreeWorks expression language or verify PDF
layout completeness. Re-upload old audits to apply the corrected parser.
Universal/level choices use the existing elective matcher. Goal 24 expands each
known requirement into multiple course selections or explicit unresolved slots.

### Requirement quantity allocation

A two-class requirement selects two distinct concrete options when available.
A six-credit requirement uses verified fixed course credits: 4 + 2 allocates six;
4 + 3 allocates seven because whole courses are selected. If only four verified
credits can be selected, the requirement explicitly reports two credits unresolved.
Missing classes become individual TBDs; missing credits become estimated TBD
amounts split by the semester target. These placeholders do not count as allocated
coursework. Variable, unknown, and unverified credit estimates also do not count
toward credit requirements. Explicit selections with uncertain credits stay visible
with an unresolved remainder; automatic selections prefer verified alternatives.

Known zero quantities generate no requirement rows. Unknown quantities retain an
advisory suggestion without invented allocation totals. Completed/in-progress
courses remain excluded. Matching requested electives can fill additional slots;
unmatched extras and optional load fillers remain separate. Expansion is bounded
by a 200-slot allocation budget (existing input rows are retained), with any
unexpanded remainder reported explicitly.

Each requirement-linked row includes nullable `allocation` metadata with
`required_quantity`, `quantity_unit`, `allocated_quantity`, `unresolved_quantity`,
and status `allocated`, `partial`, or `unknown`. This is a shared requirement
summary repeated on its rows, **not amounts to add together across rows**. Each
row has a stable occurrence-based slot ID. Progress survives save/reload and
regeneration. A local swap marks progress unknown on every row of the affected
requirement until regeneration; older saved rows need regeneration for progress.

Allocation describes planned selections, not completed degree requirements or
verified eligibility. Goal 25 prevents duplicate allocation and flags unverified sharing; overall
credit reconciliation and filler policy remain Goal 26; prerequisite rules remain
Goal 27 and validated swaps Goal 46. Real-PDF acceptance remains Goal 63. No new
migration is required; release the updated API before its frontend consumer.

### Course ownership and ambiguous overlap

Goal 25 assigns each concrete course to one requirement and schedules it once.
Its credits therefore contribute once to the selected-course totals. Known
requirements take priority over unknown quantities; requirements with only explicit
options precede wildcard choices, with fewer available options first. Equal
constraints retain audit order. A flexible requirement tries unused alternatives
instead of duplicating a course claimed elsewhere. Matching requested electives
are reused as selections, not appended again as extra copies.

If an overlap cannot be resolved, the other requirement retains its identity,
quantity, and unresolved allocation. Its row explanation and generation warning
name the claimed course and requirement and state that sharing is unverified.
This explanation survives saved-plan reloads; general warning persistence is
still Goal 40. No current audit field or verified server rule authorizes sharing,
so repeated labels/options, major/minor membership, and client-added flags do not
permit double counting. Legitimate sharing requires verified policy support.

This deterministic allocation order is not a global optimization or degree-policy
engine. TBD credits remain clearly labeled estimates for unresolved work, separate
from the selected course counted once. Total reconciliation/filler policy remains
Goal 26. Regenerate old saved plans to apply the fix; full validation of local
course replacements remains Goal 46. No schema or migration changes are needed.

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
cd apps/api
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

---

## Architectural decisions

Over 20 ADRs are documented in [`docs/DECISIONS.md`](docs/DECISIONS.md), covering every significant choice from the solver algorithm to the PDF parsing strategy to the color palette. A few worth reading:

- **ADR-5/6/7**: Why backtracking CSP beats brute-force product, why MRV ordering, why 800ms
- **ADR-8**: Minute-of-week integers — why two integer comparisons beats day-string set intersection on the hot path
- **ADR-9**: Why pdfplumber + regex beats Claude for DegreeWorks parsing
- **ADR-3**: Why transaction-level advisory locks instead of session-level with asyncpg
- **ADR-4**: The scraper error taxonomy and why 403s must not be retried
