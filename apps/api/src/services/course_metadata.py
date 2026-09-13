"""One public interpretation of retained course metadata and planning estimates."""
from __future__ import annotations

import json

from src.schemas.courses import CourseResponse


def _object(value):
    return json.loads(value) if isinstance(value, str) else (value or {})


def title_status(row) -> str:
    if not row.get("title"):
        return "missing"
    return "verified" if _object(row.get("title_source")) else "unverified"


def course_response(row) -> CourseResponse:
    source = _object(row.get("credits_source"))
    value = source.get("value", {})
    kind = value.get("kind")
    attempt = _object(row.get("metadata_latest_attempt"))
    return CourseResponse(
        course_code=row["course_code"], title=row["title"], credits=row["credits"],
        title_status=title_status(row),
        credits_status=("fixed" if kind == "fixed" else "variable") if kind else (
            "unverified" if row["credits"] is not None else "missing"
        ),
        credits_min=value.get("minimum"), credits_max=value.get("maximum"),
        credits_options=[value["minimum"], value["maximum"]] if kind == "options" else [],
        metadata_warnings=([f"{field.capitalize()} refresh: {attempt[field + '_error']}"
                            for field in ("title", "credits") if attempt.get(field + "_error")]
                           + ([attempt["save_error"]] if attempt.get("save_error") else [])),
    )


def planning_credits(course: CourseResponse) -> tuple[float, bool, str]:
    if course.credits_status == "fixed" and course.credits is not None:
        return course.credits, False, ""
    if course.credits_status == "variable" and course.credits_max is not None:
        separator = " or " if course.credits_options else "–"
        bounds = f"{course.credits_min:g}{separator}{course.credits_max:g}"
        return course.credits_max, True, f"Variable credits ({bounds}); using {course.credits_max:g} as an estimate. Confirm the selected credits."
    if course.credits is not None:
        return course.credits, True, f"Catalog credits are unverified; using {course.credits:g} as an estimate."
    return 3, True, "Credits unknown; using 3 credits as an estimate."
