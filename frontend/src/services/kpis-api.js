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
export function fetchKpiList({ signal } = {}) {
  return apiGet("/kpis", { signal });
}

/** `GET /kpis/{kpi_id}` - one KPI, resolved by `identifier` despite the path segment's name
 * (same as `fetchKpiSeries`/`computeKpi`/`updateKpi`/`deleteKpi` below). Includes `grain`,
 * which `fetchKpiList` also carries per row but which nothing needs fetched singly until the
 * chart editor (`ChartEditorPanel.jsx`) started reading it to tell an "overall"-grain KPI
 * (always exactly one row, no breakdown to table) from a per-dimension one. */
export function fetchKpi(identifier, { signal } = {}) {
  return apiGet(`/kpis/${encodeURIComponent(identifier)}`, { signal });
}

/** `POST /kpis/suggestions` - start a suggestion run for one connected source and get it back
 * `queued` (a `KpiSuggestionRun`). Nothing in the app *starts* a run any more except this: KPIs
 * the data supports are proposed for a source automatically when its harvest ends (the
 * `suggest-kpis` step of the `harvest-source` workflow), so this is only how a run that failed
 * after a harvest is started again, for the same source (contract 1.2.9). `sourceId` names it;
 * `404` for an unknown source, `409` while its harvest has not finished. Read the result with
 * `fetchCurrentKpiSuggestions`, which names the new run as `generating` and returns the suggestions
 * when it lands. (The request also accepts `business_areas` and `ask`; nothing here sends them.) */
export function startKpiSuggestionRun({ sourceId, signal } = {}) {
  const body = {};
  if (typeof sourceId === "string" && sourceId) body.source_id = sourceId;
  return apiJson("/kpis/suggestions", "POST", body, { signal });
}

/** `GET /kpis/suggestions/current` - the suggestions to show now, kept between visits (contract
 * 1.2.9): `{suggestions, notes, generating, failed}`. `suggestions` is the newest successful run
 * of each connected source (and of the deployment's own warehouse), merged, less what is already
 * in the registry and what a person dismissed -- so it is still there after a reload and on the
 * next visit, where a run read by id only lasted as long as the page that started it. A source's
 * suggestions are proposed automatically when its harvest finishes. `generating` is a run still
 * working (a `KpiSuggestionRun`; its `source_id` says which source -- read this again until it is
 * null) and `failed` the newest run that failed with no later success for its source (its
 * `error` is written for the reader); either may be null. `proposed_sql` is present only for a
 * caller holding `kpis:write`, as everywhere else. */
export function fetchCurrentKpiSuggestions({ signal } = {}) {
  return apiGet("/kpis/suggestions/current", { signal });
}

/** `POST /kpis/suggestions/dismissals` - remove a suggestion for good (`kpis:write`). It leaves
 * `fetchCurrentKpiSuggestions` and every run read afterwards, including a later run that proposes
 * the same identifier again -- the agent has no memory of what was declined, so the backend keeps
 * it. Applies to the whole deployment. Idempotent, so a double click is harmless. Resolves to
 * `{identifier, dismissed_at}`. Nothing else changes: no KPI is created or removed. */
export function dismissKpiSuggestion(identifier, { signal } = {}) {
  return apiJson("/kpis/suggestions/dismissals", "POST", { identifier }, { signal });
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

/** `POST /kpis/{kpi_id}/compute` - materialise this KPI for today, now (or for `asOf`, a past
 * business date, e.g. "YYYY-MM-DD" - the detail panel's "Re-run for another date"): the nightly
 * gold pass narrowed to one KPI and one date, so a just-adopted KPI has a value before its
 * first night, or a specific past night can be recomputed/backfilled on demand. Resolves to a
 * `KpiComputeResult` `{kpi_identifier, as_of, status: "materialised"|"failed"|"skipped",
 * rows_written, run_id, error, reason}` - a formula that fails is a 200 with `status:
 * "failed"` and `error`, not a rejected promise. 409 when the KPI is not `ready`; 503 when
 * gold itself refuses to run (the warehouse's last load did not pass, or a load is in
 * progress). Takes a few seconds: one guarded statement against the warehouse. */
export function computeKpi(identifier, { asOf, signal } = {}) {
  const query = asOf ? `?as_of=${encodeURIComponent(asOf)}` : "";
  return apiJson(`/kpis/${encodeURIComponent(identifier)}/compute${query}`, "POST", {}, { signal });
}

/** `DELETE /kpis/{identifier}` - remove a registered KPI (the reverse of `createKpi`). Resolves
 * to `null` on 204. Refused with a 409 while any dashboard still has a tile on it; the
 * `ApiError.detail` names the dashboards the caller can see, so show it as-is. */
export function deleteKpi(identifier, { signal } = {}) {
  return apiJson(`/kpis/${encodeURIComponent(identifier)}`, "DELETE", undefined, { signal });
}

/** `PATCH /kpis/{kpi_id}` - edit an already-adopted KPI's definition. `body` is a `KpiWrite`,
 * same shape as `createKpi`: the required fields (`name`, `description`, `business_area`,
 * `grain`, `sql`, `source_tables`) must be sent in full every time -- the api carries forward
 * only the *optional* ones (concept, unit, level, ...) omitted from the body, not the required
 * ones. Editing `sql` moves the KPI back to review: the server drops `validated_against_snapshot`
 * whenever `sql`/`source_tables` change. 422s naming the defect the same way `createKpi` does;
 * 409 if the row changed between the server's own read and write of it (retry by re-fetching). */
export function updateKpi(identifier, body, { signal } = {}) {
  return apiJson(`/kpis/${encodeURIComponent(identifier)}`, "PATCH", body, { signal });
}
