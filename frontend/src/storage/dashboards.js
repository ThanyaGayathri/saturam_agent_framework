import { readLocalJson, writeLocalJson } from "./local-json";

/**
 * Dashboards a user built from selected metrics.
 *
 * The local shape mirrors the API's wire model (`Dashboard`/`Tile` in
 * `product/egpt_retail/api/openapi/schemas/dashboards.yaml`), field for field and in its
 * snake_case, so wiring these accessors to the real API (D2, #233) is a transport change and not
 * a second mapping. Still localStorage only -- the same layer every other preference here uses.
 *
 * Two schedules, kept apart on purpose. **Refresh** (`refresh_schedule`, a contract field) is how
 * often the numbers are recomputed. **Delivery** (`delivery`) is who gets sent what, and when --
 * owned by different people, changing independently. The API does not carry delivery yet
 * (`DashboardWrite` has no such field, #226); it is kept here, in this browser, rather than
 * silently dropped, and the create screen says so.
 *
 * A tile is a metric REFERENCE plus a chart kind, never a copy of the metric. `kpi_identifier` is
 * the reference the API and gold rows key on (`<concept>__<name>`). A seed metric has no such
 * identifier yet, so it is `null` until the registry screens read real KPIs (C1, #232) -- null,
 * not an invented id, because the API would refuse an unregistered one anyway. `title`, `unit`
 * and `subject_area` are what the tile renders meanwhile; `unit` and `subject_area` are
 * transitional and go when `TileData.unit`/`Kpi` supply them (D2).
 */

const KEY = "retailgpt:dashboards";
const VERSION = 2;

/** The contract's `RefreshSchedule` (1.1.4), in the order the create screen offers them. */
export const REFRESH_OPTIONS = [
 { value: "nightly", label: "Every night, after the data refreshes" },
 { value: "daily", label: "Every morning at 06:00" },
 { value: "weekly", label: "Monday mornings" },
 { value: "hourly", label: "Every hour" },
 { value: "manual", label: "Only when I ask" },
];

/** The API's default; a new dashboard here defaults the same way. */
export const DEFAULT_REFRESH_SCHEDULE = "nightly";

/** Frontend-only: no `Dashboard`/`DashboardWrite` field carries this yet (#226). */
export const DELIVERY_OPTIONS = [
 { value: "none", label: "Don't send it anywhere" },
 { value: "daily", label: "Email it every morning" },
 { value: "weekly", label: "Email it Monday mornings" },
 { value: "monthly", label: "Email it on the 1st" },
];

/** The contract's `ChartKind`, with the labels the tile selector shows. */
export const CHART_KINDS = [
 { value: "number", label: "Number" },
 { value: "line", label: "Number + trend" },
 { value: "bar", label: "Bar" },
 { value: "table", label: "Table" },
];

/** The 12-column grid the contract's `TilePosition` examples use; every new tile is 3 x 2. */
export const GRID_COLUMNS = 12;
export const TILE_WIDTH = 3;
export const TILE_HEIGHT = 2;

/** Where the `index`-th tile goes: left to right, four to a row, then the next row. */
export function positionFor(index) {
 const perRow = GRID_COLUMNS / TILE_WIDTH;
 return {
  x: (index % perRow) * TILE_WIDTH,
  y: Math.floor(index / perRow) * TILE_HEIGHT,
  w: TILE_WIDTH,
  h: TILE_HEIGHT,
 };
}

/**
 * Default chart kind for a metric, from its unit. A percentage or a ratio reads as a number
 * with a trend; a currency total is worth a bar when it splits by something. Getting this roughly
 * right is what lets creation be two fields instead of one per metric.
 */
export function defaultChartKind(kpi) {
 const unit = String(kpi?.unit || "").toLowerCase();
 if (unit.includes("%") || unit.includes("percent") || unit.includes("rate")) return "line";
 if (unit.includes("day")) return "number";
 if (unit.includes("currency") || unit.includes("$")) return "bar";
 return "number";
}

/** Whether the API could accept this tile as a `TileWrite` -- it names a registered KPI. */
export function tileIsWireReady(tile) {
 return typeof tile?.kpi_identifier === "string" && tile.kpi_identifier.length > 0;
}

function tileId() {
 return `tl_${Math.random().toString(36).slice(2, 10)}`;
}

/** A tile referencing `kpi`, placed at `index` in the grid. */
export function tileFromKpi(kpi, index, { subjectArea } = {}) {
 return {
  id: tileId(),
  kpi_identifier: kpi?.identifier ?? null,
  title: kpi?.name || "",
  chart: defaultChartKind(kpi),
  position: positionFor(index),
  grain_value: null,
  unit: kpi?.unit || "",
  subject_area: kpi?.subjectArea || subjectArea || "",
 };
}

