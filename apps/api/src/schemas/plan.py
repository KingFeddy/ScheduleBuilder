from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from typing import Annotated, Literal, Optional

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints, computed_field, field_validator, model_validator
from .catalog import CatalogStatus, UNCHECKED_CATALOG_NOTE

COURSE_CODE_PATTERN = re.compile(r"^[A-Z]{2,5}\d{3}[A-Z]?$")
WILDCARD_PATTERN = re.compile(r"[Xx@*]")
MIN_CREDITS_PER_SEMESTER = 3
MAX_CREDITS_PER_SEMESTER = 24
NonBlankText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


def normalize_elective(code: str) -> str:
    if not code.isascii():
        raise ValueError("Use a course code such as CS435 with ASCII letters and digits.")
    normalized = code.strip().upper().replace(" ", "")
    if not COURSE_CODE_PATTERN.fullmatch(normalized):
        raise ValueError("Use one specific course code such as CS435 per entry.")
    return normalized


class PlanPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    courses: list[Annotated[str, AfterValidator(normalize_elective)]] = Field(default_factory=list)
    credits_per_semester: int = Field(default=15, ge=MIN_CREDITS_PER_SEMESTER, le=MAX_CREDITS_PER_SEMESTER)
    start_term: str | None = Field(default=None, pattern=r"^(19|20|21)[0-9]{2}(10|90)$",
                                  description="Spring or fall planning start (1900–2199). Null uses the configured default; no collected sections are required.")

    @field_validator("courses")
    @classmethod
    def distinct_courses(cls, courses: list[str]) -> list[str]:
        if len(set(courses)) != len(courses):
            raise ValueError("List each elective course only once.")
        return courses


StableIdentifier = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")]


def stable_identity(kind: str, *parts: object) -> str:
    """Versioned deterministic identity; never depends on Python's process hash."""
    encoded = json.dumps(parts, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return f"{kind}_v1_{hashlib.sha256(encoded.encode()).hexdigest()[:32]}"


class RequirementSource(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True, json_schema_serialization_defaults_required=True)

    document_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    block_index: int = Field(ge=1)
    line: int = Field(ge=1, description="One-based line in extracted text, not a PDF page coordinate.")
    text: str


class CourseAttemptSource(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True, json_schema_serialization_defaults_required=True)

    document_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    line: int = Field(ge=1, description="One-based line in extracted text, not a PDF page coordinate.")
    text: str


class CourseAttempt(BaseModel):
    """An observed attempt, not a minimum-grade prerequisite decision."""
    model_config = ConfigDict(extra="ignore", strict=True, json_schema_serialization_defaults_required=True)

    course_code: Annotated[str, AfterValidator(normalize_elective)]
    grade: NonBlankText | None = None
    credits: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    term: NonBlankText | None = Field(default=None, description="Term text as extracted; null means unavailable.")
    source: CourseAttemptSource | None = None

    @field_validator("grade")
    @classmethod
    def normalize_grade(cls, grade: str | None) -> str | None:
        return grade.upper() if grade is not None else None

    @field_validator("credits", mode="before")
    @classmethod
    def numeric_credits(cls, value):
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))):
            raise ValueError("Attempt credits must be a number or null.")
        return value

    @computed_field
    @property
    def status(self) -> Literal["passed", "transfer", "failed", "withdrawn", "incomplete", "in_progress", "audit", "unknown"]:
        # Retain +/- letter grades from imported coursework. A passing D or
        # non-letter credit does not establish a minimum C prerequisite.
        if self.grade and (re.fullmatch(r"[ABCD][+-]?", self.grade) or self.grade in {"P", "S"}):
            return "passed"
        return {"T": "transfer", "TR": "transfer", "F": "failed", "U": "failed",
                "W": "withdrawn", "I": "incomplete", "IP": "in_progress", "AU": "audit"}.get(self.grade, "unknown")

    @computed_field
    @property
    def earns_credit(self) -> bool | None:
        if self.status in {"failed", "withdrawn", "incomplete", "in_progress", "audit"} or self.credits == 0:
            return False
        if self.status == "unknown" or self.credits is None:
            return None
        return True


