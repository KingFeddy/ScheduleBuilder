"""
Course prerequisite parsing — pure functions for extracting structured
prerequisite data from Banner's HTML/JSON responses.

Network-calling glue (fetch_subject_lookup, fetch_prerequisites) is added
in a later step of this same file — this module has zero I/O in its core
parsing logic, so it's fast and reliable to test in isolation.
"""
from __future__ import annotations
import logging

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


def parse_prerequisite_table(html: str) -> list[tuple[str, str]]:
    """
    Extract (subject_description, course_number) pairs from Banner's
    getSectionPrerequisites HTML response. Returns [] for a course with no
    prerequisites (no matching table/rows).

    Table columns, left to right: And/Or, (blank), Test, Score, Subject,
    Course Number, Level, Grade, (blank). Only Subject and Course Number
    are extracted — And/Or and Grade are intentionally discarded (see
    docs/superpowers/specs/2026-08-06-course-prerequisites-design.md
    decision 2: this schema is AND-only, grade minimums aren't stored).
    """
    soup = BeautifulSoup(html, "html.parser")
    pairs: list[tuple[str, str]] = []
    for row in soup.select("table.basePreqTable tbody tr"):
        cells = [td.get_text(strip=True) for td in row.find_all("td")]
        if len(cells) < 6:
            continue
        subject, course_number = cells[4], cells[5]
        if subject and course_number:
            pairs.append((subject, course_number))
    return pairs


def build_subject_lookup(subject_entries: list[dict]) -> dict[str, str]:
    """
    Build a {description: code} lookup from Banner's get_subject response —
    a list of {"code": "CS", "description": "Computer Science"} dicts.
    """
    return {entry["description"]: entry["code"] for entry in subject_entries}


def resolve_prerequisite_codes(
    pairs: list[tuple[str, str]],
    subject_lookup: dict[str, str],
    course_code: str,
) -> list[str]:
    """
    Convert (subject_description, course_number) pairs into course_code
    strings (e.g. ("Computer Science", "280") -> "CS280") using the
    subject lookup. A description not found in the lookup is logged and
    skipped, not a fatal error — one bad prerequisite entry shouldn't
    block the rest of a course's prerequisites from being stored.
    """
    codes: list[str] = []
    for subject, course_number in pairs:
        code = subject_lookup.get(subject)
        if code is None:
            logger.warning(
                "Course %s: prerequisite subject '%s' not found in subject lookup, skipping",
                course_code, subject,
            )
            continue
        codes.append(f"{code}{course_number}")
    return codes


import json


async def fetch_subject_lookup(page, banner_base: str, term: str) -> dict[str, str]:
    """
    Fetch Banner's full subject code/description list and build the
    {description: code} lookup. Call once per scrape run, before the
    per-subject pagination loop — this is a fixed ~82-entry list, not
    something that needs re-fetching per subject or per course.
    """
    response = await page.request.get(
        f"{banner_base}/classSearch/get_subject?searchTerm=&term={term}&offset=1&max=100",
    )
    entries = json.loads(await response.text())
    return build_subject_lookup(entries)


async def fetch_prerequisites(
    page, banner_base: str, term: str, crn: str,
) -> list[tuple[str, str]]:
    """
    Fetch and parse one course's prerequisite table via its CRN. All
    sections of the same course return identical data (confirmed against
    6 real CS288 sections during design) — call once per unique
    course_code per scrape run, not once per CRN.
    """
    response = await page.request.post(
        f"{banner_base}/searchResults/getSectionPrerequisites",
        form={"term": term, "courseReferenceNumber": crn},
    )
    html = await response.text()
    return parse_prerequisite_table(html)