// --- migration from the version-1 shape -------------------------------------------------------
//
// v1 addressed a metric by name plus registry position (`kpiName`, `categoryIndex`,
// `sectionIndex`, `kpiIndex`), rendered it with `render: stat|trend|bar`, and had no layout.
// Read once, converted in place, written back as v2. Idempotent: a v2 record passes through.

const LEGACY_RENDER_TO_CHART = { stat: "number", trend: "line", bar: "bar" };

function isV2Tile(tile) {
 return tile != null && typeof tile === "object" && "chart" in tile && "position" in tile;
}

function migrateTile(tile, index) {
 if (isV2Tile(tile)) return tile;
 return {
  id: tileId(),
  kpi_identifier: null,
  title: tile?.kpiName || "",
  chart: LEGACY_RENDER_TO_CHART[tile?.render] || "number",
  position: positionFor(index),
  grain_value: null,
  unit: tile?.unit || "",
  subject_area: tile?.subjectArea || "",
 };
}

function isV2Dashboard(dashboard) {
 return (
  dashboard != null &&
  typeof dashboard === "object" &&
  "refresh_schedule" in dashboard &&
  Array.isArray(dashboard.tiles) &&
  dashboard.tiles.every(isV2Tile)
 );
}

export function migrateDashboard(dashboard) {
 if (isV2Dashboard(dashboard)) return dashboard;
 const created = dashboard?.createdAt || dashboard?.created_at || new Date().toISOString();
 return {
  id: dashboard?.id,
  name: dashboard?.name || "Untitled dashboard",
  description: dashboard?.description || "",
  owner: dashboard?.owner || "local",
  visibility: dashboard?.visibility || "private",
  filters: Array.isArray(dashboard?.filters) ? dashboard.filters : [],
  refresh_schedule: dashboard?.refresh_schedule || dashboard?.refresh || DEFAULT_REFRESH_SCHEDULE,
  delivery: dashboard?.delivery || "none",
  created_at: created,
  updated_at: dashboard?.updated_at || created,
  tiles: (dashboard?.tiles || []).map(migrateTile),
 };
}

function emptyState() {
 return { version: VERSION, dashboards: [] };
}

export function loadDashboards() {
 const raw = readLocalJson(KEY, null);
 if (!raw || !Array.isArray(raw.dashboards)) return emptyState();
 if (raw.version === VERSION && raw.dashboards.every(isV2Dashboard)) {
  return { version: VERSION, dashboards: raw.dashboards };
 }
 const state = { version: VERSION, dashboards: raw.dashboards.map(migrateDashboard) };
 writeLocalJson(KEY, state);
 return state;
}

export function saveDashboards(state) {
 writeLocalJson(KEY, { version: VERSION, dashboards: state?.dashboards || [] });
}

export function getDashboard(id) {
 return loadDashboards().dashboards.find((d) => d.id === id) || null;
}

export function createDashboard({ name, rows, refresh, delivery }) {
 const state = loadDashboards();
 const now = new Date().toISOString();
 const dashboard = {
  id: `dsh_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`,
  name: String(name || "").trim() || "Untitled dashboard",
  description: "",
  // What the API assigns with authorization off; a real caller identity replaces it in D2.
  owner: "local",
  visibility: "private",
  filters: [],
  refresh_schedule: refresh || DEFAULT_REFRESH_SCHEDULE,
  delivery: delivery || "none",
  created_at: now,
  updated_at: now,
  tiles: (rows || []).map((row, index) =>
   tileFromKpi(row.kpi, index, { subjectArea: row.sectionName })
  ),
 };
 state.dashboards = [dashboard, ...state.dashboards];
 saveDashboards(state);
 return dashboard;
}

export function updateDashboard(id, patch) {
 const state = loadDashboards();
 const updated_at = new Date().toISOString();
 state.dashboards = state.dashboards.map((d) => (d.id === id ? { ...d, ...patch, updated_at } : d));
 saveDashboards(state);
 return state.dashboards.find((d) => d.id === id) || null;
}

/** Change one tile -- by its id, not its index, the way the API addresses it. */
export function updateTile(dashboardId, tileId, patch) {
 const dashboard = getDashboard(dashboardId);
 if (!dashboard) return null;
 const tiles = dashboard.tiles.map((t) => (t.id === tileId ? { ...t, ...patch } : t));
 return updateDashboard(dashboardId, { tiles });
}

export function deleteDashboard(id) {
 const state = loadDashboards();
 state.dashboards = state.dashboards.filter((d) => d.id !== id);
 saveDashboards(state);
}

export function labelForRefresh(value) {
 return REFRESH_OPTIONS.find((o) => o.value === value)?.label || value;
}

export function labelForDelivery(value) {
 return DELIVERY_OPTIONS.find((o) => o.value === value)?.label || value;
}

export function labelForChart(value) {
 return CHART_KINDS.find((o) => o.value === value)?.label || value;
}
