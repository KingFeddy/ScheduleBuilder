"""Import official catalog titles and fallback credits independently of sections."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
import httpx
from sqlalchemy import text

from src.scrapers import lock
from src.scrapers.course_metadata import parse_credits, parse_title

MAX_PAGE_BYTES = 4 * 1024 * 1024
_NUMBER = r"[0-9]+(?:\.[0-9]+)?"
_HEADING = re.compile(
    rf"(?P<subject>[A-Z]{{2,5}})\s*(?P<number>[0-9]{{3}}[A-Z]?)\.\s+"
    rf"(?P<title>.+?)\.\s+(?P<low>{_NUMBER})"
    rf"(?:\s*(?P<separator>[-–]|or)\s*(?P<high>{_NUMBER}))?\s+credits?"
    r"(?:\.|,\s+[0-9]+(?:\.[0-9]+)?\s+contact hours?\s*\([^()]*\)\.)?",
)


@dataclass(frozen=True)
class CatalogCourse:
    course_code: str
    title: str
    credits: dict
    heading: str


@dataclass(frozen=True)
class CatalogPage:
    url: str
    subject: str
    catalog_year: int
    observed_at: str
    courses: tuple[CatalogCourse, ...]

    def evidence(self, course: CatalogCourse, value) -> dict:
        return {"schema_version": 1, "source_kind": "njit_catalog", "url": self.url,
                "catalog_year": self.catalog_year, "observed_at": self.observed_at,
                "heading": course.heading, "value": value}


def validate_catalog_url(url: str) -> None:
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.netloc != "catalog.njit.edu"
            or not re.fullmatch(r"/undergraduate/(?:[a-z0-9-]+/)+", parsed.path)
            or parsed.query or parsed.fragment):
        raise ValueError("Use an HTTPS undergraduate page on catalog.njit.edu without query or fragment.")


async def fetch_catalog_page(url: str, *, client: httpx.AsyncClient | None = None) -> str:
    validate_catalog_url(url)
    if client is None:
        async with httpx.AsyncClient(timeout=30, follow_redirects=False) as owned_client:
            return await fetch_catalog_page(url, client=owned_client)
    async with client.stream("GET", url, follow_redirects=False, timeout=30) as response:
        response.raise_for_status()
        if response.headers.get("content-type", "").split(";")[0].strip().lower() != "text/html":
            raise ValueError("Catalog response must be HTML.")
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body.extend(chunk)
            if len(body) > MAX_PAGE_BYTES:
                raise ValueError("Catalog page exceeds the import size limit.")
        return body.decode("utf-8", errors="strict")


def parse_catalog_page(html: str, *, url: str, subject: str, catalog_year: int) -> CatalogPage:
    """Validate the entire requested subject before returning any writable records."""
    validate_catalog_url(url)
    if not re.fullmatch(r"[A-Z]{2,5}", subject):
        raise ValueError("Subject must be an uppercase alphabetic catalog prefix.")
    if type(catalog_year) is not int or not 1900 <= catalog_year <= 2199:
        raise ValueError("Catalog year must be between 1900 and 2199.")
    if len(html.encode("utf-8")) > MAX_PAGE_BYTES:
        raise ValueError("Catalog page exceeds the import size limit.")
    soup = BeautifulSoup(html, "html.parser")
    editions = {tuple(map(int, match.groups())) for heading in soup.select("h1")
                if (match := re.fullmatch(r"University Catalog\s+([0-9]{4})\s*[-–]\s*([0-9]{4})",
                                          heading.get_text(" ", strip=True), re.IGNORECASE))}
    if editions != {(catalog_year, catalog_year + 1)}:
        raise ValueError("Page catalog edition does not match the requested year.")
    records: dict[str, CatalogCourse] = {}
    for block in soup.select(".courseblock"):
        heading_element = block.select_one(".courseblocktitle")
        if heading_element is None:
            # A damaged course block prevents claiming a complete page import.
            raise ValueError("Catalog course block has no title heading.")
        heading = " ".join(heading_element.get_text(" ", strip=True).split())
        if not re.match(rf"{re.escape(subject)}(?=\s|[0-9]|\.)", heading):
            continue
        match = _HEADING.fullmatch(heading)
        if not match or match["subject"] != subject:
            raise ValueError(f"Unsupported {subject} course heading: {heading[:200]}")
        code = subject + match["number"]
        title, title_error = parse_title(match["title"], code, lambda value: value)
        raw = {"creditHourLow": match["low"], "creditHourHigh": match["high"],
               "creditHourIndicator": ("OR" if match["separator"] == "or" else "TO") if match["high"] else None}
        credits, credits_error = parse_credits(raw)
        if title_error or credits_error:
            raise ValueError(f"Invalid catalog title or credits for {code}.")
        record = CatalogCourse(code, title, credits, heading)
        if code in records and (records[code].title, records[code].credits) != (title, credits):
            raise ValueError(f"Conflicting catalog entries for {code}.")
        records[code] = record
    if not records:
        raise ValueError(f"No supported {subject} courses found on the catalog page.")
    return CatalogPage(url, subject, catalog_year, datetime.now(timezone.utc).isoformat(), tuple(records.values()))


# Catalog titles are canonical; section titles may describe topics/honors variants.
# Catalog credits are fallback evidence: verified Banner bounds remain authoritative.
# Compare JSON numbers without unsafe casts; malformed/unknown editions are retained.
_UPSERT = text("""
    INSERT INTO courses (course_code, title, credits, title_source, credits_source)
    VALUES (:code, :title, CAST(:credits AS numeric), CAST(:title_source AS jsonb), CAST(:credits_source AS jsonb))
    ON CONFLICT (course_code) DO UPDATE SET
        title = CASE WHEN
            COALESCE(courses.title_source->>'source_kind', '') <> 'njit_catalog'
            OR (jsonb_typeof(courses.title_source->'catalog_year') = 'number'
                AND courses.title_source->'catalog_year' <= EXCLUDED.title_source->'catalog_year')
            THEN EXCLUDED.title ELSE courses.title END,
        title_source = CASE WHEN
            COALESCE(courses.title_source->>'source_kind', '') <> 'njit_catalog'
            OR (jsonb_typeof(courses.title_source->'catalog_year') = 'number'
                AND courses.title_source->'catalog_year' <= EXCLUDED.title_source->'catalog_year')
            THEN EXCLUDED.title_source ELSE courses.title_source END,
        credits = CASE WHEN
            courses.credits_source IS NULL OR courses.credits_source = '{}'::jsonb
            OR (courses.credits_source->>'source_kind' = 'njit_catalog'
                AND jsonb_typeof(courses.credits_source->'catalog_year') = 'number'
                AND courses.credits_source->'catalog_year' <= EXCLUDED.credits_source->'catalog_year')
            THEN EXCLUDED.credits ELSE courses.credits END,
        credits_source = CASE WHEN
            courses.credits_source IS NULL OR courses.credits_source = '{}'::jsonb
            OR (courses.credits_source->>'source_kind' = 'njit_catalog'
                AND jsonb_typeof(courses.credits_source->'catalog_year') = 'number'
                AND courses.credits_source->'catalog_year' <= EXCLUDED.credits_source->'catalog_year')
            THEN EXCLUDED.credits_source ELSE courses.credits_source END
""")


async def import_catalog_page(session, page: CatalogPage) -> int:
    """Apply a validated page atomically, sharing Banner's writer lock.

    No sections, offerings, prerequisites, attempt history, or absent courses change.
    The returned count is processed candidates, not necessarily changed records.
    """
    async with lock.advisory_lock(session, lock.BANNER_SCRAPER_LOCK_ID, "catalog-metadata") as acquired:
        if not acquired:
            raise RuntimeError("A Banner or catalog import is already running; retry later.")
        async with session.begin():
            await session.execute(_UPSERT, [{
                "code": course.course_code, "title": course.title,
                "credits": str(course.credits["minimum"]) if course.credits["kind"] == "fixed" else None,
                "title_source": json.dumps(page.evidence(course, course.title)),
                "credits_source": json.dumps(page.evidence(course, course.credits)),
            } for course in page.courses])
    return len(page.courses)
