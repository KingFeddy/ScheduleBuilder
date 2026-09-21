"""Catalog scope is a data limitation, never proof of degree eligibility."""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from src.config import Settings, settings
from src.schemas.plan import PlanPreferences, StillNeededItem
from src.services.plan import generate_plan
from tests.deployment.test_config_validation import make_settings
from tests.plan.test_dw_parser import TestParsePipeline as _ParseFixture
from tests.plan.test_planner import make_validated


def test_subject_configuration_normalizes_without_guessing_aliases():
    configured = make_settings(CATALOG_SUBJECTS=" cs, MATH,cs, psy,psyc ")
    assert configured.catalog_subjects == ["CS", "MATH", "PSY", "PSYC"]
    assert make_settings(CATALOG_SUBJECTS="IS").catalog_subjects == ["IS"]


@pytest.mark.parametrize("value", ["", " ", "CS,", "CS,,MATH", "CS MATH", "C", "CS100", "CS;DROP", "MÄTH", "ßß"])
@pytest.mark.parametrize("field", ["CATALOG_SUBJECTS", "GER_SUBJECTS"])
def test_invalid_subject_configuration_is_rejected(field, value):
    with pytest.raises(ValidationError):
        make_settings(**{field: value})


def test_defaults_cover_existing_program_fixture_and_browsers(monkeypatch):
    # This guards the existing synthetic CS / Applied Physics fixture; it is
    # deliberately not a claim that real program audits have passed acceptance.
    from src.catalog import course_subject
    parsed = _ParseFixture()._parse(monkeypatch)
    codes = parsed.completed_courses + parsed.in_progress_courses + [
        code for item in parsed.still_needed for code in item.options
    ]
    defaults = Settings.model_fields["CATALOG_SUBJECTS"].default.split(",")
    expected = {course_subject(code) for code in codes}
    # Further existing planner regressions and browser subject scopes.
    expected.update(["IS", "LIT", "PSY", "LIB", "SSC"])
    expected.update(Settings.model_fields["GER_SUBJECTS"].default.split(","))
    assert expected <= set(defaults)


def plan_for(code, rows, *, requested=False, credit_target=15):
    def execute(statement, params):
        result = MagicMock()
        result.mappings.return_value = rows if "FROM courses" in str(statement) else []
        return result

    session = AsyncMock()
    session.execute.side_effect = execute
    parsed = make_validated(still_needed=[StillNeededItem(
        requirement="Test requirement", options=["@" if requested else code],
    )])
    return asyncio.run(generate_plan(parsed, PlanPreferences.model_validate({
        "courses": [code] if requested else [], "credits_per_semester": credit_target,
    }), session))


def test_missing_course_is_flagged_even_when_required(monkeypatch):
    monkeypatch.setattr(settings, "CATALOG_SUBJECTS", "CS")
    plan = plan_for("CS999", [])
    course = plan.semesters[0].courses[0]
    assert course.course_code == "CS999" and course.badge == "Required"
    assert course.catalog_status == "course_missing"
    assert "not found" in course.catalog_note
    assert any("CS999" in warning and "not found" in warning for warning in plan.warnings)


@pytest.mark.parametrize("requested", [False, True])
@pytest.mark.parametrize("credit_target", [3, 15])
def test_excluded_subject_is_flagged_even_with_verified_metadata(monkeypatch, requested, credit_target):
    monkeypatch.setattr(settings, "CATALOG_SUBJECTS", "CS")
    plan = plan_for("IS999", [{
        "course_code": "IS999", "title": "Retained title", "credits": 4, "prerequisites": [],
        "title_source": {"value": "Retained title"},
        "credits_source": {"value": {"kind": "fixed", "minimum": 4, "maximum": 4}},
    }], requested=requested, credit_target=credit_target)
    course = plan.semesters[0].courses[0]
    assert course.credits_estimated is False  # Distinct from coverage.
    assert course.catalog_status == "subject_not_configured"
    assert "outside" in course.catalog_note
    assert "satisfies" not in course.reason
    assert any("IS999" in warning and "outside" in warning for warning in plan.warnings)


def test_unconfigured_wildcard_subject_still_reports_gap(monkeypatch):
    monkeypatch.setattr(settings, "CATALOG_SUBJECTS", "CS")
    plan = plan_for("PHYS3XX", [])
    assert plan.semesters[0].courses[0].catalog_status == "unresolved"
    assert "PHYS" in plan.semesters[0].courses[0].catalog_note
    assert any("PHYS" in warning and "outside" in warning for warning in plan.warnings)


def test_present_course_does_not_claim_eligibility(monkeypatch):
    monkeypatch.setattr(settings, "CATALOG_SUBJECTS", "CS")
    plan = plan_for("CS999", [{"course_code": "CS999", "title": None, "credits": None, "prerequisites": []}])
    course = plan.semesters[0].courses[0]
    assert course.catalog_status == "present"
    assert course.catalog_note == ""
    assert course.credits_estimated is True


@pytest.mark.parametrize("code", ["BME301", "ECON201", "EVSC101", "OPSE301"])
def test_default_scope_includes_subjects_from_the_expanded_refresh(monkeypatch, code):
    from src.catalog import DEFAULT_CATALOG_SUBJECTS
    monkeypatch.setattr(settings, "CATALOG_SUBJECTS", DEFAULT_CATALOG_SUBJECTS)
    generated = plan_for(code, [{"course_code": code, "title": "Synthetic title", "credits": 3, "prerequisites": []}])
    course = generated.semesters[0].courses[0]
    assert course.catalog_status == "present"
    assert not any("outside" in warning for warning in generated.warnings)


@pytest.mark.parametrize("exists", [False, True])
def test_explicit_scope_override_does_not_claim_whether_a_course_was_refreshed(monkeypatch, exists):
    from src.services.catalog import course_coverage, scope_warnings
    monkeypatch.setattr(settings, "CATALOG_SUBJECTS", "CS")
    status, note = course_coverage("BME301", exists=exists)
    assert status == "subject_not_configured"
    assert ("present" if exists else "missing") in note
    assert "automatic refresh scope" in note
    assert "not refreshed" not in note
    warnings = scope_warnings(["CS", "BME"], {"CS", "BME"} if exists else {"CS"})
    assert any("automatic refresh scope" in warning for warning in warnings)
    assert all("not refreshed" not in warning for warning in warnings)
