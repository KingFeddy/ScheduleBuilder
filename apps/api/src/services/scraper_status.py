"""Read one snapshot of selected-term attempts, successful coverage, and data age."""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.schemas.status import ScraperRunResponse, ScraperStatusResponse


async def load_scraper_status(session: AsyncSession, term: str) -> ScraperStatusResponse:
    row = (await session.execute(text("""
        WITH attempts AS NOT MATERIALIZED (
            SELECT id, status, subjects, started_at, finished_at,
                   sections_upserted, sections_failed, error_message
            FROM scraper_runs
            WHERE scraper = 'banner' AND subject IS NULL AND term = :term
        ), latest AS (
            SELECT * FROM attempts ORDER BY started_at DESC, id DESC LIMIT 1
        ), successful AS (
            SELECT * FROM attempts
            WHERE status = 'completed' AND sections_failed = 0
              AND sections_upserted >= 0
              AND subjects @> CAST(:subjects AS text[])
              AND cardinality(subjects) > 0
              AND finished_at >= started_at AND finished_at <= CURRENT_TIMESTAMP
            ORDER BY finished_at DESC, started_at DESC, id DESC LIMIT 1
        ), section_times AS (
            SELECT count(*) AS section_count,
                   count(*) FILTER (WHERE scraped_at IS NULL) AS missing_timestamps,
                   min(scraped_at) AS oldest, max(scraped_at) AS newest
            FROM sections WHERE term = :term
        )
        SELECT CURRENT_TIMESTAMP AS checked_at, to_jsonb(latest) AS latest_attempt,
               to_jsonb(successful) AS last_successful_refresh, section_times.*
        FROM section_times LEFT JOIN latest ON true LEFT JOIN successful ON true
    """), {"term": term, "subjects": settings.catalog_subjects})).mappings().one()

    latest = ScraperRunResponse.model_validate(row["latest_attempt"]) if row["latest_attempt"] else None
    success = ScraperRunResponse.model_validate(row["last_successful_refresh"]) if row["last_successful_refresh"] else None
    # Legacy mixed runs were labeled completed despite known section failures.
    if latest and latest.status == "completed" and (latest.sections_failed or 0) > 0:
        latest.status = "partial"
    data_as_of = None
    if (success and success.started_at and row["oldest"] is not None
            and row["missing_timestamps"] == 0 and row["newest"] <= row["checked_at"]):
        # Completion is later than many section observations. Retained excluded
        # subjects may be older still; neither can be labeled newly refreshed.
        data_as_of = min(success.started_at, row["oldest"])
    return ScraperStatusResponse(
        term=term, status=latest.status if latest else "never_run", checked_at=row["checked_at"],
        latest_attempt=latest, last_successful_refresh=success, data_as_of=data_as_of,
        section_count=row["section_count"], sections_missing_timestamps=row["missing_timestamps"],
    )
