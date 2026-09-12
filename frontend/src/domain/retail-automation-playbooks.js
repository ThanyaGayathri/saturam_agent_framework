/**
 * Retail (default profile) automation playbooks.
 *
 * Two playbooks:
 *   R1 - Cross-store stockout transfer  (approval-gated)
 *   R2 - Weekly gross margin briefing   (no approval, runs on schedule)
 *
 * Each is anchored to a use case already in the shared retail catalog
 * (use-case-catalog-seed.js). Playbook shape mirrors the Middleby playbooks,
 * with an inline `decisionCard` for R1 so we do not need a separate decision
 * seed registry for this profile.
 */

/** @type {import("./middleby-automation-playbooks").MiddlebyAutomationPlaybook[]} */
export const RETAIL_AUTOMATION_PLAYBOOKS = [
  {
    id: "R1",
    label: "Cross-store stockout transfer",
    catalogIds: ["gallery-uc-01"],
    trigger: "Store 42 SKU-9821 will stock out in 6 hours. Store 17 has 45 units of overstock.",
    analyzeTitle: "Stockout risk vs nearby overstock",
    monitorTitle: "Inventory Health",
    owner: "Regional Manager",
    decisionCardId: "R1-card",
    decisionCard: {
      id: "R1-card",
      title: "Approve cross-store transfer for SKU-9821",
      reasoning:
        "Store 42 is projected to stock out of SKU-9821 (Sony WH-1000XM5) in 6 hours based on the last 14 days of sell-through. Store 17 has 45 units of overstock (18 days on hand). Standard fleet has capacity for a same-day dispatch.",
      action:
        "Transfer 40 units of SKU-9821 from Store 17 to Store 42 via standard fleet. Cost $180. Est. arrival 5 hours.",
    },
    automationSummary:
      "Detects same-region stockout risk and drafts a transfer PO from the nearest overstocked store.",
    automatedActions: [
      { what: "Confirm stockout risk", where: "ERP", detail: "Compare 14-day sell-through against on-hand at Store 42." },
      { what: "Find nearest overstock", where: "ERP", detail: "Same SKU, same region, days-on-hand above threshold." },
      { what: "Calculate transfer quantity", where: "Enterprise GPT", detail: "Size the move to protect both stores through the 7-day forecast." },
      { what: "Draft carrier dispatch", where: "Fleet system", detail: "Route Store 17 to Store 42 on the standard truck lane." },
      { what: "Stage PO update", where: "ERP", detail: "Transfer PO created in draft; held until Regional Manager approves." },
    ],
    staysHuman: [
      "Regional Manager approves the transfer PO before the truck is dispatched.",
      "Store 17 associates confirm pick, pack, and dispatch.",
    ],
    journey: [
      { phase: "analyze", title: "Detect stockout risk", detail: "14-day sell-through vs on-hand at Store 42." },
      { phase: "monitor", title: "Confirm on Inventory Health", detail: "Regional overstock signal supports the transfer choice." },
      { phase: "automate", title: "Draft transfer PO", detail: "Store 17 to Store 42, 40 units, standard fleet." },
      { phase: "automate", title: "Approval gate", detail: "Regional Manager approves before dispatch." },
      { phase: "automate", title: "Dispatch and track", detail: "Truck routed; ERP updated; delivery tracked." },
    ],
    runSteps: [
      { label: "Confirm stockout risk in ERP", duration: "0.6s" },
      { label: "Find nearest overstock (Store 17)", duration: "0.5s" },
      { label: "Calculate transfer quantity", duration: "0.4s" },
      { label: "Draft carrier dispatch", duration: "0.7s" },
      { label: "Stage transfer PO (held until approval)", duration: "0.5s" },
    ],
    impact: [
      { label: "Est. stockout avoided", value: "SKU-9821", sub: "Store 42" },
      { label: "Sales protected", value: "$8,400", sub: "Est. 7-day demand" },
      { label: "Transfer cost", value: "$180", sub: "Standard fleet" },
    ],
    kpiTargets: [
      { name: "Stockout Rate", contributionType: "direct", targetDelta: { value: "-2 pts", horizon: "4 weeks" }, attribution: "Cross-store transfers close the gap between local demand and local supply." },
      { name: "Gross Margin Rate", contributionType: "indirect", targetDelta: { value: "+0.4 pts", horizon: "quarter" }, attribution: "Fewer stockouts protect full-margin sell-through." },
      { name: "Order Fill Rate", contributionType: "direct", targetDelta: { value: "+1.5 pts", horizon: "2 weeks" }, attribution: "Same-day transfers keep in-store availability higher." },
    ],
  },
  {
    id: "R2",
    label: "Weekly gross margin briefing",
    catalogIds: ["gallery-uc-09"],
    trigger: "Weekly gross margin data has refreshed. Two categories moved more than 1 point week-over-week.",
    analyzeTitle: "Gross margin by category",
    monitorTitle: "Finance",
    owner: "Executive team",
    // No decisionCardId => no approval gate; the workflow is a briefing.
    automationSummary:
      "Assembles the weekly gross margin briefing from data warehouse category cuts.",
    automatedActions: [
      { what: "Pull weekly margin data", where: "Data warehouse", detail: "Category-level gross margin for the last 8 weeks." },
      { what: "Rank categories by margin change", where: "Enterprise GPT", detail: "Movers vs prior week, with context on drivers." },
      { what: "Draft executive briefing", where: "Enterprise GPT", detail: "Headline KPIs, top movers, and 3 talking points." },
      { what: "Schedule Friday distribution", where: "Email", detail: "7am send to the executive distribution list." },
    ],
    staysHuman: [
      "Comms lead confirms the recipient list before send.",
      "Executives review and act in Monday leadership meeting.",
    ],
    journey: [
      { phase: "analyze", title: "Pull weekly signals", detail: "Category margin cuts for the last 8 weeks." },
      { phase: "monitor", title: "Finance snapshot", detail: "Category movers and headline KPIs on same monitor." },
      { phase: "automate", title: "Briefing pack", detail: "Weekly gross margin one-pager and CSV detail." },
      { phase: "automate", title: "Send briefing", detail: "Distributed on Friday morning; opens tracked." },
    ],
    runSteps: [
      { label: "Pull data warehouse category cuts", duration: "0.9s" },
      { label: "Rank categories by margin change", duration: "0.5s" },
      { label: "Draft executive briefing", duration: "1.0s" },
      { label: "Schedule Friday 7am distribution", duration: "0.3s" },
    ],
    impact: [
      { label: "Categories analyzed", value: "12", sub: "L1 categories" },
      { label: "Recipients", value: "8", sub: "Exec distro" },
      { label: "Prep time saved", value: "~4 hrs", sub: "Vs manual pack" },
    ],
    kpiTargets: [
      { name: "Gross Margin Rate", contributionType: "indirect", targetDelta: { value: "Visibility", horizon: "weekly" }, attribution: "Weekly visibility on margin movers by category enables earlier corrective action." },
    ],
  },
];