class StillNeededItem(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True, json_schema_serialization_defaults_required=True)

    requirement: NonBlankText
    options: list[NonBlankText] = Field(default_factory=list)
    remaining_quantity: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    quantity_unit: Literal["classes", "credits", "unknown"] = "unknown"
    source: RequirementSource | None = None
    # Keep omitted IDs out of model_fields_set so the parent audit can
    # disambiguate identical legacy occurrences without changing callers.
    # A factory also avoids advertising an invalid empty ID as an API default.
    requirement_id: StableIdentifier = Field(default_factory=lambda fields: stable_identity(
        "req", fields["requirement"], fields["options"], fields["remaining_quantity"],
        fields["quantity_unit"], fields["source"].model_dump() if fields["source"] else None,
    ))

    @field_validator("options")
    @classmethod
    def normalize_options(cls, v: list[str]) -> list[str]:
        return [code.strip().upper().replace(" ", "") for code in v]

    @field_validator("remaining_quantity", mode="before")
    @classmethod
    def numeric_quantity(cls, value):
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))):
            raise ValueError("Remaining quantity must be a number or null.")
        return value

    @model_validator(mode="after")
    def validate_class_quantity(self):
        if self.quantity_unit == "classes" and self.remaining_quantity is not None and not self.remaining_quantity.is_integer():
            raise ValueError("A class quantity must be a whole number.")
        return self

    @computed_field
    @property
    def quantity_status(self) -> Literal["known", "unresolved"]:
        return "known" if self.remaining_quantity is not None and self.quantity_unit != "unknown" else "unresolved"


class ParsedDegree(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    student_name: Optional[str] = None
    majors: list[NonBlankText] = Field(default_factory=list)
    minors: list[NonBlankText] = Field(default_factory=list)
    catalog_year: Optional[int] = Field(default=None, ge=1000, le=9999)
    credits_completed: Optional[int] = None
    credits_required: Optional[int] = None
    credits_remaining: Optional[int] = None
    completed_courses: list[NonBlankText] = Field(default_factory=list)
    in_progress_courses: list[NonBlankText] = Field(default_factory=list)
    course_attempts: list[CourseAttempt] | None = Field(
        default=None,
        description="Observed attempts; null is legacy history without grade evidence. When present, derives the course summary lists.",
    )
    still_needed: list[StillNeededItem] = Field(default_factory=list)
    # semesters_remaining deliberately absent — computed by the planner from
    # credits_remaining and the student's chosen credits_per_semester

    @model_validator(mode="after")
    def derive_course_history(self):
        if self.course_attempts is not None:
            # Keep every occurrence for grade/term evidence; summaries contain
            # distinct codes. A previous pass and a current retake can coexist.
            object.__setattr__(self, "completed_courses", list(dict.fromkeys(
                attempt.course_code for attempt in self.course_attempts if attempt.earns_credit is True
            )))
            object.__setattr__(self, "in_progress_courses", list(dict.fromkeys(
                attempt.course_code for attempt in self.course_attempts if attempt.status == "in_progress"
            )))
        return self

    @model_validator(mode="after")
    def assign_requirement_identities(self):
        explicit = [item.requirement_id for item in self.still_needed if "requirement_id" in item.model_fields_set]
        if len(set(explicit)) != len(explicit):
            raise ValueError("Duplicate requirement ID in this audit.")
        used = set(explicit)
        occurrences: Counter[str] = Counter()
        identified = []
        for item in self.still_needed:
            if "requirement_id" not in item.model_fields_set:
                base = item.requirement_id
                occurrences[base] += 1
                identifier = f"{base}_{occurrences[base]}"
                while identifier in used:
                    occurrences[base] += 1
                    identifier = f"{base}_{occurrences[base]}"
                used.add(identifier)
                item = item.model_copy(update={"requirement_id": identifier})
            identified.append(item)
        object.__setattr__(self, "still_needed", identified)
        return self

    @field_validator("completed_courses", "in_progress_courses")
    @classmethod
    def normalize_course_codes(cls, v: list[str]) -> list[str]:
        return [c.upper().replace(" ", "") for c in v]

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
    catalog_status: CatalogStatus = "unknown"
    catalog_note: str = UNCHECKED_CATALOG_NOTE


class GerGroup(BaseModel):
    prefix: str
    courses: list[GerCourse]


class GerCoursesResponse(BaseModel):
    groups: list[GerGroup]
    subjects: list[str]
    missing_subjects: list[str]
    unconfigured_subjects: list[str]
    warnings: list[str]


class ParseValidationError(Exception):
    def __init__(self, field: str, message: str) -> None:
        self.field = field
        self.message = message
        super().__init__(f"{field}: {message}")
