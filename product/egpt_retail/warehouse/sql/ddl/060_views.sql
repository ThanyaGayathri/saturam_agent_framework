-- Read surfaces over the quality tables.
--
-- These are what the KPI health screen will read when it is built. The screen is not R4's --
-- it needs an API contract change, which is the change protocol's business -- but the shape
-- it needs is already here, which is the useful half to get right now.

-- The most recent load, finished or not.
--
-- Deliberately NOT filtered to `finished_at IS NOT NULL`. That filter hid exactly the loads a
-- health screen most needs to show: one still running, and one that died before it could
-- record a verdict. Both used to be invisible here and indistinguishable from each other.
-- A load that crashed is now closed as 'failed' by the next load (see
-- warehouse/loads.py::close_stale_load_runs), and one still in flight shows with a NULL
-- verdict, which is the truthful thing to show rather than an absence.
CREATE OR REPLACE VIEW quality.latest_load AS
SELECT
    r.load_id,
    r.started_at,
    r.finished_at,
    r.trigger,
    r.workflow_run_id,
    r.files_loaded,
    r.rows_landed,
    r.verdict,
    r.reason
FROM quality.load_run AS r
ORDER BY r.started_at DESC
LIMIT 1;

-- Every gate result for the most recent finished load, failures first: what a person opening
-- the screen after a blocked night needs, in the order they need it.
CREATE OR REPLACE VIEW quality.latest_gate_result AS
SELECT
    g.load_id,
    g.gate,
    g.subject,
    g.measured,
    g.threshold,
    g.observed,
    g.passed,
    g.reason,
    g.checked_at
FROM quality.gate_result AS g
JOIN quality.latest_load AS l ON l.load_id = g.load_id
ORDER BY g.passed, g.gate, g.subject;

COMMENT ON VIEW quality.latest_load IS
    'The last load, finished or not. A NULL verdict means still running; ''failed'' means it '
    'died before recording one and a later load closed it. Both are states a health screen '
    'has to be able to show, which is why this is not filtered to finished loads.';
