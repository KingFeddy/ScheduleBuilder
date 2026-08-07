from __future__ import annotations
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock
import pytest

from src.scrapers.rmp import _rmp_match_is_plausible, fetch_rmp_rating


class TestRmpMatchIsPlausible:
    """
    Every case here is a real (professor_name, rmp lastName) pair found
    during a live audit of this app's actual rmp_cache table — see
    dev-log.md. RMP's search is a loose fuzzy match that can return a
    completely unrelated professor sharing no more than a first name with
    the query; these pin down which pairs must be trusted vs. rejected.
    """

    def test_matching_last_name_is_plausible(self):
        assert _rmp_match_is_plausible("Aytas, David Mustafa", "Aytas") is True

    def test_matching_last_name_with_extra_whitespace_is_plausible(self):
        """Real RMP data: 'Adubato, Julianna Leonard' matched RMP lastName
        'Adubato' but firstName had a double space ('Julianna  Adubato')."""
        assert _rmp_match_is_plausible("Adubato, Julianna Leonard", "Adubato") is True

    def test_compound_last_name_matches_on_any_token(self):
        """Real RMP data: 'Del Castillo, Trevor James' correctly matched
        RMP's 'Castillo' — a multi-word Banner last name only needs one
        token to overlap."""
        assert _rmp_match_is_plausible("Del Castillo, Trevor James", "Castillo") is True

    def test_unrelated_professor_is_not_plausible(self):
        """The bug that motivated this function: searching 'Ariel Tang'
        (Accounting, no real RMP page) returned RMP's closest fuzzy guess,
        an entirely unrelated Humanities professor 'Ariel Sykes' — first
        name coincidence only, no last-name overlap."""
        assert _rmp_match_is_plausible("Tang, Ariel", "Sykes") is False

    def test_first_name_substring_in_unrelated_last_name_is_not_plausible(self):
        """Real RMP data: 'St. Edward, Steve M.' has last name tokens
        {'st', 'edward'}; RMP's closest fuzzy guess was 'Edward Gottko' —
        'Edward' there is the unrelated professor's firstName, not a real
        last-name match. Comparing against RMP's lastName field alone
        ('Gottko') correctly rejects this, where matching against the
        full concatenated name would not have."""
        assert _rmp_match_is_plausible("St. Edward, Steve M.", "Gottko") is False

    def test_no_comma_in_professor_name_falls_back_to_whole_string(self):
        assert _rmp_match_is_plausible("Aytas", "Aytas") is True

    def test_empty_rmp_last_name_is_not_plausible(self):
        assert _rmp_match_is_plausible("Tang, Ariel", "") is False


def _make_response(status_code, json_data):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = json_data
    return response


def _edges_response(first_name, last_name):
    return _make_response(200, {
        "data": {"newSearch": {"teachers": {"edges": [{
            "node": {
                "firstName": first_name, "lastName": last_name,
                "avgRating": 2, "avgDifficulty": 4,
                "wouldTakeAgainPercent": 0, "numRatings": 1,
                "teacherRatingTags": [],
            },
        }]}}},
    })


class TestFetchRmpRatingMatchValidation:

    @pytest.mark.asyncio
    async def test_mismatched_top_result_is_treated_as_not_found(self, monkeypatch):
        """End-to-end: fetch_rmp_rating must not store or return RMP's top
        search result when it doesn't plausibly match the professor being
        searched — this is the actual bug (Tang, Ariel -> Ariel Sykes)."""
        monkeypatch.setattr("src.scrapers.rmp.asyncio.sleep", AsyncMock())
        session = AsyncMock()
        session.execute = AsyncMock(return_value=MagicMock(
            mappings=MagicMock(return_value=MagicMock(first=MagicMock(return_value=None))),
        ))
        http_client = AsyncMock()
        http_client.post = AsyncMock(return_value=_edges_response("Ariel", "Sykes"))

        result = await fetch_rmp_rating(session, "Tang, Ariel", http_client)

        assert result is None
        # Must cache the negative result (as "confirmed not found"), not
        # silently retry every call.
        insert_call = session.execute.call_args_list[-1]
        assert insert_call.args[1]["data"] is None

    @pytest.mark.asyncio
    async def test_matching_top_result_is_returned(self, monkeypatch):
        monkeypatch.setattr("src.scrapers.rmp.asyncio.sleep", AsyncMock())
        session = AsyncMock()
        session.execute = AsyncMock(return_value=MagicMock(
            mappings=MagicMock(return_value=MagicMock(first=MagicMock(return_value=None))),
        ))
        http_client = AsyncMock()
        http_client.post = AsyncMock(return_value=_edges_response("Mustafa", "Aytas"))

        result = await fetch_rmp_rating(session, "Aytas, David Mustafa", http_client)

        assert result is not None
        assert result["rmp_score"] == 2
