"""Time the current solver on deterministic synthetic inputs, without DB/network I/O.

Run from apps/api: uv run --no-sync python -m scripts.benchmark_solver
Includes filtering, search, ranking, validation, and response-model construction.
Excludes input construction, database loading, JSON encoding, HTTP, and rendering.
These samples are not a production capacity test or a worst-case guarantee.
"""
from __future__ import annotations

import argparse
from datetime import time
import json
import math
import platform
import random
import statistics
from time import perf_counter_ns

from src.scheduler.config import MAX_RESULTS, EXPLORE_LIMIT, SOLVE_TIME_BUDGET_MS
from src.scheduler.models import MeetingSlot, SectionSlot
from src.scheduler.solver import solve
from src.schemas.schedule import CommuterOptions


def build_case(courses: int, sections: int, *, overlapping: bool = False):
    rng = random.Random(202690)
    candidates = {}
    for course in range(courses):
        code = f"CS{100 + course}"
        candidates[code] = []
        for section in range(sections):
            crn = f"{course + 1}{section:04d}"
            # Separate windows form an easy case; shared windows require pruning.
            start = (480 + rng.randrange(12) * 60 if overlapping
                     else 480 + course * 100 + section * 5)
            end = start + 50
            days = rng.choice(["MW", "TR"]) if overlapping else "MW"
            candidates[code].append(SectionSlot(
                crn=crn, term="202690", course_code=code,
                professor_name=f"Synthetic Professor {section}",
                total_seats=30, open_seats=10, section_number=f"{section + 1:03d}",
                meetings=[MeetingSlot(
                    crn, "202690", days, time(*divmod(start, 60)), time(*divmod(end, 60)),
                )],
            ))
    return candidates


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=200)
    parser.add_argument("--warmup", type=int, default=10)
    args = parser.parse_args()
    if args.runs < 1 or args.warmup < 0:
        parser.error("runs must be positive and warmup nonnegative")

    options = CommuterOptions(minimize_gaps=True)
    report = {
        "python": platform.python_version(), "platform": platform.platform(),
        "max_results": MAX_RESULTS, "explore_limit": EXPLORE_LIMIT,
        "search_budget_ms": SOLVE_TIME_BUDGET_MS,
        "runs_per_case": args.runs, "warmup_per_case": args.warmup,
        "synthetic": True, "compact_week": True, "minimize_gaps": True,
        "cases": [],
    }
    for courses, sections, overlapping in [(2, 5, False), (5, 5, False), (5, 8, True), (8, 8, True)]:
        candidates = build_case(courses, sections, overlapping=overlapping)
        codes = list(candidates)
        for _ in range(args.warmup):
            solve(codes, candidates, options, {}, compact_week=True)
        durations = []
        counts = []
        timeouts = 0
        for _ in range(args.runs):
            start = perf_counter_ns()
            result = solve(codes, candidates, options, {}, compact_week=True)
            durations.append((perf_counter_ns() - start) / 1_000_000)
            counts.append(len(result.results))
            timeouts += int(result.truncated)
        durations.sort()
        report["cases"].append({
            "courses": courses, "sections_per_course": sections,
            "overlapping_candidates": overlapping,
            "mean_ms": round(statistics.mean(durations), 3),
            "median_ms": round(statistics.median(durations), 3),
            "p95_ms": round(durations[math.ceil(len(durations) * .95) - 1], 3),
            "max_ms": round(max(durations), 3),
            "under_5_ms_percent": round(sum(ms < 5 for ms in durations) / args.runs * 100, 1),
            "returned_schedules_min": min(counts), "returned_schedules_max": max(counts),
            "search_timeouts": timeouts,
        })
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
