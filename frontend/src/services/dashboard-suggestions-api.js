/**
 * The AI-generated dashboard flow's backend seam: `POST /dashboards/suggestions` +
 * `GET /dashboards/suggestions/{run_id}`. Same run-then-poll shape `services/kpis-api.js`'s
 * `startKpiSuggestionRun`/`fetchKpiSuggestionRun` already use (#254) -- a live model call takes
 * minutes, not seconds, so it is a resource to poll, not a response to await.
 */

import { apiGet, apiJson } from "../lib/api";

/** `POST /dashboards/suggestions` - start a run of the `dashboard_suggestion` agent over
 * `purpose` (what the caller says the dashboard should help them track, in their own words)
 * and get it back `queued` (a `DashboardSuggestionRun`: `{id, status, purpose, created_at,
 * started_at, finished_at, result, error}`). Poll `fetchDashboardSuggestionRun` until `status`
 * is `succeeded` (then `result` is the suggested `{name, tiles, filters, notes}`) or `failed`
 * (then `error` says why). Nothing is stored beyond the run row - a suggestion becomes a
 * dashboard only through `createDashboardApi`. */
export function startDashboardSuggestionRun(purpose, { signal } = {}) {
  return apiJson("/dashboards/suggestions", "POST", { purpose }, { signal });
}

/** `GET /dashboards/suggestions/{run_id}` - one run, for polling. The backend closes a run
 * whose serving instance was lost mid-run as `failed` after its timeout plus a grace period,
 * so polling always reaches a terminal status. */
export function fetchDashboardSuggestionRun(runId, { signal } = {}) {
  return apiGet(`/dashboards/suggestions/${encodeURIComponent(runId)}`, { signal });
}
