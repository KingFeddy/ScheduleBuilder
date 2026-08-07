from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

from src.scrapers.prerequisites import (
    parse_prerequisite_table,
    build_subject_lookup,
    resolve_prerequisite_codes,
)


# ── TestParsePrerequisiteTable ─────────────────────────────────────────────

class TestParsePrerequisiteTable:

    def test_single_prerequisite(self):
        """ACCT425-shaped: one row, no And/Or connector."""
        html = """
        <section aria-labelledby="preReqs">
            <h3>Catalog Prerequisites</h3>
            <table class="basePreqTable">
                <thead>
                    <tr><th>And/Or</th><th></th><th>Test</th><th>Score</th>
                        <th>Subject</th><th>Course Number</th><th>Level</th>
                        <th>Grade</th><th></th></tr>
                </thead>
                <tbody>
                    <tr>
                        <td></td><td></td><td></td><td></td>
                        <td>Accounting</td><td>215</td>
                        <td>Undergraduate</td><td>D</td><td></td>
                    </tr>
                </tbody>
            </table>
        </section>
        """
        assert parse_prerequisite_table(html) == [("Accounting", "215")]

    def test_two_prerequisites_and_connector(self):
        """CS288-shaped: two rows, second row's And/Or cell says 'And'."""
        html = """
        <section aria-labelledby="preReqs">
            <h3>Catalog Prerequisites</h3>
            <table class="basePreqTable">
                <thead>
                    <tr><th>And/Or</th><th></th><th>Test</th><th>Score</th>
                        <th>Subject</th><th>Course Number</th><th>Level</th>
                        <th>Grade</th><th></th></tr>
                </thead>
                <tbody>
                    <tr>
                        <td></td><td></td><td></td><td></td>
                        <td>Computer Science</td><td>100</td>
                        <td>Undergraduate</td><td>C</td><td></td>
                    </tr>
                    <tr>
                        <td>And</td><td></td><td></td><td></td>
                        <td>Computer Science</td><td>280</td>
                        <td>Undergraduate</td><td>C</td><td></td>
                    </tr>
                </tbody>
            </table>
        </section>
        """
        assert parse_prerequisite_table(html) == [
            ("Computer Science", "100"),
            ("Computer Science", "280"),
        ]

    def test_no_prerequisites_returns_empty_list(self):
        """A course with no prerequisites — no table, no rows to find."""
        html = """
        <section aria-labelledby="preReqs">
            <h3>Catalog Prerequisites</h3>
        </section>
        """
        assert parse_prerequisite_table(html) == []


# ── TestBuildSubjectLookup ──────────────────────────────────────────────────

class TestBuildSubjectLookup:

    def test_builds_description_to_code_map(self):
        entries = [
            {"code": "CS", "description": "Computer Science"},
            {"code": "ACCT", "description": "Accounting"},
        ]
        assert build_subject_lookup(entries) == {
            "Computer Science": "CS",
            "Accounting": "ACCT",
        }


# ── TestResolvePrerequisiteCodes ────────────────────────────────────────────

class TestResolvePrerequisiteCodes:

    def test_resolves_known_subjects(self):
        pairs = [("Computer Science", "100"), ("Computer Science", "280")]
        lookup = {"Computer Science": "CS"}
        assert resolve_prerequisite_codes(pairs, lookup, "CS288") == ["CS100", "CS280"]

    def test_unknown_subject_is_skipped_not_fatal(self):
        """A subject description not in the lookup is skipped, not an error — and processing continues past it to later entries."""
        pairs = [
            ("Computer Science", "280"),
            ("Mystery Subject", "999"),
            ("Computer Science", "100"),
        ]
        lookup = {"Computer Science": "CS"}
        assert resolve_prerequisite_codes(pairs, lookup, "CS350") == ["CS280", "CS100"]

    def test_empty_pairs_returns_empty_list(self):
        assert resolve_prerequisite_codes([], {"Computer Science": "CS"}, "CS101") == []


# ── TestFetchSubjectLookup ───────────────────────────────────────────────────

class TestFetchSubjectLookup:

    def test_fetches_and_builds_lookup(self):
        from src.scrapers.prerequisites import fetch_subject_lookup

        async def run():
            mock_response = MagicMock()
            mock_response.text = AsyncMock(
                return_value=json.dumps([
                    {"code": "CS", "description": "Computer Science"},
                    {"code": "ACCT", "description": "Accounting"},
                ])
            )
            mock_page = MagicMock()
            mock_page.request = MagicMock()
            mock_page.request.get = AsyncMock(return_value=mock_response)

            result = await fetch_subject_lookup(mock_page, "https://example.com/ssb", "202690")

            assert result == {"Computer Science": "CS", "Accounting": "ACCT"}
            mock_page.request.get.assert_called_once()
            called_url = mock_page.request.get.call_args[0][0]
            assert "get_subject" in called_url
            assert "term=202690" in called_url

        __import__("asyncio").run(run())


# ── TestFetchPrerequisites ───────────────────────────────────────────────────

class TestFetchPrerequisites:

    def test_fetches_and_parses_table(self):
        from src.scrapers.prerequisites import fetch_prerequisites

        async def run():
            mock_response = MagicMock()
            mock_response.text = AsyncMock(
                return_value="""
                <table class="basePreqTable"><tbody>
                    <tr><td></td><td></td><td></td><td></td>
                        <td>Accounting</td><td>215</td><td>Undergraduate</td><td>D</td><td></td></tr>
                </tbody></table>
                """
            )
            mock_page = MagicMock()
            mock_page.request = MagicMock()
            mock_page.request.post = AsyncMock(return_value=mock_response)

            result = await fetch_prerequisites(mock_page, "https://example.com/ssb", "202690", "90014")

            assert result == [("Accounting", "215")]
            mock_page.request.post.assert_called_once()
            call_kwargs = mock_page.request.post.call_args
            assert "getSectionPrerequisites" in call_kwargs[0][0]
            assert call_kwargs[1]["form"] == {"term": "202690", "courseReferenceNumber": "90014"}

        __import__("asyncio").run(run())
