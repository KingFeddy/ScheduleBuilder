MAX_COURSES = 8       # Maximum courses in a single solve request
MAX_RESULTS = 30      # Maximum schedules returned to the client — kept above
                       # EXPLORE_LIMIT so it never truncates the explored pool.
                       # Ranking toggles (Minimize Gaps, Compact Week) only
                       # reorder that pool; if MAX_RESULTS were smaller than
                       # EXPLORE_LIMIT, reordering could silently push
                       # candidates (e.g. free-day schedules) out of view
                       # even though they were never actually filtered.
EXPLORE_LIMIT = 25    # Backtracker stops after finding this many valid schedules
SOLVE_TIME_BUDGET_MS = 800   # Hard wall-clock limit per request (milliseconds)
NODE_CHECK_INTERVAL = 500    # Check the time budget every N nodes explored
