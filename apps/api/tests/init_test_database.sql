-- Bootstrap only the empty disposable PostgreSQL instance used by Compose/CI.
-- Run as its local postgres administrator, before the real migration runner.
\set ON_ERROR_STOP on

DO $$
BEGIN
    IF current_database() <> 'njit_test' OR current_user <> 'postgres' THEN
        RAISE EXCEPTION 'Test bootstrap requires the njit_test database and postgres administrator';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_tables WHERE schemaname = 'public') THEN
        RAISE EXCEPTION 'Refusing to mark a populated database as disposable';
    END IF;
END $$;

-- Public, test-only credentials: the container is bound to loopback and ephemeral.
CREATE ROLE njit_test LOGIN PASSWORD 'test-only' NOSUPERUSER NOCREATEDB NOCREATEROLE;
-- CREATE here allows private schemas, not new databases or superuser access.
GRANT CONNECT, CREATE ON DATABASE njit_test TO njit_test;
GRANT USAGE, CREATE ON SCHEMA public TO njit_test;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO njit_test;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO njit_test;

-- The restricted test role cannot set this marker on an unrelated database.
COMMENT ON DATABASE njit_test IS 'schedule-builder disposable test database v1';
