"""Public operational response shapes shared through the generated API schema."""
from typing import Literal
from datetime import datetime

from pydantic import BaseModel
from src.terms import TermCode


ScraperRunStatus = Literal[
    "running", "completed", "partial", "failed", "blocked", "schema_change", "skipped_overlap",
]


class ScraperRunResponse(BaseModel):
    status: ScraperRunStatus
    subjects: list[str] | None
    started_at: datetime | None
    finished_at: datetime | None
    sections_upserted: int | None
    sections_failed: int | None
    error_message: str | None


class ScraperStatusResponse(BaseModel):
    term: TermCode
    status: Literal["never_run"] | ScraperRunStatus
    checked_at: datetime
    latest_attempt: ScraperRunResponse | None
    last_successful_refresh: ScraperRunResponse | None
    data_as_of: datetime | None
    section_count: int
    sections_missing_timestamps: int


class HealthResponse(BaseModel):
    status: Literal["ok"]
    db: Literal["connected"]
    sections: int
    env: str


class DegradedHealthResponse(BaseModel):
    status: Literal["degraded"]
    error: Literal["db_unreachable"]


class VersionResponse(BaseModel):
    version: str
    env: str
    term: str
