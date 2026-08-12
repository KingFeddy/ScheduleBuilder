from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.schemas.plan import (
    COURSE_CODE_PATTERN,
    WILDCARD_PATTERN,
    ParsedDegree,
    ParsedDegreeValidated,
    ParseValidationError,
    StillNeededItem,
)
from src.scheduler.time_utils import (
    get_next_njit_term,
    get_planning_terms,
    term_to_label,
)

logger = logging.getLogger(__name__)

CREDIT_CONSISTENCY_TOLERANCE = 6
MIN_CREDITS_PER_SEMESTER = 3
MAX_CREDITS_PER_SEMESTER = 24
FULL_TIME_CREDITS = 12


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
        if i in already_satisfied:
            continue
        if elective_upper in item.options:
            return i

    # Phase 2: wildcard match
    for i, item in enumerate(still_needed):
        if i in already_satisfied:
            continue
        if any(matches_wildcard(elective_upper, opt) for opt in item.options):
            return i

    return None


# ── Credit + title lookup ─────────────────────────────────────────────────────

async def get_course_data(
    session: AsyncSession,
    course_codes: list[str],
) -> dict[str, tuple[int, str | None, list[str]]]:
    """
    Returns {course_code: (credits, title, prerequisites)} for all known
    codes in one query. Defaults to (3, None, []) for unknown courses.

    One ANY(:codes) round-trip replaces the N+1 per-course lookups the
    original design would have made inside the semester assignment loop.
    """
    if not course_codes:
        return {}

    result = await session.execute(
        text(
            "SELECT course_code, credits, title, prerequisites FROM courses"
            " WHERE course_code = ANY(:codes)"
        ),
        {"codes": course_codes},
    )
    data: dict[str, tuple[int, str | None, list[str]]] = {
        row["course_code"]: (row["credits"], row["title"], row["prerequisites"] or [])
        for row in result.mappings()
    }

    for code in course_codes:
        if code not in data:
            logger.warning("Course %r not found in courses table — defaulting to 3 credits", code)
            data[code] = (3, None, [])

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
        still_needed=raw.still_needed,
    )


# ── Planner output types ──────────────────────────────────────────────────────

@dataclass
class PlannedCourse:
    course_code: str
    title:       str | None
    credits:     int
    badge:       str        # "Required" | "Elective" | "TBD"
    reason:      str


@dataclass
class SemesterCard:
    term:          str      # e.g. "202710"
    term_label:    str      # e.g. "Spring 2027"
    courses:       list[PlannedCourse] = field(default_factory=list)
    total_credits: int = 0


@dataclass
class GeneratedPlan:
    semesters:            list[SemesterCard]
    projected_graduation: str
    warnings:             list[str]


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
    credits:     int = 3
    title:       str | None = None
    badge:       str = "Required"   # "Required" | "Elective" | "TBD"
    reason:      str = ""
    must_be_last: bool = False


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


def _validate_credit_target(preferences: dict) -> int:
    """
    preferences is a raw, untyped dict from a public, unauthenticated API —
    credits_per_semester must be checked here, not assumed to already be a
    sane int just because every legitimate UI control sends one.
    """
    raw = preferences.get("credits_per_semester", 15)
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ParseValidationError(
            "credits_per_semester", f"must be a whole number, got {raw!r}."
        )
    if not (MIN_CREDITS_PER_SEMESTER <= raw <= MAX_CREDITS_PER_SEMESTER):
        raise ParseValidationError(
            "credits_per_semester",
            f"must be between {MIN_CREDITS_PER_SEMESTER} and "
            f"{MAX_CREDITS_PER_SEMESTER}, got {raw}.",
        )
    return raw


# ── Prerequisite dependency graph ─────────────────────────────────────────────

