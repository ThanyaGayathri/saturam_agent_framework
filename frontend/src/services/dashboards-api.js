/**
 * The dashboards accessor against `product/egpt_retail`'s API, not `localStorage` - the
 * template for the conversions `frontend/README.md` names as a known cost ("every
 * `useState(() => load*())` initialiser becomes an effect"). See `src/lib/api.js` for the
 * client. Issue #226 (D1) aligned the frontend's local `Dashboard`/`Tile` shape to the wire
 * model field for field, so every function here passes its `body`/response straight through
 * with no second mapping - see `storage/dashboards.js` for the shared display helpers
 * (`CHART_KINDS`, `positionFor`, `kpiIsWireReady`, ...) that build/label these shapes.
 */

import { apiGet, apiJson } from "../lib/api";

/** `GET /dashboards` - every dashboard visible to the caller.
 * Pass `{ signal }` to make the request abortable (e.g. on caller unmount). */
export function fetchDashboardList({ signal } = {}) {
  return apiGet("/dashboards", { signal });
}

/** `GET /dashboards/{id}` - one dashboard, or throws `ApiError` (404) if it doesn't exist or
 * isn't visible to the caller. */
export function fetchDashboard(id) {
  return apiGet(`/dashboards/${id}`);
}

/** `GET /dashboards/{id}/data` - every tile's current value/series/rows from gold. */
export function fetchDashboardData(id) {
  return apiGet(`/dashboards/${id}/data`);
}

/** `POST /dashboards` - `body` is a `DashboardWrite`: `{name, description?, tiles?, filters?,
 * refresh_schedule?, visibility?}`. 422s if any tile names a KPI that isn't registered and
 * `ready`. */
export function createDashboardApi(body) {
  return apiJson("/dashboards", "POST", body);
}

/** `PATCH /dashboards/{id}` - partial: an omitted field is left unchanged, but `tiles`, when
 * given, replaces the whole array (the API mints a fresh `id` for every tile in it - there is
 * no per-tile PATCH). */
export function updateDashboardApi(id, body) {
  return apiJson(`/dashboards/${id}`, "PATCH", body);
}

/** `DELETE /dashboards/{id}`. Resolves to `null` (204, no body) on success. */
export function deleteDashboardApi(id) {
  return apiJson(`/dashboards/${id}`, "DELETE");
}
