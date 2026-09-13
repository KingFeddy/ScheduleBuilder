"""Discover retained section terms without claiming complete or fresh data."""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.schemas.terms import TermOption, TermsResponse
from src.scheduler.time_utils import term_to_label
from src.terms import TERM_CODE_PATTERN


async def discover_terms(session: AsyncSession) -> TermsResponse:
    result = await session.execute(text("""
        SELECT DISTINCT term FROM sections
        WHERE term ~ :pattern ORDER BY term
    """), {"pattern": TERM_CODE_PATTERN})
    collected = set(result.scalars().all())
    # The default stays visible even before its first successful section write.
    terms = sorted(collected | {settings.CURRENT_TERM})
    return TermsResponse(
        default_term=settings.CURRENT_TERM,
        terms=[TermOption(code=term, label=term_to_label(term), has_data=term in collected) for term in terms],
    )
