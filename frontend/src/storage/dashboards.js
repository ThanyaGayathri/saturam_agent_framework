/**
 * Shape helpers shared by the dashboard screens - option lists, labels, and the builders that
 * turn a selected KPI row into the API's `TileWrite`/`Tile` shape.
 *
 * D2 (#233) wired the screens to the real `dashboards.py` (`src/services/dashboards-api.js`);
 * there is no local persistence here any more (`loadDashboards`/`saveDashboards`/
 * `createDashboard`/etc., and the v1 storage migration, all removed with it - a dashboard now
 * exists only if the API has it). `delivery` (who a dashboard is emailed to, and when) goes
 * with it: `Dashboard`/`DashboardWrite` have no such field, and there is no longer anywhere in
 * this browser to keep a frontend-only setting for a resource that now lives server-side.
 * D1 (#226) flagged this rather than dropping it silently; D2 is where dropping it actually
 * happens, for the same reason - it flags again here rather than pretending a "Send it to
 * people" control still does anything. Real delivery is a backend decision, not built yet.
 */

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

/** Whether a KPI row can become a real tile - it names a registered KPI. The seed catalogue
 * (`domain/middleby-kpi-seed.js`) has no `identifier` on any card today, so this is false for
 * everything until the KPI registry screens (#232) start populating it from real `GET /kpis`. */
export function kpiIsWireReady(kpi) {
 return typeof kpi?.identifier === "string" && kpi.identifier.length > 0;
}

/** A `TileWrite` for `kpi`, placed at `index` in the grid. Only called on a wire-ready `kpi`
 * (`kpiIsWireReady`) - `kpi_identifier` is required and non-null on the wire. */
export function tileWriteFromKpi(kpi, index) {
 return {
  kpi_identifier: kpi.identifier,
  title: kpi?.name || "",
  chart: defaultChartKind(kpi),
  position: positionFor(index),
  grain_value: null,
 };
}

/** `dashboard.tiles` (the API's `Tile`, read back) as a `tiles` array for a `PATCH` body -
 * `TileWrite` has no `id`, so editing any tile means resubmitting all of them; the API mints a
 * fresh `id` for each one it saves. */
export function tilesToWrite(tiles) {
 return tiles.map((tile) => ({
  kpi_identifier: tile.kpi_identifier,
  title: tile.title,
  chart: tile.chart,
  position: tile.position,
  grain_value: tile.grain_value ?? null,
 }));
}

/**
 * The `DashboardWrite` body for the create-dashboard flow (both the AI-generated and manual
 * paths end here): `metrics` are wire-ready KPI-shaped objects (`identifier`/`name`/`unit`),
 * `businessAreaFilter` is a single `business_area` value or falsy for none. The contract's
 * `DashboardFilterField` also has `time_window`, but nothing in this app constructs a value
 * for it yet (no defined format anywhere in the contract) - this flow only ever writes
 * `business_area`, which has a real, validated vocabulary behind it.
 */
export function buildDashboardWritePayload({ name, refresh, metrics, businessAreaFilter }) {
 return {
  name: String(name || "").trim() || "Untitled dashboard",
  refresh_schedule: refresh,
  tiles: metrics.map((kpi, index) => tileWriteFromKpi(kpi, index)),
  filters: businessAreaFilter
   ? [{ field: "business_area", value: businessAreaFilter }]
   : [],
 };
}

export function labelForRefresh(value) {
 return REFRESH_OPTIONS.find((o) => o.value === value)?.label || value;
}

export function labelForChart(value) {
 return CHART_KINDS.find((o) => o.value === value)?.label || value;
}
