from src.services.dw_parser import _extract_still_needed, parse_degree_works_regex
from src.schemas.plan import ParsedDegree
from src.services.plan import validate_parsed_degree


# ── _extract_still_needed ─────────────────────────────────────────────────────

class TestExtractStillNeeded:

    def test_see_without_number_never_matches(self):
        """'Still needed: See ...' has no number — the regex never matches it."""
        text = "Still needed: See Double Major in CS and Physics section"
        items = _extract_still_needed(text)
        assert len(items) == 0


    def test_long_multiline_option_list(self):
        """Real-world H&H 300-level — 15+ options across multiple lines."""
        text = (
            "H&H 300-Level Class II\n"
            "Still needed: 1 Class in COM 303 or 310 or 312 or 313 or\n"
            "314 or 315 or 316 or 317 or LIT 320 or 321 or\n"
            "HIST 320 or 325\n"
            "HSS Capstone\n"
            "Still needed: 1 Class in HSS 404\n"
        )
        items = _extract_still_needed(text)
        ger = next((i for i in items if "COM303" in i.options), None)
        assert ger is not None
        assert "COM303" in ger.options
        assert "LIT320" in ger.options
        assert "HIST325" in ger.options
        hss = next((i for i in items if "HSS404" in i.options), None)
        assert hss is not None

    def test_completed_block_produces_nothing(self):
        text = "Double Major Applied Physics/Computer Science COMPLETE\nCatalog year: 2025-2026"
        items = _extract_still_needed(text)
        assert len(items) == 0


# ── validate_parsed_degree ────────────────────────────────────────────────────

class TestValidateParsedDegree:

    def _valid(self, **overrides) -> ParsedDegree:
        base = dict(
            majors=["Computer Science"],
            credits_completed=90,
            credits_required=124,
            credits_remaining=34,
            completed_courses=["CS280", "CS331"],
            in_progress_courses=["CS435"],
            still_needed=[{"requirement": "Senior Project", "options": ["CS491"]}],
        )
        base.update(overrides)
        return ParsedDegree(**base)


    def test_transfer_codes_filtered_not_rejected(self):
        # CHEM5 (1 digit) and ENGL1010 (4 digits) don't match ^[A-Z]{2,5}\d{3}[A-Z]?$
        # ENG121 would match the pattern and is intentionally NOT tested here —
        # the filter is format-based, not department-based.
        parsed = self._valid(
            completed_courses=["CS280", "ENGL1010", "CHEM5", "CS331"]
        )
        validated = validate_parsed_degree(parsed)
        assert "CS280" in validated.completed_courses
        assert "CS331" in validated.completed_courses
        assert "ENGL1010" not in validated.completed_courses
        assert "CHEM5" not in validated.completed_courses


# ── Full parse pipeline ───────────────────────────────────────────────────────

class TestParsePipeline:
    """
    Mocks pdfplumber to return synthetic DegreeWorks text.
    parse_degree_works_regex is tested end-to-end without a real PDF.
    Manual testing with an actual NJIT DegreeWorks PDF is the acceptance gate.
    """

    SYNTHETIC_TEXT = """\
Student name Rajakumar, Frederick Joshua
Student ID *****490
Majors Computer Science (CS), Applied Physics (APPH)
Minor Applied Mathematics Program Computer Science BS

Bachelor of Science INCOMPLETE
Credits required: 124 Credits applied: 90 Catalog year: 2025-2026

Advanced Data Structures & Algorithm Design
Still needed: 1 Class in CS 435
Calculus III A
Still needed: 1 Class in MATH 211
Senior Project/Independent Study
Still needed: 3 Credits in CS 491 or PHYS 490
Physics 300-400 Level Elective
Still needed: 3 Credits in PHYS 3@ or 4@
History/Humanities 300-Level: Class I
Still needed: 1 Class in COM 312 or 313
HSS Capstone
Still needed: 1 Class in HSS 404
Still needed: See Double Major in Computer Science and Applied Phys section

CS 341 Found Of Computer Science II IP (3) 2026 Fall
CS 350 Intro to Computer Systems IP (3) 2026 Fall

CS 280 Programming Lang Concepts B+ 3 2025 Fall
CS 331 Database System Design A 3 2025 Fall
"""

    def _parse(self, monkeypatch):
        import pdfplumber

        text = self.SYNTHETIC_TEXT

        class _MockPage:
            def extract_text(self, layout=True):
                return text

        class _MockPDF:
            pages = [_MockPage()]

            def __enter__(self):
                return self

            def __exit__(self, *a, **kw):
                pass

        monkeypatch.setattr(pdfplumber, "open", lambda _: _MockPDF())
        # Must pass magic bytes check and 5KB minimum size check
        fake_bytes = b"%PDF-1.4 " + b"x" * 5200
        return parse_degree_works_regex(fake_bytes)

    def test_majors_extracted(self, monkeypatch):
        parsed = self._parse(monkeypatch)
        assert "Computer Science" in parsed.majors
        assert "Applied Physics" in parsed.majors

    def test_credits_extracted(self, monkeypatch):
        parsed = self._parse(monkeypatch)
        assert parsed.credits_required == 124
        assert parsed.credits_completed == 90
        assert parsed.credits_remaining == 34

    def test_in_progress_extracted(self, monkeypatch):
        parsed = self._parse(monkeypatch)
        assert "CS341" in parsed.in_progress_courses
        assert "CS350" in parsed.in_progress_courses

    def test_still_needed_extracted(self, monkeypatch):
        parsed = self._parse(monkeypatch)
        options_flat = {code for item in parsed.still_needed for code in item.options}
        assert "CS435" in options_flat
        assert "MATH211" in options_flat
        assert "CS491" in options_flat
        assert "PHYS3XX" in options_flat
        assert "COM312" in options_flat
        assert "HSS404" in options_flat


    def test_completed_courses_extracted(self, monkeypatch):
        parsed = self._parse(monkeypatch)
        assert "CS280" in parsed.completed_courses
        assert "CS331" in parsed.completed_courses

    def test_in_progress_not_in_completed(self, monkeypatch):
        parsed = self._parse(monkeypatch)
        for code in parsed.in_progress_courses:
            assert code not in parsed.completed_courses

    def test_validation_passes_on_synthetic(self, monkeypatch):
        parsed = self._parse(monkeypatch)
        validated = validate_parsed_degree(parsed)
        assert validated is not None
        assert "Computer Science" in validated.majors
