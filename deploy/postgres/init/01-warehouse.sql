-- Runs once, on the first start of the compose Postgres. The warehouse is a separate database
-- from the control plane (D5): landing, silver and gold live here; agent state does not.
-- Nothing reads it in phase zero; R4 populates it. Created now so the setting that names it
-- (EGPT_RETAIL_WAREHOUSE_DB_URL) points at something real from the first boot.
CREATE DATABASE egpt_warehouse OWNER ai;
