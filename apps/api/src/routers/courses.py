from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.dependencies import get_db
from src.scheduler.models import SectionSlot
from src.schemas.courses import CourseDetailResponse, CourseResponse, ProfessorResponse
from src.schemas.schedule import MeetingResponse, SectionResponse
from src.services.courses import load_sections_with_meetings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["courses"])


def _slot_to_response(section: SectionSlot) -> SectionResponse:
    return SectionResponse(
        crn=section.crn,
        course_code=section.course_code,
        professor_name=section.professor_name,
        total_seats=section.total_seats,
        open_seats=section.open_seats,
        scraped_at=section.scraped_at.isoformat() if section.scraped_at else None,
        meetings=[
            MeetingResponse(
                days=m.days,
                start_time=m.start_time.strftime("%H:%M") if m.start_time else None,
                end_time=m.end_time.strftime("%H:%M") if m.end_time else None,
                location=m.location,
            )
            for m in section.meetings
        ],
    )


@router.get("/api/courses", response_model=list[CourseResponse])
async def search_courses(
    q: str | None = Query(None),
    subject: str | None = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(24, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> list[CourseResponse]:
    offset = (page - 1) * limit

    conditions = []
    params: dict = {"limit": limit, "offset": offset}

    if q:
        q_pattern = f"%{q}%"
        conditions.append("(course_code ILIKE :q_pattern OR title ILIKE :q_pattern)")
        params["q_pattern"] = q_pattern

    if subject:
        conditions.append("course_code ILIKE :subject_prefix")
        params["subject_prefix"] = f"{subject.upper()}%"

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    result = await db.execute(
        text(f"""
            SELECT course_code, title, credits
            FROM courses
            {where}
            ORDER BY course_code
            LIMIT :limit OFFSET :offset
        """),
        params,
    )
    rows = result.mappings().all()
    return [
        CourseResponse(
            course_code=row["course_code"],
            title=row["title"],
            credits=row["credits"],
        )
        for row in rows
    ]


@router.get("/api/courses/{code}/sections", response_model=list[SectionResponse])
async def get_course_sections(
    code: str,
    term: str = Query(..., pattern=r"^\d{4}(10|50|90)$"),
    db: AsyncSession = Depends(get_db),
) -> list[SectionResponse]:
    code = code.upper()
    sections_by_course = await load_sections_with_meetings(db, [code], term)
    return [_slot_to_response(s) for s in sections_by_course.get(code, [])]


@router.get("/api/courses/{code}", response_model=CourseDetailResponse)
async def get_course(
    code: str,
    db: AsyncSession = Depends(get_db),
) -> CourseDetailResponse:
    code = code.upper()

    result = await db.execute(
        text(
            "SELECT course_code, title, credits, prerequisites"
            " FROM courses WHERE course_code = :code"
        ),
        {"code": code},
    )
    row = result.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail=f"Course {code} not found.")

    sections_by_course = await load_sections_with_meetings(
        db, [code], settings.CURRENT_TERM
    )
    sections = [_slot_to_response(s) for s in sections_by_course.get(code, [])]

    return CourseDetailResponse(
        course_code=row["course_code"],
        title=row["title"],
        credits=row["credits"],
        prerequisites=row["prerequisites"] or [],
        sections=sections,
    )


@router.get("/api/professors/{name}", response_model=ProfessorResponse)
async def get_professor(
    name: str,
    db: AsyncSession = Depends(get_db),
) -> ProfessorResponse:
    result = await db.execute(
        text("""
            SELECT rmp_data, expires_at
            FROM rmp_cache
            WHERE professor_name = :name
        """),
        {"name": name},
    )
    row = result.mappings().first()

    if not row or row["rmp_data"] is None:
        raise HTTPException(status_code=404, detail="Professor not found in RMP cache.")

    # A row past expires_at is still the last-known-correct RMP match — serve
    # it rather than 404ing. Nothing refreshes it except the next scheduled
    # batch scrape reaching this professor, and RMP scores rarely change
    # meaningfully within that window.
    data = row["rmp_data"]
    if isinstance(data, str):
        data = json.loads(data)

    # Derive department from most-common course subject prefix in sections.
    # SUBSTRING('^[A-Z]+') extracts the alpha prefix: CS280 → CS, MATH337 → MATH.
    # The professors table exists but its schema is undocumented and currently empty.
    dept_result = await db.execute(
        text("""
            SELECT SUBSTRING(course_code FROM '^[A-Z]+') AS subject, COUNT(*) AS cnt
            FROM sections
            WHERE professor_name = :name
            GROUP BY subject
            ORDER BY cnt DESC
            LIMIT 1
        """),
        {"name": name},
    )
    dept_row = dept_result.mappings().first()

    return ProfessorResponse(
        rmp_score=data.get("rmp_score"),
        rmp_difficulty=data.get("rmp_difficulty"),
        rmp_would_take_again=data.get("rmp_would_take_again"),
        rmp_num_ratings=data.get("rmp_num_ratings"),
        rmp_tags=data.get("rmp_tags", []),
        department=dept_row["subject"] if dept_row else None,
    )
