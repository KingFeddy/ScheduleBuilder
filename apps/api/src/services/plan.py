from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Literal

from pydantic import ConfigDict

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.schemas.plan import (
    COURSE_CODE_PATTERN,
    WILDCARD_PATTERN,
    ParsedDegree,
    ParsedDegreeValidated,
    PlanPreferences,
    ParseValidationError,
    StillNeededItem,
    StableIdentifier,
    stable_identity,
)
from src.scheduler.time_utils import (
    get_next_njit_term,
    get_planning_terms,
    term_to_label,
)
from src.schemas.courses import CourseResponse
from src.services.course_metadata import course_response, planning_credits
from src.services.corequisite_groups import corequisite_groups
from src.services.flexible_prerequisites import flexible_course_ordering, prepare_flexible_groups
from src.services.mixed_prerequisite_choices import choose_mixed_prerequisite_paths
from src.services.prerequisite_checks import check_plan_prerequisites, load_prerequisite_rows, prior_course_ordering
from src.catalog import course_subject
from src.config import settings
from src.schemas.catalog import CatalogStatus, UNCHECKED_CATALOG_NOTE
from src.services.catalog import course_coverage

logger = logging.getLogger(__name__)

CREDIT_CONSISTENCY_TOLERANCE = 6
MAX_QUANTITY_ALLOCATION_SLOTS = 200


# ── Wildcard matching ─────────────────────────────────────────────────────────

def matches_wildcard(course_code: str, pattern: str) -> bool:
    """
    Returns True if course_code matches the wildcard pattern.

    Supported forms (normalized upstream by dw_parser):
      PHYS3XX → any PHYS 3xx course  (two trailing Xs = two digit positions)
      CS4XX   → any CS 4xx course
      @       → any course (universal wildcard)

    Each X matches exactly one digit. PHYS3XX → ^PHYS3\\d\\d$.
    Patterns with embedded @ (e.g. PHYS3@) should have been normalized to XX
    by the parser before reaching this function.
    """
    if pattern == "@":
        return True

    code = course_code.strip().upper()
    pat  = pattern.strip().upper()

    if "X" not in pat and "@" not in pat:
        return code == pat

    regex = "^" + re.sub(r"X", r"\\d", pat) + "$"
    try:
        return bool(re.match(regex, code))
    except re.error:
        return False


def find_matching_requirement(
    elective_code: str,
    still_needed: list[StillNeededItem],
    already_satisfied: set[int],
) -> int | None:
    """
    Returns the index of the first still_needed item whose options[] match
    elective_code. Exact matches take priority over wildcard matches.
    Returns None if no unsatisfied requirement matches.
    """
    elective_upper = elective_code.strip().upper()

    # Phase 1: exact match
    for i, item in enumerate(still_needed):
        if item.quantity_status == "known" and item.remaining_quantity == 0:
            continue
        if i in already_satisfied:
            continue
        if elective_upper in item.options:
            return i

    # Phase 2: wildcard match
    for i, item in enumerate(still_needed):
        if item.quantity_status == "known" and item.remaining_quantity == 0:
            continue
        if i in already_satisfied:
            continue
        if any(matches_wildcard(elective_upper, opt) for opt in item.options):
            return i

    return None


# ── Credit + title lookup ─────────────────────────────────────────────────────

async def get_course_data(
    session: AsyncSession,
    course_codes: list[str],
) -> dict[str, tuple[CourseResponse, list[str]]]:
    """
    Return metadata with its verification state and legacy prerequisite codes.
    Unknown courses retain null metadata; estimates are made explicitly later.

    One ANY(:codes) round-trip replaces the N+1 per-course lookups the
    original design would have made inside the semester assignment loop.
    """
    if not course_codes:
        return {}

    result = await session.execute(
        text(
            "SELECT course_code, credits, title, prerequisites, title_source, credits_source, metadata_latest_attempt FROM courses"
            " WHERE course_code = ANY(:codes)"
        ),
        {"codes": course_codes},
    )
    data = {
        row["course_code"]: (course_response(row), row["prerequisites"] or [])
        for row in result.mappings()
    }

    for code in course_codes:
        if code not in data:
            logger.warning("Course %r not found in catalog; metadata remains unknown", code)
            catalog_status, catalog_note = course_coverage(code, exists=False)
            data[code] = (CourseResponse(
                course_code=code, title=None, credits=None,
                title_status="missing", credits_status="missing",
                catalog_status=catalog_status, catalog_note=catalog_note,
            ), [])

    return data


def validate_parsed_degree(raw: ParsedDegree) -> ParsedDegreeValidated:
    """
    Business-logic validation. Raises ParseValidationError on violations.
    Only a ParsedDegreeValidated should be returned to the client or passed
    to the planner. Raw ParsedDegree is never trusted downstream.
    """
    if not raw.majors:
        raise ParseValidationError(
            "majors",
            "No major detected. Please verify this is a DegreeWorks degree audit PDF.",
        )

    if all(
        x is not None
        for x in [raw.credits_completed, raw.credits_required, raw.credits_remaining]
    ):
        computed = raw.credits_required - raw.credits_completed  # type: ignore[operator]
        delta = abs(raw.credits_remaining - computed)  # type: ignore[operator]
        if delta > CREDIT_CONSISTENCY_TOLERANCE:
            raise ParseValidationError(
                "credits",
                f"Credit counts are inconsistent: {raw.credits_completed} completed + "
                f"{raw.credits_remaining} remaining ≠ {raw.credits_required} required "
                f"(delta: {delta}). The PDF may not be a DegreeWorks audit.",
            )

    if raw.credits_required is not None and not (100 <= raw.credits_required <= 160):
        raise ParseValidationError(
            "credits_required",
            f"credits_required={raw.credits_required} is outside the plausible NJIT "
            f"range (100–160).",
        )

    if not raw.still_needed and raw.credits_remaining != 0:
        raise ParseValidationError(
            "still_needed",
            "No remaining requirements were provided, but remaining credits are not explicitly zero. "
            "Re-upload your DegreeWorks audit and verify its remaining requirements.",
        )

    # Filter completed_courses: keep valid NJIT codes, log and drop the rest.
    # AP credit and transfer credit lines produce non-standard codes (ENG121, CHEM5)
    # that the scraper never saw — drop them rather than letting them pollute the plan.
    valid_completed: list[str] = []
    for code in raw.completed_courses:
        if COURSE_CODE_PATTERN.match(code):
            valid_completed.append(code)
        elif WILDCARD_PATTERN.search(code):
            logger.warning("wildcard in completed_courses: %r — skipping", code)
        else:
            logger.warning(
                "non-standard code in completed_courses: %r (AP/transfer credit?)", code
            )

    valid_in_progress: list[str] = []
    for code in raw.in_progress_courses:
        if COURSE_CODE_PATTERN.match(code):
            valid_in_progress.append(code)
        else:
            logger.warning(
                "non-standard code in in_progress_courses: %r — skipping", code
            )

    for item in raw.still_needed:
        for code in item.options:
            if not WILDCARD_PATTERN.search(code) and not COURSE_CODE_PATTERN.match(code):
                logger.warning(
                    "non-standard code in still_needed: %r (%r)", code, item.requirement
                )

    if (
        raw.credits_remaining is not None
        and raw.credits_remaining > 15
        and len(raw.still_needed) < 2
    ):
        logger.warning(
            "credits_remaining=%d but only %d still_needed items — "
            "regex may have missed requirement blocks",
            raw.credits_remaining,
            len(raw.still_needed),
        )

    return ParsedDegreeValidated(
        student_name=raw.student_name,
        majors=raw.majors,
        minors=raw.minors,
        catalog_year=raw.catalog_year,
        credits_completed=raw.credits_completed,
        credits_required=raw.credits_required,
        credits_remaining=raw.credits_remaining,
        completed_courses=valid_completed,
        in_progress_courses=valid_in_progress,
        course_attempts=raw.course_attempts,
        still_needed=raw.still_needed,
    )


