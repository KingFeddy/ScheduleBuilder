"""Preserve the meaning and evidence of prerequisite expressions, not just their codes."""
from __future__ import annotations

from itertools import product
import hashlib
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import text

from src.scrapers import banner, prerequisites
from tests.scrapers.test_prerequisite_verification import (
    EMPTY_HTML, HEAD, SUBJECTS, TERM, SUBJECT, response, row, table,
    seed, snapshot, source as source,
)


LOOKUP = {"Fake Test Subject": "ZZZ"}
COREQ_HEAD = "<thead><tr><th>Subject</th><th>Course Number</th><th>Title</th></tr></thead>"
EMPTY_COREQUISITES = f'<table class="basePreqTable">{COREQ_HEAD}<tbody></tbody></table>'


def corequisite_table(number="995"):
    return EMPTY_COREQUISITES.replace(
        "<tbody></tbody>",
        f"<tbody><tr><td>Fake Test Subject</td><td>{number}</td><td>Synthetic Lab</td></tr></tbody>",
    )


def parse(body):
    return prerequisites.parse_prerequisite_rules(body, LOOKUP).model_dump(mode="json")


def course(number, source_row=1, **overrides):
    return {
        "kind": "course", "course_code": f"ZZZ{number}", "minimum_grade": "C",
        "level": "Undergraduate", "timing": "unspecified", "required_crn": None,
        "source_row": source_row, **overrides,
    }


def evaluate(rule, completed):
    """Truth-table oracle used only to check that grouping survives extraction."""
    if rule["kind"] == "course":
        return rule["course_code"] in completed
    if rule["kind"] == "all":
        return all(evaluate(child, completed) for child in rule["items"])
    if rule["kind"] == "any":
        return any(evaluate(child, completed) for child in rule["items"])
    raise AssertionError("Cannot evaluate an unresolved condition")


def test_course_grade_level_and_absent_timing_are_preserved():
    assert parse(table(row(grade="C-"))) == course("996", minimum_grade="C-")
    assert parse(table(row(grade="", level="Graduate"))) == course("996", minimum_grade=None, level="Graduate")


def test_empty_prerequisite_section_remains_explicitly_empty():
    assert parse(EMPTY_HTML) == {"kind": "all", "items": []}


@pytest.mark.parametrize("connector, kind", [("And", "all"), ("Or", "any")])
def test_operator_and_source_order_survive(connector, kind):
    assert parse(table(row(number="991"), row(number="992", connector=connector))) == {
        "kind": kind, "items": [course("991"), course("992", 2)],
    }


@pytest.mark.parametrize("rows, expected", [
    ([row(number="991", opening="("), row(number="992", connector="Or", closing=")"), row(number="993", connector="And")],
     lambda a, b, c: (a or b) and c),
    ([row(number="991"), row(number="992", connector="And", opening="("), row(number="993", connector="Or", closing=")")],
     lambda a, b, c: a and (b or c)),
    ([row(number="991", opening="(("), row(number="992", connector="And", closing=")"), row(number="993", connector="Or", closing=")")],
     lambda a, b, c: (a and b) or c),
])
def test_grouped_rule_truth_table(rows, expected):
    rule = parse(table(*rows))
    for a, b, c in product([False, True], repeat=3):
        completed = {code for code, present in zip(["ZZZ991", "ZZZ992", "ZZZ993"], [a, b, c]) if present}
        assert evaluate(rule, completed) is expected(a, b, c)


def test_repeated_course_with_different_grades_is_not_deduplicated():
    rule = parse(table(row(grade="C"), row(connector="Or", grade="B")))
    assert rule == {"kind": "any", "items": [course("996"), course("996", 2, minimum_grade="B")]}


@pytest.mark.parametrize("value, timing", [("Yes", "prior_or_concurrent"), ("No", "prior"), ("", "unspecified")])
def test_explicit_concurrency_is_distinct_from_corequisite_and_unknown(value, timing):
    body = table(row()).replace("</tr></thead>", "<th>Concurrency</th></tr></thead>")
    body = body.replace("</tr></tbody>", f"<td>{value}</td></tr></tbody>")
    assert parse(body) == course("996", timing=timing)


def test_corequisite_course_is_required_in_same_term():
    rule = prerequisites.parse_corequisite_rules(corequisite_table(), LOOKUP).model_dump(mode="json")
    assert rule == course("995", minimum_grade=None, level=None, timing="concurrent")


