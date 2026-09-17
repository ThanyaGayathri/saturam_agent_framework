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
 * gold, `query` tiles from their cached result unless a filter is active. `filters`, when
 * given, is a `{ "table.column": [value, ...] }` map of the dashboard's own filter columns
 * with values picked - a column left out or with an empty list is ignored (same as picking
 * "All"). With at least one active, every `query` tile it reaches is recomputed live through
 * the guard, AND-combining every selection that applies to it.
 *
 * One `f=table.column:value` param per selected value, never a comma-joined list: a distinct
 * value can itself contain a comma (a real city, "Dallas, TX"), which a comma-joined list
 * could not tell apart from the separator between values - `URLSearchParams` percent-encodes
 * each occurrence independently, so a value's own comma (or colon) never collides with the
 * one separating params. */
export function fetchDashboardData(id, { filters } = {}) {
  const params = new URLSearchParams();
  for (const [key, values] of Object.entries(filters || {})) {
    for (const value of values || []) {
      if (value) params.append("f", `${key}:${value}`);
    }
  }
  const query = params.toString();
  return apiGet(`/dashboards/${id}/data${query ? `?${query}` : ""}`);
}

/** `GET /dashboards/{id}/filter-columns` - candidate `{table, column}` pairs across this
 * dashboard's own `query` tiles (text-typed, non-personal-data columns of the tables those
 * tiles read). Drives the creation-step filter picker. Empty when the dashboard has no
 * `query` tile. */
export function fetchDashboardFilterColumns(id) {
  return apiGet(`/dashboards/${id}/filter-columns`);
}

/** `GET /dashboards/{id}/filter-values?table=..&column=..` - distinct values of one of this
 * dashboard's *chosen* filter columns, for its dropdown. 422s for a pair that isn't one of
 * the dashboard's own `filters`. Resolves to `{table, column, values: [string, ...]}`. */
export function fetchDashboardFilterValues(id, { table, column }) {
  const params = new URLSearchParams({ table, column });
  return apiGet(`/dashboards/${id}/filter-values?${params}`);
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