# ── Planner output types ──────────────────────────────────────────────────────

@dataclass
class RequirementAllocation:
    """Planned quantity only; not degree completion or prerequisite eligibility."""
    required_quantity: float | None
    quantity_unit: Literal["classes", "credits", "unknown"]
    allocated_quantity: float | None
    unresolved_quantity: float | None
    status: Literal["allocated", "partial", "unknown"]


@dataclass
class PlannedCourse:
    __pydantic_config__ = ConfigDict(json_schema_serialization_defaults_required=True)

    course_code: str
    title:       str | None
    credits:     float
    badge:       Literal["Required", "Elective", "TBD"]
    reason:      str
    slot_id:     StableIdentifier
    requirement: StillNeededItem | None = None
    allocation: RequirementAllocation | None = None
    credits_estimated: bool = True
    credits_note: str = "Credit estimate for an unresolved course."
    title_status: Literal["verified", "unverified", "missing"] = "unverified"
    catalog_status: CatalogStatus = "unknown"
    catalog_note: str = UNCHECKED_CATALOG_NOTE


@dataclass
class SemesterCard:
    # asdict() always emits defaults; the output schema must reflect that.
    __pydantic_config__ = ConfigDict(json_schema_serialization_defaults_required=True)

    term:          str      # e.g. "202710"
    term_label:    str      # e.g. "Spring 2027"
    courses:       list[PlannedCourse] = field(default_factory=list)
    total_credits: float = 0


@dataclass
class GeneratedPlan:
    semesters:            list[SemesterCard]
    projected_graduation: str
    warnings:             list[str]


def _credit_reconciliation_warnings(
    audit: ParsedDegreeValidated, semesters: list[SemesterCard],
) -> list[str]:
    """Explain scheduled amounts, without certifying degree fulfillment.

    Count course rows once, not repeated per-requirement allocation summaries.
    The audit's remaining total already reflects its own history accounting.
    """
    selected = unresolved_slots = extras = fillers = estimated = Decimal(0)
    active = {item.requirement_id for item in audit.still_needed
              if item.quantity_status != "known" or item.remaining_quantity != 0}
    represented: set[str] = set()
    unresolved: set[str] = set()
    for semester in semesters:
        for course in semester.courses:
            credits = Decimal(str(course.credits))
            if course.credits_estimated:
                estimated += credits
            if course.requirement is not None:
                identity = course.requirement.requirement_id
                represented.add(identity)
                if (course.requirement.quantity_status != "known" or course.allocation is None
                        or course.allocation.status != "allocated"):
                    unresolved.add(identity)
                if course.course_code == "TBD":
                    unresolved_slots += credits
                    unresolved.add(identity)
                else:
                    selected += credits
            elif course.course_code == "FREE":
                fillers += credits
            else:
                extras += credits
    unresolved.update(active - represented)
    linked = selected + unresolved_slots
    total = linked + extras + fillers
    difference = None if audit.credits_remaining is None else linked - Decimal(audit.credits_remaining)
    warnings = []
    if unresolved:
        count = len(unresolved)
        warnings.append(
            f"Partial plan: {count} audit {'requirement has' if count == 1 else 'requirements have'} "
            "unresolved allocation. Matching credit totals would not confirm completion."
        )
    if difference == 0 and not (unresolved or estimated or extras or fillers):
        return warnings

    def amount(value: Decimal) -> str:
        return format(value.normalize(), "f")

    audit_note = ("Audit remaining credits are unknown." if difference is None else
                  f"audit lists {audit.credits_remaining} remaining credits.")
    message = (
        f"Credit review: {audit_note} {amount(total)} scheduled credits: "
        f"{amount(selected)} selected for requirements, {amount(unresolved_slots)} unresolved slot credits, "
        f"{amount(extras)} additional elective credits, {amount(fillers)} course-load filler credits."
    )
    if estimated:
        message += f" The schedule includes {amount(estimated)} estimated credits; confirm their amounts."
    if difference:
        message += (
            f" Requirement-linked credits are {amount(abs(difference))} "
            f"{'above' if difference > 0 else 'below'} the audit figure (including unresolved slot estimates). "
            "Confirm the requirement list and any permitted sharing with your advisor."
        )
    warnings.append(message)
    return warnings


# ── Last-semester requirement detection ───────────────────────────────────

_LAST_SEMESTER_KEYWORDS = ("senior", "capstone")


