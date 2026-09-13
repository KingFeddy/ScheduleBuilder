"""Export the local API schema without loading application secrets or starting it."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from src.catalog import DEFAULT_CATALOG_SUBJECTS, DEFAULT_GER_SUBJECTS


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--stdout", action="store_true", help="Print instead of updating openapi.json.")
    mode.add_argument("--check", action="store_true", help="Fail if openapi.json is stale; do not write.")
    args = parser.parse_args()

    # Set every application setting BEFORE importing main/config. APP_ENV=test
    # disables dotenv loading; importing the app does not run its DB lifespan.
    os.environ.update({
        "APP_ENV": "test", "DATABASE_URL": "postgresql+asyncpg://127.0.0.1:1/disabled_schema_export",
        "SUPABASE_URL": "http://127.0.0.1:1", "SUPABASE_ANON_KEY": "synthetic-schema-export",
        "CURRENT_TERM": "202690", "CORS_ORIGINS": "http://127.0.0.1:3000",
        "CATALOG_SUBJECTS": DEFAULT_CATALOG_SUBJECTS, "GER_SUBJECTS": DEFAULT_GER_SUBJECTS,
        "SENTRY_DSN": "", "LOG_LEVEL": "WARNING",
    })
    from main import app

    content = json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"
    destination = Path(__file__).resolve().parents[1] / "openapi.json"
    if args.stdout:
        print(content, end="")
    elif args.check:
        if not destination.exists() or destination.read_text() != content:
            print("openapi.json is stale. Run uv run --no-sync python -m scripts.export_openapi.")
            return 1
        print("OpenAPI snapshot matches the backend.")
    else:
        destination.write_text(content)
        print("Updated openapi.json. Run pnpm --filter web api:generate next.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
