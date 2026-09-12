/**
 * Cross-UC business monitors (Middleby profile only).
 * P0: Pipeline Health; P1: all four monitors active with hero charts.
 */

export const MIDDLEBY_MONITOR_PIPELINE_HEALTH = "pipeline-health";
export const MIDDLEBY_MONITOR_CUSTOMER_EXPERIENCE = "customer-experience";
export const MIDDLEBY_MONITOR_DEMAND_INTELLIGENCE = "demand-intelligence";
export const MIDDLEBY_MONITOR_BRAND_PORTFOLIO = "brand-portfolio";

/** @typedef {{ id: string, title: string, persona: string, heroQuestion: string, catalogIds: string[], phase: 'active' | 'planned' }} MiddlebyMonitorDef */

/** @type {MiddlebyMonitorDef[]} */
export const MIDDLEBY_MONITORS = [
  {
    id: MIDDLEBY_MONITOR_PIPELINE_HEALTH,
    title: "Pipeline Health",
    persona: "Sales head / dealer ops",
    heroQuestion: "Where is pipeline stalling, and who owns the slowdown?",
    catalogIds: ["middleby-uc-04", "middleby-uc-06", "middleby-uc-10", "middleby-uc-13"],
    phase: "active",
  },
  {
    id: MIDDLEBY_MONITOR_CUSTOMER_EXPERIENCE,
    title: "Customer Experience",
    persona: "Digital / commerce",
    heroQuestion: "Is the site getting better or worse this week?",
    catalogIds: ["middleby-uc-02", "middleby-uc-08", "middleby-uc-20", "middleby-uc-21"],
    phase: "active",
  },
  {
    id: MIDDLEBY_MONITOR_DEMAND_INTELLIGENCE,
    title: "Demand Intelligence",
    persona: "Marketing / merchandising",
    heroQuestion: "Which products, brands, and channels are heating up vs underconverting?",
    catalogIds: ["middleby-uc-03", "middleby-uc-14", "middleby-uc-15", "middleby-uc-18", "middleby-uc-19"],
    phase: "active",
  },
  {
    id: MIDDLEBY_MONITOR_BRAND_PORTFOLIO,
    title: "Brand Portfolio",
    persona: "Leadership",
    heroQuestion: "How are buyers behaving across the 53 brands as a portfolio?",
    catalogIds: ["middleby-uc-11", "middleby-uc-15", "middleby-uc-18", "middleby-uc-25"],
    phase: "active",
  },
];

export function getMiddlebyMonitor(monitorId) {
  return MIDDLEBY_MONITORS.find((m) => m.id === monitorId) ?? null;
}

/** First monitor that includes this catalog entry (active monitors preferred). */
export function getMonitorForCatalogId(catalogId) {
  if (!catalogId) return null;
  const active = MIDDLEBY_MONITORS.find(
    (m) => m.phase === "active" && m.catalogIds.includes(catalogId)
  );
  if (active) return active;
  return MIDDLEBY_MONITORS.find((m) => m.catalogIds.includes(catalogId)) ?? null;
}

export function monitorPath(monitorId) {
  return `/analytics/monitor/${monitorId}`;
}