def _is_last_semester_requirement(requirement: str) -> bool:
    """
    True if the requirement's own label (DegreeWorks' text, not the course
    code) signals a senior-standing/capstone requirement — e.g. "Senior
    Project", "Senior Seminar", "Capstone Design". Generalizes across any
    major without a hardcoded course-code list.
    """
    lowered = requirement.lower()
    return any(keyword in lowered for keyword in _LAST_SEMESTER_KEYWORDS)


# ── Internal resolved-item type ───────────────────────────────────────────────

@dataclass
class _ResolvedItem:
    requirement: str
    course_code: str | None   # None = TBD
    credits:     float = 3
    title:       str | None = None
    badge:       str = "Required"   # "Required" | "Elective" | "TBD"
    reason:      str = ""
    must_be_last: bool = False
    credits_estimated: bool = True
    credits_note: str = "Credit estimate for an unresolved course."
    title_status: Literal["verified", "unverified", "missing"] = "unverified"
    catalog_status: CatalogStatus = "unresolved"
    catalog_note: str = "No specific catalog course has been selected for this slot."
    slot_id: str = ""
    source_requirement: StillNeededItem | None = None
    allocation: RequirementAllocation | None = None
    allocation_note: str = ""


# ── Option selection ──────────────────────────────────────────────────────────

async def select_best_option(
    item: StillNeededItem,
    completed: set[str],
    in_progress: set[str],
    student_electives: list[str],
    target_term: str,
    session: AsyncSession,
) -> tuple[str | None, int]:
    """
    Returns (chosen course code or None, number of real remaining options).
    course_code is None if only wildcards are available (TBD slot).
    num_available lets the caller distinguish a genuinely single-option
    requirement from one silently resolved among several valid alternatives
    (a real user-reported gap: both used to render identically as
    "Required," with no signal a choice was made on the student's behalf).

    Priority:
      1. A student-added elective that appears in options
      2. An option that has sections in target_term
      3. First non-wildcard, non-excluded option
    """
    available = [
        opt for opt in item.options
        if opt not in completed
        and opt not in in_progress
        and not WILDCARD_PATTERN.search(opt)
        and opt.isascii() and COURSE_CODE_PATTERN.fullmatch(opt)
    ]

    if not available:
        return None, 0

    # Priority 1: student elective that's an explicit option
    for elective in student_electives:
        if elective in available:
            return elective, len(available)

    # Priority 2: option with scraped sections in the target term
    result = await session.execute(
        text(
            "SELECT DISTINCT course_code FROM sections"
            " WHERE course_code = ANY(:codes) AND term = :term"
        ),
        {"codes": available, "term": target_term},
    )
    offered = {row["course_code"] for row in result.mappings()}
    for opt in available:
        if opt in offered:
            return opt, len(available)

    # Priority 3: first available
    return available[0], len(available)


# ── Prerequisite dependency graph ─────────────────────────────────────────────

def _compute_prerequisite_dependencies(
    resolved: list[_ResolvedItem],
    completed: set[str],
    in_progress: set[str],
    prerequisites_by_code: dict[str, list[str]],
    verified_history: dict[str, set[str]] | None = None,
) -> tuple[list[set[int]], list[str]]:
    """
    Returns (depends_on, warnings).

    depends_on[i] is the set of OTHER resolved-item indices that item i's
    prerequisites resolve to among the courses actually being scheduled —
    item i must not be placed in the same semester as, or any semester
    before, any index in depends_on[i]. This must be checked against each
    dependency's ACTUAL scheduled semester during packing, not a
    precomputed "earliest possible" bound: credit-budget contention from
    unrelated courses can delay a prerequisite's real placement past the
    semester a naive earliest-bound calculation would assume, which would
    silently let a course land in the same semester as its own
    prerequisite. (Confirmed live during design — three unrelated 3-credit
    courses ahead of a 1-credit prerequisite chain at credit_target=3
    delayed the prerequisite three semesters past its theoretical minimum,
    and a static-bound version of this function let the dependent get
    bundled into the same semester as its just-placed prerequisite.)

    For supported structured rules, only verified_history discharges a
    prerequisite. Other courses retain the legacy completed/in-progress
    compatibility behavior. Missing prerequisites cannot create an edge;
    they are named for review, with structured gaps marked partial by the caller.

    A genuine cycle in the prerequisite data is broken by dropping the
    back-edge that would close the loop, and the affected courses are
    folded into the same warning.
    """
    code_to_index: dict[str, int] = {}
    for i, item in enumerate(resolved):
        if item.course_code:
            code_to_index[item.course_code] = i

    depends_on: list[set[int]] = [set() for _ in resolved]
    flagged_codes: set[str] = set()
    visiting: set[int] = set()
    finished: set[int] = set()

    def visit(i: int) -> None:
        if i in finished:
            return
        item = resolved[i]
        code = item.course_code
        if not code:
            finished.add(i)
            return

        visiting.add(i)
        for prereq_code in prerequisites_by_code.get(code, []):
            if prereq_code == code:
                continue
            history = (verified_history[code] if verified_history is not None and code in verified_history
                       else completed | in_progress)
            if prereq_code in history:
                continue
            dep_idx = code_to_index.get(prereq_code)
            if dep_idx is None:
                flagged_codes.add(code)
                continue
            if dep_idx == i:
                continue
            if dep_idx in visiting:
                flagged_codes.add(code)
                flagged_codes.add(prereq_code)
                continue
            depends_on[i].add(dep_idx)
            visit(dep_idx)
        visiting.discard(i)
        finished.add(i)

    for i in range(len(resolved)):
        visit(i)

    warnings: list[str] = []
    if flagged_codes:
        codes = ", ".join(sorted(flagged_codes))
        warnings.append(
            f"Prerequisites for {codes} could not be verified against your "
            f"completed or planned courses — confirm you meet them before "
            f"registering."
        )

    return depends_on, warnings


# ── Semester packing ───────────────────────────────────────────────────────────

