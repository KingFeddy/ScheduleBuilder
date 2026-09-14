from __future__ import annotations

import hashlib
import io
import logging
import math
import re
from decimal import Decimal

import pdfplumber

from src.schemas.plan import CourseAttempt, CourseAttemptSource, ParsedDegree, RequirementSource, StillNeededItem, stable_identity

logger = logging.getLogger(__name__)

# ── Compiled regex patterns ───────────────────────────────────────────────────

# Standard NJIT course code with explicit dept prefix: "CS 280" or "CS280"
_COURSE_CODE_RE = re.compile(r"\b([A-Z]{2,5})\s{0,2}(\d{3}[A-Z]?)\b")

# Matches either a dept+number pair OR a bare number after "or".
# Used in _extract_course_codes to handle "COM 303 or 310 or 312" → COM303,
# COM310, COM312. The spec's _COURSE_CODE_RE alone misses the bare numbers;
# this version carries the last-seen department forward across an "or" list.
# Groups: (dept, num_with_dept, bare_num_after_or)
_CODE_PART_RE = re.compile(
    r"\b(?:([A-Z]{2,5})\s{0,2}(\d{3}[A-Z]?)|or\s+(\d{3}[A-Z]?))\b"
)

# Wildcard course: "PHYS 3@" → ("PHYS", "3"), bare "4@" → ("", "4").
# Dept prefix is optional — DegreeWorks writes "PHYS 3@ or 4@" where the
# second wildcard inherits PHYS from the first.
_WILDCARD_CODE_RE = re.compile(r"(?:([A-Z]{2,5})\s{0,2})?(\d)@")

# Rutgers cross-listed codes to exclude. In practice [A-Z]{2,5} already
# rejects alphanumeric prefixes like R510/R512, but kept for explicitness.
_RUTGERS_DEPTS = {"R510", "R512"}

# Preserve every non-reference block, including an unreadable amount. A failed
# amount extraction must not silently remove an unfulfilled requirement.
_STILL_NEEDED_RE = re.compile(
    r"Still needed:\s*(?P<body>.*?)(?=Still needed:|$)",
    re.DOTALL,
)
_REQUIREMENT_AMOUNT_RE = re.compile(
    r"^(?:(?P<amount>.*?)\s+)?(?P<unit>Class(?:es)?|Credits?)\s+in\s+(?P<options>.*)$",
    re.DOTALL | re.IGNORECASE,
)

# Credits summary
_CREDITS_REQUIRED_RE = re.compile(r"Credits required:\s*(\d+)")
_CREDITS_APPLIED_RE = re.compile(r"Credits applied:\s*(\d+)")

# Prose fallback: "you still need 21 more credits"
_CREDITS_PROSE_RE = re.compile(r"you still need\s+(\d+)\s+more credits", re.IGNORECASE)

# Catalog year: "Catalog year: 2025-2026" → 2025
_CATALOG_YEAR_RE = re.compile(r"Catalog year:\s*(\d{4})-\d{4}")

# Student name: "Student name LastName, FirstName Middle"
_STUDENT_NAME_RE = re.compile(r"Student name\s+(.+?)(?:\n|Student ID)")

# Majors/minors header lines
_MAJORS_RE = re.compile(
    r"\bMajors?\s+(.+?)(?=\n|Minor|Program|College|Academic)", re.DOTALL
)
_MINOR_RE = re.compile(
    r"\bMinors?\s+(.+?)(?=\n|Program|College|Academic)", re.DOTALL
)

# Match the full grade token, including unknown grades, rather than a passing
# suffix inside e.g. WF/ABC. Only standalone course rows are attempt evidence.
_ATTEMPT_ROW_RE = re.compile(
    r"^[ \t]*(?P<dept>[A-Z]{2,5})[ \t]*(?P<number>[0-9]{3}[A-Z]?)[ \t]+"
    r"(?:.*?[ \t]+)?(?P<grade>[^\s()]+)[ \t]+"
    r"(?P<credits>[0-9]+(?:\.[0-9]+)?|\([0-9]+(?:\.[0-9]+)?\))"
    r"(?:[ \t]+(?P<term>[0-9]{4}[ \t]+[A-Za-z]+|[A-Za-z]+[ \t]+[0-9]{4}))?[ \t]*$"
)
_ATTEMPT_CODE_RE = re.compile(r"\b[A-Z]{2,5}[ \t]*[0-9]{3}[A-Z]?\b")
_ATTEMPT_COLUMN_RE = re.compile(r"[ \t]{2,}(?=[A-Z]{2,5}[ \t]*[0-9]{3}[A-Z]?\b)")


# ── Helper functions ──────────────────────────────────────────────────────────

