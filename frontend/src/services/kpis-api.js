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
 * or `failed` (then `error` says why). `businessArea` narrows the run; omit it to suggest
 * across every area. Nothing is stored beyond the run row - a suggestion becomes a KPI only
 * through `createKpi`. */
export function startKpiSuggestionRun(businessArea, { signal } = {}) {
  const body = businessArea ? { business_area: businessArea } : {};
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

/** `GET /kpis/health` - freshness of every registered KPI from gold (a `KpiHealth[]`:
 * `{kpi_id, identifier, status, last_computed_at, as_of, staleness_hours, last_run_id,
 * last_error}`). `status` is one of `fresh`/`stale`/`never_computed`/`failed`/`not_scheduled` -
 * every registered KPI gets a row, even one gold has never materialised. */
export function fetchKpiHealth({ signal } = {}) {
  return apiGet("/kpis/health", { signal });
}

/** `GET /kpis/{kpi_id}/series` - one KPI's value over its last `days` business dates, read
 * from gold (a `KpiSeries`: `{kpi_identifier, grain, interval: "day", days, unit, dimension,
 * dimensions, points: [{at, value}], value, delta, as_of, computed_at, status,
 * provenance_ref}`). One point per night the KPI was materialised for, oldest first; a night
 * with no row is absent, not `null`. Never a recomputation: a KPI adopted today has no point
 * until `computeKpi` (or tonight's run) writes one, which the api reports as a `200` with
 * empty `points` and `status: "never_computed"`, not an error. `dimension` (an object of
 * grain-key values) picks which row of a per-dimension KPI to follow. */
export function fetchKpiSeries(identifier, { days = 30, dimension, signal } = {}) {
  const query = new URLSearchParams({ days: String(days) });
  if (dimension && Object.keys(dimension).length > 0) {
    query.set("dimension", JSON.stringify(dimension));
  }
  return apiGet(`/kpis/${encodeURIComponent(identifier)}/series?${query}`, { signal });
}

/** `POST /kpis/{kpi_id}/compute` - materialise this KPI for today, now: the nightly gold pass
 * narrowed to one KPI, so a just-adopted KPI has a value before its first night. Resolves to a
 * `KpiComputeResult` `{kpi_identifier, as_of, status: "materialised"|"failed"|"skipped",
 * rows_written, run_id, error, reason}` - a formula that fails is a 200 with `status:
 * "failed"` and `error`, not a rejected promise. 409 when the KPI is not `ready`; 503 when
 * gold itself refuses to run (the warehouse's last load did not pass, or a load is in
 * progress). Takes a few seconds: one guarded statement against the warehouse. */
export function computeKpi(identifier, { signal } = {}) {
  return apiJson(`/kpis/${encodeURIComponent(identifier)}/compute`, "POST", {}, { signal });
}

/** `DELETE /kpis/{identifier}` - remove a registered KPI (the reverse of `createKpi`). Resolves
 * to `null` on 204. Refused with a 409 while any dashboard still has a tile on it; the
 * `ApiError.detail` names the dashboards the caller can see, so show it as-is. */
export function deleteKpi(identifier, { signal } = {}) {
  return apiJson(`/kpis/${encodeURIComponent(identifier)}`, "DELETE", undefined, { signal });
}