def _pack_semesters(
    items_pool: list[_ResolvedItem],
    depends_on: list[set[int]],
    index_by_item: dict[int, int],
    credit_target: int,
    planning_terms: list[str],
    placed_at: dict[int, int],
    start_term_idx: int = 0,
    initial_card: SemesterCard | None = None,
    concurrent_groups: dict[int, frozenset[int]] | None = None,
    same_or_before: list[set[int]] | None = None,
) -> list[SemesterCard]:
    """
    Packs items_pool into semester cards term-by-term, starting at
    start_term_idx, respecting depends_on and the ACTUAL (not
    precomputed) placement of every dependency via placed_at — see ADR-27
    for why this must be dynamic. Mutates placed_at in place with every
    item this call places, so a second call packing a different item pool
    (e.g. senior/capstone courses, packed after everything else — see
    ADR-28) sees accurate prior placements from this call. Mutates
    planning_terms in place too (appending further-out terms) if the plan
    runs past the initially pre-computed window.

    Members of concurrent_groups are placed atomically and wait for every
    member's prior dependencies. The caller keeps groups within one phase
    and rejects groups that conflict with strict prior ordering.
    same_or_before permits an external predecessor in the current semester;
    newly eligible courses are retried before advancing to the next semester.

    If initial_card is given, the very first term processed
    (start_term_idx) tops it up in place — adding courses/credits to that
    existing card rather than creating a new one — instead of starting a
    fresh semester. initial_card is never included in this function's
    return value; the caller already holds a reference to it. The
    force-add fallback (an oversized course or group gets its own semester
    rather than blocking all progress) is skipped specifically on a
    topping-up pass: initial_card is guaranteed already non-empty (the
    caller only ever passes the last semester from a prior packing call,
    and a prior call's `if placed:` guard means every card it produced
    has at least one course), so placing nothing new there this term
    isn't a stuck state — leftover items simply proceed to the next,
    fresh term, where force-add resumes normally.
    """
    concurrent_groups = concurrent_groups or {}
    same_or_before = same_or_before or [set() for _ in depends_on]

    def group_for(item, pool_by_index):
        index = index_by_item[id(item)]
        members = concurrent_groups.get(index, frozenset({index}))
        if not members <= pool_by_index.keys():
            raise ValueError("Corequisite group spans packing phases or was partially placed")
        return [pool_by_index[i] for i in sorted(members)]

    def planned(item):
        return PlannedCourse(
            slot_id=item.slot_id, requirement=item.source_requirement, allocation=item.allocation,
            course_code=item.course_code or "TBD", title=item.title, credits=item.credits,
            badge=item.badge, reason=item.reason, credits_estimated=item.credits_estimated,
            credits_note=item.credits_note, title_status=item.title_status,
            catalog_status=item.catalog_status, catalog_note=item.catalog_note,
        )

    semesters: list[SemesterCard] = []
    term_idx = start_term_idx

    while items_pool:
        if term_idx >= len(planning_terms):
            last = planning_terms[-1]
            for _ in range(5):
                last = get_next_njit_term(last)
                if not last.endswith("50"):
                    planning_terms.append(last)

        term = planning_terms[term_idx]
        topping_up = term_idx == start_term_idx and initial_card is not None
        credits_used = initial_card.total_credits if topping_up else 0
        remaining: list[_ResolvedItem] = []
        blocked:   list[_ResolvedItem] = []
        placed:    list[PlannedCourse] = []

        pool_by_index = {index_by_item[id(item)]: item for item in items_pool}
        pending = items_pool
        while pending:
            visited = set()
            blocked = []
            progressed = False
            for item in pending:
                idx = index_by_item[id(item)]
                if idx in visited:
                    continue
                group = group_for(item, pool_by_index)
                indices = {index_by_item[id(member)] for member in group}
                visited.update(indices)
                deps = set().union(*(depends_on[index] for index in indices))
                flexible_deps = set().union(*(same_or_before[index] for index in indices)) - indices
                group_credits = round(sum(member.credits for member in group), 2)
                if (any(d not in placed_at or placed_at[d] >= term_idx for d in deps)
                        or any(d not in placed_at or placed_at[d] > term_idx for d in flexible_deps)):
                    blocked.extend(group)
                elif credits_used + group_credits <= credit_target:
                    placed.extend(planned(member) for member in group)
                    placed_at.update({index: term_idx for index in indices})
                    credits_used = round(credits_used + group_credits, 2)
                    progressed = True
                else:
                    remaining.extend(group)
            # A newly placed predecessor can unlock same-term coursework that
            # appeared earlier in the pool. Strict prior edges still wait. Each
            # retry must place something, so this loop cannot spin without progress.
            if not progressed:
                break
            pending = blocked

        # An oversized eligible group gets its own semester, with an explicit
        # target-conflict notice supplied by group construction. Never split it,
        # take blocked members, or partially top up an existing semester.
        if not placed and remaining and not topping_up:
            group = group_for(remaining[0], pool_by_index)
            indices = {index_by_item[id(member)] for member in group}
            placed.extend(planned(member) for member in group)
            placed_at.update({index: term_idx for index in indices})
            credits_used = round(sum(member.credits for member in group), 2)
            remaining = [member for member in remaining if index_by_item[id(member)] not in indices]

        # A semester where everything left is prerequisite-blocked (nothing
        # placed) must not appear as an empty card — skip it and let the
        # blocked items retry at the next term.
        if placed:
            if topping_up:
                initial_card.courses.extend(placed)
                initial_card.total_credits = credits_used
            else:
                card = SemesterCard(term=term, term_label=term_to_label(term))
                card.courses = placed
                card.total_credits = credits_used
                semesters.append(card)

        items_pool = remaining + blocked
        term_idx  += 1

    return semesters


