from __future__ import annotations

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
        """A subject description not in the lookup is skipped, not an error."""
        pairs = [("Computer Science", "280"), ("Mystery Subject", "999")]
        lookup = {"Computer Science": "CS"}
        assert resolve_prerequisite_codes(pairs, lookup, "CS350") == ["CS280"]

    def test_empty_pairs_returns_empty_list(self):
        assert resolve_prerequisite_codes([], {"Computer Science": "CS"}, "CS101") == []
