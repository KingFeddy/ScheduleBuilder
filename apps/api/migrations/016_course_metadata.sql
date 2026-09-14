-- Legacy values retain their numbers/titles but acquire no invented provenance.
-- New unknown courses can exist without a placeholder title or credit amount.
ALTER TABLE courses
    ALTER COLUMN title DROP NOT NULL,
    ALTER COLUMN credits TYPE NUMERIC USING credits::numeric,
    ALTER COLUMN credits DROP NOT NULL,
    ADD COLUMN title_source JSONB,
    ADD COLUMN credits_source JSONB,
    ADD COLUMN metadata_latest_attempt JSONB;

ALTER TABLE courses
    ADD CONSTRAINT courses_credits_valid CHECK (
        credits IS NULL OR (credits >= 0 AND credits < 'Infinity'::numeric)
    ),
    ADD CONSTRAINT courses_title_source_object CHECK (
        title_source IS NULL OR jsonb_typeof(title_source) = 'object'
    ),
    ADD CONSTRAINT courses_credits_source_object CHECK (
        credits_source IS NULL OR jsonb_typeof(credits_source) = 'object'
    ),
    ADD CONSTRAINT courses_metadata_attempt_object CHECK (
        metadata_latest_attempt IS NULL OR jsonb_typeof(metadata_latest_attempt) = 'object'
    );

COMMENT ON COLUMN courses.credits IS
    'Fixed credits, nullable for unknown/variable values. Without credits_source this is unverified legacy data.';
COMMENT ON COLUMN courses.title_source IS
    'Validated title observation, including term, sections, timestamp, and original field evidence.';
COMMENT ON COLUMN courses.credits_source IS
    'Validated fixed/range/discrete-choice credits with source scope and evidence. Variable credits have no fixed scalar.';
COMMENT ON COLUMN courses.metadata_latest_attempt IS
    'Latest complete subject observation for this course, including missing/conflicting metadata; retained verified sources survive failures.';