def _synchronized_capstone_start(
    capstone_items: list[_ResolvedItem],
    depends_on: list[set[int]],
    index_by_item: dict[int, int],
    placed_at: dict[int, int],
) -> int:
    """
    Earliest term_idx at which EVERY senior/capstone item could possibly be
    scheduled, ignoring credit-budget constraints — the natural floor
    imposed by prerequisite depth alone. Used only to pick Phase 2's
    starting term_idx; the actual packing that follows still uses
    _pack_semesters' fully dynamic, placed_at-based eligibility check (see
    ADR-27/ADR-28) — this function never gates an individual item's
    placement, only where the whole second phase begins.

    Without this, a capstone item with no prerequisite of its own (e.g. a
    Senior Seminar) becomes individually eligible immediately and jumps
    into whatever room Phase 1 left behind, while a sibling capstone item
    genuinely delayed by a real prerequisite chain (e.g. a Senior Project
    depending on an earlier course) keeps waiting — scattering "must be
    last" courses across multiple non-adjacent trailing semesters instead
    of clustering them at the true end of the plan. Confirmed live: a real
    user's plan placed a prerequisite-free Senior Seminar in the semester
    right after normal packing ended, while their Senior Project (delayed
    by a real prerequisite) landed two semesters later — exactly this bug.

    Safe to compute from Phase 1's `placed_at` values for normal-item
    dependencies because Phase 1 is fully complete and its placements are
    fixed by the time this runs — unlike the earlier, rejected "precompute
    an earliest bound" design for ADR-27, which failed specifically because
    it tried to predict placements that were STILL being decided.
    """
    if not capstone_items:
        return 0

    capstone_indices = {index_by_item[id(item)] for item in capstone_items}
    natural_term: dict[int, int] = {}
    visiting: set[int] = set()

    def resolve(idx: int) -> int:
        if idx in natural_term:
            return natural_term[idx]
        if idx in visiting:
            return 0  # cycle guard — real cycles are already broken upstream
        visiting.add(idx)
        max_dep = -1
        for dep_idx in depends_on[idx]:
            if dep_idx in capstone_indices:
                max_dep = max(max_dep, resolve(dep_idx) + 1)
            elif dep_idx in placed_at:
                max_dep = max(max_dep, placed_at[dep_idx] + 1)
        visiting.discard(idx)
        result = max(0, max_dep)
        natural_term[idx] = result
        return result

    return max(resolve(idx) for idx in capstone_indices)


def _apply_course_data(item, course_data, warnings, prerequisites_by_code):
    if not item.course_code:
        return
    course, prereqs = course_data[item.course_code]
    item.catalog_status, item.catalog_note = course.catalog_status, course.catalog_note
    if item.catalog_note:
        warnings.append(f"{item.course_code}: {item.catalog_note}")
        if item.badge == "Elective":
            item.reason = f"Option for '{item.requirement}'; catalog coverage needs confirmation."
    item.credits, item.credits_estimated, item.credits_note = planning_credits(course)
    item.title = course.title or item.requirement
    item.title_status = course.title_status if course.title else "unverified"
    warnings.extend(f"{item.course_code}: {warning}" for warning in course.metadata_warnings)
    prerequisites_by_code[item.course_code] = prereqs


def _allocated_amount(rows: list[_ResolvedItem], unit: str) -> Decimal:
    if unit == "classes":
        return Decimal(sum(row.course_code is not None for row in rows))
    return sum((Decimal(str(row.credits)) for row in rows
                if row.course_code and not row.credits_estimated
                and math.isfinite(row.credits) and row.credits > 0), Decimal(0))


