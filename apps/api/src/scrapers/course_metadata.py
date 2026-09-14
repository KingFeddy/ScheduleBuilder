"""Refresh course metadata from complete, consistent Banner section observations."""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import json
import math
import re

from sqlalchemy import text


_CREDIT_FIELDS = ("creditHours", "creditHourLow", "creditHourHigh", "creditHourIndicator")


def _number(raw):
    if raw is None or raw == "":
        return None
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
        raise ValueError("Invalid credit value.")
    if isinstance(raw, str) and not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", raw.strip()):
        raise ValueError("Invalid credit value.")
    value = float(raw)
    if not math.isfinite(value) or not 0 <= value <= 100 or round(value, 2) != value:
        raise ValueError("Unsupported credit value.")
    return value


def parse_credits(raw: dict) -> tuple[dict | None, str | None]:
    """Keep ranges and discrete choices distinct, including section-selected hours."""
    try:
        hours, low, high = (_number(raw.get(key)) for key in _CREDIT_FIELDS[:3])
        indicator = raw.get("creditHourIndicator")
        if indicator is not None and not isinstance(indicator, str):
            raise ValueError("Unrecognized credit indicator.")
        indicator = (indicator or "").strip().upper()
        if indicator not in {"", "OR", "TO"}:
            raise ValueError("Unrecognized credit indicator.")
        if hours is None and low is None and high is None:
            return None, "Credits are missing from Banner."
        if high is not None and low is None:
            raise ValueError("Incomplete credit bounds.")
        if low is None:
            if indicator:
                raise ValueError("Incomplete credit bounds.")
            return {"kind": "fixed", "minimum": hours, "maximum": hours}, None
        if high is None or low == high:
            if (high is None and indicator) or (hours is not None and hours != low):
                raise ValueError("Inconsistent fixed credit values.")
            return {"kind": "fixed", "minimum": low, "maximum": low}, None
        if high < low or not indicator:
            raise ValueError("Unresolved variable credit bounds.")
        if hours is not None and (
            not low <= hours <= high or (indicator == "OR" and hours not in {low, high})
        ):
            raise ValueError("Selected credits conflict with the catalog bounds.")
        return {"kind": "options" if indicator == "OR" else "range", "minimum": low, "maximum": high}, None
    except (ValueError, OverflowError):
        return None, "Credit fields are invalid or inconsistent."


def parse_title(raw, course_code: str, clean_title) -> tuple[str | None, str | None]:
    if raw is None or raw == "":
        return None, "Title is missing from Banner."
    if (not isinstance(raw, str) or len(raw) > 512 or "\x00" in raw
            or any(0xD800 <= ord(character) <= 0xDFFF for character in raw)):
        return None, "Title is invalid."
    title = " ".join(clean_title(raw).split())
    if not title or title.upper().replace(" ", "") in {course_code.upper(), "TBD", "UNKNOWN", "N/A"}:
        return None, "Title is missing from Banner."
    return title, None


def _evidence_value(value):
    """Keep selected catalog fields bounded and JSONB-compatible on bad input."""
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else {"invalid_number": str(value)}
    if isinstance(value, str):
        try:
            encoded = value.encode("utf-8")
        except UnicodeError:
            return {"invalid_unicode": value.encode("utf-8", errors="backslashreplace").decode()[:2048]}
        if len(encoded) > 2048 or "\x00" in value:
            return {"base64_prefix": base64.b64encode(encoded[:2048]).decode(),
                    "sha256": hashlib.sha256(encoded).hexdigest(), "truncated": len(encoded) > 2048}
        return value
    return {"unsupported_type": type(value).__name__}


def metadata_observation(raw: dict) -> dict:
    return {key: raw.get(key) for key in ("courseReferenceNumber", "courseTitle", *_CREDIT_FIELDS)}


def _consistent(values, label):
    if any(error for _, error in values):
        return None, f"{label} is missing or invalid in one or more sections."
    first = values[0][0]
    if any(value != first for value, _ in values):
        return None, f"Conflicting section {label.lower()}."
    return first, None


async def refresh_course_metadata(session, course_code: str, term: str, observations: list[dict], clean_title, *, source_url: str) -> None:
    """Caller only invokes this after complete pagination and successful writes.

    Title and credit validation are independent; each valid replacement carries
    its own evidence. Both replacements and latest-attempt metadata commit together.
    """
    title, title_error = _consistent([
        parse_title(row.get("courseTitle"), course_code, clean_title) for row in observations
    ], "Title")
    credits, credits_error = _consistent([parse_credits(row) for row in observations], "Credits")
    evidence = [{"crn": row["courseReferenceNumber"], **{
        key: _evidence_value(row.get(key)) for key in ("courseTitle", *_CREDIT_FIELDS)
    }} for row in observations]
    scope = {"schema_version": 1, "term": term, "url": source_url, "observed_at": datetime.now(timezone.utc).isoformat(),
             "observations": evidence}
    attempt = {**scope, "title_error": title_error, "credits_error": credits_error}
    title_source = {**scope, "value": title} if title is not None else None
    credits_source = {**scope, "value": credits} if credits is not None else None
    try:
        async with session.begin():
            await session.execute(text("""
                UPDATE courses SET
                    title = CASE WHEN :has_title THEN :title ELSE title END,
                    title_source = COALESCE(CAST(:title_source AS jsonb), title_source),
                    credits = CASE WHEN :has_credits THEN CAST(:credits AS numeric) ELSE credits END,
                    credits_source = COALESCE(CAST(:credits_source AS jsonb), credits_source),
                    metadata_latest_attempt = CAST(:attempt AS jsonb)
                WHERE course_code = :code
            """), {
                "code": course_code, "has_title": title is not None, "title": title,
                "has_credits": credits is not None,
                "credits": str(credits["minimum"]) if credits and credits["kind"] == "fixed" else None,
                "title_source": json.dumps(title_source) if title_source else None,
                "credits_source": json.dumps(credits_source) if credits_source else None,
                "attempt": json.dumps(attempt),
            })
    except Exception:
        # Keep the previous sources/values and expose the rejected refresh.
        # Cancellation is a BaseException and propagates without a false outcome.
        attempt["save_error"] = "Could not save course metadata."
        async with session.begin():
            await session.execute(text("UPDATE courses SET metadata_latest_attempt=CAST(:attempt AS jsonb) WHERE course_code=:code"),
                                  {"attempt": json.dumps(attempt), "code": course_code})
