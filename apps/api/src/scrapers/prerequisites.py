"""Validate Banner responses before replacing stored prerequisite course codes.

Verification covers complete course-code extraction, not student eligibility.
OR/groups/tests remain unresolved until structured rules are implemented.
Minimum grades in supported course rows are not enforced by the legacy array.
"""
from __future__ import annotations

import html
from html.parser import HTMLParser
import json
import re

from bs4 import BeautifulSoup, Comment, Tag


class PrerequisiteRequestError(RuntimeError):
    """The upstream request failed; keep the previous prerequisite data."""


class PrerequisiteDataError(RuntimeError):
    """The response cannot be completely interpreted; keep the previous data."""


_HEADERS = ["And/Or", "", "Test", "Score", "Subject", "Course Number", "Level", "Grade", ""]
_SUBJECT_LIMIT = 100


def _normalize(value: str) -> str:
    return " ".join(value.split())


class _CompleteMarkup(HTMLParser):
    """Do not let BeautifulSoup repair a truncated fragment into success."""

    required_closures = {"section", "h3", "table", "thead", "tbody", "tr", "th", "td"}

    def __init__(self):
        super().__init__()
        self.stack: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self.required_closures:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in self.required_closures:
            if not self.stack or self.stack.pop() != tag:
                raise PrerequisiteDataError("Malformed prerequisite HTML.")


def _children(element) -> list[Tag]:
    """Whitespace/comments are harmless; stray prose may contain an unparsed rule."""
    children = []
    for child in element.children:
        if isinstance(child, Tag):
            children.append(child)
        elif not isinstance(child, Comment) and str(child).strip():
            raise PrerequisiteDataError("Unrecognized text in prerequisite HTML.")
    return children


def parse_prerequisite_table(body: str) -> list[tuple[str, str]]:
    """Return complete subject/number pairs, or raise instead of returning partial data.

    Only the complete, recognized Catalog Prerequisites section containing its
    heading alone proves no prerequisites. Missing tables elsewhere, empty table
    bodies, errors, changed columns, and unsupported rows do not prove absence.
    """
    markup = _CompleteMarkup()
    markup.feed(body)
    markup.close()
    if markup.stack:
        raise PrerequisiteDataError("Truncated prerequisite HTML.")

    soup = BeautifulSoup(body, "html.parser")
    if soup.find(["script", "style", "form", "input", "select", "iframe"]):
        raise PrerequisiteDataError("Dynamic or interactive prerequisite HTML is unresolved.")
    roots = _children(soup)
    if len(roots) != 1:
        raise PrerequisiteDataError("Unrecognized prerequisite HTML envelope.")
    root = roots[0]
    if root.name == "section" and root.get("aria-labelledby") == "preReqs":
        children = _children(root)
        if (
            not children or children[0].name != "h3"
            or _normalize(children[0].get_text(" ", strip=True)) != "Catalog Prerequisites"
        ):
            raise PrerequisiteDataError("Unrecognized prerequisite heading.")
        if len(children) == 1:
            return []
        if len(children) != 2:
            raise PrerequisiteDataError("Unrecognized content in prerequisite section.")
        root = children[1]

    if root.name != "table" or "basePreqTable" not in root.get("class", []):
        raise PrerequisiteDataError("Unrecognized prerequisite table.")
    parts = _children(root)
    if [part.name for part in parts] != ["thead", "tbody"]:
        raise PrerequisiteDataError("Missing or unrecognized prerequisite table structure.")
    header_rows = _children(parts[0])
    if len(header_rows) != 1 or header_rows[0].name != "tr":
        raise PrerequisiteDataError("Unrecognized prerequisite headers.")
    headers = _children(header_rows[0])
    if [cell.name for cell in headers] != ["th"] * len(_HEADERS) or [
        _normalize(cell.get_text(" ", strip=True)) for cell in headers
    ] != _HEADERS:
        raise PrerequisiteDataError("Changed prerequisite table columns.")

    rows = _children(parts[1])
    if not rows:
        raise PrerequisiteDataError("Empty prerequisite table does not verify absence.")
    pairs = []
    for index, row in enumerate(rows):
        cells = _children(row)
        if row.name != "tr" or [cell.name for cell in cells] != ["td"] * len(_HEADERS):
            raise PrerequisiteDataError("Incomplete prerequisite row.")
        if any(
            cell.has_attr("colspan") or cell.has_attr("rowspan") or cell.find("table")
            for cell in cells
        ):
            raise PrerequisiteDataError("Unrecognized prerequisite cell structure.")
        values = [_normalize(cell.get_text(" ", strip=True)) for cell in cells]
        connector, opening, test, score, subject, number, _level, _grade, closing = values
        if (
            connector not in ("", "And") or (index > 0 and connector != "And")
            or any((opening, test, score, closing))
        ):
            raise PrerequisiteDataError("Unsupported prerequisite condition; complete rules required.")
        if not subject or not re.fullmatch(r"[0-9]+[A-Z]?", number):
            raise PrerequisiteDataError("Missing or unsupported prerequisite subject/course number.")
        pairs.append((subject, number))
    return pairs


