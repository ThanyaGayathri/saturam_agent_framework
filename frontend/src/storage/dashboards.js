/**
 * Shape helpers shared by the dashboard screens - option lists, labels, and the builders that
 * turn a selected KPI row or a composed chart into the API's `TileWrite`/`Tile` shape.
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
 *
 * Contract 1.2.0: a tile is either `source: kpi` (a registered KPI, read from gold) or
 * `source: query` (its own guarded SQL, cached per refresh), and carries a `description`
 * shown under the chart. The compose flow's `DashboardChartSuggestion` maps onto a
 * `TileWrite` one to one (`tileWriteFromChart`).
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
 { value: "line", label: "Line" },
 { value: "bar", label: "Bar" },
 { value: "table", label: "Table" },
];

/** The 12-column grid the contract's `TilePosition` examples use. A number tile is 3 x 2; a
 * chart with an axis gets twice the width so the series has room. */
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

/** How many grid columns a chart of `kind` wants: a number or a short table fits a quarter
 * row, a line or bar wants half. The dashboard view reads this to span its grid. */
export function spanForChart(kind) {
 return kind === "line" || kind === "bar" ? 2 : 1;
}

/** The vocabulary's "overall" grain (`warehouse/gold.py`'s `GRAIN_OVERALL`): a KPI defined at
 * it has no dimension keys, so its formula always returns exactly one row -- structurally,
 * not just for the current data. */
export const OVERALL_GRAIN = "overall";

/**
 * Whether `chart` cannot legally render as a `table`: scoped to one row via `grain_value` (a
 * per-dimension KPI tile pinned to one supplier/location/etc.), or always one row because its
 * KPI is defined at `OVERALL_GRAIN`. Both collapse the same way server-side
 * (`dashboards/data.py`'s `_resolve_row`), so the tile would silently render as a plain number
 * with a "table" label instead of an actual table (`ChartTile.jsx`'s `tile.chart === "table"`
 * branch falls back to the scalar `value` when `rows` comes back empty).
 *
 * The one guard both `ChartEditorPanel.jsx` (a single chart, opened in the editor) and the
 * bulk create-dashboard flow (`DashboardBuilderStep.jsx`, an accepted AI suggestion that may
 * never open the editor at all) check against, so the two can't drift the way the editor's own
 * inline guard alone did for three review rounds. `kpiGrain`, when known, is the chart's own
 * KPI's `grain`; unknown (`undefined`) is never treated as unsafe here -- this only flags a
 * *known* collapse. A caller that can tell "still checking" from "positively safe" apart (the
 * editor) is expected to fail closed on the unknown case itself.
 */
export function tableUnavailableForChart(chart, kpiGrain) {
 if (!chart || chart.source === "query" || chart.chart !== "table") return false;
 if (chart.grain_value != null) return true;
 return kpiGrain === OVERALL_GRAIN;
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
  source: "kpi",
  kpi_identifier: kpi.identifier,
  title: kpi?.name || "",
  chart: defaultChartKind(kpi),
  position: positionFor(index),
  grain_value: null,
  description: kpi?.description || "",
 };
}

/** A `TileQuery` for the wire from a composed chart's or the editor's query object: only the
 * fields the contract carries, `source_tables` left to the server to derive. */
export function queryToWire(query) {
 if (!query) return null;
 return {
  sql: query.sql,
  dialect: query.dialect || "postgres",
  x: query.x ? { column: query.x.column, kind: query.x.kind || "category", label: query.x.label || "", unit: query.x.unit || "" } : null,
  y: (query.y || []).map((axis) => ({ column: axis.column, kind: axis.kind || "category", label: axis.label || "", unit: axis.unit || "" })),
  series_by: query.series_by ?? null,
 };
}

/** A `TileWrite` from a composed chart (`DashboardChartSuggestion`, or the same shape after
 * the person edited it on the review step), placed at `index`. `id`, when the chart carries
 * one, is kept so an edit on the review step keeps its identity through create. */
