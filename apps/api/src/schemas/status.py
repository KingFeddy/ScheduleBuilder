"""Public operational response shapes shared through the generated API schema."""
from typing import Literal

from pydantic import BaseModel


class ScraperStatusResponse(BaseModel):
    last_scrape: str | None
    status: Literal[
        "never_run", "running", "completed", "failed", "blocked", "schema_change", "skipped_overlap",
    ]
    sections_upserted: int | None
    error_message: str | None


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
