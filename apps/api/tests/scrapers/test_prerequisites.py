from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

# ── TestFetchPrerequisites ───────────────────────────────────────────────────

class TestFetchPrerequisites:

    def test_non_200_status_raises_instead_of_silently_returning_empty(self):
        """A blocked/error response (e.g. 403) must raise, not parse to [] and
        silently overwrite a course's real prerequisites with an empty list."""
        from src.scrapers.prerequisites import fetch_rule_source

        async def run():
            import pytest

            mock_response = MagicMock()
            mock_response.status = 403
            mock_response.headers = {"content-type": "text/html"}
            mock_response.text = AsyncMock(return_value="")
            mock_page = MagicMock()
            mock_page.request = MagicMock()
            mock_page.request.post = AsyncMock(return_value=mock_response)

            with pytest.raises(RuntimeError):
                await fetch_rule_source(mock_page, "https://example.com/ssb", "202690", "90014", "prerequisites")

        __import__("asyncio").run(run())
