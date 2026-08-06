from __future__ import annotations
import re
from pydantic import BaseModel, Field, field_validator

from ..scheduler.config import MAX_COURSES


class CommuterOptions(BaseModel):
    blocked_days:   list[str] = Field(default_factory=list)
    earliest_start: str | None = None
    latest_end:     str | None = None
    minimize_gaps:  bool = True

    @field_validator("blocked_days")
    @classmethod
    def validate_blocked_days(cls, v: list[str]) -> list[str]:
        valid = set("MTWRF")
        for d in v:
            if d not in valid:
                raise ValueError(f"Invalid day '{d}'. Must be one of M, T, W, R, F.")
        return list(set(v))

    @field_validator("earliest_start", "latest_end", mode="before")
    @classmethod
    def validate_time_string(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        if not re.match(r"^\d{2}:\d{2}$", v):
            raise ValueError(f"Time must be HH:MM format, got '{v}'")
        h, m = map(int, v.split(":"))
        if not (0 <= h <= 23 and m in (0, 30)):
            raise ValueError(
                f"Hours must be 0-23 and minutes must be 0 or 30, got '{v}'"
            )
        return v


class SolveRequest(BaseModel):
    course_codes:          list[str] = Field(min_length=1, max_length=MAX_COURSES)
    term:                  str
    options:               CommuterOptions = Field(default_factory=CommuterOptions)
    professor_preferences: dict[str, list[str]] = Field(default_factory=dict)
    compact_week:          bool = False

    @field_validator("course_codes")
    @classmethod
    def validate_no_duplicates(cls, v: list[str]) -> list[str]:
        seen: set[str] = set()
        deduped: list[str] = []
        for code in v:
            code = code.strip().upper()
            if code not in seen:
                seen.add(code)
                deduped.append(code)
        return deduped

    @field_validator("term")
    @classmethod
    def validate_term(cls, v: str) -> str:
        if not re.match(r"^\d{4}(10|50|90)$", v):
            raise ValueError(
                f"Invalid term format '{v}'. Expected 6-digit NJIT term, e.g. 202690"
            )
        return v


class MeetingResponse(BaseModel):
    days:       str | None
    start_time: str | None
    end_time:   str | None
    location:   str | None


class SectionResponse(BaseModel):
    crn:            str
    course_code:    str
    professor_name: str | None
    total_seats:    int
    open_seats:     int
    scraped_at:     str | None
    meetings:       list[MeetingResponse]


class SolveSectionResponse(BaseModel):
    crn:            str
    term:           str
    course_code:    str
    professor_name: str | None
    total_seats:    int
    open_seats:     int
    scraped_at:     str | None
    meetings:       list[MeetingResponse]
    section_number: str | None


class ScheduleResult(BaseModel):
    sections:           list[SolveSectionResponse]
    gap_minutes:        int
    campus_days:        int
    has_async_sections: bool


class SolveResponse(BaseModel):
    results:   list[ScheduleResult]
    warnings:  list[str]
    truncated: bool