async def _allocate_requirement_quantities(
    resolved, course_data, completed, in_progress, target_term, credit_target,
    session, warnings, prerequisites_by_code, requested_codes,
) -> list[_ResolvedItem]:
    """Allocate each concrete course once, without inferring sharing permission.

    Credits are counted only from verified fixed metadata. Uncertain courses
    remain planned suggestions, with their requirement remainder still explicit.
    """
    linked = [row for row in resolved if row.source_requirement is not None]
    extras = [row for row in resolved if row.source_requirement is None]
    requested_rows = {row.course_code: row for row in resolved if row.course_code in requested_codes}
    allocated_to: dict[str, StillNeededItem] = {}
    excluded = completed | in_progress
    # Protect narrow requirements before flexible choices. Equal constraints
    # retain audit order; unknown quantities never displace known requirements.
    linked.sort(key=lambda row: (
        row.source_requirement.quantity_status != "known",
        any(WILDCARD_PATTERN.search(code) for code in row.source_requirement.options),
        len({code for code in row.source_requirement.options
             if code.isascii() and COURSE_CODE_PATTERN.fullmatch(code) and code not in excluded}),
    ))
    # Preserve the initial batched lookup and fetch any additional candidates
    # together, rather than a separate metadata query for each added course.
    additional_codes = list(dict.fromkeys(
        code for row in linked
        for code in row.source_requirement.options
        if code.isascii() and COURSE_CODE_PATTERN.fullmatch(code) and code not in course_data and code not in excluded
    ))
    if additional_codes:
        course_data.update(await get_course_data(session, additional_codes))

    remaining_slots = max(0, MAX_QUANTITY_ALLOCATION_SLOTS - len(resolved))
    expanded = []
    for base in linked:
        requirement = base.source_requirement
        unit = requirement.quantity_unit
        unavailable = excluded | allocated_to.keys()
        if base.course_code is None or base.course_code in unavailable:
            requested = next((code for code in requested_rows if code not in unavailable
                              and any(matches_wildcard(code, option) for option in requirement.options)), None)
            if requested:
                selected, count = requested, 2
            else:
                selected, count = await select_best_option(requirement, unavailable, in_progress, [], target_term, session)
            base = replace(base, course_code=selected, title=requirement.requirement,
                           credits=3, credits_estimated=True, title_status="unverified",
                           credits_note="Estimated credits for an unresolved class.",
                           catalog_status="unresolved", catalog_note=base.catalog_note if not base.course_code else
                           "No specific catalog course has been selected for this slot.",
                           badge="Elective" if selected and count > 1 else "Required" if selected else "TBD",
                           reason=(f"Alternative allocated toward '{requirement.requirement}'" if selected else
                                   f"Unresolved requirement '{requirement.requirement}'." if base.course_code else base.reason))
            _apply_course_data(base, course_data, [], prerequisites_by_code)

        def record_allocation(rows, unresolved):
            overlaps = [(code, owner) for code, owner in allocated_to.items()
                        if any(matches_wildcard(code, option) for option in requirement.options)]
            if unresolved and overlaps:
                owners = "; ".join(f"{code} is allocated to '{owner.requirement}'" for code, owner in overlaps)
                note = (f"Requirement overlap: {owners}. Sharing with '{requirement.requirement}' is unverified; "
                        "the course is scheduled and its credits counted once. Confirm the overlap with your advisor.")
                warnings.append(note)
                for row in rows:
                    row.allocation_note = note
            for row in rows:
                if row.course_code:
                    allocated_to[row.course_code] = requirement

        if requirement.quantity_status == "unresolved":
            base.allocation = RequirementAllocation(requirement.remaining_quantity, unit, None, None, "unknown")
            record_allocation([base], base.course_code is None)
            expanded.append(base)
            continue

        target = Decimal(str(requirement.remaining_quantity))
        verified_options = [code for code in requirement.options if code in course_data
                            and course_data[code][0].credits_status == "fixed"
                            and course_data[code][0].credits is not None
                            and math.isfinite(course_data[code][0].credits) and course_data[code][0].credits > 0
                            and code not in unavailable]
        # Automatic choices may prefer verified-credit alternatives. Explicit
        # student selections remain intact and visibly unresolved if uncertain.
        if unit == "credits" and base.course_code and base.credits_estimated and base.course_code not in requested_codes and verified_options:
            selected, count = await select_best_option(requirement.model_copy(update={"options": verified_options}),
                                                       unavailable, in_progress, [], target_term, session)
            if selected in verified_options:
                base = replace(base, course_code=selected, badge="Elective" if count > 1 else "Required",
                               reason=f"Course with verified credits allocated toward '{requirement.requirement}'")
                _apply_course_data(base, course_data, [], prerequisites_by_code)
        rows = [base] if base.course_code else []
        used_codes = {row.course_code for row in rows}
        limit_reached = False
        while _allocated_amount(rows, unit) < target:
            # Do not pile extra courses/placeholders beside an uncertain-credit
            # selection and pretend the missing credit amount is known.
            if unit == "credits" and any(row.credits_estimated for row in rows):
                break
            extra = next((row for row in requested_rows.values() if row.course_code not in unavailable
                          and row.course_code not in used_codes
                          and any(matches_wildcard(row.course_code, option) for option in requirement.options)), None)
            if extra is not None:
                # An unmatched extra already occupies a base slot. Reusing a
                # choice from another requirement adds a slot to this one.
                if rows and extra.source_requirement is not None:
                    if remaining_slots == 0:
                        limit_reached = True
                        break
                    remaining_slots -= 1
                candidate = replace(extra, requirement=requirement.requirement,
                                    source_requirement=requirement.model_copy(deep=True),
                                    must_be_last=base.must_be_last,
                                    reason=f"Your elective {extra.course_code} is allocated toward '{requirement.requirement}'")
            else:
                if rows and remaining_slots == 0:
                    limit_reached = True
                    break
                options = [code for code in requirement.options
                           if code.isascii() and COURSE_CODE_PATTERN.fullmatch(code) and code not in used_codes and code not in unavailable]
                if unit == "credits":
                    options = [code for code in options if code in verified_options] or options
                if not options:
                    break
                candidate_requirement = requirement.model_copy(update={"options": options})
                code, count = await select_best_option(candidate_requirement, completed, in_progress, [], target_term, session)
                if code is None or code not in options:
                    break
                candidate = _ResolvedItem(
                    requirement=requirement.requirement, course_code=code,
                    source_requirement=requirement.model_copy(deep=True), must_be_last=base.must_be_last,
                    badge="Elective" if count > 1 else "Required",
                    reason=f"Additional course allocated toward '{requirement.requirement}'",
                )
                _apply_course_data(candidate, course_data, [], prerequisites_by_code)
                if rows:
                    remaining_slots -= 1
            rows.append(candidate)
            used_codes.add(candidate.course_code)

        allocated = _allocated_amount(rows, unit)
        unresolved = max(target - allocated, Decimal(0))
        uncertain_credits = unit == "credits" and any(row.credits_estimated for row in rows)
        # Preserve missing classes as individual TBDs. Missing credits are
        # represented in credit-target-sized placeholders, never fake courses.
        pending = unresolved
        if not uncertain_credits:
            while pending > 0:
                if rows and remaining_slots == 0:
                    limit_reached = True
                    break
                chunk = Decimal(1) if unit == "classes" else min(pending, Decimal(credit_target))
                placeholder = replace(
                    base, course_code=None, credits=3 if unit == "classes" else float(chunk),
                    title=requirement.requirement, badge="TBD", credits_estimated=True,
                    credits_note=("Estimated credits for an unresolved class." if unit == "classes"
                                  else "Unresolved requirement credits; no qualifying course has been selected."),
                    title_status="unverified", catalog_status="unresolved",
                    catalog_note="No specific catalog course has been selected for this slot.",
                    reason=(base.reason if not requirement.options else
                            f"Unresolved {unit} for '{requirement.requirement}'; select additional qualifying coursework."),
                )
                if rows:
                    remaining_slots -= 1
                rows.append(placeholder)
                pending -= chunk

        allocation = RequirementAllocation(float(target), unit, float(allocated), float(unresolved),
                                           "allocated" if unresolved == 0 else "partial")
        for occurrence, row in enumerate(rows, start=1):
            row.slot_id = stable_identity("slot", requirement.requirement_id, occurrence)
            row.allocation = allocation
        if unresolved:
            warnings.append(f"'{requirement.requirement}' has {float(unresolved):g} {unit} unresolved. "
                            "Only selected courses and verified fixed credits count toward this allocation.")
        if limit_reached:
            warnings.append(f"The allocation limit was reached for '{requirement.requirement}'. "
                            "Its unresolved remainder is preserved; additional slots were not generated.")
        record_allocation(rows, unresolved > 0)
        expanded.extend(rows)
    return expanded + [row for row in extras if row.course_code not in allocated_to]


# ── Main planner ──────────────────────────────────────────────────────────────

