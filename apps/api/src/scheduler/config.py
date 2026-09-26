MAX_COURSES = 8       # Maximum courses in a single solve request
MAX_RESULTS = 99      # Maximum schedules returned to the client
# Explore enough candidates to fill the displayed limit, without truncating
# the ranked pool a second time.
EXPLORE_LIMIT = MAX_RESULTS
SOLVE_TIME_BUDGET_MS = 800   # Hard wall-clock limit per request (milliseconds)
NODE_CHECK_INTERVAL = 500    # Check the time budget every N nodes explored
MIN_SIGNIFICANT_GAP_MINUTES = 10   # Gaps at or under this are a normal passing
                                    # period, not a real break — excluded from
                                    # both gap_count and gap_minutes entirely
