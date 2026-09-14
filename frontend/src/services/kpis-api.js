/**
 * The real KPI registry (`GET /kpis`), not `localStorage`. See `src/lib/api.js` for the client.
 *
 * C1 (#232) merges this alongside the seed catalogue rather than replacing it -- see
 * `components/kpi/KpiDiscoveryPage.jsx`'s own comment for why. The whole (small) list is
 * fetched once and grouped/filtered client-side, the same way the seed/suggested rows already
 * are, rather than passing `business_area` as a query filter.
 */

import { apiGet, apiJson } from "../lib/api";

/** `GET /kpis` - every registered KPI, each with real `feasibility` and `identifier`. */
export function fetchKpiList() {
  return apiGet("/kpis");
}

/** `POST /kpis/suggestions` - start a run of the `kpi_suggestion` agent over the current
 * semantic layer and get it back `queued` (a `KpiSuggestionRun`: `{id, status, business_area,
 * created_at, started_at, finished_at, result, error}`). A run is two frontier-tier model calls
 * and takes minutes, so it is a resource to poll (issue #254), not a response to await:
 * `fetchKpiSuggestionRun` until `status` is `succeeded` (then `result` is the suggestion list)
 * or `failed` (then `error` says why). `businessAreas` is the focus (contract 1.1.8): the
 * areas the customer cares about, handed to the agents as a priority -- "propose for these
 * first" -- never as a filter, so nothing is dropped for being outside them. An empty list (or
 * none at all) means no focus -- the backend leaves the prompt untouched in that case, so there
 * is nothing to gain by listing every supported area when nothing was actually chosen. Nothing
 * is stored beyond the run row - a suggestion becomes a KPI only through `createKpi`. */
export function startKpiSuggestionRun(businessAreas, { signal } = {}) {
  const areas = Array.isArray(businessAreas) ? businessAreas.filter(Boolean) : [];
  const body = areas.length ? { business_areas: areas } : {};
  return apiJson("/kpis/suggestions", "POST", body, { signal });
}

/** `GET /kpis/suggestions/{run_id}` - one run, for polling. `result.suggestions[].proposed_sql`
 * is only present for a caller holding `kpis:write`, as on every other KPI read. The backend
 * closes a run whose serving instance was lost mid-run as `failed` after its timeout plus a
 * grace period, so polling always reaches a terminal status - but see `KpiDiscoveryPage.jsx`'s
 * own client-side ceiling for the stub server, which serves the `queued` example forever. */
export function fetchKpiSuggestionRun(runId, { signal } = {}) {
  return apiGet(`/kpis/suggestions/${encodeURIComponent(runId)}`, { signal });
}

/** `GET /kpis/suggestions` - the synchronous form of the run above, deprecated by it (#254):
 * the whole run happens inside one request, now bounded by the deployment's suggestion timeout
 * (504 past it). Kept because the contract keeps the path; nothing in this app calls it any
 * more. `businessArea` narrows the agent's search; omit it to suggest across every area.
 * @deprecated Use `startKpiSuggestionRun` + `fetchKpiSuggestionRun`. */
export function fetchKpiSuggestions(businessArea, { signal } = {}) {
  const query = businessArea ? `?business_area=${encodeURIComponent(businessArea)}` : "";
  return apiGet(`/kpis/suggestions${query}`, { signal });
}

/** `POST /kpis` - `body` is a `KpiWrite`: `{name, description, business_area, grain, sql,
 * source_tables, ...}`. What "adopt" on a suggestion actually does - turns a `KpiSuggestion`
 * (never persisted) into a real, governed `KpiDefinition`. 422s naming the defect if the
 * formula does not validate (declared parameters vs. placeholders, source tables vs. the
 * formula). */
export function createKpi(body, { signal } = {}) {
  return apiJson("/kpis", "POST", body, { signal });
}
