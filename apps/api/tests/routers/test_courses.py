from __future__ import annotations
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock
import pytest

from fastapi import HTTPException

from src.routers.courses import get_professor


def mock_session_with(rmp_row, dept_row=None):
    """Build a mock AsyncSession returning rmp_row for the cache query and
    dept_row for the department-derivation query, in that order."""
    def make_result(row):
        result = MagicMock()
        result.mappings.return_value.first.return_value = row
        return result

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[
        make_result(rmp_row),
        make_result(dept_row),
    ])
    return session


class TestGetProfessor:

    @pytest.mark.asyncio
    async def test_expired_but_present_cache_row_still_returns_data(self):
        """
        A cache row past its expires_at still holds the last-known-correct
        RMP match — it must be served, not discarded, since nothing
        refreshes it except the next scheduled batch scrape reaching that
        specific professor.
        """
        row = {
            "rmp_data": {
                "rmp_score": 4.7, "rmp_difficulty": 2.9,
                "rmp_num_ratings": 10, "rmp_would_take_again": 100,
                "rmp_tags": [],
            },
            "expires_at": datetime.now(timezone.utc) - timedelta(hours=5),
        }
        session = mock_session_with(row, dept_row={"subject": "MATH", "cnt": 3})

        result = await get_professor("Aytas, David Mustafa", db=session)

        assert result.rmp_score == 4.7
        assert result.rmp_num_ratings == 10

    @pytest.mark.asyncio
    async def test_fresh_cache_row_returns_data(self):
        row = {
            "rmp_data": {
                "rmp_score": 4.3, "rmp_difficulty": 2.5,
                "rmp_num_ratings": 20, "rmp_would_take_again": 90,
                "rmp_tags": ["Caring"],
            },
            "expires_at": datetime.now(timezone.utc) + timedelta(hours=5),
        }
        session = mock_session_with(row, dept_row={"subject": "CS", "cnt": 5})

        result = await get_professor("Cirillo, Michelle", db=session)

        assert result.rmp_score == 4.3
        assert result.department == "CS"

    @pytest.mark.asyncio
    async def test_no_cache_row_raises_404(self):
        session = mock_session_with(rmp_row=None)

        with pytest.raises(HTTPException) as exc_info:
            await get_professor("Nobody, Nowhere", db=session)

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_confirmed_not_on_rmp_raises_404_even_when_expired(self):
        """rmp_data is None means the scraper already confirmed this
        professor has no RMP page — that's permanently absent data, not
        staleness, so it must still 404 regardless of expires_at."""
        row = {
            "rmp_data": None,
            "expires_at": datetime.now(timezone.utc) - timedelta(hours=5),
        }
        session = mock_session_with(row)

        with pytest.raises(HTTPException) as exc_info:
            await get_professor("Ghost, Professor", db=session)

        assert exc_info.value.status_code == 404
