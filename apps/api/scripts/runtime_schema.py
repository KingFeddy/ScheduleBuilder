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
        "title": Column("text"),
        "credits": Column("integer"),
        "prerequisites": Column("text[]", default="'{}'::text[]"),
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
