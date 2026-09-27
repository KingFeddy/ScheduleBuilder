from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies import get_db
from src.schemas.schedule import SolveRequest, SolveResponse
from src.services.courses import load_sections_with_meetings
from src.scheduler.solver import solve

# Single shared limiter defined once in main.py — never instantiate a second one here
from main import limiter

logger = logging.getLogger(__name__)

router = APIRouter(tags=["schedule"])


@router.post("/api/schedule/solve", response_model=SolveResponse)
@limiter.limit("10/minute")
async def solve_schedule(
    request: Request,
    body: SolveRequest,
    db: AsyncSession = Depends(get_db),
) -> SolveResponse:
    logger.debug(
        "SolveRequest | courses=%r term=%r earliest=%r latest=%r compact_week=%r",
        body.course_codes,
        body.term,
        body.options.earliest_start,
        body.options.latest_end,
        body.compact_week,
    )

    # Two DB queries regardless of course count (bulk loader, no N+1)
    sections_by_course = await load_sections_with_meetings(
        db, body.course_codes, body.term
    )

    return solve(
        course_codes=body.course_codes,
        sections_by_course=sections_by_course,
        options=body.options,
        professor_preferences=body.professor_preferences,
        compact_week=body.compact_week,
        topic_preferences=body.topic_preferences,
    )
