import { KPI_DATA_CATALOG_DATASETS } from "./kpi-data-catalog";
import { getKpiNamesForCatalogId } from "./kpi-usecase-links";

/**
 * Whether a use case can be built from the data this workspace has connected.
 *
 * Derived from links that already exist:
 *
 *   use case --getKpiNamesForCatalogId--> KPI names --linkedKpiNames--> datasets --> connected source
 *
 * Nothing new is authored, so the badge on a template cannot promise something the data does not
 * support -- and it moves on its own as sources are connected.
 *
 * **Why the labels differ from the KPI screen.** A KPI is measurable or it is not. A use case
 * targets several, so it can be genuinely part-way there, and "1 of 2 metrics" tells someone how
 * close they are and what to fix. "Partial" tells them neither.
 */

let MEASURABLE = null;

/** KPI names something in the catalogue can feed. */
function measurableKpiNames() {
 if (MEASURABLE) return MEASURABLE;
 const set = new Set();
 for (const ds of KPI_DATA_CATALOG_DATASETS) {
  for (const name of ds.linkedKpiNames || []) set.add(name);
 }
 MEASURABLE = set;
 return set;
}

// The chip UI caps at three; feasibility needs the whole set or a use case with four metrics
// would be judged on three of them.
const ALL = 999;

export const FEASIBILITY = {
 ready: {
  id: "ready",
  label: "Ready",
  cls: "bg-status-success-light text-status-success border-status-success-border",
  rank: 0,
 },
 mostly: {
  id: "mostly",
  label: "Mostly ready",
  cls: "bg-status-warning-light text-status-warning border-status-warning-border",
  rank: 1,
 },
 blocked: {
  id: "blocked",
  label: "Needs more data",
  cls: "bg-surface-tertiary text-text-muted border-border",
  rank: 2,
 },
 unknown: {
  id: "unknown",
  label: "",
  cls: "",
  rank: 3,
 },
};

/**
 * @returns {{ state: string, ready: number, total: number, missing: string[] }}
 * `unknown` when a use case declares no metrics -- silence is honest there, whereas calling it
 * blocked would be an accusation the data does not support.
 */
export function feasibilityForUseCase(catalogId) {
 const kpis = getKpiNamesForCatalogId(catalogId, ALL);
 if (kpis.length === 0) return { state: "unknown", ready: 0, total: 0, missing: [] };

 const measurable = measurableKpiNames();
 const missing = kpis.filter((k) => !measurable.has(k));
 const ready = kpis.length - missing.length;

 if (missing.length === 0) return { state: "ready", ready, total: kpis.length, missing };
 if (ready > 0) return { state: "mostly", ready, total: kpis.length, missing };
 return { state: "blocked", ready: 0, total: kpis.length, missing };
}

/** Counts for the header line. Leads with what the workspace CAN do. */
export function feasibilitySummary(useCases) {
 let ready = 0;
 let mostly = 0;
 let blocked = 0;
 for (const uc of useCases) {
  const f = feasibilityForUseCase(uc.id);
  if (f.state === "ready") ready += 1;
  else if (f.state === "mostly") mostly += 1;
  else if (f.state === "blocked") blocked += 1;
 }
 return { ready, mostly, blocked, scored: ready + mostly + blocked };
}
