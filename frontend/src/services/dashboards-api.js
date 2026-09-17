/**
 * The dashboards accessor against `product/egpt_retail`'s API, not `localStorage` - the
 * template for the conversions `frontend/README.md` names as a known cost ("every
 * `useState(() => load*())` initialiser becomes an effect"). See `src/lib/api.js` for the
 * client. Issue #226 (D1) aligned the frontend's local `Dashboard`/`Tile` shape to the wire
 * model field for field, so every function here passes its `body`/response straight through
 * with no second mapping - see `storage/dashboards.js` for the shared display helpers
 * (`CHART_KINDS`, `positionFor`, `kpiIsWireReady`, ...) that build/label these shapes.
 *
 * Contract 1.2.0 added `query` tiles (their own guarded SQL, cached per refresh) next to
 * `kpi` tiles, per-tile edits that keep every other tile's id, a query preview for the chart
 * editor, and "refresh now" for the query tiles.
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

/** `GET /dashboards/{id}/data` - every tile's current value/series/rows: `kpi` tiles from
 * gold, `query` tiles from their cached result. Never runs a tile's SQL. */
export function fetchDashboardData(id) {
  return apiGet(`/dashboards/${id}/data`);
}

/** `POST /dashboards` - `body` is a `DashboardWrite`: `{name, description?, tiles?, filters?,
 * refresh_schedule?, visibility?}`. 422s if any `kpi` tile names a KPI that isn't registered
 * and `ready`, or any `query` tile's SQL is refused by the warehouse read guard. */
export function createDashboardApi(body) {
  return apiJson("/dashboards", "POST", body);
}

/** `PATCH /dashboards/{id}` - partial: an omitted field is left unchanged, but `tiles`, when
 * given, replaces the whole array (a tile keeps its `id` when the write carries one). For a
 * single tile prefer `updateDashboardTile`, which never touches the others. */
export function updateDashboardApi(id, body) {
  return apiJson(`/dashboards/${id}`, "PATCH", body);
}

/** `DELETE /dashboards/{id}`. Resolves to `null` (204, no body) on success. */
export function deleteDashboardApi(id) {
  return apiJson(`/dashboards/${id}`, "DELETE");
}

/** `POST /dashboards/{id}/tiles` - add one tile (a `TileWrite`); every other tile's id is
 * untouched. A `query` tile is computed once here so it renders immediately. Resolves to the
 * updated `Dashboard`. */
export function addDashboardTile(id, tile) {
  return apiJson(`/dashboards/${id}/tiles`, "POST", tile);
}

/** `PATCH /dashboards/{id}/tiles/{tileId}` - edit one tile in place (a `TilePatch`: any of
 * `title`, `chart`, `position`, `grain_value`, `query`, `description`). A changed `query` is
 * re-checked by the guard (422 when refused) and recomputed. Resolves to the `Dashboard`. */
export function updateDashboardTile(id, tileId, patch) {
  return apiJson(`/dashboards/${id}/tiles/${encodeURIComponent(tileId)}`, "PATCH", patch);
}

/** `DELETE /dashboards/{id}/tiles/{tileId}`. Resolves to `null` (204) on success. */
export function deleteDashboardTile(id, tileId) {
  return apiJson(`/dashboards/${id}/tiles/${encodeURIComponent(tileId)}`, "DELETE");
}

/** `POST /dashboards/{id}/refresh` - recompute every `query` tile now and get the data back
 * (`DashboardData`). `kpi` tiles are untouched; gold refreshes on its own schedule. */
export function refreshDashboardApi(id) {
  return apiJson(`/dashboards/${id}/refresh`, "POST");
}

/** `POST /dashboards/queries/preview` - the chart editor's "Run preview": the guard, then a
 * bounded run (at most 50 rows). `query` is a `TileQuery` (`{sql, x?, y?, series_by?}`);
 * `tableNames`, when given, narrows what the statement may read. Resolves to a `QueryPreview`:
 * `{guard: {status: ok|rewritten|refused, detail, tables}, sql, columns, rows, duration_ms,
 * error}`. Nothing is stored. */
export function previewDashboardQuery(query, { tableNames, signal } = {}) {
  const body = { query };
  if (tableNames) body.table_names = tableNames;
  return apiJson("/dashboards/queries/preview", "POST", body, { signal });
}
