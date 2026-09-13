from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from .schedule import SectionResponse


class CourseResponse(BaseModel):
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)
    course_code: str
    title: str | None
    credits: float | None
    title_status: Literal["verified", "unverified", "missing"] = "unverified"
    credits_status: Literal["fixed", "variable", "unverified", "missing"] = "unverified"
    credits_min: float | None = None
    credits_max: float | None = None
    credits_options: list[float] = Field(default_factory=list)
    metadata_warnings: list[str] = Field(default_factory=list)


class CourseDetailResponse(CourseResponse):
    prerequisites: list[str]
    sections: list[SectionResponse]


class ProfessorResponse(BaseModel):
    rmp_score: float | None
    rmp_difficulty: float | None
    rmp_would_take_again: float | None
    rmp_num_ratings: int | None
    rmp_tags: list[str]
    department: str | None
