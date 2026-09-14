"""Preview official undergraduate catalog metadata; use --apply to persist it."""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
import json
import os
import sys

from sqlalchemy import make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.scrapers.catalog_metadata import fetch_catalog_page, import_catalog_page, parse_catalog_page


async def run(args) -> int:
    try:
        page = parse_catalog_page(await fetch_catalog_page(args.url), url=args.url,
                                  subject=args.subject, catalog_year=args.catalog_year)
    except ValueError as error:
        # Parsing errors contain bounded public source details, never DB settings.
        print(f"Catalog page rejected: {error}", file=sys.stderr)
        return 1
    if not args.apply:
        # Preview is source data, not a database diff; no settings or DB are loaded.
        print(json.dumps({"mode": "preview", "database_accessed": False,
                          "candidate_count": len(page.courses), **asdict(page)}, indent=2))
        return 0
    raw_url = os.environ.get("DATABASE_URL", "")
    try:
        url = make_url(raw_url)
        valid = url.drivername == "postgresql+asyncpg" and bool(url.host) and bool(url.database)
    except (ArgumentError, ValueError, TypeError):
        valid = False
    if not valid:
        raise ValueError("Set DATABASE_URL explicitly to a postgresql+asyncpg URL with a host and database.")
    from scripts.verify_migrations import verify
    if not await verify(raw_url):
        return 1
    engine = create_async_engine(raw_url, pool_size=3, connect_args={"timeout": 10})
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            count = await import_catalog_page(session, page)
        print(f"Applied metadata policy to {count} catalog candidates. Section and prerequisite data unchanged.")
    finally:
        await engine.dispose()
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="Official undergraduate department page")
    parser.add_argument("--subject", required=True, help="One uppercase subject prefix, such as PHYS")
    parser.add_argument("--catalog-year", required=True, type=int, help="First year of the catalog edition, such as 2026")
    parser.add_argument("--apply", action="store_true", help="Write candidates after schema verification; default only previews source records")
    args = parser.parse_args(argv)
    try:
        return asyncio.run(run(args))
    except Exception:
        # Driver/configuration exceptions may contain credentials or SQL parameters.
        print("Catalog import failed; no page was partially applied. Check the URL, edition, subject, database schema/access, and writer lock.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
