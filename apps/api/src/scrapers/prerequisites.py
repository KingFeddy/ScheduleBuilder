"""Extract versioned prerequisite rules and retain their original Banner evidence.

Only explicit grouping is used for mixed AND/OR expressions. Missing concurrency
information stays unspecified; unsupported conditions remain in the expression.
"""
from __future__ import annotations

from datetime import datetime, timezone
import base64
import hashlib
import html
from html.parser import HTMLParser
import json
import re

from bs4 import BeautifulSoup, Comment, Tag

from src.schemas.prerequisites import (
    AllConditions, AnyConditions, CourseCondition, ObservationScope,
    PrerequisiteRefresh, PrerequisiteRules, Rule, SourceEvidence, SubjectLookup,
    UnresolvedCondition, has_unresolved, is_empty,
)


class PrerequisiteRequestError(RuntimeError):
    """The upstream request failed; retain prior verified data and this attempt's evidence."""

    def __init__(self, message: str, source: SourceEvidence | None = None):
        super().__init__(message)
        self.source = source


class PrerequisiteDataError(PrerequisiteRequestError):
    """The source cannot be fully interpreted, so it must remain unresolved."""


_HEADERS = ["And/Or", "", "Test", "Score", "Subject", "Course Number", "Level", "Grade", ""]
_COREQ_HEADERS = ["Subject", "Course Number", "Title"]
_SECTION_COREQ_HEADERS = ["CRN", "Subject", "Course Number", "Title", "Section"]
_SUBJECT_LIMIT = 100
MAX_SOURCE_BYTES = 256 * 1024
MAX_RULE_ROWS = 200
MAX_RULE_DEPTH = 32
_GRADES = {"A+", "A", "A-", "B+", "B", "B-", "C+", "C", "C-", "D+", "D", "D-", "F", "P", "S", "CR"}


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
    children = []
    for child in element.children:
        if isinstance(child, Tag):
            children.append(child)
        elif not isinstance(child, Comment) and str(child).strip():
            raise PrerequisiteDataError("Unrecognized text in prerequisite HTML.")
    return children


def _table_data(body: str, *, corequisites: bool = False) -> tuple[list[str], list[list[str]]] | None:
    if len(body.encode("utf-8")) > MAX_SOURCE_BYTES:
        raise PrerequisiteDataError("Prerequisite source exceeds the supported size.")
    markup = _CompleteMarkup()
    markup.feed(body)
    markup.close()
    if markup.stack:
        raise PrerequisiteDataError("Truncated prerequisite HTML.")
    soup = BeautifulSoup(body, "html.parser")
    if soup.find(["script", "style", "form", "input", "select", "iframe"]):
        raise PrerequisiteDataError("Dynamic or interactive prerequisite HTML is unresolved.")
    roots = _children(soup)
    heading = "Corequisites" if corequisites else "Catalog Prerequisites"
    section_id = "coReqs" if corequisites else "preReqs"
    section = len(roots) == 1 and roots[0].name == "section" and roots[0].get("aria-labelledby") == section_id
    if section:
        roots = _children(roots[0])
    if roots and roots[0].name == "h3" and _normalize(roots[0].get_text(" ", strip=True)) == heading:
        roots = roots[1:]
        if section and not roots:
            return None  # The recognized, explicitly closed empty section.
    if len(roots) != 1 or roots[0].name != "table":
        raise PrerequisiteDataError("Unrecognized prerequisite HTML envelope.")
    root = roots[0]
    if not corequisites and "basePreqTable" not in root.get("class", []):
        raise PrerequisiteDataError("Unrecognized prerequisite table.")
    parts = _children(root)
    if [part.name for part in parts] != ["thead", "tbody"]:
        raise PrerequisiteDataError("Missing or unrecognized prerequisite table structure.")
    header_rows = _children(parts[0])
    if len(header_rows) != 1 or header_rows[0].name != "tr":
        raise PrerequisiteDataError("Unrecognized prerequisite headers.")
    header_cells = _children(header_rows[0])
    if any(cell.name != "th" or cell.has_attr("colspan") or cell.has_attr("rowspan") for cell in header_cells):
        raise PrerequisiteDataError("Unrecognized prerequisite headers.")
    headers = [_normalize(cell.get_text(" ", strip=True)) for cell in header_cells]
    accepted = [_COREQ_HEADERS, _SECTION_COREQ_HEADERS] if corequisites else [_HEADERS, [*_HEADERS, "Concurrency"]]
    if headers not in accepted:
        raise PrerequisiteDataError("Changed prerequisite table columns.")
    rows = _children(parts[1])
    if not rows and not corequisites:
        raise PrerequisiteDataError("Empty prerequisite table does not verify absence.")
    if len(rows) > MAX_RULE_ROWS:
        raise PrerequisiteDataError("Too many prerequisite conditions.")
    values = []
    for row in rows:
        cells = _children(row)
        if row.name != "tr" or [cell.name for cell in cells] != ["td"] * len(headers):
            raise PrerequisiteDataError("Incomplete prerequisite row.")
        if any(cell.has_attr("colspan") or cell.has_attr("rowspan") or cell.find("table") for cell in cells):
            raise PrerequisiteDataError("Unrecognized prerequisite cell structure.")
        values.append([_normalize(cell.get_text(" ", strip=True)) for cell in cells])
    return headers, values


