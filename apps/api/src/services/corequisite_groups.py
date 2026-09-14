"""Choose concurrent corequisite paths and group already selected courses."""
from src.schemas.plan import COURSE_CODE_PATTERN
from src.schemas.prerequisites import AllConditions, AnyConditions, CourseCondition
from src.services.prerequisite_checks import verified_rules

MAX_COREQUISITE_ALTERNATIVES = 64
MAX_COREQUISITE_RULE_NODES = 512
MAX_COREQUISITE_SEARCH_STEPS = 2048


class _AlternativeLimit(ValueError):
    pass


def _alternatives(rule, visited):
    """Expand complete concurrent trees; no unsupported OR branch is discarded."""
    visited[0] += 1
    if visited[0] > MAX_COREQUISITE_RULE_NODES:
        raise _AlternativeLimit
    if isinstance(rule, CourseCondition):
        if rule.timing == 'concurrent' and COURSE_CODE_PATTERN.fullmatch(rule.course_code):
            return [frozenset({rule.course_code})]
        return None
    if not isinstance(rule, (AllConditions, AnyConditions)):
        return None
    result = [] if isinstance(rule, AnyConditions) else [frozenset()]
    for child in rule.items:
        values = _alternatives(child, visited)
        if values is None:
            return None
        if isinstance(rule, AnyConditions):
            if len(result) + len(values) > MAX_COREQUISITE_ALTERNATIVES:
                raise _AlternativeLimit
            result.extend(values)
        else:
            if len(result) * len(values) > MAX_COREQUISITE_ALTERNATIVES:
                raise _AlternativeLimit
            result = [left | right for left in result for right in values]
    return list(dict.fromkeys(result))


def _root(parent, index):
    while parent[index] != index:
        parent[index] = parent[parent[index]]
        index = parent[index]
    return index


def _join(parent, index, others):
    for other in others:
        left, right = _root(parent, index), _root(parent, other)
        parent[max(left, right)] = min(left, right)


def _graph(parent, depends_on):
    graph = {_root(parent, index): set() for index in range(len(parent))}
    for index, prerequisites in enumerate(depends_on):
        graph[_root(parent, index)].update(_root(parent, other) for other in prerequisites)
    return graph


def _closes_cycle(graph, group):
    # Contracting a DAG can create a strict-prior cycle even through an outside
    # course (A with C while A -> B -> C). Check the entire reachable path.
    pending, seen = list(graph[group]), set()
    while pending:
        current = pending.pop()
        if current == group:
            return True
        if current not in seen:
            seen.add(current)
            pending.extend(graph[current])
    return False


def corequisite_groups(rows, resolved, depends_on, credit_target):
    """Choose bounded AND/OR concurrent paths without adding courses.

    Search selected paths for compatible prior ordering and credit limits. If
    only oversized groups work, keep them with target notices. Unresolved choices
    retain deterministic partial proposals and the final rule diagnostics.
    """
    by_code = {item.course_code: i for i, item in enumerate(resolved) if item.course_code}
    warnings, candidates = [], {}
    for code in sorted(by_code):
        rules = verified_rules(rows.get(code, {}))
        if rules is None:
            continue
        try:
            options = _alternatives(rules.corequisites, [0])
        except (_AlternativeLimit, RecursionError):
            warnings.append(f'Partial plan: corequisite alternatives for {code} exceed the supported '
                            'expansion limit; review their grouping.')
            continue
        if options is None:
            continue
        # Do not use a missing-course escape before retrying available choices
        # elsewhere. Missing paths are kept only if every path lacks selections.
        complete = [option for option in options if option <= by_code.keys()]
        candidates[code] = sorted(complete or options, key=lambda option: (
            len(option - by_code.keys()),
            sum(resolved[by_code[other]].credits for other in option | {code} if other in by_code),
            tuple(sorted(option)),
        ))

    selected = {code: options[0] for code, options in candidates.items()}
    base_parent = list(range(len(resolved)))
    for code, options in candidates.items():
        if len(options) == 1:
            _join(base_parent, by_code[code], (by_code[other] for other in options[0] if other in by_code))
    choices = sorted((code for code, options in candidates.items() if len(options) > 1),
                     key=lambda code: (len(candidates[code]), code))
    steps = 0

    def search(allow_oversized):
        nonlocal steps
        # Each level owns a union snapshot. Rejected choices cannot leak unions
        # into later branches. Fixed unrelated conflicts remain final diagnostics.
        parents, picked = [base_parent.copy()], {}
        next_option = [0] * len(choices)
        position = 0
        while 0 <= position < len(choices):
            code = choices[position]
            if next_option[position] == len(candidates[code]):
                next_option[position] = 0
                picked.pop(code, None)
                position -= 1
                continue
            if steps >= MAX_COREQUISITE_SEARCH_STEPS:
                return None, True
            option = candidates[code][next_option[position]]
            next_option[position] += 1
            steps += 1
            parent = parents[position].copy()
            _join(parent, by_code[code], (by_code[other] for other in option if other in by_code))
            group = _root(parent, by_code[code])
            if _closes_cycle(_graph(parent, depends_on), group):
                continue
            credits = round(sum(item.credits for index, item in enumerate(resolved)
                                if _root(parent, index) == group), 2)
            if not allow_oversized and credits > credit_target:
                continue
            picked[code] = option
            position += 1
            if len(parents) <= position:
                parents.append(parent)
            else:
                parents[position] = parent
        return (picked if position == len(choices) else None), False

    picked, limited = search(allow_oversized=False)
    if picked is None and not limited:
        picked, limited = search(allow_oversized=True)
    if picked is not None:
        selected.update(picked)
    elif choices:
        reason = 'reached the search limit' if limited else 'could not avoid a prior-course conflict'
        warnings.append('Partial plan: corequisite alternative selection for ' + ', '.join(choices)
                        + f' {reason}. Review the proposed grouping.')

    parent = list(range(len(resolved)))
    for code, required in selected.items():
        missing = sorted(required - by_code.keys())
        if missing:
            warnings.append(f'Partial plan: the chosen corequisite path for {code} needs '
                            + ', '.join(missing) + ' that is not selected. Review the course choices.')
        _join(parent, by_code[code], (by_code[other] for other in required if other in by_code))

    members = {}
    for index in range(len(resolved)):
        members.setdefault(_root(parent, index), set()).add(index)
    graph = _graph(parent, depends_on)
    groups = {}
    for group, indices in sorted(members.items()):
        if len(indices) < 2:
            continue
        codes = ', '.join(sorted(resolved[index].course_code for index in indices))
        if _closes_cycle(graph, group):
            warnings.append(f'Partial plan: corequisite grouping for {codes} conflicts with prior-course ordering. '
                            'These courses remain separate proposals for review.')
            continue
        credits = round(sum(resolved[index].credits for index in indices), 2)
        if credits > credit_target:
            warnings.append(f'Partial plan: corequisite group {codes} needs {credits:g} credits together, '
                            f'above your {credit_target}-credit target. Review the target or course choices.')
        for index in indices:
            groups[index] = frozenset(indices)
    return groups, warnings