def test_section_specific_corequisite_retains_required_crn():
    body = """<table><thead><tr><th>CRN</th><th>Subject</th><th>Course Number</th>
        <th>Title</th><th>Section</th></tr></thead><tbody><tr><td>82222</td>
        <td>Fake Test Subject</td><td>995</td><td>Synthetic Lab</td><td>002</td></tr></tbody></table>"""
    rule = prerequisites.parse_corequisite_rules(body, LOOKUP).model_dump(mode="json")
    assert rule == course("995", minimum_grade=None, level=None, timing="concurrent", required_crn="82222")


def test_verified_empty_corequisite_table_is_explicit():
    assert prerequisites.parse_corequisite_rules(EMPTY_COREQUISITES, LOOKUP).model_dump() == {"kind": "all", "items": []}


def test_multiple_catalog_corequisites_are_all_required():
    body = corequisite_table().replace("</tbody>", "<tr><td>Fake Test Subject</td><td>994</td><td>Workshop</td></tr></tbody>")
    rule = prerequisites.parse_corequisite_rules(body, LOOKUP)
    assert rule.kind == "all"
    assert [(item.course_code, item.timing) for item in rule.items] == [("ZZZ995", "concurrent"), ("ZZZ994", "concurrent")]


def test_alternative_corequisite_sections_are_not_guessed_as_mandatory():
    body = """<table><thead><tr><th>CRN</th><th>Subject</th><th>Course Number</th>
        <th>Title</th><th>Section</th></tr></thead><tbody>
        <tr><td>82222</td><td>Fake Test Subject</td><td>995</td><td>Lab</td><td>002</td></tr>
        <tr><td>82223</td><td>Fake Test Subject</td><td>995</td><td>Lab</td><td>003</td></tr>
        </tbody></table>"""
    rule = prerequisites.parse_corequisite_rules(body, LOOKUP)
    assert rule.kind == "unresolved"
    assert rule.reason == "ambiguous_corequisite_sections"


def test_missing_corequisite_crn_is_unresolved():
    body = """<table><thead><tr><th>CRN</th><th>Subject</th><th>Course Number</th>
        <th>Title</th><th>Section</th></tr></thead><tbody><tr><td></td>
        <td>Fake Test Subject</td><td>995</td><td>Lab</td><td>002</td></tr></tbody></table>"""
    assert prerequisites.parse_corequisite_rules(body, LOOKUP).kind == "unresolved"


def test_unknown_concurrency_value_remains_unresolved():
    body = table(row()).replace("</tr></thead>", "<th>Concurrency</th></tr></thead>")
    body = body.replace("</tr></tbody>", "<td>Maybe</td></tr></tbody>")
    assert parse(body) == {"kind": "unresolved", "reason": "unsupported_concurrency", "source_row": 1}


@pytest.mark.parametrize("body", ["", "<h3>Corequisites</h3>", "<h1>Session expired</h1>", EMPTY_COREQUISITES.replace("Title", "Changed")])
def test_unrecognized_corequisite_response_never_means_no_corequisites(body):
    with pytest.raises(prerequisites.PrerequisiteDataError):
        prerequisites.parse_corequisite_rules(body, LOOKUP)


def test_storage_round_trip_preserves_scope_and_rules_and_rejects_unknown_fields():
    from pydantic import ValidationError
    from src.schemas.prerequisites import PrerequisiteRules
    value = {
        "schema_version": 1, "scope": {"term": TERM, "crn": "81111"},
        "prerequisites": parse(table(row(), row(connector="Or", number="994", grade="B-"))),
        "corequisites": {"kind": "all", "items": []},
    }
    assert PrerequisiteRules.model_validate_json(json.dumps(value)).model_dump(mode="json") == value
    value["prerequisites"]["items"][1]["silently_dropped_condition"] = "No"
    with pytest.raises(ValidationError):
        PrerequisiteRules.model_validate(value)


@pytest.mark.parametrize("body, expected", [
    (table(row(), row(connector="And", number="994")), ["ZZZ996", "ZZZ994"]),
    (table(row(), row(connector="Or", number="994")), None),
])
def test_legacy_projection_never_flattens_alternatives(body, expected):
    from src.schemas.prerequisites import AllConditions, ObservationScope, PrerequisiteRules, legacy_course_codes
    rules = PrerequisiteRules(
        scope=ObservationScope(term=TERM, crn="81111"),
        prerequisites=prerequisites.parse_prerequisite_rules(body, LOOKUP),
        corequisites=AllConditions(items=[]),
    )
    assert legacy_course_codes(rules) == expected


