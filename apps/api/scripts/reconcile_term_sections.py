"""Fill missing sections from a complete Banner term inventory; preview by default.

Existing sections and historical courses are retained. This repairs coverage,
not a claim that every existing seat count or prerequisite was refreshed.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from datetime import datetime, timezone, timedelta
import gzip
import json
import os
from pathlib import Path
import re

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.scrapers.banner import (
    BANNER_BASE, PAGE_SIZE, _clean_course_title, _upsert_section_with_meetings, _validate_results_page,
)
from src.scrapers.course_metadata import metadata_observation, refresh_course_metadata
from src.scrapers.lock import advisory_lock, BANNER_SCRAPER_LOCK_ID
from src.scrapers.term_inventory import TermInventory, collect_term_inventory
from src.terms import TERM_CODE_PATTERN


def load_inventory(path: Path, term: str) -> TermInventory:
    data = json.loads(path.read_text())
    rows = data['sections']
    if data['term'] != term or not isinstance(rows, list) or not rows or data['total'] != len(rows):
        raise ValueError('Snapshot term/count is incomplete or inconsistent.')
    seen: set[str] = set()
    for offset in range(0, len(rows), PAGE_SIZE):
        page, _ = _validate_results_page(
            {'success': True, 'data': rows[offset:offset + PAGE_SIZE], 'totalCount': len(rows)},
            subject=None, term=term, offset=offset, page_size=PAGE_SIZE,
            expected_total=len(rows), received_crns=seen,
        )
        seen.update(row['courseReferenceNumber'] for row in page)
    started, finished = (datetime.fromisoformat(data[key]) for key in ('started_at', 'finished_at'))
    if started.tzinfo is None or finished.tzinfo is None or not started <= finished <= datetime.now(timezone.utc):
        raise ValueError('Invalid snapshot observation times.')
    return TermInventory(term, started, finished, rows)


async def compare(session, inventory: TermInventory) -> dict:
    rows = (await session.execute(text('SELECT crn, course_code FROM sections WHERE term=:term'),
                                  {'term': inventory.term})).mappings().all()
    await session.rollback()
    stored = {row['crn']: row['course_code'] for row in rows}
    live = {row['courseReferenceNumber']: row['subject'] + row['courseNumber'] for row in inventory.sections}
    mismatches = sorted(crn for crn in live.keys() & stored.keys() if live[crn] != stored[crn])
    return {'term': inventory.term, 'banner_sections': len(live), 'banner_courses': len(set(live.values())),
            'stored_sections': len(stored), 'missing_crns': sorted(live.keys() - stored.keys()),
            'extra_crns': sorted(stored.keys() - live.keys()), 'identity_mismatches': mismatches}


async def backup_catalog(engine, term: str, directory: Path) -> Path:
    """Independent, consistent row backup of all courses and this term's sections/meetings."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f'catalog-before-{term}-{datetime.now(timezone.utc):%Y%m%dT%H%M%S%fZ}.json.gz'
    tables = {}
    async with engine.connect() as connection:
        await connection.execution_options(isolation_level='REPEATABLE READ')
        async with connection.begin():
            await connection.execute(text('SET TRANSACTION READ ONLY'))
            for table in ('courses', 'sections', 'meetings'):
                where = '' if table == 'courses' else ' WHERE term=:term'
                result = await connection.execute(text(f'SELECT to_jsonb(t) FROM {table} t{where}'), {'term': term})
                tables[table] = list(result.scalars())
    payload = {'format': 'catalog-row-backup-v1', 'term': term, 'tables': tables}
    # Exclusive creation and owner-only permissions; never write credentials.
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'wb') as raw:
        with gzip.GzipFile(fileobj=raw, mode='wb') as compressed:
            compressed.write(json.dumps(payload).encode())
    with gzip.open(path, 'rt') as saved:
        restored = json.load(saved)
    if restored != payload:
        raise ValueError('Backup verification failed; no records will be imported.')
    return path


async def run(args) -> None:
    if not re.fullmatch(TERM_CODE_PATTERN, args.term):
        raise ValueError('Invalid term.')
    inventory = load_inventory(args.snapshot, args.term) if args.snapshot else await collect_term_inventory(args.term)
    if args.apply and datetime.now(timezone.utc) - inventory.started_at > timedelta(hours=1):
        raise ValueError('Collect a fresh snapshot before applying; this snapshot is over an hour old.')
    from src.config import settings
    from scripts.verify_migrations import verify
    if args.apply and not await verify(settings.DATABASE_URL):
        raise ValueError('Database schema verification failed.')
    engine = create_async_engine(settings.DATABASE_URL, pool_size=3, connect_args={'timeout': 15})
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            if not args.apply:
                print(json.dumps(await compare(session, inventory), indent=2))
                return
            async with advisory_lock(session, BANNER_SCRAPER_LOCK_ID, 'term-inventory') as acquired:
                if not acquired:
                    raise ValueError('Another scraper holds the writer lock; retry after it finishes.')
                before = await compare(session, inventory)
                if before['identity_mismatches']:
                    raise ValueError('Existing section identities differ from Banner; manual review required.')
                missing = set(before['missing_crns'])
                if not missing:
                    print(json.dumps({'before': before, 'added_sections': 0, 'after': before}, indent=2))
                    return
                backup = await backup_catalog(engine, args.term, args.backup_dir)
                print(f'Verified catalog row backup: {backup}', flush=True)
                affected = set()
                added = 0
                for row in inventory.sections:
                    if row['courseReferenceNumber'] not in missing:
                        continue
                    await _upsert_section_with_meetings(session, row, args.term)
                    affected.add(row['subject'] + row['courseNumber'])
                    added += 1
                    if added % 25 == 0:
                        print(f'Added {added}/{len(missing)} sections', flush=True)
                observations = defaultdict(list)
                for row in inventory.sections:
                    code = row['subject'] + row['courseNumber']
                    if code in affected:
                        observations[code].append(metadata_observation(row))
                for code, rows in observations.items():
                    await refresh_course_metadata(session, code, args.term, rows, _clean_course_title,
                                                  source_url=f'{BANNER_BASE}/searchResults/searchResults')
                after = await compare(session, inventory)
                print(json.dumps({'before': before, 'added_sections': added, 'after': after,
                                  'backup': str(backup)}, indent=2))
                if after['missing_crns'] or after['identity_mismatches']:
                    raise ValueError('Coverage verification failed after import.')
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--term', required=True)
    parser.add_argument('--snapshot', type=Path, help='Previously collected complete Banner JSON snapshot')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--backup-dir', type=Path, help='Required independent catalog row backup directory when applying')
    args = parser.parse_args()
    if args.apply and args.backup_dir is None:
        parser.error('--apply requires --backup-dir')
    try:
        asyncio.run(run(args))
    except Exception as error:
        # Connection and driver errors may include credentials or source payloads.
        print(f'Inventory reconciliation stopped ({type(error).__name__}). Existing completed writes may remain; preview before retrying.')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
