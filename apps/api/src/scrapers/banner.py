"""
Banner scraper — resilient Playwright implementation (S7).
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
import re
import time as time_module
from datetime import time
from typing import Optional

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.schemas.prerequisites import PrerequisiteRefresh, SubjectLookup, legacy_course_codes

from .lock import advisory_lock, BANNER_SCRAPER_LOCK_ID
from .prerequisites import (
    PrerequisiteDataError,
    PrerequisiteRequestError,
    fetch_subject_lookup,
    fetch_prerequisites,
)

logger = logging.getLogger(__name__)

BANNER_HOST = "https://reg-prod.ec.njit.edu"
BANNER_BASE = f"{BANNER_HOST}/StudentRegistrationSsb/ssb"
PAGE_SIZE   = 500
RETRY_DELAYS = [5, 15, 30]

# Keys that must be present in every Banner section object.
# Absence means Banner's JSON schema changed — abort, don't silently corrupt.
REQUIRED_SECTION_KEYS = {"courseReferenceNumber", "subject", "courseNumber", "meetingsFaculty"}

# Banner day keys in calendar order — order determines the output string.
_DAY_MAP = [
    ("monday",    "M"),
    ("tuesday",   "T"),
    ("wednesday", "W"),
    ("thursday",  "R"),
    ("friday",    "F"),
    ("saturday",  "S"),
]


class BannerBlockedError(Exception):
    """Banner returned 403, an HTML error page, or unparseable content."""


class BannerSchemaError(Exception):
    """Banner JSON has an unexpected structure — possibly an Ellucian upgrade."""


class BannerResponseError(Exception):
    """Banner failed the search or returned an unverifiable result set."""


# ─── Parsing helpers ──────────────────────────────────────────────────────────

def _parse_hhmm(banner_time: str) -> time:
    """Parse Banner's 4-digit 'HHMM' string to datetime.time."""
    return time(int(banner_time[:2]), int(banner_time[2:]))


