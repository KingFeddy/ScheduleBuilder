-- Goal 14: distinguish verified course-code extraction from failed/unknown data.
-- Additive only: preserve every existing prerequisite array as unverified.
-- A failed attempt retains the array and its last successful verification time.
ALTER TABLE courses
    ADD COLUMN prerequisites_status TEXT NOT NULL DEFAULT 'unverified',
    ADD COLUMN prerequisites_attempted_at TIMESTAMPTZ,
    ADD COLUMN prerequisites_verified_at TIMESTAMPTZ,
    ADD COLUMN prerequisites_error TEXT,
    ADD CONSTRAINT courses_prerequisites_status_check CHECK (
        prerequisites_status IN ('unverified', 'verified', 'verified_empty', 'failed', 'unresolved')
    );

COMMENT ON COLUMN courses.prerequisites_status IS
    'Latest course-code extraction outcome, not full prerequisite-rule or student-eligibility verification.';
COMMENT ON COLUMN courses.prerequisites_verified_at IS
    'Last successful course-code extraction; retained with prerequisites when a later attempt fails.';
