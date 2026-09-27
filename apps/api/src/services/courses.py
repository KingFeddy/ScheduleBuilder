from __future__ import annotations
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..scheduler.models import MeetingSlot, SectionSlot
from .course_topics import is_topic_title, topic_label, topic_key
from .meeting_integrity import IncompleteMeetingData, meeting_kind


# Banner FP records are administrative placeholders, not teaching sections.
# Keep the source records for reconciliation, but never offer them for scheduling.
PLACEHOLDER_SECTION_NUMBER = "FP"


async def load_sections_with_meetings(
    session: AsyncSession,
    course_codes: list[str],
    term: str,
) -> dict[str, list[SectionSlot]]:
    """
    Load all sections and all meetings for the given courses in two queries.
    Returns: { course_code: [SectionSlot, ...] }

    Never issues more than two queries regardless of how many courses are
    requested — sections in one shot, then meetings for all returned CRNs
    in one shot.
    """
    sections_result = await session.execute(
        text("""
            SELECT s.crn, s.term, s.course_code, s.professor_name,
                   s.total_seats, s.open_seats, s.scraped_at, s.section_number,
                   s.section_title, c.title AS catalog_title
            FROM sections s JOIN courses c ON c.course_code = s.course_code
            WHERE s.course_code = ANY(:codes) AND s.term = :term
            ORDER BY s.course_code, s.crn
        """),
        {"codes": course_codes, "term": term},
    )
    sections_rows = [
        row for row in sections_result.mappings().all()
        if (row["section_number"] or "").strip().upper() != PLACEHOLDER_SECTION_NUMBER
    ]

    if not sections_rows:
        return {code: [] for code in course_codes}

    crns = [row["crn"] for row in sections_rows]

    meetings_result = await session.execute(
        text("""
            SELECT crn, term, days, start_time, end_time, location
            FROM meetings
            WHERE crn = ANY(:crns) AND term = :term
        """),
        {"crns": crns, "term": term},
    )
    meetings_rows = meetings_result.mappings().all()

    meetings_by_crn: dict[str, list[MeetingSlot]] = {}
    for row in meetings_rows:
        if meeting_kind(row["days"], row["start_time"], row["end_time"]) == "invalid":
            raise IncompleteMeetingData()
        meetings_by_crn.setdefault(row["crn"], []).append(
            MeetingSlot(
                crn=row["crn"],
                term=row["term"],
                days=row["days"],
                start_time=row["start_time"],
                end_time=row["end_time"],
                location=row["location"],
            )
        )

    topic_courses = {
        row["course_code"] for row in sections_rows
        if is_topic_title(row.get("section_title")) or is_topic_title(row.get("catalog_title"))
    }
    labels: dict[tuple[str, str], str] = {}
    result: dict[str, list[SectionSlot]] = {code: [] for code in course_codes}
    for row in sections_rows:
        if not meetings_by_crn.get(row["crn"]):
            # Absence may mean an unfinished migration/scrape, not an online class.
            raise IncompleteMeetingData()
        topic = None
        if row["course_code"] in topic_courses:
            label = topic_label(row.get("section_title"), row["section_number"], row["crn"])
            topic = labels.setdefault((row["course_code"], topic_key(label)), label)
        result[row["course_code"]].append(
            SectionSlot(
                crn=row["crn"],
                term=row["term"],
                course_code=row["course_code"],
                professor_name=row["professor_name"],
                total_seats=row["total_seats"],
                open_seats=row["open_seats"],
                scraped_at=row["scraped_at"],
                section_number=row["section_number"],
                section_title=row.get("section_title"),
                topic=topic,
                meetings=meetings_by_crn.get(row["crn"], []),
            )
        )

    return result