def _clean_course_title(raw: str) -> str:
    """
    Normalize a raw Banner courseTitle into consistent, readable text.

    Banner's raw title field has three independent quality issues, all
    confirmed against live data during design (see
    docs/superpowers/specs/2026-08-07-course-title-cleanup-design.md):
    - HTML entities left un-decoded (e.g. "Elect &amp; Comp Engr Tech")
    - An Honors section's title carries a "- Honors" suffix in varying
      case/spacing that doesn't belong on the course's canonical name —
      Honors is a section variant, not a different course
    - Honors sections, and separately NJIT's "Special Topics" (ST:)
      courses, are often rendered fully uppercase by Banner

    Casing normalization uses plain str.title() — acronyms embedded in an
    all-caps title (e.g. "AI", "ST:") come out imperfect ("Ai", "St:"),
    and the same limitation applies to possessives/contractions (e.g.
    "WOMEN'S STUDIES" -> "Women'S Studies", "INT'L BUSINESS" ->
    "Int'L Business"), since there's no reliable way to distinguish an
    acronym or a letter-after-apostrophe from a regular word boundary
    without a maintained whitelist. Accepted tradeoff, not a bug.
    """
    cleaned = html.unescape(raw)
    cleaned = re.sub(r"\s*-\s*honors\s*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.strip()
    if cleaned and cleaned == cleaned.upper() and cleaned != cleaned.lower():
        cleaned = cleaned.title()
    return cleaned


def _parse_meeting_pattern(
    pattern: dict,
) -> tuple[Optional[str], Optional[time], Optional[time], Optional[str]]:
    """
    Extract (days, start_time, end_time, location) from one Banner meetingsFaculty entry.
    Returns None for any field Banner doesn't provide (async/TBA sections).
    Day characters are always in MTWRFS calendar order.
    """
    meeting_time = pattern.get("meetingTime", {})

    days_str = "".join(char for key, char in _DAY_MAP if meeting_time.get(key))
    days = days_str or None

    start_raw = meeting_time.get("beginTime")
    end_raw   = meeting_time.get("endTime")
    start_time = _parse_hhmm(start_raw) if start_raw else None
    end_time   = _parse_hhmm(end_raw)   if end_raw   else None

    location = (
        f"{pattern.get('building', '')} {pattern.get('room', '')}".strip() or None
    )

    return days, start_time, end_time, location


def _extract_professor_name(raw_section: dict) -> Optional[str]:
    """Return the primary faculty displayName for a section.

    Banner stores faculty at the top-level 'faculty' key on the section object.
    The nested meetingsFaculty[i].faculty list is always empty in the current
    Banner version — checking it last as a fallback for older API responses.
    """
    # Primary path: top-level faculty array (current Banner behavior)
    for member in raw_section.get("faculty", []):
        if member.get("primaryIndicator") and member.get("displayName"):
            return member["displayName"]
    # Fallback: first non-empty displayName regardless of primaryIndicator
    for member in raw_section.get("faculty", []):
        if member.get("displayName"):
            return member["displayName"]
    # Legacy path: meetingsFaculty[i].faculty (older Banner versions)
    for meeting in raw_section.get("meetingsFaculty", []):
        for member in meeting.get("faculty", []):
            if member.get("displayName"):
                return member["displayName"]
    return None


# ─── Schema validation ────────────────────────────────────────────────────────

def _validate_section_schema(section: object) -> None:
    """
    Validate the required section structure before writing any row on its page.
    """
    if not isinstance(section, dict):
        raise BannerSchemaError("Banner section must be a JSON object")
    missing = REQUIRED_SECTION_KEYS - set(section.keys())
    if missing:
        raise BannerSchemaError(
            f"Banner section missing expected keys: {missing}. "
            f"Banner may have been upgraded. Keys present: {list(section.keys())[:10]}"
        )

    for key in ("courseReferenceNumber", "subject", "courseNumber"):
        value = section[key]
        if not isinstance(value, str) or not value.strip() or value != value.strip():
            raise BannerSchemaError(f"Banner section '{key}' must be a non-empty string without surrounding whitespace")

    meetings = section["meetingsFaculty"]
    if not isinstance(meetings, list):
        raise BannerSchemaError("Banner section 'meetingsFaculty' must be an array")
    for meeting in meetings:
        if not isinstance(meeting, dict):
            raise BannerSchemaError("Banner meeting entry must be a JSON object")
        if not isinstance(meeting.get("meetingTime"), dict):
            raise BannerSchemaError(
                f"Banner meeting entry missing or invalid 'meetingTime' object. "
                f"Meeting keys present: {list(meeting.keys())}"
            )


def _validate_results_page(
    payload: object, *, subject: str, term: str, offset: int, page_size: int,
    expected_total: int | None, received_crns: set[str],
) -> tuple[list[dict], int]:
    """Require a complete, consistent page before it can contribute to cleanup.

    Only success=true, data=[], totalCount=0 at offset zero proves an empty
    result set. The total must remain stable, each page must fill its expected
    range, and every CRN must appear once in the requested subject and term.
    Received IDs are independent of successful database writes.
    """
    if not isinstance(payload, dict):
        raise BannerSchemaError("Banner search response must be a JSON object")
    if payload.get("success") is False:
        raise BannerResponseError(f"Banner search reported success=false at offset {offset}")
    if payload.get("success") is not True:
        raise BannerSchemaError("Banner search response must include boolean success=true")

    sections = payload.get("data")
    total = payload.get("totalCount")
    if not isinstance(sections, list):
        raise BannerSchemaError("Banner search 'data' must be an array; missing/null data is not an empty catalog")
    # bool is a subclass of int in Python, but is not a valid result count.
    if type(total) is not int or total < 0:
        raise BannerSchemaError("Banner search 'totalCount' must be a non-negative integer")
    if expected_total is not None and total != expected_total:
        raise BannerResponseError(
            f"Banner totalCount changed from {expected_total} to {total} at offset {offset}"
        )

    # Validate pagination echoes when supplied. Omitted echoes do not replace
    # the count/identity checks.
    for key, expected in (("pageOffset", offset), ("pageMaxSize", page_size)):
        if key in payload and (type(payload[key]) is not int or payload[key] != expected):
            raise BannerResponseError(f"Banner '{key}' does not match requested value {expected}")

    expected_rows = min(page_size, total - offset)
    if offset > total or len(sections) != expected_rows:
        raise BannerResponseError(
            f"Banner incomplete page at offset {offset}: received {len(sections)} rows, "
            f"expected {expected_rows} for totalCount {total}"
        )

    page_crns: set[str] = set()
    for section in sections:
        _validate_section_schema(section)
        if section["subject"] != subject or ("term" in section and section["term"] != term):
            raise BannerResponseError(f"Banner section does not match requested subject {subject} and term {term}")
        crn = section["courseReferenceNumber"]
        if crn in received_crns or crn in page_crns:
            raise BannerResponseError(f"Banner repeated CRN {crn} at offset {offset}")
        page_crns.add(crn)
    return sections, total


# ─── HTTP layer ───────────────────────────────────────────────────────────────

async def _fetch_page(
    page,
    url: str,
    params: dict,
    timeout_ms: int = 30_000,
) -> dict:
    """
    Fetch an HTTP 200 JSON object. Search/pagination validation is by the caller.

    Raises:
      BannerBlockedError — 401/403, non-JSON content-type, or non-JSON body
      BannerResponseError — absent response or any other non-200 HTTP status
      BannerSchemaError — JSON root is not an object
      PlaywrightTimeout  — network timeout (retriable by caller)
    """
    query = "&".join(f"{k}={v}" for k, v in params.items())
    response = await page.goto(
        f"{url}?{query}",
        timeout=timeout_ms,
        wait_until="networkidle",
    )

    if response is None:
        raise BannerResponseError("Banner navigation returned no HTTP response")
    if response.status in (401, 403):
        raise BannerBlockedError(f"Banner returned {response.status} — session or IP may be blocked.")

    content_type = response.headers.get("content-type", "").lower()
    if "text/html" in content_type:
        raise BannerBlockedError(
            f"Banner returned HTML (status {response.status}) — session may have expired."
        )
    if response.status != 200:
        raise BannerResponseError(f"Banner returned HTTP {response.status} for search results")
    media_type = content_type.split(";", 1)[0].strip()
    if media_type != "application/json" and not media_type.endswith("+json"):
        raise BannerBlockedError(f"Banner returned non-JSON content type '{content_type}'")

    body = await response.text()
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise BannerBlockedError(f"Banner returned non-JSON body: {exc}") from exc

    if not isinstance(data, dict):
        raise BannerSchemaError("Banner search response must be a JSON object")

    # Log only structural metadata; the caller rejects ambiguous/null data.
    data_field = data.get("data")
    logger.debug(
        "_fetch_page: status=%s keys=%s data_type=%s totalCount=%s success=%s",
        response.status,
        list(data.keys()),
        type(data_field).__name__,
        data.get("totalCount"),
        data.get("success"),
    )
    return data


# ─── Section upsert ───────────────────────────────────────────────────────────

async def _upsert_section_with_meetings(
    session: AsyncSession,
    raw_section: dict,
    term: str,
) -> None:
    """
    Upsert one section and atomically replace all its meetings.
    Takes a raw Banner section dict and extracts all fields internally.
    DELETE + INSERT within one transaction so the solver never sees a section
    with zero meetings mid-update.
    Invalid or failed meeting writes reject the entire replacement, retaining
    the previous section and every previous meeting through rollback.
    open_seats is clamped to 0 — Banner returns negative values for waitlisted sections.
    """
    crn            = raw_section["courseReferenceNumber"]
    course_code    = f"{raw_section['subject']}{raw_section['courseNumber']}"
    open_seats     = max(0, raw_section.get("seatsAvailable", 0))
    total_seats    = raw_section.get("maximumEnrollment", 0)
    section_number = raw_section.get("sequenceNumber")
    professor      = _extract_professor_name(raw_section)
    patterns       = raw_section.get("meetingsFaculty", [])

    # Section-level location from the first meeting that has one
    section_location: Optional[str] = None
    for p in patterns:
        loc = f"{p.get('building', '')} {p.get('room', '')}".strip() or None
        if loc:
            section_location = loc
            break

    async with session.begin():
        # Banner may reference a course_code not yet in the courses table
        # (e.g. a newly-added special-topics number). Stub it in first so the
        # sections FK doesn't reject the section outright. DO NOTHING — never
        # overwrite a real catalog title with this fallback.
        await session.execute(
            text("""
                INSERT INTO courses (course_code, title, credits)
                VALUES (:course_code, :title, :credits)
                ON CONFLICT (course_code) DO NOTHING
            """),
            {
                "course_code": course_code,
                "title":       _clean_course_title(raw_section.get("courseTitle") or course_code),
                "credits":     3,
            },
        )

        await session.execute(
            text("""
                INSERT INTO sections (crn, term, course_code, professor_name,
                                      total_seats, open_seats, location, scraped_at,
                                      section_number)
                VALUES (:crn, :term, :course_code, :professor_name,
                        :total_seats, :open_seats, :location, NOW(),
                        :section_number)
                ON CONFLICT (crn, term) DO UPDATE SET
                    professor_name = EXCLUDED.professor_name,
                    total_seats    = EXCLUDED.total_seats,
                    open_seats     = EXCLUDED.open_seats,
                    location       = EXCLUDED.location,
                    scraped_at     = EXCLUDED.scraped_at,
                    section_number = EXCLUDED.section_number
            """),
            {
                "crn":            crn,
                "term":           term,
                "course_code":    course_code,
                "professor_name": professor,
                "total_seats":    total_seats,
                "open_seats":     open_seats,
                "location":       section_location,
                "section_number": section_number,
            },
        )

        await session.execute(
            text("DELETE FROM meetings WHERE crn = :crn AND term = :term"),
            {"crn": crn, "term": term},
        )

        seen: set[tuple] = set()
        for pattern in patterns:
            days, start_time, end_time, location = _parse_meeting_pattern(pattern)

            dedup_key = (days, start_time, end_time)
            if dedup_key in seen:
                continue
            seen.add(dedup_key)

            if (start_time is None) != (end_time is None):
                raise ValueError(f"CRN {crn}: incomplete meeting time; section update rejected")

            if start_time is not None and end_time is not None and start_time >= end_time:
                raise ValueError(
                    f"CRN {crn}: meeting start {start_time} must precede end {end_time}; "
                    "section update rejected"
                )

            await session.execute(
                text("""
                    INSERT INTO meetings (crn, term, days, start_time, end_time, location)
                    VALUES (:crn, :term, :days, :start_time, :end_time, :location)
                    ON CONFLICT (crn, term, days, start_time, end_time) DO NOTHING
                """),
                {
                    "crn":        crn,
                    "term":       term,
                    "days":       days,
                    "start_time": start_time,
                    "end_time":   end_time,
                    "location":   location,
                },
            )


async def _delete_stale_sections(
    session: AsyncSession,
    subject: str,
    term: str,
    seen_crns: set[str],
) -> int:
    """
    Remove sections for this subject+term that Banner did not return in a
    completed scrape — cancelled/removed CRNs that upserts alone would
    otherwise leave sitting in the DB forever, since upserts only ever
    add or update, never remove. Meetings cascade-delete via their FK to
    sections. Only call this after a subject's scrape has fully completed and
    every section update has succeeded;
    "not seen" only means "removed" when the whole subject was checked.

    course_code is matched as "{subject}" followed by a digit, not a plain
    prefix — a plain 'LIKE subject%' would wrongly match a subject whose
    code happens to start with this one (e.g. a hypothetical 'CSE' matching
    a 'CS' prefix search).
    """
    async with session.begin():
        result = await session.execute(
            text("""
                DELETE FROM sections
                WHERE term = :term
                  AND course_code ~ :pattern
                  AND NOT (crn = ANY(:seen_crns))
            """),
            {
                "term":      term,
                "pattern":   f"^{subject}[0-9]",
                "seen_crns": list(seen_crns),
            },
        )
        return result.rowcount


# ─── Subject scrape ───────────────────────────────────────────────────────────

async def _refresh_course_prerequisites(
    session: AsyncSession, page, term: str, crn: str, course_code: str,
    subject_lookup: SubjectLookup | None,
    lookup_error: PrerequisiteDataError | PrerequisiteRequestError | None,
) -> None:
    """Save a complete replacement atomically, or record uncertainty without erasing data.

    The verified timestamp belongs to the retained rules and source evidence.
    Failed attempts preserve those together and record their candidate/evidence
    separately. Cancellation propagates; persistence failures are logged by type.
    """
    if lookup_error is not None:
        refresh = PrerequisiteRefresh(
            rules=None, sources=[lookup_error.source] if lookup_error.source else [],
            status="unresolved" if isinstance(lookup_error, PrerequisiteDataError) else "failed",
            error=str(lookup_error),
        )
    else:
        refresh = await fetch_prerequisites(page, BANNER_BASE, term, crn, subject_lookup)
    status, error = refresh.status, refresh.error
    attempt = {
        "schema_version": 1, "scope": {"term": term, "crn": crn},
        **refresh.model_dump(mode="json"),
    }
    if status in {"verified", "verified_empty"}:
        try:
            async with session.begin():
                await session.execute(text("""
                    UPDATE courses SET prerequisites = COALESCE(CAST(:prerequisites AS text[]), prerequisites),
                        prerequisites_rules = CAST(:rules AS jsonb),
                        prerequisites_source = CAST(:source AS jsonb),
                        prerequisites_latest_attempt = CAST(:attempt AS jsonb),
                        prerequisites_status = :status,
                        prerequisites_attempted_at = now(), prerequisites_verified_at = now(),
                        prerequisites_error = NULL
                    WHERE course_code = :course_code
                """), {
                    "prerequisites": legacy_course_codes(refresh.rules), "status": status,
                    "rules": refresh.rules.model_dump_json(),
                    "source": json.dumps({"schema_version": 1, "sources": attempt["sources"]}),
                    "attempt": json.dumps(attempt),
                    "course_code": course_code,
                })
            return
        except Exception:
            status, error = "failed", "Could not save prerequisite refresh."

    attempt.update(status=status, error=error)
    async with session.begin():
        await session.execute(text("""
            UPDATE courses SET prerequisites_status = :status,
                prerequisites_attempted_at = now(), prerequisites_error = :error,
                prerequisites_latest_attempt = CAST(:attempt AS jsonb)
            WHERE course_code = :course_code
        """), {"status": status, "error": error, "course_code": course_code, "attempt": json.dumps(attempt)})
    logger.warning("Banner/%s/%s/%s: prerequisites %s: %s", term, crn, course_code, status, error)


async def scrape_subject(
    session: AsyncSession,
    subject: str,
    term: str,
) -> tuple[int, int, int]:
    """
    Scrape all sections for one subject+term via Playwright.
    Returns (sections_upserted, sections_failed, sections_deleted).

    Raises BannerBlockedError, BannerSchemaError, or BannerResponseError if the
    subject's response set cannot be verified as complete.
    Timeouts and transient errors are retried per RETRY_DELAYS.

    Stale-section cleanup only runs when every page for this subject was
    successfully fetched and validated (the `complete` flag), and every section
    upsert has succeeded. A failed write must never turn a returned CRN into
    an apparently removed section, or authorize any subject-wide deletion.
    """
    upserted          = 0
    failed            = 0
    offset            = 0
    seen_crns: set[str] = set()
    received_crns: set[str] = set()
    expected_total: int | None = None
    complete           = False

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        )
        page = await context.new_page()

        try:
            await page.goto(
                f"{BANNER_BASE}/classSearch/classSearch",
                timeout=30_000,
                wait_until="networkidle",
            )

            # Banner requires a term selection POST before searchResults returns data.
            # Without this the session has no active term and every query returns data=null.
            term_resp = await page.request.post(
                f"{BANNER_BASE}/term/search?mode=search",
                form={
                    "term":            term,
                    "studyPath":       "",
                    "studyPathText":   "",
                    "startDatepicker": "",
                    "endDatepicker":   "",
                    "uniqueSessionId": f"scraper-{term}-{subject}",
                },
            )
            if term_resp.status != 200:
                raise BannerBlockedError(
                    f"Banner term/search POST returned {term_resp.status} for term {term}"
                )

            # Banner responds with {"fwdURL": "/StudentRegistrationSsb/ssb/classSearch/classSearch"}.
            # Navigating there switches the session from registration context to classSearch
            # context — without this step every subsequent searchResults query returns 500.
            fwd_body = json.loads(await term_resp.text())
            fwd_path = fwd_body.get("fwdURL", "")
            if fwd_path:
                await page.goto(
                    f"{BANNER_HOST}{fwd_path}",
                    timeout=30_000,
                    wait_until="networkidle",
                )

            lookup_error = None
            try:
                subject_lookup = await fetch_subject_lookup(page, BANNER_BASE, term)
            except Exception as exc:
                lookup_error = (
                    exc if isinstance(exc, (PrerequisiteDataError, PrerequisiteRequestError))
                    else PrerequisiteRequestError("Subject lookup request failed.")
                )
                logger.warning(
                    "Banner/%s: prerequisite refreshes will retain previous data: %s",
                    subject, lookup_error,
                )
                subject_lookup = None
            seen_course_codes: set[str] = set()

            while True:
                params = {
                    "txt_term":    term,
                    "txt_subject": subject,
                    "pageOffset":  offset,
                    "pageMaxSize": PAGE_SIZE,
                }

                page_data = None
                for attempt, delay in enumerate([0] + RETRY_DELAYS):
                    if delay > 0:
                        logger.info(
                            "Banner/%s: offset %d, attempt %d, waiting %ds",
                            subject, offset, attempt + 1, delay,
                        )
                        await asyncio.sleep(delay)

                    try:
                        page_data = await _fetch_page(
                            page,
                            f"{BANNER_BASE}/searchResults/searchResults",
                            params,
                        )
                        break
                    except BannerBlockedError:
                        raise  # never retry a block
                    except BannerSchemaError:
                        raise  # never retry a schema error
                    except PlaywrightTimeout:
                        if attempt == len(RETRY_DELAYS):
                            raise
                        logger.warning(
                            "Banner/%s: timeout at offset %d, retrying in %ds",
                            subject, offset, RETRY_DELAYS[attempt],
                        )
                    except Exception as exc:
                        if attempt == len(RETRY_DELAYS):
                            raise
                        logger.warning("Banner/%s: %s, retrying", subject, exc)

                sections, total = _validate_results_page(
                    page_data, subject=subject, term=term, offset=offset,
                    page_size=PAGE_SIZE, expected_total=expected_total,
                    received_crns=received_crns,
                )
                expected_total = total
                received_crns.update(raw["courseReferenceNumber"] for raw in sections)

                for raw in sections:
                    try:
                        await _upsert_section_with_meetings(session, raw, term)
                        upserted += 1
                        seen_crns.add(raw["courseReferenceNumber"])

                        course_code = f"{raw['subject']}{raw['courseNumber']}"
                        if course_code not in seen_course_codes:
                            seen_course_codes.add(course_code)
                            try:
                                await _refresh_course_prerequisites(
                                    session, page, term, raw["courseReferenceNumber"],
                                    course_code, subject_lookup, lookup_error,
                                )
                            except Exception as exc:
                                logger.warning(
                                    "Failed to record prerequisite outcome for %s (%s)",
                                    course_code, type(exc).__name__,
                                )
                    except Exception as exc:
                        logger.error(
                            "Failed to upsert CRN %s: %s",
                            raw.get("courseReferenceNumber"), exc,
                        )
                        failed += 1

                offset += len(sections)
                if offset == total:
                    complete = True
                    break

                await asyncio.sleep(2)

            deleted = 0
            if complete and failed == 0:
                deleted = await _delete_stale_sections(session, subject, term, seen_crns)
                if deleted:
                    logger.info(
                        "Banner/%s: removed %d stale section(s) no longer returned by Banner",
                        subject, deleted,
                    )
            elif complete:
                logger.warning(
                    "Banner/%s: skipping stale-section cleanup after %d failed section update(s)",
                    subject, failed,
                )

        finally:
            await browser.close()

    return upserted, failed, deleted


