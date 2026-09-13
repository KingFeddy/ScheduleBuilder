"""Database contract used by the API, loaders, planner, and scrapers.

Keep this independent of the ledger: recorded SQL does not prove the live schema
is intact. Update the contract alongside new runtime SQL. Deprecated section
time columns are deliberately not required.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Column:
    sql_type: str
    nullable: bool = False
    default: str | None = None
    generated: str | None = None


COLUMNS = {
    "courses": {
        "course_code": Column("text"),
        "title": Column("text", nullable=True),
        "credits": Column("numeric", nullable=True),
        "title_source": Column("jsonb", nullable=True),
        "credits_source": Column("jsonb", nullable=True),
        "metadata_latest_attempt": Column("jsonb", nullable=True),
        "prerequisites": Column("text[]", default="'{}'::text[]"),
        "prerequisites_status": Column("text", default="'unverified'::text"),
        "prerequisites_attempted_at": Column("timestamp with time zone", nullable=True),
        "prerequisites_verified_at": Column("timestamp with time zone", nullable=True),
        "prerequisites_error": Column("text", nullable=True),
        "prerequisites_rules": Column("jsonb", nullable=True),
        "prerequisites_source": Column("jsonb", nullable=True),
        "prerequisites_latest_attempt": Column("jsonb", nullable=True),
    },
    "sections": {
        "crn": Column("text"),
        "term": Column("text"),
        "course_code": Column("text"),
        "professor_name": Column("text", nullable=True),
        "total_seats": Column("integer", default="0"),
        "open_seats": Column("integer", default="0"),
        "location": Column("text", nullable=True),
        "scraped_at": Column("timestamp with time zone", nullable=True),
        "section_number": Column("text", nullable=True),
    },
    "meetings": {
        "id": Column("bigint", default="sequence"),
        "crn": Column("text"),
        "term": Column("text"),
        "days": Column("text", nullable=True),
        "start_time": Column("time without time zone", nullable=True),
        "end_time": Column("time without time zone", nullable=True),
        "location": Column("text", nullable=True),
    },
    "professors": {
        "professor_name": Column("text"),
        "department": Column("text", nullable=True),
    },
    "scraper_runs": {
        "id": Column("bigint", default="sequence"),
        "scraper": Column("text"),
        "subject": Column("text", nullable=True),
        "term": Column("text", nullable=True),
        "status": Column("text"),
        "sections_upserted": Column("integer", nullable=True),
        "sections_failed": Column("integer", nullable=True, default="0"),
        "error_message": Column("text", nullable=True),
        "started_at": Column("timestamp with time zone", default="now()"),
        "finished_at": Column("timestamp with time zone", nullable=True),
        "duration_ms": Column(
            "integer", nullable=True,
            generated="((EXTRACT(epoch FROM (finished_at - started_at)) * (1000)::numeric))::integer",
        ),
    },
    "rmp_cache": {
        "professor_name": Column("text"),
        "rmp_data": Column("jsonb", nullable=True),
        "cached_at": Column("timestamp with time zone", default="now()"),
        "expires_at": Column("timestamp with time zone"),
    },
}

PRIMARY_KEYS = {
    "courses": ("course_code",),
    "sections": ("crn", "term"),
    "meetings": ("id",),
    "professors": ("professor_name",),
    "scraper_runs": ("id",),
    "rmp_cache": ("professor_name",),
}

# Scraper ON CONFLICT needs an immediate unique index on these exact keys.
UNIQUE_KEYS = {"meetings": ("crn", "term", "days", "start_time", "end_time")}

# Source table/columns, referenced table/columns. Both cascade on delete.
FOREIGN_KEYS = (
    ("sections", ("course_code",), "courses", ("course_code",)),
    ("meetings", ("crn", "term"), "sections", ("crn", "term")),
)

# PostgreSQL's deparsed expressions, compared without insignificant whitespace.
# Match definitions, not names. Equivalent alternative definitions require review
# and an explicit contract update; arbitrary SQL equivalence is not inferred.
CHECKS = {
    "courses": {
        "credits_valid": "((credits IS NULL) OR ((credits >= (0)::numeric) AND (credits < 'Infinity'::numeric)))",
        "title_source_object": "((title_source IS NULL) OR (jsonb_typeof(title_source) = 'object'::text))",
        "credits_source_object": "((credits_source IS NULL) OR (jsonb_typeof(credits_source) = 'object'::text))",
        "metadata_attempt_object": "((metadata_latest_attempt IS NULL) OR (jsonb_typeof(metadata_latest_attempt) = 'object'::text))",
        "prerequisites_status": "(prerequisites_status = ANY (ARRAY['unverified'::text, 'verified'::text, 'verified_empty'::text, 'failed'::text, 'unresolved'::text]))",
        "prerequisites_rules_object": "((prerequisites_rules IS NULL) OR (jsonb_typeof(prerequisites_rules) = 'object'::text))",
        "prerequisites_source_object": "((prerequisites_source IS NULL) OR (jsonb_typeof(prerequisites_source) = 'object'::text))",
        "prerequisites_attempt_object": "((prerequisites_latest_attempt IS NULL) OR (jsonb_typeof(prerequisites_latest_attempt) = 'object'::text))",
        "prerequisites_source_pair": "((prerequisites_rules IS NULL) = (prerequisites_source IS NULL))",
    },
    "meetings": {
        "time_pair": "(((start_time IS NULL) AND (end_time IS NULL)) OR ((start_time IS NOT NULL) AND (end_time IS NOT NULL)))",
        "time_order": "((start_time IS NULL) OR (start_time < end_time))",
    },
    "scraper_runs": {
        "scraper": "(scraper = ANY (ARRAY['banner'::text, 'rmp'::text]))",
        "status": "(status = ANY (ARRAY['running'::text, 'completed'::text, 'failed'::text, 'blocked'::text, 'schema_change'::text, 'skipped_overlap'::text]))",
    },
}

# Any valid nonpartial btree with these leading keys supports the access path;
# names are immaterial and a unique index may provide the same coverage.
INDEXES = {
    "sections": (("course_code", "term"), ("term",)),
    "meetings": (("crn", "term"),),
    "scraper_runs": (("scraper", "started_at"),),
    "rmp_cache": (("expires_at",),),
}
