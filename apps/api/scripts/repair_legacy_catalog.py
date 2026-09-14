"""Targeted adoption of the legacy catalog inspected on 2026-09-13.

Defaults to read-only inspection. --apply changes the configured database in one
transaction, verifies it, and records explicitly reconciled migration history.
The original professor table is retained intact, including duplicate names/ratings.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from scripts.migrate import _select_schema, load_migrations, MIGRATION_LOCK_NAMESPACE
from scripts.verify_migrations import schema_errors


class RepairError(RuntimeError):
    pass


async def inspect_legacy(connection, schema_name="public"):
    await _select_schema(connection, schema_name)
    errors = await schema_errors(connection, schema_name=schema_name)
    await _select_schema(connection, schema_name)
    if not errors:
        return {"ready": False, "already_compatible": True, "blockers": []}
    if await connection.scalar(text("SELECT to_regclass('schema_migrations')")) is not None:
        raise RepairError('This repair only accepts the reviewed legacy schema without migration history.')
    columns = (await connection.execute(text("SELECT table_name,column_name FROM information_schema.columns WHERE table_schema=:schema"),
                                       {"schema": schema_name})).all()
    present = set(columns)
    required = {('professors', 'id'), ('professors', 'name'), ('professors', 'department'),
                ('courses', 'prerequisites'), ('sections', 'section_number')}
    added = {('professors', 'professor_name'), ('courses', 'title_source'),
             ('courses', 'prerequisites_status'), ('courses', 'prerequisites_rules'), ('scraper_runs', 'subjects')}
    if not required <= present or added & present:
        raise RepairError('Schema differs from the reviewed legacy layout; no automatic repair is available.')
    if await connection.scalar(text("SELECT to_regclass('professors_legacy_20260913')")) is not None:
        raise RepairError('The archive name is already occupied; nothing will be overwritten.')
    report = dict((await connection.execute(text('''
        SELECT (SELECT count(*) FROM professors) AS professor_records,
          (SELECT count(*) FROM (SELECT name FROM professors GROUP BY name HAVING count(*)>1) d) AS duplicate_professor_names,
          (SELECT count(*) FROM sections WHERE total_seats IS NULL OR open_seats IS NULL) AS unknown_seats,
          (SELECT count(*) FROM courses WHERE prerequisites IS NULL) AS unknown_prerequisites,
          (SELECT count(*) FROM courses WHERE credits<0) AS invalid_credits,
          (SELECT count(*) FROM professors WHERE name IS NULL OR btrim(name)='') AS invalid_names
    '''))).mappings().one())
    blockers = []
    for key, message in [('unknown_seats', 'There are unknown seat counts; review them before setting defaults.'),
                         ('unknown_prerequisites', 'There are NULL prerequisite arrays; review their meaning first.'),
                         ('invalid_credits', 'There are negative course credits.'),
                         ('invalid_names', 'There are missing professor names.')]:
        if report[key]:
            blockers.append(message)
    report.update(ready=not blockers, already_compatible=False, blockers=blockers,
                  archive='professors_legacy_20260913', schema_issues=errors)
    return report


async def repair_legacy(connection, schema_name="public"):
    if not connection.in_transaction():
        raise RepairError('The repair requires an enclosing transaction.')
    await _select_schema(connection, schema_name)
    await connection.execute(text("SET LOCAL lock_timeout = '5s'"))
    await connection.execute(text("SET LOCAL statement_timeout = '60s'"))
    await connection.execute(text('SELECT pg_advisory_xact_lock(:namespace, hashtext(:schema))'),
                             {'namespace': MIGRATION_LOCK_NAMESPACE, 'schema': schema_name})
    await connection.execute(text('LOCK TABLE courses, sections, meetings, professors, scraper_runs, rmp_cache IN ACCESS EXCLUSIVE MODE'))
    report = await inspect_legacy(connection, schema_name)
    if report['already_compatible']:
        return report
    if not report['ready']:
        raise RepairError(' '.join(report['blockers']))
    migrations = [m for m in load_migrations() if m.deferred_reason is None]
    if [m.version for m in migrations] != ['000', '007', '009', '012', '013', '014', '015', '016', '017']:
        raise RepairError('The migration inventory has changed; this targeted repair needs review.')
    raw = await connection.get_raw_connection()
    await raw.driver_connection.execute('''
        ALTER TABLE courses ALTER COLUMN prerequisites SET NOT NULL;
        ALTER TABLE sections ALTER COLUMN total_seats SET DEFAULT 0,
          ALTER COLUMN total_seats SET NOT NULL, ALTER COLUMN open_seats SET DEFAULT 0,
          ALTER COLUMN open_seats SET NOT NULL;
        ALTER TABLE sections DROP CONSTRAINT sections_course_code_fkey;
        ALTER TABLE sections ADD CONSTRAINT sections_course_code_fkey
          FOREIGN KEY(course_code) REFERENCES courses(course_code) ON DELETE CASCADE;
        CREATE INDEX idx_sections_course_term ON sections(course_code,term);
        CREATE INDEX idx_sections_term ON sections(term);

        ALTER TABLE professors RENAME TO professors_legacy_20260913;
        CREATE TABLE professors (
          professor_name text CONSTRAINT professors_current_pkey PRIMARY KEY,
          department text
        );
        ALTER TABLE professors ENABLE ROW LEVEL SECURITY;
        INSERT INTO professors(professor_name,department)
          SELECT name, CASE WHEN count(DISTINCT department)=1 AND count(department)=count(*)
                           THEN min(department) ELSE NULL END
          FROM professors_legacy_20260913 GROUP BY name;
        COMMENT ON TABLE professors_legacy_20260913 IS
          'Intact legacy professor records preserved during explicit catalog reconciliation, 2026-09-13. Do not drop without a separately verified backup/review.';
    ''')
    for migration in migrations:
        if migration.version in {'014', '015', '016', '017'}:
            await raw.driver_connection.execute(migration.sql)
    errors = await schema_errors(connection, schema_name=schema_name)
    await _select_schema(connection, schema_name)
    if errors:
        raise RepairError('Post-upgrade verification failed: ' + '; '.join(errors))
    # Explicit adoption only after the current schema has passed verification.
    # Baseline/007/009/012/013 were reconciled, not replayed over existing data.
    await connection.execute(text('''CREATE TABLE schema_migrations (
        version TEXT PRIMARY KEY, filename TEXT NOT NULL,
        checksum TEXT NOT NULL CHECK(length(checksum)=64),
        applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )'''))
    await connection.execute(text("COMMENT ON TABLE schema_migrations IS 'Legacy schema explicitly reconciled on 2026-09-13: adopted 000/007/009/012/013 after verification; executed 014/015/016/017. Original professor records retained in professors_legacy_20260913.'"))
    for migration in migrations:
        await connection.execute(text('INSERT INTO schema_migrations(version,filename,checksum) VALUES (:version,:filename,:checksum)'),
                                 {'version': migration.version, 'filename': migration.filename, 'checksum': migration.checksum})
    errors = await schema_errors(connection, schema_name=schema_name)
    await _select_schema(connection, schema_name)
    if errors:
        raise RepairError('Migration history verification failed: ' + '; '.join(errors))
    return {**report, "schema_issues": [], "reconciled_issue_count": len(report["schema_issues"])}


async def run(database_url, apply=False, schema_name='public'):
    engine = create_async_engine(database_url, connect_args={'timeout': 10})
    try:
        async with engine.begin() as connection:
            if not apply:
                await connection.execute(text('SET TRANSACTION READ ONLY'))
                await connection.execute(text("SET LOCAL statement_timeout = '15s'"))
            report = await (repair_legacy(connection, schema_name) if apply else inspect_legacy(connection, schema_name))
        print(json.dumps(report, indent=2))
        if report['already_compatible']:
            print('Runtime schema is already compatible; nothing changed.')
        elif apply:
            print('Repair committed and verified. Legacy professor records are preserved in professors_legacy_20260913.')
        else:
            print('READ ONLY: no changes made. --apply will archive the original professor table and upgrade the runtime schema.')
        return 0 if report['already_compatible'] or report['ready'] else 1
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Apply the reviewed repair and commit only after verification.')
    parser.add_argument('--schema', default='public')
    args = parser.parse_args()
    database_url = os.environ.get('DATABASE_URL', '')
    try:
        url = make_url(database_url)
        if url.drivername != 'postgresql+asyncpg' or not url.host or not url.database:
            raise ValueError()
    except Exception:
        print('Set DATABASE_URL to an explicit postgresql+asyncpg URL.')
        return 2
    try:
        return asyncio.run(run(database_url, args.apply, args.schema))
    except RepairError as error:
        print(str(error))
        return 1
    except Exception:
        # Driver errors may contain credentials or full SQL; never print them.
        print('Repair did not report successful completion. Run read-only inspection before retrying; check permissions, lock contention, and schema compatibility.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
