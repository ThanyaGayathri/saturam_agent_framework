import { readLocalJson, writeLocalJson } from "./local-json";

/**
 * Dashboards a user built from selected metrics.
 *
 * Two schedules, kept apart on purpose. **Refresh** is how often the numbers are recomputed;
 * **delivery** is who gets sent what, and when. They are owned by different people and answer
 * different questions -- a dashboard can refresh hourly and be emailed weekly, or refresh daily
 * and never be sent at all. Collapsing them into one "schedule" field is the kind of shortcut
 * that has to be unpicked later, so they are separate from the start.
 *
 * A tile is a metric REFERENCE plus a render type, never a copy of the metric. Changing a
 * definition in Configure changes every dashboard that uses it, which is the point of having a
 * master list at all.
 *
 * localStorage only -- the same layer every other preference here uses.
 */

const KEY = "retailgpt:dashboards";
const VERSION = 1;

export const REFRESH_OPTIONS = [
 { value: "hourly", label: "Every hour" },
 { value: "daily", label: "Every morning at 06:00" },
 { value: "weekly", label: "Monday mornings" },
 { value: "manual", label: "Only when I ask" },
];

export const DELIVERY_OPTIONS = [
 { value: "none", label: "Don't send it anywhere" },
 { value: "daily", label: "Email it every morning" },
 { value: "weekly", label: "Email it Monday mornings" },
 { value: "monthly", label: "Email it on the 1st" },
];

/**
 * Default render type for a metric, from its unit. A percentage or a ratio reads as a single
 * number with a trend; a currency total is worth a bar when it splits by something. Getting this
 * roughly right matters because it is what lets creation be two fields instead of one per metric.
 */
export function defaultRenderType(kpi) {
 const unit = String(kpi?.unit || "").toLowerCase();
 if (unit.includes("%") || unit.includes("percent") || unit.includes("rate")) return "trend";
 if (unit.includes("day")) return "stat";
 if (unit.includes("currency") || unit.includes("$")) return "bar";
 return "stat";
}

function emptyState() {
 return { version: VERSION, dashboards: [] };
}

export function loadDashboards() {
 const raw = readLocalJson(KEY, null);
 if (!raw || !Array.isArray(raw.dashboards)) return emptyState();
 return { version: VERSION, dashboards: raw.dashboards };
}

export function saveDashboards(state) {
 writeLocalJson(KEY, { version: VERSION, dashboards: state?.dashboards || [] });
}

export function getDashboard(id) {
 return loadDashboards().dashboards.find((d) => d.id === id) || null;
}

export function createDashboard({ name, rows, refresh, delivery }) {
 const state = loadDashboards();
 const dashboard = {
  id: `db_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`,
  name: String(name || "").trim() || "Untitled dashboard",
  refresh: refresh || "daily",
  delivery: delivery || "none",
  createdAt: new Date().toISOString(),
  // Reference by name plus registry position: name is what a human recognises, the indices are
  // what the registry addresses. Both are stored so a rename does not orphan the tile.
  tiles: (rows || []).map((row) => ({
   kpiName: row.kpi?.name || "",
   subjectArea: row.kpi?.subjectArea || row.sectionName || "",
   unit: row.kpi?.unit || "",
   categoryIndex: row.categoryIndex,
   sectionIndex: row.sectionIndex,
   kpiIndex: row.kpiIndex,
   render: defaultRenderType(row.kpi),
  })),
 };
 state.dashboards = [dashboard, ...state.dashboards];
 saveDashboards(state);
 return dashboard;
}

export function updateDashboard(id, patch) {
 const state = loadDashboards();
 state.dashboards = state.dashboards.map((d) => (d.id === id ? { ...d, ...patch } : d));
 saveDashboards(state);
 return state.dashboards.find((d) => d.id === id) || null;
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