def _course_condition(
    subject: str, number: str, lookup: dict[str, str], source_row: int,
    *, grade: str = "", level: str = "", timing: str = "unspecified", crn: str | None = None,
) -> Rule:
    reason = None
    if not subject or not number:
        reason = "missing_course_identity"
    elif not re.fullmatch(r"[0-9]+[A-Z]?", number):
        reason = "unsupported_course_number"
    elif subject not in lookup:
        reason = "unresolved_subject"
    elif grade and grade not in _GRADES:
        reason = "unsupported_minimum_grade"
    elif timing not in {"prior", "prior_or_concurrent", "concurrent", "unspecified"}:
        reason = "unsupported_concurrency"
    elif crn is not None and not re.fullmatch(r"[0-9]+", crn):
        reason = "unsupported_corequisite_section"
    if reason:
        return UnresolvedCondition(reason=reason, source_row=source_row)
    return CourseCondition(
        course_code=f"{lookup[subject]}{number}", minimum_grade=grade or None,
        level=level or None, timing=timing, required_crn=crn, source_row=source_row,
    )


def _expression(tokens: list[str | Rule]) -> Rule:
    """Parse explicit parentheses without guessing mixed-operator precedence."""
    index = 0

    def group(depth: int) -> Rule:
        nonlocal index
        if depth > MAX_RULE_DEPTH:
            raise PrerequisiteDataError("Prerequisite grouping is too deep.")
        children = []
        operator = None
        while index < len(tokens):
            token = tokens[index]
            index += 1
            if token == "(":
                child = group(depth + 1)
            elif isinstance(token, str):
                raise PrerequisiteDataError("Malformed prerequisite grouping.")
            else:
                child = token
            children.append(child)
            if index == len(tokens):
                if depth:
                    raise PrerequisiteDataError("Unclosed prerequisite group.")
                break
            connector = tokens[index]
            index += 1
            if connector == ")":
                if not depth:
                    raise PrerequisiteDataError("Unexpected closing prerequisite group.")
                break
            if connector not in ("And", "Or"):
                raise PrerequisiteDataError("Missing prerequisite connector.")
            if operator is not None and connector != operator:
                raise PrerequisiteDataError("Mixed AND/OR requires explicit grouping.")
            operator = connector
        else:
            raise PrerequisiteDataError("Incomplete prerequisite expression.")
        if len(children) == 1:
            return children[0]
        return (AllConditions if operator == "And" else AnyConditions)(items=children)

    result = group(0)
    if index != len(tokens):
        raise PrerequisiteDataError("Unexpected trailing prerequisite expression.")
    return result


def parse_prerequisite_rules(body: str, subject_lookup: dict[str, str]) -> Rule:
    data = _table_data(body)
    if data is None:
        return AllConditions(items=[])
    headers, rows = data
    tokens: list[str | Rule] = []
    for index, values in enumerate(rows, start=1):
        connector, opening, test, score, subject, number, level, grade, closing = values[:9]
        if (index == 1 and connector) or (index > 1 and connector not in {"And", "Or"}):
            raise PrerequisiteDataError("Missing or unexpected prerequisite connector.")
        if not re.fullmatch(r"\(*", opening) or not re.fullmatch(r"\)*", closing):
            raise PrerequisiteDataError("Unsupported prerequisite grouping.")
        if connector:
            tokens.append(connector)
        tokens.extend(opening)
        if test or score:
            rule = UnresolvedCondition(reason="unsupported_test_condition", source_row=index)
        else:
            timing = {"Yes": "prior_or_concurrent", "No": "prior", "": "unspecified"}.get(
                values[9] if len(headers) == 10 else "", "unrecognized",
            )
            rule = _course_condition(subject, number, subject_lookup, index, grade=grade, level=level, timing=timing)
        tokens.append(rule)
        tokens.extend(closing)
    return _expression(tokens)


def parse_corequisite_rules(body: str, subject_lookup: dict[str, str]) -> Rule:
    data = _table_data(body, corequisites=True)
    if data is None:
        return AllConditions(items=[])
    headers, rows = data
    if headers == _SECTION_COREQ_HEADERS:
        identities = [(values[1], values[2]) for values in rows]
        if len(identities) != len(set(identities)):
            # Multiple CRNs for the same course do not specify whether these
            # sections are alternatives. Keep the evidence instead of guessing.
            return UnresolvedCondition(reason="ambiguous_corequisite_sections")
    rules = []
    for index, values in enumerate(rows, start=1):
        section_specific = headers == _SECTION_COREQ_HEADERS
        subject, number = values[1:3] if section_specific else values[:2]
        rules.append(_course_condition(
            subject, number, subject_lookup, index, timing="concurrent",
            crn=values[0] if section_specific else None,
        ))
    return rules[0] if len(rules) == 1 else AllConditions(items=rules)


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


