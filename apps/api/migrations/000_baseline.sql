-- Baseline for a fresh database: tables required before migration 007.
-- This is the retained application's base schema, not a reconstruction of the
-- removed subsystems. Existing untracked databases require explicit reconciliation.

CREATE TABLE courses (
  course_code TEXT PRIMARY KEY,
  title       TEXT NOT NULL,
  credits     INTEGER NOT NULL
);

CREATE TABLE sections (
  crn            TEXT NOT NULL,
  term           TEXT NOT NULL,
  course_code    TEXT NOT NULL REFERENCES courses(course_code) ON DELETE CASCADE,
  professor_name TEXT,
  total_seats    INTEGER NOT NULL DEFAULT 0,
  open_seats     INTEGER NOT NULL DEFAULT 0,
  location       TEXT,
  scraped_at     TIMESTAMPTZ,
  -- Retained until the separate migration 008 backfill/coverage gates are met.
  days           TEXT,
  start_time     TIME,
  end_time       TIME,
  PRIMARY KEY (crn, term)
);

CREATE INDEX idx_sections_course_term ON sections(course_code, term);
CREATE INDEX idx_sections_term ON sections(term);

CREATE TABLE professors (
  professor_name TEXT PRIMARY KEY,
  department     TEXT
);
