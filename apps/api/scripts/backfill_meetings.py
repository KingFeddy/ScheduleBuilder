"""Preview or apply a conservative backfill from retained section time columns.

BACKFILL_DATABASE_URL=... python -m scripts.backfill_meetings [--apply]
The default is read-only. Existing meeting patterns are never replaced or extended.
Flat legacy data cannot reconstruct different lecture/lab patterns: review source
data and refresh through the scraper before attesting production correctness.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import json
import os
import re
import sys

from sqlalchemy import make_url, text
from sqlalchemy.exc import ArgumentError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from scripts.migrate import MigrationError, _select_schema, parse_verified_since
from src.services.meeting_integrity import meeting_kind


@dataclass
class CoverageReport:
    total_sections: int
    meeting_count: int
    legacy_columns_present: bool
    candidates: list[dict] = field(default_factory=list)
    issues: list[dict] = field(default_factory=list)
    inserted: int = 0

    def as_dict(self) -> dict:
        return {
            "total_sections": self.total_sections,
            "meeting_count": self.meeting_count,
            "legacy_columns_present": self.legacy_columns_present,
            "would_insert": len(self.candidates),
            "inserted": self.inserted,
            "unresolved": len(self.issues),
            "candidates": self.candidates,
            "issues": self.issues,
        }


class BackfillError(MigrationError):
    def __init__(self, message: str, report: CoverageReport | None = None):
        super().__init__(message)
        self.report = report


def _covers_legacy(section, meetings) -> bool:
    """Every legacy day/time interval must be covered, including split patterns."""
    for day in section["days"]:
        cursor = section["start_time"]
        intervals = sorted(
            (meeting["start_time"], meeting["end_time"])
            for meeting in meetings if meeting["days"] and day in meeting["days"]
        )
        for start, end in intervals:
            if start > cursor:
                break
            cursor = max(cursor, end)
        if cursor < section["end_time"]:
            return False
    return True


async def inspect_coverage(
    connection: AsyncConnection, *, schema_name: str = "public", term: str | None = None,
) -> CoverageReport:
    if term is not None and not re.fullmatch(r"[0-9]{4}(10|50|90)", term):
        raise BackfillError("Use a six-digit Fall, Spring, or Summer term code.")
    await _select_schema(connection, schema_name)
    tables = set((await connection.execute(text("""
        SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = :schema AND c.relkind IN ('r', 'p')
    """), {"schema": schema_name})).scalars())
    if not {"sections", "meetings"} <= tables:
        raise BackfillError("Both sections and meetings are required; apply migration 007 first.")
    legacy = set((await connection.execute(text("""
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = :schema AND table_name = 'sections'
          AND column_name IN ('days', 'start_time', 'end_time')
    """), {"schema": schema_name})).scalars())
    if len(legacy) not in {0, 3}:
        raise BackfillError("Legacy time columns are only partially present; reconcile the schema first.")
    # SQL fragments are fixed alternatives, not operator-provided identifiers.
    legacy_fields = "days, start_time, end_time" if legacy else "NULL::text AS days, NULL::time AS start_time, NULL::time AS end_time"
    sections = (await connection.execute(text(f"""
        SELECT crn, term, location, {legacy_fields} FROM sections
        WHERE (CAST(:term AS text) IS NULL OR term = :term) ORDER BY term, crn
    """), {"term": term})).mappings().all()
    meetings = (await connection.execute(text("""
        SELECT crn, term, days, start_time, end_time FROM meetings
        WHERE (CAST(:term AS text) IS NULL OR term = :term)
    """), {"term": term})).mappings().all()
    by_section = {}
    for meeting in meetings:
        by_section.setdefault((meeting["crn"], meeting["term"]), []).append(meeting)

    report = CoverageReport(len(sections), len(meetings), bool(legacy))
    for section in sections:
        current = by_section.get((section["crn"], section["term"]), [])
        kind = meeting_kind(section["days"], section["start_time"], section["end_time"])
        issue = None
        if current:
            if any(meeting_kind(m["days"], m["start_time"], m["end_time"]) == "invalid" for m in current):
                issue = "Existing meeting data is incomplete or invalid; refresh from the source."
            elif kind == "invalid":
                issue = "Legacy time data is invalid; reconcile it against the source."
            elif kind == "timed" and not _covers_legacy(section, current):
                issue = "Existing meetings do not cover every legacy day/time; preserve them and reconcile the source."
        elif kind == "timed" and legacy:
            report.candidates.append(dict(section))
        else:
            issue = "No meeting rows and no complete legacy time pattern; asynchronous status is unverified."
        if issue:
            report.issues.append({"crn": section["crn"], "term": section["term"], "reason": issue})
    return report


async def apply_backfill(
    connection: AsyncConnection, *, schema_name: str = "public", term: str | None = None,
) -> CoverageReport:
    """Caller commits or rolls back the complete batch in a READ COMMITTED transaction."""
    if not connection.in_transaction():
        raise BackfillError("Backfill requires an enclosing transaction.")
    await _select_schema(connection, schema_name)
    # Serialize with other backfills and scraper writes, then inspect fresh data.
    await connection.execute(text("LOCK TABLE sections, meetings IN SHARE ROW EXCLUSIVE MODE"))
    report = await inspect_coverage(connection, schema_name=schema_name, term=term)
    if report.issues:
        raise BackfillError("Backfill blocked by unresolved meeting data; no changes were applied.", report)
    inserted = 0
    for candidate in report.candidates:
        result = await connection.execute(text("""
            INSERT INTO meetings (crn, term, days, start_time, end_time, location)
            VALUES (:crn, :term, :days, :start_time, :end_time, :location)
            RETURNING id
        """), candidate)
        inserted += len(result.all())
    result = await inspect_coverage(connection, schema_name=schema_name, term=term)
    if result.issues or result.candidates:
        raise BackfillError("Backfill left unresolved coverage; roll back the transaction.", result)
    result.inserted = inserted
    return result


def cleanup_errors(
    report: CoverageReport, *, production_verified_since: datetime | None,
    production_verification_note: str | None, now: datetime,
) -> list[str]:
    """Coverage is measured; production correctness is an explicit operator attestation."""
    errors = []
    if not report.legacy_columns_present:
        errors.append("Legacy columns are absent; reconcile migration history before cleanup.")
    if not report.total_sections or not report.meeting_count or report.candidates or report.issues:
        errors.append("Meeting coverage is incomplete: every section in every term needs verified meeting rows.")
    if not production_verification_note or not production_verification_note.strip():
        errors.append("Production verification needs a note identifying the reviewed scraper and multi-pattern evidence.")
    if (
        production_verified_since is None or production_verified_since.tzinfo is None
        or now - production_verified_since < timedelta(days=14)
    ):
        errors.append("Production meetings must have been explicitly verified for at least 14 days (timezone-aware timestamp required).")
    return errors


async def guard_meetings_cleanup(
    connection: AsyncConnection, *, schema_name: str,
    production_verified_since: datetime | None, production_verification_note: str | None,
) -> None:
    await _select_schema(connection, schema_name)
    # Check and drop under the same lock/transaction; a prior preview is insufficient.
    await connection.execute(text("LOCK TABLE sections, meetings IN ACCESS EXCLUSIVE MODE"))
    report = await inspect_coverage(connection, schema_name=schema_name)
    errors = cleanup_errors(
        report, production_verified_since=production_verified_since,
        production_verification_note=production_verification_note,
        now=await connection.scalar(text("SELECT CURRENT_TIMESTAMP")),
    )
    if errors:
        raise BackfillError("Cleanup refused: " + " ".join(errors), report)


async def _run(args, database_url: str) -> int:
    engine = create_async_engine(database_url, isolation_level="READ COMMITTED", connect_args={"timeout": 10})
    try:
        async with engine.begin() as connection:
            if not args.apply:
                await connection.execute(text("SET TRANSACTION READ ONLY"))
            await connection.execute(text("SET LOCAL lock_timeout = '5s'"))
            await connection.execute(text("SET LOCAL statement_timeout = '30s'"))
            if args.apply:
                report = await apply_backfill(connection, schema_name=args.schema, term=args.term)
            else:
                report = await inspect_coverage(connection, schema_name=args.schema, term=args.term)
            output = report.as_dict()
            output["mode"] = "apply" if args.apply else "dry-run"
            errors = []
            if args.check_cleanup:
                errors = cleanup_errors(
                    report, production_verified_since=args.production_verified_since,
                    production_verification_note=args.production_verification_note,
                    now=await connection.scalar(text("SELECT CURRENT_TIMESTAMP")),
                )
                output["cleanup_errors"] = errors
                output["cleanup_ready"] = not errors
        # Report inserted counts only after commit succeeds.
        print(json.dumps(output, default=str, indent=2))
        return 1 if errors or report.issues else 0
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="Apply the complete backfill batch")
    mode.add_argument("--check-cleanup", action="store_true", help="Read-only check of migration 008 safeguards")
    parser.add_argument("--schema", default="public")
    parser.add_argument("--term", help="Optional backfill scope; cleanup always checks every term")
    parser.add_argument("--production-verified-since", type=parse_verified_since)
    parser.add_argument("--production-verification-note")
    args = parser.parse_args()
    if args.check_cleanup and args.term:
        parser.error("Cleanup checks must cover every term; omit --term.")
    if not args.check_cleanup and (args.production_verified_since or args.production_verification_note):
        parser.error("Production verification arguments are only used with --check-cleanup.")
    database_url = os.environ.get("BACKFILL_DATABASE_URL")
    try:
        url = make_url(database_url or "")
        valid = url.drivername == "postgresql+asyncpg" and bool(url.host) and bool(url.database)
    except (ArgumentError, ValueError, TypeError):
        valid = False
    if not valid:
        print("Set BACKFILL_DATABASE_URL explicitly to a postgresql+asyncpg URL with a host and database.", file=sys.stderr)
        return 2
    try:
        return asyncio.run(_run(args, database_url))
    except BackfillError as error:
        if error.report is not None:
            print(json.dumps(error.report.as_dict(), default=str, indent=2))
        print(str(error), file=sys.stderr)
        return 1
    except MigrationError as error:
        print(str(error), file=sys.stderr)
        return 1
    except Exception:
        print("Backfill command failed; its transaction was rolled back. Check database access and data constraints.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
