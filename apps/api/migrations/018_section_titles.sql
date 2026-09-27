-- Topics belong to individual sections, not the shared course number.
ALTER TABLE sections ADD COLUMN IF NOT EXISTS section_title TEXT;

-- Recover only titles observed for this exact course, term and CRN. Never
-- copy the course-wide title: that may describe a different special topic.
WITH observations AS (
    SELECT c.course_code, evidence.doc->>'term' AS term,
           observation->>'crn' AS crn,
           btrim(regexp_replace(observation->>'courseTitle', '\s+', ' ', 'g')) AS title,
           evidence.priority
    FROM courses c
    CROSS JOIN LATERAL (VALUES
        (c.metadata_latest_attempt, 0), (c.title_source, 1)
    ) AS evidence(doc, priority)
    CROSS JOIN LATERAL jsonb_array_elements(
        CASE WHEN jsonb_typeof(evidence.doc->'observations') = 'array'
             THEN evidence.doc->'observations' ELSE '[]'::jsonb END
    ) AS observation
    WHERE jsonb_typeof(observation->'courseTitle') = 'string'
), candidates AS (
    SELECT course_code, term, crn, priority, min(title) AS title
    FROM observations
    WHERE length(title) BETWEEN 1 AND 512
    GROUP BY course_code, term, crn, priority
    HAVING count(DISTINCT title) = 1
), preferred AS (
    SELECT DISTINCT ON (course_code, term, crn) course_code, term, crn, title
    FROM candidates ORDER BY course_code, term, crn, priority
)
UPDATE sections s SET section_title = p.title
FROM preferred p
WHERE s.course_code = p.course_code AND s.term = p.term AND s.crn = p.crn
  AND s.section_title IS NULL;
