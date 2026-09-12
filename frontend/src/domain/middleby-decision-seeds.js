/**
 * Middleby automation pattern seeds (A1–A7 POC). Retail/GFI never loads these.
 */

export const MIDDLEBY_DECISIONS_KEY = "retailgpt:middleby-decisions:v1";

export const MIDDLEBY_AUTOMATION_PATTERNS = [
  { id: "A1", label: "Weekly dealer worklist refresh", catalogIds: ["middleby-uc-04"], poc: true },
  { id: "A2", label: "Dealer SLA escalation", catalogIds: ["middleby-uc-04", "middleby-uc-06"], poc: true },
  { id: "A3", label: "Friday executive briefing", catalogIds: ["middleby-uc-16", "middleby-uc-07"], poc: true },
  { id: "A4", label: "Brand gap nudge to marketing", catalogIds: ["middleby-uc-18"], poc: true },
  { id: "A5", label: "SKU watchlist export", catalogIds: ["middleby-uc-19"], poc: true },
  { id: "A6", label: "Consultation-yes priority routing", catalogIds: ["middleby-uc-10"], poc: true },
  { id: "A7", label: "Post-deploy regression alert", catalogIds: ["middleby-uc-08", "middleby-uc-20"], poc: true },
  { id: "A8", label: "Quarterly dealer scorecard pack", catalogIds: ["middleby-uc-06"], poc: false },
  { id: "A9", label: "Cross-sell at quote submission", catalogIds: ["middleby-uc-27"], poc: false },
  { id: "A10", label: "HubSpot stage write-back", catalogIds: ["middleby-uc-04", "middleby-uc-06"], poc: false },
];

export function getPatternsForCatalogId(catalogId) {
  return MIDDLEBY_AUTOMATION_PATTERNS.filter((p) => p.catalogIds.includes(catalogId));
}

export function getPatternsForMonitor(monitorId, manifest) {
  if (!manifest?.catalogIds) return [];
  const ids = new Set(manifest.catalogIds);
  return MIDDLEBY_AUTOMATION_PATTERNS.filter((p) => p.catalogIds.some((id) => ids.has(id)));
}

export function getMiddlebyDecisionSeedCards() {
  return [
    {
      id: "mb-a1",
      patternId: "A1",
      title: "Weekly dealer worklist refresh",
      reasoning:
        "18 quotes in the 0-1 month window need near-term follow-up. Enterprise GPT can rank them and create HubSpot tasks after your approval.",
      action:
        "On approval: rank quotes, draft Monday brief, and create HubSpot follow-up tasks for assigned reps.",
      dealer: "Dealer ops",
      value: 0,
      status: "pending",
    },
    {
      id: "mb-a2",
      patternId: "A2",
      title: "Dealer SLA escalation (>14 days)",
      reasoning:
        "12 quotes exceeded the 14-day SLA threshold. Enterprise GPT can build a leadership escalation digest and queue rep notifications after approval.",
      action:
        "On approval: flag overdue deals, attach rep context, and queue leadership and rep notifications.",
      dealer: "Sales leadership",
      value: 0,
      status: "pending",
    },
    {
      id: "mb-a4",
      patternId: "A4",
      title: "Brand browse-vs-quote gap: Globe",
      reasoning:
        "Globe shows 2,236 GA sessions but only 3 quotes. Enterprise GPT can surface the gap and open a marketing review task after approval.",
      action:
        "On approval: calculate browse-to-quote gap, attach landing pages, and create Asana RFQ review task.",
      dealer: "Marketing",
      value: 0,
      status: "pending",
    },
    {
      id: "mb-a6",
      patternId: "A6",
      title: "Consultation-yes priority routing",
      reasoning:
        "28 quotes requested consultation. Enterprise GPT can route them to senior reps in priority order after ops confirms assignments.",
      action:
        "On approval: filter consultation=yes quotes, match senior reps, and stage HubSpot ownership updates.",
      dealer: "Dealer ops",
      value: 0,
      status: "pending",
    },
    {
      id: "mb-a7",
      patternId: "A7",
      title: "Post-deploy regression alert",
      reasoning:
        "RequireJS errors spiked after the last site release. Enterprise GPT can compare error rates and post an incident alert after commerce lead approval.",
      action:
        "On approval: compare pre/post deploy error rate, attach error detail, and post to the commerce incident channel.",
      dealer: "Digital / commerce",
      value: 0,
      status: "pending",
    },
  ];
}

export function getMiddlebyDecisionSeedCardsForPattern(patternId) {
  return getMiddlebyDecisionSeedCards().filter((c) => c.patternId === patternId);
}

/** Pending decision cards linked to a catalog use case (POC patterns only). */
export function getDecisionCardsForCatalogId(catalogId) {
  const patternIds = new Set(
    getPatternsForCatalogId(catalogId)
      .filter((p) => p.poc)
      .map((p) => p.id)
  );
  if (!patternIds.size) return [];
  return getMiddlebyDecisionSeedCards().filter((c) => patternIds.has(c.patternId));
}