def build_subject_lookup(subject_entries: list[dict]) -> dict[str, str]:
    """Build an unambiguous description-to-code map, never a partially valid list."""
    if not isinstance(subject_entries, list) or not subject_entries:
        raise PrerequisiteDataError("Subject lookup must be a nonempty list.")
    lookup: dict[str, str] = {}
    codes: set[str] = set()
    for entry in subject_entries:
        if not isinstance(entry, dict):
            raise PrerequisiteDataError("Invalid subject lookup entry.")
        code, description = entry.get("code"), entry.get("description")
        if (
            not isinstance(code, str) or not re.fullmatch(r"[A-Z][A-Z0-9]*", code)
            or not isinstance(description, str)
        ):
            raise PrerequisiteDataError("Invalid subject lookup code/description.")
        description = _normalize(html.unescape(description))
        if not description or description in lookup or code in codes:
            raise PrerequisiteDataError("Empty or ambiguous subject lookup description/code.")
        lookup[description] = code
        codes.add(code)
    return lookup


def resolve_prerequisite_codes(
    pairs: list[tuple[str, str]], subject_lookup: dict[str, str], course_code: str,
) -> list[str]:
    """Resolve every pair or reject the entire replacement, retaining the known array."""
    codes = []
    for subject, course_number in pairs:
        code = subject_lookup.get(subject)
        if code is None:
            raise PrerequisiteDataError(f"Unresolved prerequisite subject for {course_code}.")
        codes.append(f"{code}{course_number}")
    return codes


async def fetch_subject_lookup(page, banner_base: str, term: str) -> dict[str, str]:
    """Fetch a validated subject lookup once per subject scrape.

    A full page may be capped, so it cannot prove completeness. Refuse it until
    pagination is supported; an incomplete lookup must never remove dependencies.
    """
    response = await page.request.get(
        f"{banner_base}/classSearch/get_subject?searchTerm=&term={term}&offset=1&max={_SUBJECT_LIMIT}",
        timeout=30_000,
    )
    if response.status != 200:
        raise PrerequisiteRequestError(f"Subject lookup returned HTTP {response.status}.")
    if response.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise PrerequisiteDataError("Subject lookup did not return JSON.")
    try:
        entries = json.loads(await response.text())
    except ValueError as exc:
        raise PrerequisiteDataError("Invalid subject lookup JSON.") from exc
    lookup = build_subject_lookup(entries)
    if len(entries) >= _SUBJECT_LIMIT:
        raise PrerequisiteDataError("Subject lookup may be truncated at its page limit.")
    return lookup


async def fetch_prerequisites(page, banner_base: str, term: str, crn: str) -> list[tuple[str, str]]:
    """Fetch one course's prerequisite evidence using a representative section CRN."""
    response = await page.request.post(
        f"{banner_base}/searchResults/getSectionPrerequisites",
        form={"term": term, "courseReferenceNumber": crn}, timeout=30_000,
    )
    if response.status != 200:
        raise PrerequisiteRequestError(f"Prerequisite lookup returned HTTP {response.status}.")
    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type not in {"text/html", "application/xhtml+xml"}:
        raise PrerequisiteDataError("Prerequisite lookup did not return HTML.")
    return parse_prerequisite_table(await response.text())
