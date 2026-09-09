-- Type coercion that reports instead of aborting (R4, G8: "type coercion").
--
-- CAST(payload ->> 'occurred_at' AS timestamptz) aborts the whole statement on one bad
-- value, so a single malformed timestamp in a 100k-row file would fail the entire stream.
-- Guarding with a WHERE clause is not reliable protection: the planner may evaluate the
-- SELECT-list cast before the filter, so the abort happens anyway.
--
-- Postgres 18 added CAST(expr AS type DEFAULT NULL ON ERROR), which replaces both functions
-- outright. The compose image is already agnohq/pgvector:18 -- but Azure Database for
-- PostgreSQL Flexible Server, which D5 names as the AKS target, is not there yet. Revisit
-- when the whole fleet is on 18; until then these two functions are the portable answer.
--
-- Cost, recorded so nobody rediscovers it under load: the EXCEPTION block opens a
-- subtransaction per row. Acceptable at fixture and early-production volume, and the first
-- thing to look at if a load starts taking minutes.
--
-- A NULL returned here is NEVER silent. The conformance SQL distinguishes "absent",
-- "present but blank" and "present and uncoercible", and writes each to quality.row_defect
-- with its own defect_kind.

CREATE OR REPLACE FUNCTION silver.try_timestamptz(value text)
RETURNS timestamptz
LANGUAGE plpgsql
IMMUTABLE
STRICT
AS $fn$
BEGIN
    RETURN CAST(value AS timestamptz);
EXCEPTION WHEN others THEN
    RETURN NULL;
END;
$fn$;

CREATE OR REPLACE FUNCTION silver.try_integer(value text)
RETURNS integer
LANGUAGE plpgsql
IMMUTABLE
STRICT
AS $fn$
BEGIN
    RETURN CAST(value AS integer);
EXCEPTION WHEN others THEN
    RETURN NULL;
END;
$fn$;

COMMENT ON FUNCTION silver.try_timestamptz(text) IS
    'NULL on an uncoercible value instead of aborting the statement. The caller reports the '
    'failure as an ''uncoercible'' row defect. Replace with CAST(... DEFAULT NULL ON ERROR) '
    'once every deployment is on PostgreSQL 18.';
COMMENT ON FUNCTION silver.try_integer(text) IS
    'See silver.try_timestamptz.';
