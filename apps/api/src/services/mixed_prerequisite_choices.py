"""Bounded prerequisite path selection across explicit timing modes."""
from dataclasses import dataclass

from src.schemas.plan import COURSE_CODE_PATTERN
from src.schemas.prerequisites import AllConditions, AnyConditions, CourseCondition
from src.services.flexible_prerequisites import _components
from src.services.prerequisite_checks import evaluate_rule, verified_rules

MAX_TIMING_RULE_NODES = 512
MAX_TIMING_PATHS = 64
MAX_TIMING_SEARCH_STEPS = 2048


class _ExpansionLimit(ValueError):
    pass


def _mixed_choice(rule):
    pending, count, choice, non_prior = [rule], 0, False, False
    while pending:
        node = pending.pop()
        count += 1
        if count > MAX_TIMING_RULE_NODES:
            return True  # Let the bounded expander report unsupported size.
        if isinstance(node, CourseCondition):
            non_prior |= node.timing in {'concurrent', 'prior_or_concurrent'}
        elif isinstance(node, (AllConditions, AnyConditions)):
            choice |= isinstance(node, AnyConditions)
            pending.extend(node.items)
    return choice and non_prior


def _paths(rule, visited):
    visited[0] += 1
    if visited[0] > MAX_TIMING_RULE_NODES:
        raise _ExpansionLimit
    if isinstance(rule, CourseCondition):
        if rule.timing in {'prior', 'prior_or_concurrent', 'concurrent'} and COURSE_CODE_PATTERN.fullmatch(rule.course_code):
            return [(rule,)]
        return None
    if not isinstance(rule, (AllConditions, AnyConditions)):
        return None
    result = [] if isinstance(rule, AnyConditions) else [()]
    for child in rule.items:
        values = _paths(child, visited)
        if values is None:
            return None
        if isinstance(rule, AnyConditions):
            if len(result) + len(values) > MAX_TIMING_PATHS:
                raise _ExpansionLimit
            result.extend(values)
        else:
            if len(result) * len(values) > MAX_TIMING_PATHS:
                raise _ExpansionLimit
            result = [left + right for left in result for right in values]
    return result


@dataclass(frozen=True)
class _Path:
    conditions: tuple[CourseCondition, ...]
    strict: frozenset[str]
    flexible: frozenset[str]
    concurrent: frozenset[str]

    @property
    def pending(self):
        return self.strict | self.flexible | self.concurrent


def _path(conditions, audit, start_term):
    by_code = {}
    for rule in conditions:
        by_code.setdefault(rule.course_code, []).append(rule)
    strict, flexible, concurrent = set(), set(), set()
    for code, rules in by_code.items():
        earlier = [rule for rule in rules if rule.timing != 'concurrent']
        if any(rule.timing == 'concurrent' for rule in rules):
            concurrent.add(code)
        if earlier and not all(evaluate_rule(rule, audit, {}, start_term).status == 'satisfied' for rule in earlier):
            if any(rule.timing == 'prior' for rule in earlier):
                strict.add(code)
            elif code not in concurrent:
                flexible.add(code)
    return _Path(conditions, frozenset(strict), frozenset(flexible), frozenset(concurrent))


