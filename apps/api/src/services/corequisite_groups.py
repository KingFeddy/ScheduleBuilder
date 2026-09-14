"""Build mandatory concurrent groups for already selected courses."""
from src.schemas.plan import COURSE_CODE_PATTERN
from src.schemas.prerequisites import AllConditions, CourseCondition
from src.services.prerequisite_checks import verified_rules


def corequisite_groups(rows, resolved, depends_on, credit_target):
    """Return compatible groups and partial-plan notices without inventing courses.

    Only complete AND trees of explicit concurrent corequisites are supported.
    Inconsistent grouping falls back to individual proposals with diagnostics.
    """
    by_code = {item.course_code: i for i, item in enumerate(resolved) if item.course_code}
    parent = list(range(len(resolved)))
    warnings = []

    def root(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def leaves(rule, count):
        count[0] += 1
        if count[0] > 512:
            return None
        if isinstance(rule, CourseCondition):
            return [rule.course_code] if rule.timing == 'concurrent' and COURSE_CODE_PATTERN.fullmatch(rule.course_code) else None
        if not isinstance(rule, AllConditions):
            return None
        codes = []
        for child in rule.items:
            values = leaves(child, count)
            if values is None:
                return None
            codes.extend(values)
        return codes

    for code, index in sorted(by_code.items()):
        rules = verified_rules(rows.get(code, {}))
        if rules is None:
            continue
        try:
            required = leaves(rules.corequisites, [0])
        except RecursionError:
            required = None
        if required is None:
            continue
        missing = sorted(set(required) - by_code.keys())
        if missing:
            warnings.append(f'Partial plan: {code} requires concurrent corequisite coursework '
                            + ', '.join(missing) + ' that is not selected. Review the course choices.')
        for other in required:
            if other in by_code:
                a, b = root(index), root(by_code[other])
                parent[max(a, b)] = min(a, b)

    members = {}
    for index in range(len(resolved)):
        members.setdefault(root(index), set()).add(index)
    graph = {group: set() for group in members}
    for index, prerequisites in enumerate(depends_on):
        for prerequisite in prerequisites:
            graph[root(index)].add(root(prerequisite))

    # Contracting a DAG can create a strict-prior cycle, even without a direct
    # edge inside a group (A with C while A -> B -> C). Detect the entire path.
    invalid = set()
    for group, indices in members.items():
        if len(indices) < 2:
            continue
        pending, seen = list(graph[group]), set()
        while pending:
            current = pending.pop()
            if current == group:
                invalid.add(group)
                break
            if current not in seen:
                seen.add(current)
                pending.extend(graph[current])

    groups = {}
    for group, indices in sorted(members.items()):
        if len(indices) < 2:
            continue
        codes = ', '.join(sorted(resolved[i].course_code for i in indices))
        if group in invalid:
            warnings.append(f'Partial plan: corequisite grouping for {codes} conflicts with prior-course ordering. '
                            'These courses remain separate proposals for review.')
            continue
        credits = round(sum(resolved[i].credits for i in indices), 2)
        if credits > credit_target:
            warnings.append(f'Partial plan: corequisite group {codes} needs {credits:g} credits together, '
                            f'above your {credit_target}-credit target. Review the target or course choices.')
        for index in indices:
            groups[index] = frozenset(indices)
    return groups, warnings
