"""Produce auditable seasonal classifications from complete local Banner snapshots."""
import argparse
from collections import Counter
import json
from pathlib import Path
from src.services.offering_history import classify_history
from src.scheduler.time_utils import term_to_label


def write_report(history: dict, output: Path) -> None:
    counts = Counter(row['category'] for row in history['courses'].values())
    lines = ['# NJIT historical course offerings', '',
             'Source: [NJIT Banner public course schedules](https://reg-prod.ec.njit.edu/StudentRegistrationSsb/ssb/term/termSelection?mode=search).', '',
             f"Observation window: {term_to_label(history['baseline_start'])}–{term_to_label(history['latest_term'])}.",
             f"Recent comparison window: {term_to_label(history['recent_start'])}–{term_to_label(history['latest_term'])}.", '',
             'Seasonal patterns require at least two recent observations in that season and no recent opposite-season observations. '
             'Inactive means seen in the older baseline but absent from every recent fall, spring, and summer. '
             'These are planning assumptions, not official cancellation or guaranteed future offering notices. '
             'FP placeholder sections are excluded. Courses absent from the entire history remain unknown.', '',
             'Coverage includes every subject and level returned by Banner. Counts refer to course codes, not sections. '
             'Special-topic titles can rotate independently within one course code; these patterns do not guarantee a particular topic.', '',
             'PHYS485 retains the separately confirmed spring-only rule regardless of historical classification.', '',
             '| Classification | Courses |', '| --- | ---: |']
    lines += [f'| {category} | {count} |' for category, count in sorted(counts.items())]
    lines += ['', '## Snapshot coverage', '', '| Term | Real sections | Retrieved (UTC) |', '| --- | ---: | --- |']
    lines += [f"| {term_to_label(row['term'])} | {row['real_sections']} | {row['finished_at']} |" for row in history['terms']]
    for category in ('spring_only', 'fall_only', 'spring_and_summer', 'fall_and_summer', 'summer_only', 'inactive', 'insufficient_pattern'):
        lines += ['', f'## {category.replace("_", " ").title()}', '', '| Course | Latest observed title | Observed terms |', '| --- | --- | --- |']
        for code, row in history['courses'].items():
            if row['category'] == category:
                title = row['title'].replace('|', '/').replace('\n', ' ')
                terms = ', '.join(term_to_label(term) for term in row['observed_terms'])
                lines.append(f'| {code} | {title} | {terms} |')
    lines += ['', '## Refreshing this dataset', '',
              'From `apps/api`, collect complete term snapshots with '
              '`python -m scripts.collect_offering_history --terms <term codes> --output <snapshot directory>`. '
              'Then run `python -m scripts.analyze_offering_history --snapshots <snapshot directory> '
              '--output src/data/course_offering_history.json --report <report path>` with matching '
              '`--baseline-start`, `--recent-start`, and `--latest-term` boundaries. '
              'Use a new snapshot directory to refresh previously collected terms. '
              'Collection is read-only and does not import historical sections into the live database.', '',
              'The planner reads the bundled dataset. Updating live seat counts does not refresh this history; '
              'regenerate and redeploy it when a new term becomes available. Explicit confirmed offering rules take precedence.']
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text('\n'.join(lines) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshots', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--baseline-start', default='202290')
    parser.add_argument('--recent-start', default='202390')
    parser.add_argument('--latest-term', default='202690')
    args = parser.parse_args()
    history = classify_history([json.loads(path.read_text()) for path in sorted(args.snapshots.glob('*.json'))],
                               baseline_start=args.baseline_start, recent_start=args.recent_start, latest_term=args.latest_term)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(history, indent=2) + '\n')
    write_report(history, args.report)
    print(dict(Counter(row['category'] for row in history['courses'].values())))
