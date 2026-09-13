from __future__ import annotations

import re
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

COURSE_CODE_PATTERN = re.compile(r"^[A-Z]{2,5}\d{3}[A-Z]?$")
WILDCARD_PATTERN = re.compile(r"[Xx@*]")


class StillNeededItem(BaseModel):
    model_config = ConfigDict(extra="ignore", json_schema_serialization_defaults_required=True)

    requirement: str
    options: list[str] = Field(default_factory=list)

    @field_validator("options")
    @classmethod
    def normalize_options(cls, v: list[str]) -> list[str]:
        return [code.strip().upper().replace(" ", "") for code in v]


class ParsedDegree(BaseModel):
    model_config = ConfigDict(extra="ignore")

    student_name: Optional[str] = None
    majors: list[str] = Field(default_factory=list)
    minors: list[str] = Field(default_factory=list)
    catalog_year: Optional[int] = None
    credits_completed: Optional[int] = None
    credits_required: Optional[int] = None
    credits_remaining: Optional[int] = None
    completed_courses: list[str] = Field(default_factory=list)
    in_progress_courses: list[str] = Field(default_factory=list)
    still_needed: list[StillNeededItem] = Field(default_factory=list)
    # semesters_remaining deliberately absent — computed by the planner from
    # credits_remaining and the student's chosen credits_per_semester

    @field_validator("completed_courses", "in_progress_courses", mode="before")
    @classmethod
    def normalize_course_codes(cls, v: list[str]) -> list[str]:
        return [c.strip().upper().replace(" ", "") for c in v if c]

    @field_validator("credits_completed", "credits_required", "credits_remaining")
    @classmethod
    def non_negative(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and v < 0:
            raise ValueError(f"Credit count cannot be negative: {v}")
        return v


class ParsedDegreeValidated(ParsedDegree):
    """Produced only by validate_parsed_degree(). Never instantiate directly.
    Nothing downstream should accept a raw ParsedDegree."""
    # Responses always serialize defaults, including explicit null metadata.
    # Input validation still accepts omitted optional fields.
    model_config = ConfigDict(extra="ignore", json_schema_serialization_defaults_required=True)


class GerCourse(BaseModel):
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)
    code: str
    title: str | None
    title_status: Literal["verified", "unverified", "missing"] = "unverified"


class GerGroup(BaseModel):
    prefix: str
    courses: list[GerCourse]


class GerCoursesResponse(BaseModel):
    groups: list[GerGroup]


class ParseValidationError(Exception):
    def __init__(self, field: str, message: str) -> None:
        self.field = field
        self.message = message
        super().__init__(f"{field}: {message}")