# ─── Full run ─────────────────────────────────────────────────────────────────

async def run_banner_scrape(
    session: AsyncSession,
    subjects: list[str],
    term: str,
) -> None:
    """
    Full Banner scrape with concurrency guard, per-subject isolation, and health log.

    Each subject is independent: a block on one subject logs it and continues.
    A schema change on any subject aborts all remaining subjects — the whole
    Banner JSON structure has changed and continuing would corrupt data.
    """
    async with advisory_lock(session, BANNER_SCRAPER_LOCK_ID, "banner") as acquired:
        if not acquired:
            await session.execute(
                text("""
                    INSERT INTO scraper_runs (scraper, term, status)
                    VALUES ('banner', :term, 'skipped_overlap')
                """),
                {"term": term},
            )
            await session.commit()
            return

        result = await session.execute(
            text("""
                INSERT INTO scraper_runs (scraper, term, status)
                VALUES ('banner', :term, 'running')
                RETURNING id
            """),
            {"term": term},
        )
        run_id = result.scalar()
        await session.commit()

        total_upserted = 0
        total_failed   = 0
        total_deleted  = 0

        for subject in subjects:
            t0 = time_module.monotonic()
            try:
                upserted, failed, deleted = await scrape_subject(session, subject, term)
                total_upserted += upserted
                total_failed   += failed
                total_deleted  += deleted
                logger.info(
                    "Banner/%s: %d upserted, %d failed, %d deleted, %.1fs",
                    subject, upserted, failed, deleted, time_module.monotonic() - t0,
                )

            except BannerBlockedError as exc:
                logger.error("Banner/%s: BLOCKED — %s", subject, exc)
                total_failed += 1
                await session.execute(
                    text("""
                        INSERT INTO scraper_runs (scraper, subject, term, status, error_message)
                        VALUES ('banner', :subject, :term, 'blocked', :msg)
                    """),
                    {"subject": subject, "term": term, "msg": str(exc)},
                )
                await session.commit()
                # One block may be subject-specific — continue with others

            except BannerSchemaError as exc:
                logger.error("Banner/%s: SCHEMA CHANGE — %s", subject, exc)
                total_failed += 1
                await session.execute(
                    text("""
                        INSERT INTO scraper_runs
                            (scraper, subject, term, status, error_message)
                        VALUES ('banner', :subject, :term, 'schema_change', :msg)
                    """),
                    {"subject": subject, "term": term, "msg": str(exc)},
                )
                await session.commit()
                break  # Schema change affects all subjects — abort

            except Exception as exc:
                logger.error("Banner/%s: unexpected — %s", subject, exc, exc_info=True)
                total_failed += 1

        final_status = (
            "failed"    if total_upserted == 0 and total_failed > 0
            else "completed"
        )
        await session.execute(
            text("""
                UPDATE scraper_runs
                SET status            = :status,
                    sections_upserted = :upserted,
                    sections_failed   = :failed,
                    finished_at       = NOW()
                WHERE id = :run_id
            """),
            {
                "status":   final_status,
                "upserted": total_upserted,
                "failed":   total_failed,
                "run_id":   run_id,
            },
        )
        await session.commit()
        logger.info(
            "Banner scrape complete: %d upserted, %d failed, %d deleted, status=%s",
            total_upserted, total_failed, total_deleted, final_status,
        )
