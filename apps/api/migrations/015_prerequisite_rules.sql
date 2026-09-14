-- Structured, source-scoped rules replace the flattened array as the authority.
-- Keep every legacy array intact; it cannot establish AND/OR, grade, or timing rules.
ALTER TABLE courses
    ADD COLUMN prerequisites_rules JSONB,
    ADD COLUMN prerequisites_source JSONB,
    ADD COLUMN prerequisites_latest_attempt JSONB;

UPDATE courses SET
    prerequisites_status = CASE
        WHEN prerequisites_status IN ('verified', 'verified_empty') THEN 'unverified'
        ELSE prerequisites_status
    END,
    prerequisites_verified_at = NULL;

ALTER TABLE courses
    ADD CONSTRAINT courses_prerequisites_rules_object CHECK (
        prerequisites_rules IS NULL OR jsonb_typeof(prerequisites_rules) = 'object'
    ),
    ADD CONSTRAINT courses_prerequisites_source_object CHECK (
        prerequisites_source IS NULL OR jsonb_typeof(prerequisites_source) = 'object'
    ),
    ADD CONSTRAINT courses_prerequisites_attempt_object CHECK (
        prerequisites_latest_attempt IS NULL OR jsonb_typeof(prerequisites_latest_attempt) = 'object'
    ),
    ADD CONSTRAINT courses_prerequisites_source_pair CHECK (
        (prerequisites_rules IS NULL) = (prerequisites_source IS NULL)
    );

COMMENT ON COLUMN courses.prerequisites IS
    'Legacy, unverified AND-only course-code projection. Never use it to infer structured rules or eligibility.';
COMMENT ON COLUMN courses.prerequisites_status IS
    'Latest structured-source extraction outcome, not student eligibility or catalog-wide/term-independent verification.';
COMMENT ON COLUMN courses.prerequisites_verified_at IS
    'Last successful structured rules/evidence save. Legacy extraction timestamps were invalidated by migration 015.';
COMMENT ON COLUMN courses.prerequisites_rules IS
    'Versioned prerequisite/corequisite expression trees, scoped to the observed term and representative section CRN.';
COMMENT ON COLUMN courses.prerequisites_source IS
    'Original source evidence associated with the retained rules; changed only together with those rules.';
COMMENT ON COLUMN courses.prerequisites_latest_attempt IS
    'Latest candidate rules, source evidence, scope, and outcome, including failed and unresolved attempts.';