export function tileWriteFromChart(chart, index) {
 const isQuery = chart.source === "query";
 return {
  ...(chart.id ? { id: chart.id } : {}),
  source: isQuery ? "query" : "kpi",
  kpi_identifier: isQuery ? null : chart.kpi_identifier,
  query: isQuery ? queryToWire(chart.query) : null,
  title: chart.title || "",
  chart: chart.chart,
  position: chart.position || positionFor(index),
  grain_value: chart.grain_value ?? null,
  description: chart.description || "",
  rationale: chart.rationale || "",
 };
}

/** `dashboard.tiles` (the API's `Tile`, read back) as a `tiles` array for a `PATCH` body.
 * The `id` travels with each tile so the write keeps every tile's identity. */
export function tilesToWrite(tiles) {
 return tiles.map((tile) => ({
  id: tile.id,
  source: tile.source || "kpi",
  kpi_identifier: tile.kpi_identifier ?? null,
  query: tile.query ? queryToWire(tile.query) : null,
  title: tile.title,
  chart: tile.chart,
  position: tile.position,
  grain_value: tile.grain_value ?? null,
  description: tile.description || "",
  rationale: tile.rationale || "",
 }));
}

/**
 * The `DashboardWrite` body for the create-dashboard flow. The manual path hands in
 * `metrics` (wire-ready KPI-shaped objects: `identifier`/`name`/`unit`); the composed path
 * hands in `charts` (the review step's chart list) and wins when both are given.
 * `filterColumns` is the checklist's selection - `{table, column}` pairs (R13.1) naming which
 * columns become end-user filter dropdowns on the published dashboard; picking a value in one
 * is view-time state, never written here (see `fetchDashboardData`'s `filters` option).
 */
export function buildDashboardWritePayload({ name, refresh, metrics, charts, filterColumns }) {
 const tiles = charts
  ? charts.map((chart, index) => tileWriteFromChart(chart, index))
  : (metrics || []).map((kpi, index) => tileWriteFromKpi(kpi, index));
 return {
  name: String(name || "").trim() || "Untitled dashboard",
  refresh_schedule: refresh,
  tiles,
  filters: (filterColumns || []).map(({ table, column }) => ({ table, column })),
 };
}

/**
 * A `TileData`-shaped object from a `QueryPreview` (the editor's "Run preview", or the
 * preview a composed chart arrives with) through the chart's axes - the same mapping
 * `dashboards/data.py`'s `query_tile_data` makes from the cached result, so the review step
 * and the saved dashboard render one shape. `null` when there is nothing to show.
 */
export function tileDataFromPreview(chart, preview) {
 if (!preview || preview.error || !Array.isArray(preview.rows)) return null;
 const columns = preview.columns || [];
 const rows = preview.rows;
 const query = chart.query || {};
 const yAxis = (query.y || [])[0] || null;
 let yColumn = yAxis?.column;
 if (!yColumn || !columns.includes(yColumn)) yColumn = columns[columns.length - 1];
 const toNumber = (v) => (v === null || v === undefined || v === "" || Number.isNaN(Number(v)) ? null : Number(v));

 let series = [];
 if (query.x?.kind === "time" && !query.series_by && yColumn && (chart.chart === "line" || chart.chart === "bar")) {
  series = rows
   .map((row) => ({ at: row[query.x.column], value: toNumber(row[yColumn]) }))
   .filter((p) => p.at != null)
   .sort((a, b) => String(a.at).localeCompare(String(b.at)));
 }
 const plotted = series.filter((p) => p.value != null);
 let value = rows.length && yColumn ? toNumber(rows[0][yColumn]) : null;
 let delta = null;
 if (plotted.length >= 2) {
  value = plotted[plotted.length - 1].value;
  delta = value - plotted[plotted.length - 2].value;
 }
 return {
  status: "fresh",
  value,
  unit: yAxis?.unit || "",
  delta,
  series,
  rows,
  columns,
  computed_at: null,
 };
}

export function labelForRefresh(value) {
 return REFRESH_OPTIONS.find((o) => o.value === value)?.label || value;
}

export function labelForChart(value) {
 return CHART_KINDS.find((o) => o.value === value)?.label || value;
}