async def fetch_rule_source(page, banner_base: str, term: str, crn: str | None, kind: str) -> SourceEvidence:
    if kind == "subjects":
        url = f"{banner_base}/classSearch/get_subject?searchTerm=&term={term}&offset=1&max={_SUBJECT_LIMIT}"
    else:
        endpoint = {"prerequisites": "getSectionPrerequisites", "corequisites": "getCorequisites"}[kind]
        url = f"{banner_base}/searchResults/{endpoint}"
    source = SourceEvidence(
        kind=kind, url=url, term=term, crn=crn, fetched_at=datetime.now(timezone.utc),
        http_status=None, content_type=None, body=None, sha256=None,
    )
    try:
        if kind == "subjects":
            response = await page.request.get(url, timeout=30_000)
        else:
            response = await page.request.post(url, form={"term": term, "courseReferenceNumber": crn}, timeout=30_000)
        source.http_status = response.status
        source.content_type = response.headers.get("content-type", "")
        body = await response.text()
    except Exception as exc:
        source.fetched_at = datetime.now(timezone.utc)
        raise PrerequisiteRequestError(f"{kind.capitalize()} request failed.", source) from exc
    source.fetched_at = datetime.now(timezone.utc)
    encoded = body.encode("utf-8")
    source.sha256 = hashlib.sha256(encoded).hexdigest()
    source.truncated = len(encoded) > MAX_SOURCE_BYTES
    source.body = encoded[:MAX_SOURCE_BYTES].decode("utf-8", errors="ignore") if source.truncated else body
    if "\x00" in source.body:
        source.body_encoding = "base64"
        source.body = base64.b64encode(source.body.encode("utf-8")).decode("ascii")
    if source.http_status != 200:
        raise PrerequisiteRequestError(f"{kind.capitalize()} lookup returned HTTP {source.http_status}.", source)
    content_type = source.content_type.split(";", 1)[0].strip().lower()
    accepted = {"application/json"} if kind == "subjects" else {"text/html", "application/xhtml+xml"}
    if content_type not in accepted:
        raise PrerequisiteDataError(f"Unexpected {kind} content type.", source)
    if source.truncated:
        raise PrerequisiteDataError(f"{kind.capitalize()} source exceeds the supported size.", source)
    return source


async def fetch_subject_lookup(page, banner_base: str, term: str) -> SubjectLookup:
    source = await fetch_rule_source(page, banner_base, term, None, "subjects")
    try:
        entries = json.loads(source.text)
        lookup = build_subject_lookup(entries)
        if len(entries) >= _SUBJECT_LIMIT:
            raise PrerequisiteDataError("Subject lookup may be truncated at its page limit.")
    except (ValueError, PrerequisiteDataError) as exc:
        message = str(exc) if isinstance(exc, PrerequisiteDataError) else "Invalid subject lookup JSON."
        raise PrerequisiteDataError(message, source) from exc
    return SubjectLookup(mapping=lookup, source=source)


async def fetch_prerequisites(
    page, banner_base: str, term: str, crn: str, subject_lookup: SubjectLookup,
) -> PrerequisiteRefresh:
    sources = [subject_lookup.source]
    rules = {}
    errors = []
    status = "verified"
    for kind, parser in (("prerequisites", parse_prerequisite_rules), ("corequisites", parse_corequisite_rules)):
        source = None
        try:
            source = await fetch_rule_source(page, banner_base, term, crn, kind)
            rule = parser(source.text, subject_lookup.mapping)
            if has_unresolved(rule):
                if status != "failed":
                    status = "unresolved"
                errors.append(f"Unresolved {kind} conditions.")
        except PrerequisiteDataError as exc:
            source = source or exc.source
            if status != "failed":
                status = "unresolved"
            errors.append(str(exc))
            rule = UnresolvedCondition(reason="unrecognized_source")
        except PrerequisiteRequestError as exc:
            source = exc.source
            status = "failed"
            errors.append(str(exc))
            rule = UnresolvedCondition(reason="source_request_failed")
        if source is not None:
            sources.append(source)
        rules[kind] = rule
    if status == "verified" and all(is_empty(rule) for rule in rules.values()):
        status = "verified_empty"
    return PrerequisiteRefresh(
        rules=PrerequisiteRules(scope=ObservationScope(term=term, crn=crn), **rules),
        sources=sources, status=status, error=" ".join(errors) or None,
    )
