"""Bounded joint prerequisite and corequisite path selection."""
from dataclasses import dataclass

from src.schemas.plan import COURSE_CODE_PATTERN
from src.schemas.prerequisites import AllConditions, AnyConditions, CourseCondition, is_empty
from src.services.flexible_prerequisites import _components
from src.services.prerequisite_checks import evaluate_rule, verified_rules

MAX_TIMING_RULE_NODES = 512
MAX_TIMING_PATHS = 64
MAX_TIMING_SEARCH_STEPS = 2048


class _ExpansionLimit(ValueError):
    pass


def _mixed_choice(rule, *, include_prior=False):
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
    return choice and (non_prior or include_prior)


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


def choose_rule_paths(rows, audit, legacy, start_term, credits, credit_target):
    """Select ordering-only copies while retaining original evidence for checks.

    Joint search activates when prerequisite choices meet corequisite constraints,
    or corequisite choices meet prerequisite constraints. Mixed prerequisite OR
    also activates this stage. Simpler cases retain their existing selectors.
    Each rule tree is a separate decision; do not materialize a cross product.
    """
    rules = {code: rule for code in sorted(legacy) if (rule := verified_rules(rows.get(code, {}))) is not None}
    mixed_codes = {code for code, rule in rules.items() if _mixed_choice(rule.prerequisites)}
    prior_choices = {code for code, rule in rules.items() if _mixed_choice(rule.prerequisites, include_prior=True)}
    coreq_choices = {code for code, rule in rules.items() if _mixed_choice(rule.corequisites, include_prior=True)}
    has_corequisites = any(not is_empty(rule.corequisites) for rule in rules.values())
    has_prerequisites = any(not is_empty(rule.prerequisites) for rule in rules.values()) or any(legacy.values())
    joint = has_corequisites and (bool(prior_choices) or (bool(coreq_choices) and has_prerequisites))
    if not mixed_codes and not joint:
        return rows, []
    active_keys = {(code, 'prerequisites') for code in mixed_codes | (prior_choices if joint else set())}
    if joint:
        active_keys.update((code, 'corequisites') for code in coreq_choices)
    candidates, warnings = {}, []
    for code, rule in rules.items():
        for field in ('prerequisites', 'corequisites'):
            try:
                paths = _paths(getattr(rule, field), [0])
            except (_ExpansionLimit, RecursionError):
                label = 'mixed prerequisite' if field == 'prerequisites' else 'corequisite'
                warnings.append(f'Partial plan: {label} alternatives for {code} exceed the '
                                'supported expansion limit; review their timing.')
                continue
            if paths is None or (field == 'corequisites' and any(
                item.timing != 'concurrent' for path in paths for item in path
            )):
                continue
            options = [_path(path, audit, start_term) for path in paths]
            available = [option for option in options if option.pending <= legacy.keys()]
            options = sorted(available or options, key=lambda option: (
                len(option.pending - legacy.keys()), len(option.pending),
                sum(credits.get(other, 0) for other in option.concurrent | {code}),
                tuple(sorted(condition.model_dump_json() for condition in option.conditions)),
            ))
            # Equivalent timing choices need only one branch; final checks still
            # evaluate every original grade/section alternative.
            unique = {}
            for option in options:
                unique.setdefault((option.strict, option.flexible, option.concurrent), option)
            candidates[(code, field)] = list(unique.values())

    if not active_keys & candidates.keys():
        return rows, warnings

    codes = sorted(legacy)
    indices = {code: index for index, code in enumerate(codes)}
    historical = set(audit.completed_courses + audit.in_progress_courses)
    fixed_strict = [{indices[other] for other in legacy[code] if other in indices and other not in historical}
                    if (code, 'prerequisites') not in candidates else set() for code in codes]
    fixed = {key: options[0] for key, options in candidates.items() if len(options) == 1}
    choices = sorted((key for key in candidates if len(candidates[key]) > 1),
                     key=lambda key: (len(candidates[key]), key))
    steps = 0

    def compatible(picked, key, allow_oversized):
        strict = [set(edges) for edges in fixed_strict]
        graph = [set(edges) for edges in strict]
        for (owner, _), option in (fixed | picked).items():
            index = indices[owner]
            strict[index].update(indices[other] for other in option.strict if other in indices)
            graph[index].update(strict[index])
            graph[index].update(indices[other] for other in option.flexible | option.concurrent if other in indices)
            for other in option.concurrent:
                if other in indices:
                    graph[indices[other]].add(index)
        group = next(group for group in _components(graph) if indices[key[0]] in group)
        # Each added relation touches its course owner. A newly formed timing
        # conflict includes that owner; unrelated fixed conflicts stay diagnostic.
        if any(strict[index] & group for index in group):
            return False
        return allow_oversized or round(sum(credits.get(codes[index], 0) for index in group), 2) <= credit_target

    def search(allow_oversized):
        nonlocal steps
        picked, position = {}, 0
        next_option = [0] * len(choices)
        while 0 <= position < len(choices):
            key = choices[position]
            if next_option[position] == len(candidates[key]):
                next_option[position] = 0
                picked.pop(key, None)
                position -= 1
                continue
            if steps >= MAX_TIMING_SEARCH_STEPS:
                return None, True
            picked[key] = candidates[key][next_option[position]]
            next_option[position] += 1
            steps += 1
            if compatible(picked, key, allow_oversized):
                position += 1
            else:
                picked.pop(key, None)
        return (picked if position == len(choices) else None), False

    picked, limited = search(False)
    if picked is None and not limited:
        picked, limited = search(True)
    selected = {key: options[0] for key, options in candidates.items()}
    if picked is not None:
        selected.update(picked)
    elif choices:
        reason = 'reached the search limit' if limited else 'could not avoid a prior-course timing conflict'
        label = 'joint prerequisite/corequisite' if joint else 'mixed prerequisite'
        warnings.append(f'Partial plan: {label} alternative selection for '
                        + ', '.join(sorted({key[0] for key in choices}))
                        + f' {reason}. Review the proposed timing.')

    effective, selected_rules = dict(rows), dict(rules)
    for (code, field), option in selected.items():
        # Update both fields on the same temporary rule, so one selected path
        # cannot overwrite the other. Original rows remain unchanged.
        selected_rules[code] = selected_rules[code].model_copy(update={field: AllConditions(items=list(option.conditions))})
    for code in {key[0] for key in selected}:
        effective[code] = dict(rows[code], prerequisites_rules=selected_rules[code].model_dump())
    return effective, warnings
