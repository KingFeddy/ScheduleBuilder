"""Read-only collection coverage. Presence is not eligibility or freshness."""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.catalog import course_subject
from src.config import settings
from src.schemas.catalog import CatalogCoverageResponse, CatalogStatus, SubjectCoverage


def course_coverage(code: str, *, exists: bool) -> tuple[CatalogStatus, str]:
    if code in {"TBD", "FREE"}:
        return "unresolved", "No specific catalog course has been selected for this slot."
    subject = course_subject(code)
    if subject not in settings.catalog_subjects:
        return "subject_not_configured", "Subject is outside the configured collection scope. Course data is not refreshed; confirm this course with NJIT."
    if not exists:
        return "course_missing", "Course not found in the collected catalog. Confirm its details and availability with NJIT."
    return "present", ""


def scope_warnings(subjects: list[str], present_subjects: set[str]) -> list[str]:
    warnings = []
    excluded = sorted(set(subjects) - set(settings.catalog_subjects))
    if excluded:
        warnings.append(f"Subjects outside collection scope: {', '.join(excluded)}. Data in these subjects is not refreshed.")
    missing = sorted((set(subjects) & set(settings.catalog_subjects)) - present_subjects)
    if missing:
        warnings.append(f"No catalog courses collected for: {', '.join(missing)}. The catalog is incomplete for these subjects.")
    return warnings


async def load_catalog_coverage(session: AsyncSession, term: str) -> CatalogCoverageResponse:
    result = await session.execute(text("""
        SELECT subject, SUM(course_count)::int AS course_count,
               SUM(section_count)::int AS section_count
        FROM (
            SELECT SUBSTRING(course_code FROM '^[A-Z]+') AS subject,
                   COUNT(*) AS course_count, 0 AS section_count
            FROM courses GROUP BY subject
            UNION ALL
            SELECT SUBSTRING(course_code FROM '^[A-Z]+') AS subject,
                   0 AS course_count, COUNT(*) AS section_count
            FROM sections WHERE term = :term GROUP BY subject
        ) AS collected GROUP BY subject ORDER BY subject
    """), {"term": term})
    counts = {row["subject"]: row for row in result.mappings().all() if row["subject"]}
    configured = settings.catalog_subjects
    subjects = sorted(set(configured) | set(settings.ger_subjects) | counts.keys())
    coverage = [SubjectCoverage(
        subject=subject, configured=subject in configured,
        course_count=counts.get(subject, {}).get("course_count", 0),
        section_count=counts.get(subject, {}).get("section_count", 0),
    ) for subject in subjects]
    warnings = scope_warnings(subjects, {s.subject for s in coverage if s.course_count})
    no_sections = [s.subject for s in coverage if s.configured and not s.section_count]
    if no_sections:
        warnings.append(
            f"No sections collected for term {term} in: {', '.join(no_sections)}. "
            "This does not confirm that no classes are offered."
        )
    return CatalogCoverageResponse(
        term=term, configured_subjects=configured, elective_subjects=settings.ger_subjects,
        subjects=coverage, warnings=warnings,
    )