def choose_mixed_prerequisite_paths(rows, audit, legacy, start_term, credits, credit_target):
    """Return ordering-only rule copies; final diagnostics must use original rows.

    Activated for mixed-timing OR trees. Include supported prior-only choices so
    earlier choices can be retried. Fixed corequisite groups constrain search;
    corequisite OR remains a later separate selection stage.
    """
    rules = {code: rule for code in sorted(legacy) if (rule := verified_rules(rows.get(code, {}))) is not None}
    mixed_codes = {code for code, rule in rules.items() if _mixed_choice(rule.prerequisites)}
    if not mixed_codes:
        return rows, []
    candidates, warnings = {}, []
    for code, rule in rules.items():
        try:
            paths = _paths(rule.prerequisites, [0])
        except (_ExpansionLimit, RecursionError):
            warnings.append(f'Partial plan: mixed prerequisite alternatives for {code} exceed the '
                            'supported expansion limit; review their timing.')
            continue
        if paths is None:
            continue
        options = [_path(path, audit, start_term) for path in paths]
        available = [option for option in options if option.pending <= legacy.keys()]
        options = sorted(available or options, key=lambda option: (
            len(option.pending - legacy.keys()), len(option.pending),
            sum(credits.get(other, 0) for other in option.concurrent | {code}),
            tuple(sorted(condition.model_dump_json() for condition in option.conditions)),
        ))
        # Equivalent scheduling constraints need only one search branch. Original
        # grade/section alternatives remain in the final diagnostic snapshot.
        unique = {}
        for option in options:
            unique.setdefault((option.strict, option.flexible, option.concurrent), option)
        candidates[code] = list(unique.values())

    if not mixed_codes & candidates.keys():
        return rows, warnings

    codes = sorted(legacy)
    indices = {code: index for index, code in enumerate(codes)}
    historical = set(audit.completed_courses + audit.in_progress_courses)
    fixed_strict = [{indices[other] for other in legacy[code] if other in indices and other not in historical}
                    if code not in candidates else set() for code in codes]
    fixed_same = [set() for _ in codes]
    for code, rule in rules.items():
        try:
            paths = _paths(rule.corequisites, [0])
        except (_ExpansionLimit, RecursionError):
            continue
        if paths is None or len(paths) != 1 or any(item.timing != 'concurrent' for item in paths[0]):
            continue
        for item in paths[0]:
            if item.course_code in indices:
                fixed_same[indices[code]].add(indices[item.course_code])
                fixed_same[indices[item.course_code]].add(indices[code])

    fixed = {code: options[0] for code, options in candidates.items() if len(options) == 1}
    choices = sorted((code for code in candidates if len(candidates[code]) > 1),
                     key=lambda code: (len(candidates[code]), code))
    steps = 0

    def compatible(picked, code, allow_oversized):
        strict = [set(edges) for edges in fixed_strict]
        graph = [left | right for left, right in zip(strict, fixed_same)]
        for owner, option in (fixed | picked).items():
            index = indices[owner]
            strict[index].update(indices[other] for other in option.strict if other in indices)
            graph[index].update(strict[index])
            graph[index].update(indices[other] for other in option.flexible | option.concurrent if other in indices)
            for other in option.concurrent:
                if other in indices:
                    graph[indices[other]].add(index)
        group = next(group for group in _components(graph) if indices[code] in group)
        # Any cycle created by this path passes through its owner. Unrelated fixed
        # contradictions remain under existing partial-plan handling.
        if any(strict[index] & group for index in group):
            return False
        return allow_oversized or round(sum(credits.get(codes[index], 0) for index in group), 2) <= credit_target

    def search(allow_oversized):
        nonlocal steps
        picked, position = {}, 0
        next_option = [0] * len(choices)
        while 0 <= position < len(choices):
            code = choices[position]
            if next_option[position] == len(candidates[code]):
                next_option[position] = 0
                picked.pop(code, None)
                position -= 1
                continue
            if steps >= MAX_TIMING_SEARCH_STEPS:
                return None, True
            picked[code] = candidates[code][next_option[position]]
            next_option[position] += 1
            steps += 1
            if compatible(picked, code, allow_oversized):
                position += 1
            else:
                picked.pop(code, None)
        return (picked if position == len(choices) else None), False

    picked, limited = search(False)
    if picked is None and not limited:
        picked, limited = search(True)
    selected = {code: options[0] for code, options in candidates.items()}
    if picked is not None:
        selected.update(picked)
    elif choices:
        reason = 'reached the search limit' if limited else 'could not avoid a timing conflict'
        warnings.append('Partial plan: mixed prerequisite alternative selection for ' + ', '.join(choices)
                        + f' {reason}. Review the proposed timing.')

    effective = dict(rows)
    for code, option in selected.items():
        # These copies select scheduling paths only. Do not write them to storage
        # or replace the evidence used by post-generation checks.
        selected_rule = rules[code].model_copy(update={'prerequisites': AllConditions(items=list(option.conditions))})
        effective[code] = dict(rows[code], prerequisites_rules=selected_rule.model_dump())
    return effective, warnings