@pytest.mark.parametrize("too_large", ["rows", "depth"])
def test_parser_limits_are_explicit_failures(too_large):
    if too_large == "rows":
        body = table(row(), *[row(connector="And") for _ in range(prerequisites.MAX_RULE_ROWS)])
    else:
        depth = prerequisites.MAX_RULE_DEPTH + 1
        body = table(row(opening="(" * depth, closing=")" * depth))
    with pytest.raises(prerequisites.PrerequisiteDataError):
        parse(body)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["subjects", "prerequisites", "corequisites"])
async def test_oversized_source_is_bounded_hashed_and_flagged_unresolved(kind):
    body = "Synthetic evidence " + "é" * prerequisites.MAX_SOURCE_BYTES
    reply = response(body, content_type="application/json" if kind == "subjects" else "text/html")
    page = MagicMock(request=MagicMock(get=AsyncMock(return_value=reply), post=AsyncMock(return_value=reply)))
    with pytest.raises(prerequisites.PrerequisiteDataError) as error:
        await prerequisites.fetch_rule_source(page, "https://example.test/ssb", TERM, "81111", kind)
    evidence = error.value.source
    assert evidence.truncated
    assert len(evidence.body.encode("utf-8")) <= prerequisites.MAX_SOURCE_BYTES
    assert body.startswith(evidence.body)
    assert evidence.sha256 == hashlib.sha256(body.encode("utf-8")).hexdigest()


@pytest.mark.asyncio
async def test_body_read_failure_preserves_http_metadata_without_logging_exception_payload():
    reply = response("")
    reply.text = AsyncMock(side_effect=TimeoutError("DO NOT PERSIST THIS PAYLOAD"))
    page = MagicMock(request=MagicMock(post=AsyncMock(return_value=reply)))
    with pytest.raises(prerequisites.PrerequisiteRequestError) as error:
        await prerequisites.fetch_rule_source(page, "https://example.test/ssb", TERM, "81111", "corequisites")
    evidence = error.value.source
    assert evidence.http_status == 200
    assert evidence.content_type == "text/html"
    assert evidence.body is None
    assert "DO NOT PERSIST" not in str(error.value)


@pytest.mark.parametrize("body", [
    table(row(opening="(")), table(row(closing=")")),
    table(row(connector="And")), table(row(), row()),
    table(row(), row(connector="Or"), row(connector="And")),
    table(row(opening="[ ")), table(row(), row(connector="XOR")),
])
def test_malformed_or_ambiguous_logic_does_not_get_a_guessed_interpretation(body):
    with pytest.raises(prerequisites.PrerequisiteDataError):
        parse(body)


@pytest.mark.parametrize("bad_row, reason", [
    (row(subject="", number="", test="Placement", score="80", connector="Or"), "unsupported_test_condition"),
    (row(subject="Unknown department", connector="Or"), "unresolved_subject"),
    (row(number="3@", connector="Or"), "unsupported_course_number"),
    (row(grade="Instructor approval", connector="Or"), "unsupported_minimum_grade"),
])
def test_unsupported_branch_stays_in_its_boolean_position(bad_row, reason):
    assert parse(table(row(), bad_row)) == {
        "kind": "any", "items": [course("996"), {"kind": "unresolved", "reason": reason, "source_row": 2}],
    }


