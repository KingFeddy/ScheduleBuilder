"""Mandatory prior-or-concurrent constraints for selected degree-plan courses."""
from src.schemas.plan import COURSE_CODE_PATTERN
from src.schemas.prerequisites import AllConditions, CourseCondition
from src.services.prerequisite_checks import evaluate_rule, verified_rules

MAX_FLEXIBLE_RULE_NODES = 512


class _RuleLimit(ValueError):
    pass


def flexible_course_ordering(rows, audit, legacy, start_term):
    """Replace complete mixed AND trees with strict and same-or-before edges.

    Every repeated condition must be established before history discharges a
    course. Unknown trees and OR choices retain the existing fallback behavior.
    """
    def leaves(rule, visited):
        visited[0] += 1
        if visited[0] > MAX_FLEXIBLE_RULE_NODES:
            raise _RuleLimit
        if isinstance(rule, CourseCondition):
            if rule.timing in {'prior', 'prior_or_concurrent'} and COURSE_CODE_PATTERN.fullmatch(rule.course_code):
                return [rule]
            return None
        if not isinstance(rule, AllConditions):
            return None
        result = []
        for child in rule.items:
            values = leaves(child, visited)
            if values is None:
                return None
            result.extend(values)
        return result

    dependencies, history, flexible, warnings = dict(legacy), {}, {}, []
    for code in sorted(legacy):
        rules = verified_rules(rows.get(code, {}))
        if rules is None:
            continue
        try:
            conditions = leaves(rules.prerequisites, [0])
        except (_RuleLimit, RecursionError):
            warnings.append(f'Partial plan: prior-or-concurrent rule processing for {code} '
                            'exceeds the supported expansion limit; review its ordering.')
            continue
        if conditions is None or not any(rule.timing == 'prior_or_concurrent' for rule in conditions):
            continue
        by_code = {}
        for rule in conditions:
            by_code.setdefault(rule.course_code, []).append(rule)
        history[code] = {other for other, requirements in by_code.items()
                         if all(evaluate_rule(rule, audit, {}, start_term).status == 'satisfied'
                                for rule in requirements)}
        strict = {other for other, requirements in by_code.items()
                  if any(rule.timing == 'prior' for rule in requirements)}
        dependencies[code] = sorted(strict)
        flexible[code] = set(by_code) - strict - history[code]
        missing = sorted(set(by_code) - history[code] - legacy.keys())
        if missing:
            warnings.append(f'Partial plan: prior-or-concurrent requirements for {code} lack qualifying history '
                            'or selected coursework for ' + ', '.join(missing) + '. Review grades and course choices.')
    return dependencies, history, flexible, warnings


def _components(graph):
    """Iterative strongly connected components, including isolated courses."""
    seen, order = set(), []
    for start in range(len(graph)):
        if start in seen:
            continue
        seen.add(start)
        stack = [(start, iter(sorted(graph[start])))]
        while stack:
            node, children = stack[-1]
            child = next(children, None)
            if child is None:
                order.append(node)
                stack.pop()
            elif child not in seen:
                seen.add(child)
                stack.append((child, iter(sorted(graph[child]))))
    reverse = [set() for _ in graph]
    for node, children in enumerate(graph):
        for child in children:
            reverse[child].add(node)
    seen, result = set(), []
    for start in reversed(order):
        if start in seen:
            continue
        group, pending = set(), [start]
        seen.add(start)
        while pending:
            node = pending.pop()
            group.add(node)
            for child in reverse[node] - seen:
                seen.add(child)
                pending.append(child)
        result.append(frozenset(group))
    return result


def prepare_flexible_groups(resolved, depends_on, flexible, concurrent_groups, credit_target):
    """Collapse mutual same-or-before edges; report contradictions with prior edges.

    In a cycle, all courses must share a semester unless a strict edge makes the
    cycle impossible. Remove flexible edges inside contradictory components from
    the fallback proposal, retaining strict ordering and existing corequisites.
    """
    by_code = {item.course_code: index for index, item in enumerate(resolved) if item.course_code}
    same_or_before = [set() for _ in resolved]
    for code, requirements in flexible.items():
        same_or_before[by_code[code]] = {by_code[other] for other in requirements if other in by_code}
    if not any(same_or_before):
        return same_or_before, concurrent_groups, []

    def graph():
        return [depends_on[index] | same_or_before[index] | (set(concurrent_groups.get(index, ())) - {index})
                for index in range(len(resolved))]

    warnings = []
    for group in _components(graph()):
        if any(depends_on[index] & group for index in group):
            codes = ', '.join(sorted(resolved[index].course_code for index in group))
            warnings.append(f'Partial plan: prior-or-concurrent requirements for {codes} conflict with '
                            'strict prior-course ordering. Review the separate proposals and recorded rules.')
            for index in group:
                same_or_before[index] -= group

    groups = dict(concurrent_groups)
    for group in _components(graph()):
        if len(group) < 2:
            continue
        credits = round(sum(resolved[index].credits for index in group), 2)
        existing = all(concurrent_groups.get(index) == group for index in group)
        if credits > credit_target and not existing:
            codes = ', '.join(sorted(resolved[index].course_code for index in group))
            warnings.append(f'Partial plan: concurrent prerequisite group {codes} needs {credits:g} credits '
                            f'together, above your {credit_target}-credit target. Review the target or course choices.')
        for index in group:
            groups[index] = group
    return same_or_before, groups, warnings
