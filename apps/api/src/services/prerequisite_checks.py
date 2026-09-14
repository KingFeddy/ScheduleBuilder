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


MAX_PRIOR_ALTERNATIVES = 64
MAX_PRIOR_RULE_NODES = 512
MAX_PRIOR_SEARCH_STEPS = 2048


class _PriorAlternativeLimit(ValueError):
    pass


def prior_course_ordering(rows, audit, legacy, start_term):
    """Choose bounded, deterministic AND/OR prior-course paths among selections.

    Prefer verified history, then selected coursework. Never add a course or
    flatten a concurrency/unsupported condition. Search only controls prior edges;
    actual credit-aware packing and conditional-grade diagnostics remain separate.
    """
    def alternatives(rule, visited):
        visited[0] += 1
        if visited[0] > MAX_PRIOR_RULE_NODES:
            raise _PriorAlternativeLimit
        if isinstance(rule, CourseCondition):
            return [[rule]] if rule.timing == 'prior' and COURSE_CODE_PATTERN.fullmatch(rule.course_code) else None
        if not isinstance(rule, (AllConditions, AnyConditions)):
            return None
        groups = []
        for child in rule.items:
            values = alternatives(child, visited)
            if values is None:
                return None
            groups.append(values)
        result = [] if isinstance(rule, AnyConditions) else [[]]
        for values in groups:
            if isinstance(rule, AnyConditions):
                if len(result) + len(values) > MAX_PRIOR_ALTERNATIVES:
                    raise _PriorAlternativeLimit
                result.extend(values)
            else:
                if len(result) * len(values) > MAX_PRIOR_ALTERNATIVES:
                    raise _PriorAlternativeLimit
                result = [left + right for left in result for right in values]
        return result

    dependencies, history, warnings, candidates = dict(legacy), {}, [], {}
    for code in sorted(legacy):
        rules = verified_rules(rows.get(code, {}))
        if rules is None:
            continue
        try:
            paths = alternatives(rules.prerequisites, [0])
            if paths is None:
                continue
            options = []
            for conditions in paths:
                by_code = {}
                for rule in conditions:
                    by_code.setdefault(rule.course_code, []).append(rule)
                satisfied = frozenset(prereq for prereq, requirements in by_code.items()
                                      if all(evaluate_rule(rule, audit, {}, start_term).status == 'satisfied'
                                             for rule in requirements))
                candidate = (tuple(sorted(by_code)), satisfied)
                if candidate not in options:
                    options.append(candidate)
            complete = [option for option in options
                        if all(prereq in legacy or prereq in option[1] for prereq in option[0])]
            # A missing-course escape must not prevent backtracking to an
            # available path elsewhere in the graph. Keep incomplete choices only
            # when this course has no path fully backed by history/selections.
            options = complete or options
            options.sort(key=lambda option: (
                sum(prereq not in legacy and prereq not in option[1] for prereq in option[0]),
                sum(prereq not in option[1] for prereq in option[0]), option[0],
            ))
            candidates[code] = options
            dependencies[code], history[code] = list(options[0][0]), set(options[0][1])
        except (_PriorAlternativeLimit, RecursionError):
            warnings.append(f'Partial plan: prerequisite alternatives for {code} exceed the supported expansion limit; review their ordering.')

    # Fixed rules and legacy edges constrain the search. Unassigned OR nodes have
    # no outgoing edges yet, so a later assignment can trigger backtracking.
    legacy_history = set(audit.completed_courses + audit.in_progress_courses)
    graph = {code: {prereq for prereq in deps if prereq in legacy
                    and prereq not in history.get(code, legacy_history)}
             for code, deps in dependencies.items()}
    choices = sorted((code for code, options in candidates.items() if len(options) > 1),
                     key=lambda code: (len(candidates[code]), code))
    for code in choices:
        graph[code] = set()
    selected = {}

    def closes_cycle(code):
        pending, seen = list(graph[code]), set()
        while pending:
            current = pending.pop()
            if current == code:
                return True
            if current not in seen:
                seen.add(current)
                pending.extend(graph.get(current, ()))
        return False

    # Iterative backtracking avoids recursion limits on large elective lists.
    position, steps, limited = 0, 0, False
    next_option = [0] * len(choices)
    while 0 <= position < len(choices):
        code = choices[position]
        if next_option[position] == len(candidates[code]):
            next_option[position] = 0
            graph[code] = set()
            selected.pop(code, None)
            position -= 1
            continue
        if steps >= MAX_PRIOR_SEARCH_STEPS:
            limited = True
            break
        option = candidates[code][next_option[position]]
        next_option[position] += 1
        steps += 1
        graph[code] = {prereq for prereq in option[0] if prereq in legacy and prereq not in option[1]}
        if closes_cycle(code):
            graph[code] = set()
            selected.pop(code, None)
            continue
        selected[code] = option
        position += 1

    if position == len(choices):
        for code, option in selected.items():
            dependencies[code], history[code] = list(option[0]), set(option[1])
    elif choices:
        reason = ('reached the search limit' if limited else 'could not avoid a prerequisite cycle')
        warnings.append('Partial plan: prerequisite alternative selection for ' + ', '.join(choices)
                        + f' {reason}. Review the proposed ordering.')
    return dependencies, history, warnings


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
