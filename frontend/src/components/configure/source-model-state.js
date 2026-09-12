import { KPI_DATA_CATALOG_DATASETS } from "../../domain/kpi-data-catalog";

/**
 * Semantic-layer readiness per connected source — presentation only.
 *
 * A reachable connection and a source the product can answer questions from are different
 * things, and "Active" told the user neither. This is what the card actually needs to say.
 *
 * Deliberately a lookup here rather than a field on the stored source: this is UI state for the
 * redesign, and adding it to the storage layer would be data-model work rather than screen work.
 * When the real pipeline lands it replaces this module and the card is unchanged.
 */

export const MODEL_STATES = {
 modelled: {
  label: "Modelled",
  pill: "bg-[#ecfdf3] text-[#027a48]",
  dot: "bg-[#12b76a]",
 },
 needs_review: {
  label: "Needs review",
  pill: "bg-status-warning-light text-status-warning",
  dot: "bg-status-warning",
 },
 building: {
  label: "Building model",
  pill: "bg-status-info-light text-status-info",
  dot: "bg-status-info animate-pulse",
 },
 error: {
  label: "Failed",
  pill: "bg-status-critical-light text-status-critical",
  dot: "bg-status-critical",
 },
 not_started: {
  label: "Not modelled",
  pill: "bg-surface-tertiary text-text-secondary",
  dot: "bg-[#717680]",
 },
};

/**
 * Seeded so every state is represented on a first look at the screen — one source mid-build, one
 * awaiting a human decision, one failed. A demo that only ever shows the happy path hides exactly
 * the states the design exists to handle.
 */
const BY_ID = {
 seed_postgres_demo: { state: "modelled", tables: 42, modelled: 42, syncedMinsAgo: 2 },
 seed_snowflake_demo: { state: "modelled", tables: 63, modelled: 63, syncedMinsAgo: 2 },
 seed_sap_s4_gallery: { state: "needs_review", tables: 18, modelled: 14, reviewItems: 4, syncedMinsAgo: 11 },
 seed_salesforce_gallery: {
  state: "building",
  progress: 42,
  step: "Profiling columns — 5 of 12 entities",
  tables: 12,
  modelled: 5,
 },
};

/** Anything added by the user starts unmodelled; nothing has looked at it yet. */
const DEFAULT = { state: "not_started" };

export function modelStateFor(sourceId) {
 return BY_ID[sourceId] || DEFAULT;
}

/** Rolls the per-source states into the one line the top of the screen needs. */
export function workspaceReadiness(sources) {
 let modelled = 0;
 let tables = 0;
 let reviewItems = 0;
 let building = 0;
 for (const s of sources) {
  const m = modelStateFor(s.id);
  if (m.state === "modelled") modelled += 1;
  if (m.state === "building") building += 1;
  reviewItems += m.reviewItems || 0;
  tables += m.tables || 0;
 }
 return {
  modelled,
  total: sources.length,
  tables,
  reviewItems,
  building,
  pct: sources.length ? Math.round((modelled / sources.length) * 100) : 0,
 };
}

export function formatSync(minsAgo) {
 if (minsAgo == null) return "Never";
 if (minsAgo < 1) return "Just now";
 if (minsAgo < 60) return `${minsAgo} min ago`;
 const hrs = Math.round(minsAgo / 60);
 return hrs < 24 ? `${hrs} hr ago` : `${Math.round(hrs / 24)} d ago`;
}

/**
 * Configure source types to the ids the dataset catalogue uses. Two vocabularies for the same
 * systems, aligned by hand in the seed data; this is the one place that alignment is written down.
 */
const CATALOG_SOURCE_ID = {
 postgres: "postgres",
 snowflake: "snowflake",
 sap_s4hana: "sap",
 salesforce: "salesforce",
};

/**
 * The metrics a source makes measurable.
 *
 * Derived from the catalogue rather than listed by hand, so it stays true when datasets change
 * and cannot drift into a promise the data does not support.
 *
 * This is what a person actually wants to know after connecting something. "63 of 63 tables" is a
 * plumbing fact; nobody connects a warehouse to obtain tables. "Five metrics you can now measure,
 * including Inventory Turnover" is the answer to the question they were really asking, which is
 * whether that was worth doing.
 */
export function metricsUnlockedBy(sourceType) {
 const catalogId = CATALOG_SOURCE_ID[sourceType];
 if (!catalogId) return [];
 const names = new Set();
 for (const ds of KPI_DATA_CATALOG_DATASETS) {
  if (ds.sourceId !== catalogId) continue;
  for (const name of ds.linkedKpiNames || []) names.add(name);
 }
 return [...names].sort();
}
