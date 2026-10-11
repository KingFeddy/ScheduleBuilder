"""Read complete Banner inventories into compact historical snapshots; no DB writes."""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import json
from pathlib import Path
from playwright.async_api import Error as PlaywrightError, TimeoutError as PlaywrightTimeout

from src.scrapers.term_inventory import collect_term_inventory


async def collect_with_retries(term: str):
    for attempt in range(3):
        try:
            return await collect_term_inventory(term)
        except PlaywrightError as error:
            transient = isinstance(error, PlaywrightTimeout) or 'ERR_NETWORK_IO_SUSPENDED' in str(error)
            if not transient or attempt == 2:
                raise
            print(f'{term}: transient network interruption; retrying the complete term', flush=True)
            await asyncio.sleep(5 * (attempt + 1))


async def run(terms: list[str], directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for term in terms:
        path = directory / f'{term}.json'
        if path.exists():
            print(f'{term}: using saved snapshot', flush=True)
            continue
        inventory = await collect_with_retries(term)
        sections = [row for row in inventory.sections if str(row.get('sequenceNumber', '')).strip().upper() != 'FP']
        counts = Counter(row['subject'] + row['courseNumber'] for row in sections)
        titles = {row['subject'] + row['courseNumber']: row.get('courseTitle', '') for row in sections}
        payload = {
            'format': 1, 'term': term, 'complete': True,
            'started_at': inventory.started_at.isoformat(), 'finished_at': inventory.finished_at.isoformat(),
            'total_sections': len(inventory.sections), 'real_sections': len(sections),
            'courses': {code: {'sections': counts[code], 'title': titles[code]} for code in sorted(counts)},
        }
        pending = path.with_suffix('.tmp')
        pending.write_text(json.dumps(payload, indent=2) + '\n')
        pending.replace(path)
        print(f'{term}: {len(counts)} courses, {len(sections)} real sections; complete', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--terms', nargs='+', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if any(len(term) != 6 or not term.isdigit() or term[-2:] not in {'10', '50', '90'} for term in args.terms):
        parser.error('Use six-digit NJIT fall, spring, or summer term codes.')
    asyncio.run(run(args.terms, args.output))
