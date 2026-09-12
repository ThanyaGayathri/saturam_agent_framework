/**
 * Stable seed identifiers for the hide-seed feature.
 *
 * Custom rows (added via Add KPI / Add Process / Builder Launch) live in
 * Supabase and carry their own uuid. Seed rows come from code and don't
 * all have consistent id fields — KPIs in particular are identified by
 * position in a nested category/section tree. This helper produces one
 * stable string per seed so the `hidden_seed_rows` table can reference
 * them consistently.
 *
 * KPIs: composite of category + section name + kpi name.
 * Processes / Use Cases: use the existing `id` field on the seed row.
 */

export function kpiSeedId(categoryName, sectionName, kpiName) {
  return `${categoryName || ""}::${sectionName || ""}::${kpiName || ""}`;
}

export function processSeedId(process) {
  return process?.id || "";
}

export function useCaseSeedId(entry) {
  return entry?.id || "";
}
