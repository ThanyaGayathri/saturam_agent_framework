-- The read-only role's access to gold (R5 closing R6's own deferral in 065_readonly_role.sql).
--
-- After 070_gold.sql on purpose: `GRANT SELECT ON ALL TABLES IN SCHEMA` covers exactly the
-- relations that exist when it runs, so placed before the CREATEs it would have granted
-- nothing. The default-privileges line is what reaches the per-KPI views the materialiser
-- creates afterwards -- as with silver, unqualified, so it applies to whatever role runs
-- warehouse-migrate and materialisation (the same role, by construction of the DSN).
--
-- landing and quality stay ungranted, for the reason 065 gives.

GRANT USAGE ON SCHEMA gold TO egpt_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA gold TO egpt_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA gold GRANT SELECT ON TABLES TO egpt_reader;
