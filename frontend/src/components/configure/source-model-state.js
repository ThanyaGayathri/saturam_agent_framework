import { KPI_DATA_CATALOG_DATASETS } from "../../domain/kpi-data-catalog";

/**
 * Semantic-layer readiness per connected source — presentation only.
 *
 * A reachable connection and a source the product can answer questions from are different
 * things, and "Active" told the user neither. This is what the card actually needs to say.
 *
 * For a real source (one `GET /configure/sources` returned) the state IS the row's
 * `harvest_state` (contract 1.2.8): #306's harvest workflow stamps it as it runs, the
 * workspace context polls it while any source is `building`, and this module only turns it
 * into the card's words. The five states here are the backend's five, by name, on purpose.
 *
 * The seeded demo rows never had a harvest, so their state still comes from the fixture table
 * below -- every state represented on a first look at the screen, which is what the fixtures
 * exist for. They are marked "Demo" on the card and hidden by default.
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
 * Demo fixtures, keyed by seed id: one source mid-build, one awaiting a human decision, one
 * failed. A demo that only ever shows the happy path hides exactly the states the design exists
 * to handle. Real sources never consult this table.
 */
const SEED_FIXTURES = {
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

/** Anything nothing has looked at yet. */
const DEFAULT = { state: "not_started" };

/** Minutes since an ISO timestamp, floored at zero; `null` for no timestamp. */
function minutesSince(iso) {
 if (!iso) return null;
 const t = Date.parse(iso);
 if (Number.isNaN(t)) return null;
 return Math.max(0, Math.round((Date.now() - t) / 60000));
}

/** The only reason a real harvest stops at `needs_review` today (#306's collision guard). Said
 * here once so the card and the semantic-layer screen tell the same story.
 *
 * A real conflict only: the schema is held by another *registered* source or by the deployment's
 * own warehouse. A claim left by a source that was deleted is not a conflict -- the harvest
 * reclaims it and carries on -- so this never describes one, and the last sentence is true for
 * the case where the other source has since been removed. The two ways out are controls that
 * exist: "Edit source" (a different schema, and saving re-runs the harvest) and "Retry
 * modelling". */
export const NEEDS_REVIEW_REASON =
 "This source's schema name is already used by another connected source or by the deployment's own warehouse, so nothing was written. Edit the source to use a different schema, then retry modelling. If the other source has since been removed, retrying is enough.";

function liveModelState(source) {
 const state = MODEL_STATES[source.harvestState] ? source.harvestState : "not_started";
 const model = { state, live: true, syncedMinsAgo: minutesSince(source.lastHarvestAt) };
 if (state === "building") {
  // No percentage exists: the harvest is one step (read the schema, write the graph), and a
  // number invented to move a bar would be exactly the fabricated progress the card is meant
  // not to show. `progress` stays undefined and the card renders an indeterminate bar.
  model.step = "Reading the schema and writing it to the semantic graph…";
 }
 if (state === "needs_review") {
  model.reviewItems = 1;
  model.reason = NEEDS_REVIEW_REASON;
 }
 if (state === "error") {
  model.error = "Modelling failed. Test the connection, then retry.";
 }
 return model;
}

/**
 * The card's state for one source. Takes the source (or card) object: a seeded row
 * (`seed`/`isSeed`) reads its fixture, a real one reads its `harvestState`/`lastHarvestAt`.
 * A bare id is still accepted for the fixtures, for any older call site.
 */
export function modelStateFor(source) {
 if (source == null) return DEFAULT;
 if (typeof source === "string") return SEED_FIXTURES[source] || DEFAULT;
 if (source.seed || source.isSeed) return SEED_FIXTURES[source.id] || DEFAULT;
 return liveModelState(source);
}

/** Rolls the per-source states into the one line the top of the screen needs. */
export function workspaceReadiness(sources) {
 let modelled = 0;
 let tables = 0;
 let reviewItems = 0;
 let building = 0;
 for (const s of sources) {
  const m = modelStateFor(s);
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
 * The metrics a DEMO source makes measurable, derived from the seed dataset catalogue.
 *
 * Derived from the catalogue rather than listed by hand, so it stays true when datasets change
 * and cannot drift into a promise the data does not support.
 *
 * This is what a person actually wants to know after connecting something. "63 of 63 tables" is a
 * plumbing fact; nobody connects a warehouse to obtain tables. "Five metrics you can now measure,
 * including Inventory Turnover" is the answer to the question they were really asking, which is
 * whether that was worth doing.
 *
 * Only the seeded demo rows may make this claim: the catalogue is demo data, and a real
 * source's tables have nothing to do with it. Callers pass a real source's type only when it is
 * a seed; for anything else the honest answer is the empty list (see #308 / #314).
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