@pytest.mark.asyncio
async def test_structured_refresh_stores_rules_and_exact_evidence_without_flattening_or(db_session, db_session_factory, source):
    await seed(db_session)
    source.prerequisite = response(table(row(opening="("), row(number="994", connector="Or", closing=")", grade="B-")))
    source.corequisite = response(corequisite_table())
    assert await banner.scrape_subject(db_session, SUBJECT, TERM) == (1, 0, 0)
    saved = await snapshot(db_session_factory)
    assert saved["prerequisites"] == ["ZZZ990", "ZZZ991"]
    assert saved["prerequisites_status"] == "verified"
    rules = saved["prerequisites_rules"]
    assert rules["schema_version"] == 1
    assert rules["scope"] == {"term": TERM, "crn": "81111"}
    assert rules["prerequisites"]["kind"] == "any"
    assert rules["prerequisites"]["items"][1]["minimum_grade"] == "B-"
    assert rules["corequisites"]["timing"] == "concurrent"
    sources = saved["prerequisites_source"]["sources"]
    assert [item["kind"] for item in sources] == ["subjects", "prerequisites", "corequisites"]
    assert sources[0]["body"] == json.dumps(SUBJECTS)
    assert sources[1]["body"] == await source.prerequisite.text()
    assert sources[2]["body"] == await source.corequisite.text()
    assert all(item["sha256"] and item["fetched_at"] and not item["truncated"] for item in sources)
    assert sources[1]["url"].endswith("/searchResults/getSectionPrerequisites")
    assert sources[2]["url"].endswith("/searchResults/getCorequisites")
    assert saved["prerequisites_latest_attempt"]["rules"] == rules


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_kind", ["unsupported", "corequisite_http", "prerequisite_html"])
async def test_failure_retains_verified_tree_and_evidence_but_records_new_attempt(
    db_session, db_session_factory, source, failure_kind,
):
    source.corequisite = response(EMPTY_COREQUISITES)
    await banner.scrape_subject(db_session, SUBJECT, TERM)
    before = await snapshot(db_session_factory)
    if failure_kind == "unsupported":
        source.prerequisite = response(table(row(), row(subject="", number="", test="Placement", score="80", connector="Or")))
    elif failure_kind == "corequisite_http":
        source.corequisite = response("Synthetic upstream failure", status=503)
    else:
        source.prerequisite = response("<h1>Changed response</h1>")
    await banner.scrape_subject(db_session, SUBJECT, TERM)
    after = await snapshot(db_session_factory)
    for field in ("prerequisites", "prerequisites_rules", "prerequisites_source", "prerequisites_verified_at"):
        assert after[field] == before[field]
    assert after["prerequisites_status"] == ("failed" if failure_kind == "corequisite_http" else "unresolved")
    attempt = after["prerequisites_latest_attempt"]
    assert attempt != before["prerequisites_latest_attempt"]
    assert attempt["status"] == after["prerequisites_status"]
    assert attempt["rules"] is not None
    if failure_kind == "unsupported":
        assert attempt["rules"]["prerequisites"]["items"][1]["kind"] == "unresolved"
        assert "Placement" in attempt["sources"][1]["body"]
    if failure_kind == "corequisite_http":
        assert attempt["sources"][2]["http_status"] == 503


@pytest.mark.asyncio
async def test_unknown_new_rule_never_becomes_verified_empty(db_session, db_session_factory, source):
    source.corequisite = response(EMPTY_COREQUISITES)
    source.prerequisite = response(table(row(subject="Unknown department")))
    await banner.scrape_subject(db_session, SUBJECT, TERM)
    saved = await snapshot(db_session_factory)
    assert saved["prerequisites_status"] == "unresolved"
    assert saved["prerequisites_rules"] is None
    assert saved["prerequisites_source"] is None
    assert saved["prerequisites_verified_at"] is None
    assert saved["prerequisites_latest_attempt"]["rules"]["prerequisites"]["kind"] == "unresolved"


@pytest.mark.asyncio
@pytest.mark.parametrize("body", ["<h1>Broken\x00response</h1>", "é" * (256 * 1024)], ids=["nul-character", "oversized-unicode"])
async def test_unusable_source_can_still_be_recorded_without_losing_known_data(db_session, db_session_factory, source, body):
    from src.schemas.prerequisites import SourceEvidence
    await banner.scrape_subject(db_session, SUBJECT, TERM)
    before = await snapshot(db_session_factory)
    source.prerequisite = response(body)
    await banner.scrape_subject(db_session, SUBJECT, TERM)
    after = await snapshot(db_session_factory)
    assert after["prerequisites_status"] == "unresolved"
    assert after["prerequisites_rules"] == before["prerequisites_rules"]
    assert after["prerequisites_source"] == before["prerequisites_source"]
    evidence = SourceEvidence.model_validate(after["prerequisites_latest_attempt"]["sources"][1])
    assert evidence.sha256 == hashlib.sha256(body.encode("utf-8")).hexdigest()
    assert body.startswith(evidence.text)
    if "\x00" in body:
        assert evidence.text == body
    else:
        assert evidence.truncated


@pytest.mark.asyncio
async def test_rejected_rule_write_cannot_separate_rule_from_evidence(db_session, db_session_factory, source):
    source.corequisite = response(EMPTY_COREQUISITES)
    await banner.scrape_subject(db_session, SUBJECT, TERM)
    before = await snapshot(db_session_factory)
    async with db_session.begin():
        await db_session.execute(text("""
            ALTER TABLE courses ADD CONSTRAINT reject_rule
            CHECK (prerequisites_rules -> 'prerequisites' ->> 'kind' <> 'any')
        """))
    source.prerequisite = response(table(row(), row(connector="Or", number="994")))
    await banner.scrape_subject(db_session, SUBJECT, TERM)
    after = await snapshot(db_session_factory)
    assert after["prerequisites_status"] == "failed"
    for field in ("prerequisites", "prerequisites_rules", "prerequisites_source", "prerequisites_verified_at"):
        assert after[field] == before[field]
    assert after["prerequisites_latest_attempt"]["rules"]["prerequisites"]["kind"] == "any"