def _extract_course_attempts(text: str, *, document_id: str | None = None) -> list[CourseAttempt]:
    attempts = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        # layout=True separates columns with whitespace. Never let the title
        # wildcard consume a second course and borrow its grade. Ambiguous
        # merged rows without a column boundary remain unclassified.
        for column in _ATTEMPT_COLUMN_RE.split(line):
            if len(_ATTEMPT_CODE_RE.findall(column)) != 1:
                continue
            match = _ATTEMPT_ROW_RE.fullmatch(column)
            if match is None:
                continue
            credits = float(match["credits"].strip("()"))
            # An unrepresentable amount is unknown, never earned credit.
            if not math.isfinite(credits) or (credits == 0 and Decimal(match["credits"].strip("()")) != 0):
                credits = None
            attempts.append(CourseAttempt(
                course_code=match["dept"] + match["number"], grade=match["grade"],
                credits=credits, term=match["term"],
                source=CourseAttemptSource(document_id=document_id, line=line_number, text=line),
            ))
    return attempts


def _extract_course_codes(text: str) -> list[str]:
    """
    Extract all NJIT course codes from a text block.

    Two passes:
      1. Wildcard codes: "PHYS 3@" → "PHYS3XX", bare "4@" → "PHYS4XX"
         (@ expands to TWO X's — one per remaining digit of a 3-digit number).
         PHYS3XX is required by Subsystem 6's matches_wildcard: each X matches
         exactly one digit, so PHYS3XX → ^PHYS3\\d\\d$ correctly matches PHYS310.
         A single X would only match 2-digit numbers and silently break matching.

      2. Standard codes with department inheritance: "COM 303 or 310 or 312"
         → COM303, COM310, COM312. Bare numbers after 'or' inherit the most
         recently seen department. This is necessary for DegreeWorks option lists
         which only repeat the dept prefix for the first code in a run.

    Excludes Rutgers cross-listed codes (R510, R512). Deduplicates, order preserved.
    """
    seen: set[str] = set()
    result: list[str] = []

    # Pass 1 — wildcard codes
    last_dept: str | None = None
    for dept, lead_digit in _WILDCARD_CODE_RE.findall(text):
        if dept:
            last_dept = dept
        if not last_dept or last_dept in _RUTGERS_DEPTS:
            continue
        code = f"{last_dept}{lead_digit}XX"
        if code not in seen:
            seen.add(code)
            result.append(code)

    # Pass 2 — standard codes with dept inheritance
    last_dept = None
    for m in _CODE_PART_RE.finditer(text):
        dept, num_full, bare_num = m.group(1), m.group(2), m.group(3)
        if dept:
            if dept in _RUTGERS_DEPTS:
                last_dept = None
                continue
            last_dept = dept
            code = f"{dept}{num_full.upper()}"
        else:
            if not last_dept:
                continue
            code = f"{last_dept}{bare_num.upper()}"  # type: ignore[union-attr]
        if code not in seen:
            seen.add(code)
            result.append(code)

    return result


def _infer_requirement_name(pos: int, full_text: str, options: list[str]) -> str:
    """
    Derive the requirement name from the text immediately before a 'Still needed:'
    line. DegreeWorks puts the requirement name on the line above. Falls back to
    a dept-derived name when no clean line is found.
    """
    preceding = full_text[max(0, pos - 300) : pos]
    lines = [line.strip() for line in preceding.split("\n") if line.strip()]

    for candidate in reversed(lines):
        if re.search(
            r"\b(COMPLETE|INCOMPLETE|IN-PROGRESS|Catalog year|Credits)\b", candidate
        ):
            continue
        if re.search(r"\b[A-Z]{2,5}\s+\d{3}\b", candidate):
            continue
        if 3 < len(candidate) < 100:
            return candidate

    dept_match = re.match(r"^([A-Z]{2,5})", options[0]) if options else None
    return f"{dept_match.group(1)} Requirement" if dept_match else "Requirement"


