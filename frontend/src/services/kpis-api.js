/**
 * The real KPI registry (`GET /kpis`), not `localStorage`. See `src/lib/api.js` for the client.
 *
 * C1 (#232) merges this alongside the seed catalogue rather than replacing it -- see
 * `components/kpi/KpiDiscoveryPage.jsx`'s own comment for why. The whole (small) list is
 * fetched once and grouped/filtered client-side, the same way the seed/suggested rows already
 * are, rather than passing `business_area` as a query filter.
 */

import { apiGet } from "../lib/api";

/** `GET /kpis` - every registered KPI, each with real `feasibility` and `identifier`. */
export function fetchKpiList() {
  return apiGet("/kpis");
}
