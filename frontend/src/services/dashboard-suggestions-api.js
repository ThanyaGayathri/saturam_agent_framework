/**
 * The AI-composed dashboard flow's backend seam: `POST /dashboards/suggestions` +
 * `GET /dashboards/suggestions/{run_id}`. Same run-then-poll shape `services/kpis-api.js`'s
 * `startKpiSuggestionRun`/`fetchKpiSuggestionRun` already use (#254) -- a live model call takes
 * minutes, not seconds, so it is a resource to poll, not a response to await.
 */

import { apiGet, apiJson } from "../lib/api";

/** `POST /dashboards/suggestions` - start a run of the `dashboard_suggestion` agent and get it
 * back `queued` (a `DashboardSuggestionRun`: `{id, status, stage, purpose, created_at,
 * started_at, finished_at, result, error}`).
 *
 * `purpose` is what the caller says the dashboard should help them see, in their own words.
 * `kpiIdentifiers` are the metrics the agent reads as context - their definitions and the
 * tables behind them - not a list it tiles one for one; `tableNames` widens the tables it may
 * read; `chartBudget` is a soft cap on how many charts to compose. Poll
 * `fetchDashboardSuggestionRun` until `status` is `succeeded` (then `result` is the composed
 * `{name, charts, filters, notes, context}`) or `failed` (then `error` says why). Nothing is
 * stored beyond the run row - a suggestion becomes a dashboard only through
 * `createDashboardApi`. */
export function startDashboardSuggestionRun(
  { purpose, kpiIdentifiers = [], tableNames = [], chartBudget = null },
  { signal } = {},
) {
  const body = { purpose, kpi_identifiers: kpiIdentifiers, table_names: tableNames };
  if (chartBudget) body.chart_budget = chartBudget;
  return apiJson("/dashboards/suggestions", "POST", body, { signal });
}

/** `GET /dashboards/suggestions/{run_id}` - one run, for polling. `stage` says where a
 * running run is (`reading`, `drafting`, `guarding`, `previewing`, `done`). The backend
 * closes a run whose serving instance was lost mid-run as `failed` after its timeout plus a
 * grace period, so polling always reaches a terminal status. */
export function fetchDashboardSuggestionRun(runId, { signal } = {}) {
  return apiGet(`/dashboards/suggestions/${encodeURIComponent(runId)}`, { signal });
}
