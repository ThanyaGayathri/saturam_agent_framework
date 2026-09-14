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

/** `GET /kpis/suggestions` - candidate KPIs proposed live by the `kpi_suggestion` agent over
 * the current semantic layer. "Read on demand and not stored" - a fresh call every time, not
 * a registry read. `businessArea` narrows the agent's search; omit it to suggest across every
 * area. This is a synchronous frontier-tier model call inside the request (issue #234's own
 * note: "likely several seconds") - callers need a real loading state, not an instant-response
 * assumption. */
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
