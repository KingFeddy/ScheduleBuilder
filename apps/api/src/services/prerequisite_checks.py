"""Diagnose a proposed plan against recorded rules without certifying registration.

Course selection/packing remain separate. Missing evidence, future completion and
source applicability stay explicit; legacy arrays cannot establish complete rules.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Literal

from pydantic import ValidationError
from sqlalchemy import text

from src.schemas.plan import COURSE_CODE_PATTERN, ParsedDegreeValidated
from src.schemas.prerequisites import (
    AllConditions, AnyConditions, CourseCondition, PrerequisiteRules, Rule, UnresolvedCondition, is_empty,
)

# Published NJIT undergraduate ordering. Other imported +/- variants remain
# unknown rather than borrowing an unverified external grading scale.
# https://www.njit.edu/registrar/grading-instructions
_GRADES = {grade: rank for rank, grade in enumerate(('F', 'D', 'C', 'C+', 'B', 'B+', 'A'))}


@dataclass(frozen=True)
class RuleEvaluation:
    status: Literal['satisfied', 'unmet', 'unknown']
    detail: str = ''


def _combine(results: list[RuleEvaluation], *, alternative=False) -> RuleEvaluation:
    if alternative:
        if any(result.status == 'satisfied' for result in results):
            return RuleEvaluation('satisfied')
        status = 'unknown' if any(result.status == 'unknown' for result in results) else 'unmet'
        return RuleEvaluation(status, 'one of (' + ' OR '.join(dict.fromkeys(r.detail for r in results)) + ')')
    if all(result.status == 'satisfied' for result in results):
        return RuleEvaluation('satisfied')
    status = 'unmet' if any(result.status == 'unmet' for result in results) else 'unknown'
    return RuleEvaluation(status, '; '.join(dict.fromkeys(r.detail for r in results if r.status != 'satisfied')))


def _term(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if re.fullmatch(r'(19|20|21)[0-9]{2}(10|50|90)', value):
        return value
    match = re.fullmatch(r'(Spring|Summer|Fall)\s+((?:19|20|21)[0-9]{2})', value, re.IGNORECASE)
    return match[2] + {'spring':'10', 'summer':'50', 'fall':'90'}[match[1].lower()] if match else None


def _timing(code: str, timing: str, observed_term: str | None, target_term: str) -> RuleEvaluation:
    if timing == 'unspecified' or observed_term is None:
        return RuleEvaluation('unknown', f'{code}: course timing is not established')
    fits = {'prior': observed_term < target_term, 'prior_or_concurrent': observed_term <= target_term,
            'concurrent': observed_term == target_term}[timing]
    if fits:
        return RuleEvaluation('satisfied')
    required = {'prior':'an earlier semester', 'prior_or_concurrent':'this semester or earlier',
                'concurrent':'the same semester'}[timing]
    return RuleEvaluation('unmet', f'{code}: requires {required}')


def _course(rule: CourseCondition, audit: ParsedDegreeValidated, planned: dict[str, str], target: str) -> RuleEvaluation:
    code = rule.course_code
    if not re.fullmatch(COURSE_CODE_PATTERN, code):
        return RuleEvaluation('unknown', 'unsupported course identity in recorded rule')
    attempts = [attempt for attempt in (audit.course_attempts or []) if attempt.course_code == code]
    possibilities = []
    for attempt in attempts:
        if attempt.status in {'failed', 'withdrawn', 'audit'}:
            grade = RuleEvaluation('unmet', f'{code}: no successful completion in this attempt')
        elif attempt.status in {'in_progress', 'incomplete'}:
            grade = RuleEvaluation('unknown', f'{code}: completion and final grade are not confirmed')
        elif rule.minimum_grade:
            if rule.minimum_grade not in _GRADES or attempt.grade not in _GRADES:
                grade = RuleEvaluation('unknown', f'{code}: grade evidence cannot confirm minimum {rule.minimum_grade}')
            elif _GRADES[attempt.grade] < _GRADES[rule.minimum_grade]:
                grade = RuleEvaluation('unmet', f'{code}: recorded grade {attempt.grade} is below minimum {rule.minimum_grade}')
            else:
                grade = RuleEvaluation('satisfied')
        elif attempt.status in {'passed', 'transfer'}:
            grade = RuleEvaluation('satisfied')
        else:
            grade = RuleEvaluation('unknown', f'{code}: completion evidence is incomplete')
        possibilities.append(_combine([grade, _timing(code, rule.timing, _term(attempt.term), target)]))
    if code in planned:
        possibilities.append(_combine([
            _timing(code, rule.timing, _term(planned[code]), target),
            RuleEvaluation('unknown', f'{code}: depends on successful completion of planned coursework'
                           + (f' with minimum grade {rule.minimum_grade}' if rule.minimum_grade else '')),
        ]))
    if not attempts and code in set(audit.completed_courses + audit.in_progress_courses):
        possibilities.append(RuleEvaluation('unknown', f'{code}: summary history has no verified grade/term evidence'))
    if not possibilities:
        result = RuleEvaluation('unmet', f'{code}: no completion or scheduled coursework is recorded')
    else:
        # A later failed retake does not erase a qualifying earlier attempt.
        result = _combine(possibilities, alternative=True) if len(possibilities) > 1 else possibilities[0]
    checks = [result]
    if rule.level:
        checks.append(RuleEvaluation('unknown', f'{code}: academic level {rule.level} cannot be checked from this audit'))
    if rule.required_crn:
        checks.append(RuleEvaluation('unknown', f'{code}: required section {rule.required_crn} has not been selected'))
    return _combine(checks)


def evaluate_rule(rule: Rule, audit: ParsedDegreeValidated, planned: dict[str, str], target_term: str) -> RuleEvaluation:
    """Tri-state AND/OR evaluation; future grades never become successful outcomes."""
    if _term(target_term) != target_term:
        return RuleEvaluation('unknown', 'unsupported target semester')
    if isinstance(rule, UnresolvedCondition):
        return RuleEvaluation('unknown', 'recorded condition is unresolved')
    if isinstance(rule, CourseCondition):
        return _course(rule, audit, planned, target_term)
    return _combine([evaluate_rule(child, audit, planned, target_term) for child in rule.items],
                    alternative=isinstance(rule, AnyConditions))


async def load_prerequisite_rows(session, codes) -> dict[str, dict]:
    """Share one rule snapshot between ordering and final diagnostics."""
    if not codes:
        return {}
    result = await session.execute(text(
        'SELECT course_code, prerequisites_status, prerequisites_rules FROM courses WHERE course_code = ANY(:codes)'
    ), {'codes': sorted(set(codes))})
    return {row['course_code']: row for row in result.mappings()}


def verified_rules(row) -> PrerequisiteRules | None:
    if row.get('prerequisites_status') not in {'verified', 'verified_empty'}:
        return None
    try:
        raw = row.get('prerequisites_rules')
        rules = PrerequisiteRules.model_validate(json.loads(raw) if isinstance(raw, str) else raw)
        if (_term(rules.scope.term) != rules.scope.term or not re.fullmatch(r'[0-9]{5}', rules.scope.crn)
                or (row['prerequisites_status'] == 'verified_empty'
                    and not (is_empty(rules.prerequisites) and is_empty(rules.corequisites)))):
            return None
        return rules
    except (ValidationError, ValueError, TypeError, RecursionError):
        return None


def prior_course_ordering(rows, audit, legacy, start_term):
    """Use complete AND-only prior-course trees without flattening OR/concurrency.

    Return dependency codes and the independently verified history for each
    supported course. Empty history must override legacy completed/IP summaries.
    Grades for planned prerequisites remain conditional in the final diagnostics.
    """
    def leaves(rule):
        if isinstance(rule, CourseCondition):
            return [rule] if rule.timing == 'prior' and COURSE_CODE_PATTERN.fullmatch(rule.course_code) else None
        if isinstance(rule, AllConditions):
            result = []
            for child in rule.items:
                values = leaves(child)
                if values is None:
                    return None
                result.extend(values)
            return result
        return None

    dependencies, history = dict(legacy), {}
    for code in legacy:
        rules = verified_rules(rows.get(code, {}))
        if rules is None:
            continue
        try:
            conditions = leaves(rules.prerequisites)
            if conditions is None:
                continue
            by_code = {}
            for rule in conditions:
                by_code.setdefault(rule.course_code, []).append(rule)
            dependencies[code] = list(by_code)
            history[code] = {prereq for prereq, requirements in by_code.items()
                             if all(evaluate_rule(rule, audit, {}, start_term).status == 'satisfied'
                                    for rule in requirements)}
        except RecursionError:
            continue
    return dependencies, history


async def check_plan_prerequisites(session, audit: ParsedDegreeValidated, planned: dict[str, str], *, rows=None) -> list[str]:
    """Use supplied ordering evidence, or load once when called independently."""
    if not planned:
        return []
    if rows is None:
        rows = await load_prerequisite_rows(session, planned)
    warnings, unavailable, other_term = [], [], []
    for code, term in sorted(planned.items()):
        row = rows.get(code, {})
        rules = verified_rules(row)
        if rules is None:
            unavailable.append(code)
            continue
        try:
            same_term = rules.scope.term == term
            if not same_term:
                other_term.append(code)
            for label, rule in [('prerequisite', rules.prerequisites), ('corequisite', rules.corequisites)]:
                checked = evaluate_rule(rule, audit, planned, term)
                if checked.status == 'satisfied':
                    continue
                outcome = 'conflict with recorded rules' if checked.status == 'unmet' and same_term else 'needs review'
                warnings.append(f'{code}: {label} {outcome} — {checked.detail}.')
        except (ValidationError, ValueError, TypeError, RecursionError):
            unavailable.append(code)
    if unavailable:
        warnings.append('Prerequisite and corequisite rules are unavailable or unverified for: '
                        + ', '.join(unavailable) + '. Missing rule data does not establish that a course has no requirements.')
    if other_term:
        warnings.append('Recorded rules for ' + ', '.join(other_term)
                        + ' come from a different term; confirm which rules apply to the planned semester.')
    return warnings