async def generate_plan(
    validated: ParsedDegreeValidated,
    preferences: PlanPreferences,
    session: AsyncSession,
) -> GeneratedPlan:
    """
    Produces a semester-by-semester plan from a validated ParsedDegree.

    Validated preferences:
      courses (list[str])        — student-chosen electives
      credits_per_semester (int) — MIN_CREDITS_PER_SEMESTER..MAX_CREDITS_PER_SEMESTER
    """
    warnings: list[str] = []

    credit_target = preferences.credits_per_semester
    student_electives = preferences.courses

    completed   = set(validated.completed_courses)
    in_progress = set(validated.in_progress_courses)
    all_excluded = completed | in_progress

    # ── 1. Early exit: already graduated ─────────────────────────────────────

    if not validated.still_needed and validated.credits_remaining == 0:
        return GeneratedPlan(
            semesters=[],
            projected_graduation="This semester",
            warnings=["You've completed all degree requirements. Congratulations!"],
        )

    # ── 2. Filter student electives ───────────────────────────────────────────

    electives_to_place: list[str] = []
    for code in student_electives:
        if code in all_excluded:
            warnings.append(
                f"{code} is already completed or in progress — removed from elective list."
            )
        else:
            electives_to_place.append(code)

    # ── 3. Resolve still_needed → concrete courses ────────────────────────────

    required_subjects = {
        subject for item in validated.still_needed for option in item.options
        if (subject := course_subject(option)) is not None
    }
    excluded_subjects = sorted(required_subjects - set(settings.catalog_subjects))
    if excluded_subjects:
        warnings.append(
            f"Requirement options include subjects outside the configured automatic refresh scope: {', '.join(excluded_subjects)}. "
            "Options in those subjects need confirmation with NJIT."
        )

    planning_terms = get_planning_terms(n=10, start_term=preferences.start_term or settings.CURRENT_TERM)
    current_term   = planning_terms[0]

    resolved: list[_ResolvedItem] = []
    satisfied_indices: set[int] = set()

    # Match student electives to requirements (exact first, then wildcard)
    elective_to_req: dict[str, int] = {}   # elective code → still_needed index
    for elective in electives_to_place:
        idx = find_matching_requirement(elective, validated.still_needed, satisfied_indices)
        if idx is not None:
            satisfied_indices.add(idx)
            elective_to_req[elective] = idx

    # Build resolved items
    for i, item in enumerate(validated.still_needed):
        if item.quantity_status == "known" and item.remaining_quantity == 0:
            continue
        unreadable_options_note = (
            f"Course options could not be read for '{item.requirement}'. "
            "Review the original DegreeWorks requirement with your advisor."
        )
        if not item.options:
            warnings.append(unreadable_options_note)
        if item.quantity_status == "unresolved":
            warnings.append(f"Remaining quantity for '{item.requirement}' is unknown. Confirm the required amount with your advisor.")
        must_be_last = _is_last_semester_requirement(item.requirement)
        if i in satisfied_indices:
            code = next(e for e, idx in elective_to_req.items() if idx == i)
            resolved.append(_ResolvedItem(
                slot_id=stable_identity("slot", item.requirement_id, 1), source_requirement=item.model_copy(deep=True),
                requirement=item.requirement,
                course_code=code,
                badge="Elective",
                reason=f"Your elective {code} is allocated toward '{item.requirement}'",
                must_be_last=must_be_last,
            ))
        else:
            best, num_available = await select_best_option(
                item, completed, in_progress, electives_to_place, current_term, session
            )
            is_choice = best is not None and num_available > 1
            missing_scope = sorted({
                subject for option in item.options
                if (subject := course_subject(option)) is not None
                and subject not in settings.catalog_subjects
            })
            catalog_note = "No specific catalog course has been selected for this slot."
            if best is None and missing_scope:
                catalog_note += f" Requirement subjects outside collection scope: {', '.join(missing_scope)}. Confirm options with NJIT."
            resolved.append(_ResolvedItem(
                slot_id=stable_identity("slot", item.requirement_id, 1), source_requirement=item.model_copy(deep=True),
                requirement=item.requirement,
                course_code=best,
                badge="Elective" if is_choice else ("Required" if best else "TBD"),
                reason=(
                    f"One of {num_available} options for '{item.requirement}'" if is_choice
                    else f"Required for {validated.majors[0]}" if best
                    else unreadable_options_note if not item.options
                    else f"Requirement '{item.requirement}' — discuss with advisor."
                ),
                must_be_last=must_be_last,
                catalog_note=catalog_note,
            ))

    # Add unmatched electives as extra courses
    extra_occurrences: dict[str, int] = {}
    for code in electives_to_place:
        if code not in elective_to_req:
            extra_occurrences[code] = extra_occurrences.get(code, 0) + 1
            resolved.append(_ResolvedItem(
                slot_id=stable_identity("slot", "extra-elective", code, extra_occurrences[code]),
                requirement="Elective",
                course_code=code,
                badge="Elective",
                reason="Additional elective you requested",
            ))

    # ── 4. Fetch credits + titles + prerequisites in one batched query ───────

    all_codes = [r.course_code for r in resolved if r.course_code]
    course_data = await get_course_data(session, all_codes)

    prerequisites_by_code: dict[str, list[str]] = {}
    for r in resolved:
        _apply_course_data(r, course_data, [], prerequisites_by_code)

    resolved = await _allocate_requirement_quantities(
        resolved, course_data, completed, in_progress, current_term, credit_target,
        session, warnings, prerequisites_by_code, set(student_electives),
    )
    # Automatic choices can change during quantity allocation. Publish metadata
    # warnings and dependencies only for the final selections.
    prerequisites_by_code.clear()
    for r in resolved:
        _apply_course_data(r, course_data, warnings, prerequisites_by_code)
        if r.allocation_note:
            r.reason = f"{r.reason} {r.allocation_note}"

    # ── 6. Compute prerequisite ordering, then sort within it ────────────────

    rule_rows = await load_prerequisite_rows(session, prerequisites_by_code)
    ordering_rows, choice_warnings = choose_mixed_prerequisite_paths(
        rule_rows, validated, prerequisites_by_code, current_term,
        {item.course_code: item.credits for item in resolved if item.course_code}, credit_target,
    )
    warnings.extend(choice_warnings)
    prerequisites_by_code, flexible_history, flexible, concurrent, flexible_warnings = flexible_course_ordering(
        ordering_rows, validated, prerequisites_by_code, current_term,
    )
    warnings.extend(flexible_warnings)
    prerequisites_by_code, verified_history, ordering_warnings = prior_course_ordering(
        ordering_rows, validated, prerequisites_by_code, current_term, fixed_history=flexible_history,
    )
    warnings.extend(ordering_warnings)
    depends_on, prereq_warnings = _compute_prerequisite_dependencies(
        resolved, completed, in_progress, prerequisites_by_code, verified_history,
    )
    warnings.extend(prereq_warnings)

    index_by_code = {r.course_code: i for i, r in enumerate(resolved) if r.course_code}
    unresolved_ordering = {
        code for code, history in verified_history.items()
        if any(prereq not in history and (prereq not in index_by_code
               or index_by_code[prereq] not in depends_on[index_by_code[code]])
               for prereq in prerequisites_by_code[code])
    }
    if unresolved_ordering:
        warnings.append(
            "Partial plan: prerequisite ordering for " + ", ".join(sorted(unresolved_ordering))
            + " could not be established from the available history and selected courses. "
            "Review missing prerequisites, grades, or circular requirements."
        )

    concurrent_groups, group_warnings = corequisite_groups(
        rule_rows, resolved, depends_on, credit_target, mandatory_concurrent=concurrent,
    )
    warnings.extend(group_warnings)
    same_or_before, concurrent_groups, flexible_warnings = prepare_flexible_groups(
        resolved, depends_on, flexible, concurrent_groups, credit_target,
    )
    warnings.extend(flexible_warnings)

    # A normal item's prerequisite may resolve to a senior/capstone-flagged
    # item (ADR-28). Phase 1 packing never places capstone items, so
    # placed_at would never gain an entry for that index and the normal
    # item would stay permanently blocked — an infinite loop in
    # _pack_semesters' `while items_pool:` with no `await` inside it,
    # hanging the whole event loop, not just one request. Treat this the
    # same way ADR-27 already treats an unverifiable prerequisite for legacy
    # rules. Supported structured chains move into the later phase instead.
    capstone_indices = {i for i, r in enumerate(resolved) if r.must_be_last}
    # Keep supported prior-course edges across the phase boundary. Move their
    # dependents (and downstream legacy dependents) into the later packing phase;
    # this is scheduling membership, not a new academic capstone classification.
    promoted = set()
    while True:
        additions = {i for i, deps in enumerate(depends_on) if i not in capstone_indices
                     and (deps & promoted or (resolved[i].course_code in verified_history
                                              and deps & capstone_indices))}
        additions.update(index for index, group in concurrent_groups.items()
                         if index not in capstone_indices and group & capstone_indices)
        additions.update(index for index, deps in enumerate(same_or_before)
                         if index not in capstone_indices and deps & capstone_indices)
        if not additions:
            break
        capstone_indices.update(additions)
        promoted.update(additions)
    cross_phase_flagged: set[str] = set()
    for i, deps in enumerate(depends_on):
        if i in capstone_indices:
            continue
        conflicting = deps & capstone_indices
        if conflicting:
            depends_on[i] = deps - capstone_indices
            if resolved[i].course_code:
                cross_phase_flagged.add(resolved[i].course_code)

    if cross_phase_flagged:
        codes = ", ".join(sorted(cross_phase_flagged))
        warnings.append(
            f"Prerequisites for {codes} depend on a senior/capstone course that's "
            f"scheduled in your final semester — this ordering could not be fully "
            f"honored. Confirm you meet the actual prerequisite before registering."
        )

    index_by_item: dict[int, int] = {id(item): i for i, item in enumerate(resolved)}

    normal_items   = [r for i, r in enumerate(resolved) if i not in capstone_indices]
    capstone_items = [r for i, r in enumerate(resolved) if i in capstone_indices]

    def _sorted_pool(items: list[_ResolvedItem]) -> list[_ResolvedItem]:
        concrete = [r for r in items if r.course_code is not None]
        tbd      = [r for r in items if r.course_code is None]
        concrete.sort(key=lambda r: -r.credits)
        return concrete + tbd

    # ── 7. Assign to semesters — normal courses first, then senior/capstone ──
    #
    # Senior Seminar/Senior Project-type requirements (ADR-28) must land in
    # the student's actual final semester, independent of whatever their own
    # prerequisite chain would otherwise allow. Packed in a second phase,
    # continuing from wherever normal packing left off — topping up the last
    # normal semester if there's room, or starting a fresh trailing semester
    # otherwise — never mixed into earlier, non-final semesters.
    #
    # resolved-index -> the term_idx it was ACTUALLY scheduled in, shared
    # across both phases. Eligibility is checked against this, never a
    # precomputed "earliest possible" bound — see ADR-27 for why.
    placed_at: dict[int, int] = {}

    semesters = _pack_semesters(
        _sorted_pool(normal_items), depends_on, index_by_item,
        credit_target, planning_terms, placed_at, concurrent_groups=concurrent_groups,
        same_or_before=same_or_before,
    )

    if capstone_items:
        # Structured chains use actual placement checks in the packer. Applying
        # the legacy deepest-capstone floor would unnecessarily postpone roots
        # before scheduling the entire chain again from that later starting point.
        natural_start = (0 if any(resolved[i].course_code in verified_history for i in capstone_indices)
                         else _synchronized_capstone_start(
                             capstone_items, depends_on, index_by_item, placed_at,
                         ))
        start_term_idx = max(len(semesters) - 1, natural_start)
        # Only top up the last normal semester's card when the synchronized
        # floor lands exactly there — if a real prerequisite chain pushes
        # capstone packing later, merging into a semester that's
        # chronologically "in the past" relative to that floor would be wrong.
        initial_card = (
            semesters[-1]
            if semesters and start_term_idx == len(semesters) - 1
            else None
        )
        capstone_semesters = _pack_semesters(
            _sorted_pool(capstone_items), depends_on, index_by_item,
            credit_target, planning_terms, placed_at,
            start_term_idx=start_term_idx, initial_card=initial_card, concurrent_groups=concurrent_groups,
            same_or_before=same_or_before,
        )
        semesters.extend(capstone_semesters)

    # Credit targets guide packing; a lighter final semester needs no filler.
    # Additional courses must come from the student's explicit elective choices.

    # Reconcile the final schedule, including requested extras.
    # Keep these diagnostics first so a partial allocation is visible immediately.
    warnings = _credit_reconciliation_warnings(validated, semesters) + warnings

    # ── 8. Check structured rules against the actual proposed semesters ───────
    warnings.extend(await check_plan_prerequisites(session, validated, {
        course.course_code: semester.term for semester in semesters for course in semester.courses
        if re.fullmatch(COURSE_CODE_PATTERN, course.course_code)
    }, rows=rule_rows))

    warnings.append(
        "Review prerequisite and corequisite issues before using this proposed schedule. "
        "Supported prerequisite chains and mandatory corequisite groups guide scheduling; flagged issues still need review. "
        "Requirements may differ by section. Confirm registration eligibility with NJIT."
    )

    return GeneratedPlan(
        semesters=semesters,
        projected_graduation=semesters[-1].term_label if semesters else "Unknown",
        warnings=warnings,
    )