def _extract_still_needed(text: str, *, document_id: str | None = None) -> list[StillNeededItem]:
    """
    Extract all unfulfilled requirements from DegreeWorks text.

    Formats handled:
      Still needed: 1 Class in CS 435
      Still needed: 3 Credits in CS 491 or PHYS 490
      Still needed: 3 Credits in PHYS 3@ or 4@
      Still needed: 1 Class in COM 303 or 310 or 312 ...  (long multi-line list)
      Unknown amounts are retained as unresolved; See-block references are skipped.
    """
    items: list[StillNeededItem] = []

    identity_context = document_id or hashlib.sha256(text.encode()).hexdigest()
    for block_index, match in enumerate(_STILL_NEEDED_RE.finditer(text), start=1):
        body = match.group("body").strip()
        amount_match = _REQUIREMENT_AMOUNT_RE.fullmatch(body)
        quantity = None
        unit = "unknown"
        options_text = body
        if amount_match:
            options_text = amount_match.group("options").strip()
            unit = "classes" if amount_match.group("unit").lower().startswith("class") else "credits"
            raw_amount = (amount_match.group("amount") or "").strip()
            if re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", raw_amount):
                candidate = float(raw_amount)
                # Preserve the written amount; float rounding must not turn a
                # fractional class count into an integer or a tiny amount into 0.
                if (math.isfinite(candidate) and Decimal(str(candidate)) == Decimal(raw_amount)
                        and (unit == "credits" or candidate.is_integer())):
                    quantity = candidate

        # Safety net for malformed extractions like "Still needed: 1 Class in See ..."
        if re.match(r"^See\s+", options_text, re.IGNORECASE):
            continue

        options = _extract_course_codes(options_text)
        requirement = _infer_requirement_name(match.start(), text, options)
        items.append(StillNeededItem(
            requirement_id=stable_identity("req", identity_context, block_index),
            requirement=requirement, options=options,
            remaining_quantity=quantity, quantity_unit=unit,
            source=RequirementSource(document_id=document_id, block_index=block_index,
                                     line=text.count("\n", 0, match.start()) + 1, text=match.group(0).strip()),
        ))

    return items


def _clean_major_minor(raw: str) -> list[str]:
    """
    Parse 'Computer Science (CS), Applied Physics (APPH)' into
    ['Computer Science', 'Applied Physics'].
    """
    result = []
    for entry in raw.split(","):
        clean = re.sub(r"\s*\([A-Z]{2,5}\)", "", entry).strip()
        if 3 < len(clean) < 80:
            result.append(clean)
    return result


# ── Main parser ───────────────────────────────────────────────────────────────

def parse_degree_works_regex(pdf_bytes: bytes) -> ParsedDegree:
    """
    Parse a DegreeWorks PDF using pdfplumber text extraction + regex.
    Returns a ParsedDegree for further validation via validate_parsed_degree().
    Raises ValueError on unrecoverable parse failure (caller maps to 422).
    """
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            pages = []
            for page in pdf.pages:
                # layout=True preserves column separation — DegreeWorks uses a
                # two-column layout on some pages; without it, right-column text
                # interleaves with left-column text and breaks the regex.
                text = page.extract_text(layout=True)
                if text:
                    pages.append(text)
            full_text = "\n".join(pages)
    except Exception as e:
        raise ValueError(f"pdfplumber could not read this PDF: {e}") from e

    if len(full_text.strip()) < 500:
        raise ValueError(
            "Could not extract text from this PDF. "
            "If it is a screenshot or scan, please download the actual DegreeWorks PDF."
        )

    logger.debug("extracted %d characters from DegreeWorks PDF", len(full_text))

    # Credits
    credits_required: int | None = None
    credits_completed: int | None = None
    credits_remaining: int | None = None

    req_match = _CREDITS_REQUIRED_RE.search(full_text)
    app_match = _CREDITS_APPLIED_RE.search(full_text)
    if req_match and app_match:
        credits_required = int(req_match.group(1))
        credits_completed = int(app_match.group(1))
        credits_remaining = credits_required - credits_completed
    else:
        prose_match = _CREDITS_PROSE_RE.search(full_text)
        if prose_match:
            credits_remaining = int(prose_match.group(1))

    # Catalog year
    catalog_year: int | None = None
    cy_match = _CATALOG_YEAR_RE.search(full_text)
    if cy_match:
        catalog_year = int(cy_match.group(1))

    # Student name
    student_name: str | None = None
    name_match = _STUDENT_NAME_RE.search(full_text)
    if name_match:
        student_name = name_match.group(1).strip()

    # Majors
    majors: list[str] = []
    majors_match = _MAJORS_RE.search(full_text)
    if majors_match:
        majors = _clean_major_minor(majors_match.group(1))

    # Minors
    minors: list[str] = []
    minor_match = _MINOR_RE.search(full_text)
    if minor_match:
        minors = _clean_major_minor(minor_match.group(1))

    document_id = hashlib.sha256(pdf_bytes).hexdigest()
    course_attempts = _extract_course_attempts(full_text, document_id=document_id)

    # Still needed requirements
    still_needed = _extract_still_needed(full_text, document_id=document_id)

    logger.info(
        "DegreeWorks parse: majors=%r  credits_remaining=%s  "
        "still_needed=%d items  course_attempts=%d",
        majors,
        credits_remaining,
        len(still_needed),
        len(course_attempts),
    )

    return ParsedDegree(
        student_name=student_name,
        majors=majors,
        minors=minors,
        catalog_year=catalog_year,
        credits_completed=credits_completed,
        credits_required=credits_required,
        credits_remaining=credits_remaining,
        course_attempts=course_attempts,
        still_needed=still_needed,
    )
