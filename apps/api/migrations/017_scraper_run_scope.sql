-- Record the actual requested scope without inventing coverage for old runs.
ALTER TABLE scraper_runs ADD COLUMN subjects TEXT[];

-- The schema gate allows constraint renames. Replace the known old definition,
-- rather than assuming that its original generated name was preserved.
DO $migration$
DECLARE old_check RECORD;
BEGIN
  FOR old_check IN
    SELECT conname FROM pg_constraint
    WHERE conrelid = 'scraper_runs'::regclass AND contype = 'c'
      AND pg_get_expr(conbin, conrelid) =
        $old$(status = ANY (ARRAY['running'::text, 'completed'::text, 'failed'::text, 'blocked'::text, 'schema_change'::text, 'skipped_overlap'::text]))$old$
  LOOP
    EXECUTE format('ALTER TABLE scraper_runs DROP CONSTRAINT %I', old_check.conname);
  END LOOP;
END;
$migration$;

ALTER TABLE scraper_runs ADD CONSTRAINT scraper_runs_status_check
  CHECK (status IN ('running', 'completed', 'partial', 'failed',
                    'blocked', 'schema_change', 'skipped_overlap'));

CREATE INDEX idx_scraper_runs_term_recent
  ON scraper_runs(scraper, term, started_at DESC, id DESC);
