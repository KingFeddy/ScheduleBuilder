from typing import Literal

from pydantic import BaseModel

CatalogStatus = Literal["present", "subject_not_configured", "course_missing", "unresolved", "unknown"]
UNCHECKED_CATALOG_NOTE = "Catalog coverage has not been checked. Regenerate the plan to check it."


class SubjectCoverage(BaseModel):
    subject: str
    configured: bool
    course_count: int
    section_count: int


class CatalogCoverageResponse(BaseModel):
    term: str
    configured_subjects: list[str]
    elective_subjects: list[str]
    subjects: list[SubjectCoverage]
    warnings: list[str]