def _compute_prerequisite_dependencies(
    resolved: list[_ResolvedItem],
    completed: set[str],
    in_progress: set[str],
    prerequisites_by_code: dict[str, list[str]],
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

    A prerequisite already in `completed` or `in_progress` imposes no
    constraint. A prerequisite not found in `completed`, `in_progress`, or
    as another resolved item's course_code is assumed already satisfied
    (real DegreeWorks data is known to be incomplete) and is named in the
    returned warning instead of creating a dependency.

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
            if prereq_code in completed or prereq_code in in_progress:
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

    If initial_card is given, the very first term processed
    (start_term_idx) tops it up in place — adding courses/credits to that
    existing card rather than creating a new one — instead of starting a
    fresh semester. initial_card is never included in this function's
    return value; the caller already holds a reference to it. The
    force-add fallback (a single oversized course gets its own semester
    rather than blocking all progress) is skipped specifically on a
    topping-up pass: initial_card is guaranteed already non-empty (the
    caller only ever passes the last semester from a prior packing call,
    and a prior call's `if placed:` guard means every card it produced
    has at least one course), so placing nothing new there this term
    isn't a stuck state — leftover items simply proceed to the next,
    fresh term, where force-add resumes normally.
    """
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

        for item in items_pool:
            idx = index_by_item[id(item)]
            deps = depends_on[idx]
            if any(d not in placed_at or placed_at[d] >= term_idx for d in deps):
                blocked.append(item)
            elif credits_used + item.credits <= credit_target:
                placed.append(PlannedCourse(
                    course_code=item.course_code or "TBD",
                    title=item.title,
                    credits=item.credits,
                    badge=item.badge,
                    reason=item.reason,
                ))
                placed_at[idx] = term_idx
                credits_used += item.credits
            else:
                remaining.append(item)

        # Force-add if nothing fit (single course exceeds credit_target) —
        # only from `remaining` (eligible but over budget), never from
        # `blocked` (prerequisite not yet satisfied), and never on a
        # topping-up pass (see docstring).
        if not placed and remaining and not topping_up:
            forced = remaining[0]
            forced_idx = index_by_item[id(forced)]
            placed.append(PlannedCourse(
                course_code=forced.course_code or "TBD",
                title=forced.title,
                credits=forced.credits,
                badge=forced.badge,
                reason=forced.reason,
            ))
            placed_at[forced_idx] = term_idx
            credits_used = forced.credits
            remaining = remaining[1:]

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


# ── Main planner ──────────────────────────────────────────────────────────────

async def generate_plan(
    validated: ParsedDegreeValidated,
    preferences: dict,
    session: AsyncSession,
) -> GeneratedPlan:
    """
    Produces a semester-by-semester plan from a validated ParsedDegree.

    preferences keys:
      courses (list[str])        — student-chosen electives
      credits_per_semester (int) — MIN_CREDITS_PER_SEMESTER..MAX_CREDITS_PER_SEMESTER
    """
    warnings: list[str] = []

    credit_target: int = _validate_credit_target(preferences)
    student_electives: list[str] = [
        e.strip().upper() for e in preferences.get("courses", [])
    ]

    completed   = set(validated.completed_courses)
    in_progress = set(validated.in_progress_courses)
    all_excluded = completed | in_progress

    # ── 1. Early exit: already graduated ─────────────────────────────────────

    if not validated.still_needed and (validated.credits_remaining or 0) == 0:
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

    planning_terms = get_planning_terms(n=10)
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
        must_be_last = _is_last_semester_requirement(item.requirement)
        if i in satisfied_indices:
            code = next(e for e, idx in elective_to_req.items() if idx == i)
            resolved.append(_ResolvedItem(
                requirement=item.requirement,
                course_code=code,
                badge="Elective",
                reason=f"Your elective {code} satisfies '{item.requirement}'",
                must_be_last=must_be_last,
            ))
        else:
            best, num_available = await select_best_option(
                item, completed, in_progress, electives_to_place, current_term, session
            )
            is_choice = best is not None and num_available > 1
            resolved.append(_ResolvedItem(
                requirement=item.requirement,
                course_code=best,
                badge="Elective" if is_choice else ("Required" if best else "TBD"),
                reason=(
                    f"One of {num_available} options for '{item.requirement}'" if is_choice
                    else f"Required for {validated.majors[0]}" if best
                    else f"Requirement '{item.requirement}' — discuss with advisor."
                ),
                must_be_last=must_be_last,
            ))

    # Add unmatched electives as extra courses
    for code in electives_to_place:
        if code not in elective_to_req:
            resolved.append(_ResolvedItem(
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
        if r.course_code:
            credits, title, prereqs = course_data.get(r.course_code, (3, None, []))
            r.credits = credits
            # A course never scraped into `courses` has no title — the bare
            # code repeated as its own title ("IS350: IS350") is far less
            # informative than the DegreeWorks requirement name we already
            # have on hand ("Computers, Society, and Ethics").
            r.title   = title or r.requirement
            prerequisites_by_code[r.course_code] = prereqs

    # ── 5. Detect credit overflow ─────────────────────────────────────────────

    total_planned = sum(r.credits for r in resolved)
    available_credits = validated.credits_remaining or 0

    if total_planned > available_credits + 6:
        warnings.append(
            f"Your plan requires approximately {total_planned} credits, "
            f"but your remaining credits are listed as {available_credits}. "
            f"Some requirements may double-count. Verify with your advisor."
        )

    # ── 6. Compute prerequisite ordering, then sort within it ────────────────

    depends_on, prereq_warnings = _compute_prerequisite_dependencies(
        resolved, completed, in_progress, prerequisites_by_code,
    )
    warnings.extend(prereq_warnings)

    # A normal item's prerequisite may resolve to a senior/capstone-flagged
    # item (ADR-28). Phase 1 packing never places capstone items, so
    # placed_at would never gain an entry for that index and the normal
    # item would stay permanently blocked — an infinite loop in
    # _pack_semesters' `while items_pool:` with no `await` inside it,
    # hanging the whole event loop, not just one request. Treat this the
    # same way ADR-27 already treats an unverifiable prerequisite: drop
    # the edge so it can't block anything, and surface it in a warning
    # instead of silently ignoring it.
    capstone_indices = {i for i, r in enumerate(resolved) if r.must_be_last}
    cross_phase_flagged: set[str] = set()
    for i, deps in enumerate(depends_on):
        if resolved[i].must_be_last:
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

    normal_items   = [r for r in resolved if not r.must_be_last]
    capstone_items = [r for r in resolved if r.must_be_last]

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
        credit_target, planning_terms, placed_at,
    )

    if capstone_items:
        natural_start = _synchronized_capstone_start(
            capstone_items, depends_on, index_by_item, placed_at,
        )
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
            start_term_idx=start_term_idx, initial_card=initial_card,
        )
        semesters.extend(capstone_semesters)

        # Clustering every senior/capstone requirement into the true final
        # semester (above) can leave that semester thin — e.g. a single
        # 3-credit capstone with nothing else left to pack alongside it.
        # Pad it to a full-time load with one placeholder rather than
        # showing the student a near-empty final semester. Never pad past
        # the student's own credit_target — that's a hard ceiling the rest
        # of the planner already respects everywhere else.
        last = semesters[-1]
        pad_target = min(FULL_TIME_CREDITS, credit_target)
        if last.total_credits < pad_target:
            gap = pad_target - last.total_credits
            last.courses.append(PlannedCourse(
                course_code="FREE",
                title="Free Elective",
                credits=gap,
                badge="Elective",
                reason=(
                    f"Added to reach a full-time course load "
                    f"({FULL_TIME_CREDITS} credits) — pick any elective "
                    f"that interests you."
                ),
            ))
            last.total_credits = pad_target

    # ── 8. Prerequisite disclaimer ────────────────────────────────────────────

    warnings.append(
        "This plan orders courses using scraped prerequisite data, which "
        "may be incomplete for some courses. Verify with your advisor if a "
        "semester's course list looks unexpected."
    )

    return GeneratedPlan(
        semesters=semesters,
        projected_graduation=semesters[-1].term_label if semesters else "Unknown",
        warnings=warnings,
    )
