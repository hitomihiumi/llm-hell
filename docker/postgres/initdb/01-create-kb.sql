-- Creates the knowledge-base database and the read-only role the
-- postgres-mcp sidecar connects as.
--
-- NOTE: files in /docker-entrypoint-initdb.d run ONLY when the data volume
-- is empty. On an existing stack, apply them by hand:
--   docker compose exec -T postgres psql -U <user> -d postgres < 01-create-kb.sql
--
-- Why a separate database rather than a schema in `llmhell`: search against
-- this source works by asking an LLM to write SQL. Whatever that role can
-- reach, generated SQL can reach - so `users.password_hash` and
-- `user_sessions.token_hash` must not be reachable at all. A prompt
-- injection buried in an indexed document is a realistic way to try, and
-- "the model was told not to" is not a control. This is.

CREATE DATABASE kb;

-- Password is intentionally a fixed dev-only value: this role can read the
-- demo corpus and nothing else, and the connection string has to be shared
-- with the sidecar anyway. Override KB_DATABASE_URI in production.
CREATE ROLE kb_ro WITH LOGIN PASSWORD 'kb_ro_password';

-- Belt and braces on top of postgres-mcp's own --access-mode=restricted:
-- even a statement that slips past its SQL parser cannot write, and cannot
-- run longer than five seconds.
ALTER ROLE kb_ro SET default_transaction_read_only = on;
ALTER ROLE kb_ro SET statement_timeout = '5s';

-- Deny kb_ro the ability to even open a connection to the application
-- database. Table-level permissions already block reading `users`, but a
-- role that can connect can still enumerate schemas and table names, and
-- there is no reason for this one to reach that database at all.
--
-- Safe for the app itself: its role owns that database and connects on
-- ownership, not on the PUBLIC grant.
REVOKE CONNECT ON DATABASE llmhell FROM PUBLIC;

\connect kb

-- Strip the implicit CREATE grant every role gets on `public`, so kb_ro
-- cannot create temp objects to stage data in.
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT CONNECT ON DATABASE kb TO kb_ro;
GRANT USAGE ON SCHEMA public TO kb_ro;
