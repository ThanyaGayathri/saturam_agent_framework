/**
 * Gourmet Foods International (GFI) automation playbooks.
 *
 * Two playbooks:
 *   G1 - Inter-DC perishable transfer   (approval-gated)
 *   G2 - Weekly supplier risk digest    (no approval, runs on schedule)
 *
 * Each is anchored to a use case already in the GFI catalog
 * (gfi-demo-seed.js). Playbook shape mirrors the Middleby playbooks, with
 * an inline `decisionCard` for G1 so we do not need a separate decision
 * seed registry for this profile.
 */

/** @type {import("./middleby-automation-playbooks").MiddlebyAutomationPlaybook[]} */
export const GFI_AUTOMATION_PLAYBOOKS = [
  {
    id: "G1",
    label: "Inter-DC perishable transfer",
    catalogIds: ["gfi-uc-02-inter-dc-rebalance"],
    trigger: "Denver DC is overstocked on Brie (5 days remaining) while NJ DC will stock out in 3 days.",
    analyzeTitle: "Perishable stock imbalance across DCs",
    monitorTitle: "Fleet & Inventory",
    owner: "Logistics Manager",
    decisionCardId: "G1-card",
    decisionCard: {
      id: "G1-card",
      title: "Approve Denver -> NJ Brie transfer",
      reasoning:
        "Denver DC has 260 lbs of Brie de Meaux with 5 days to code expiry. NJ DC sell-through will exhaust stock in 3 days. GFI multi-temp fleet has an empty return leg on the Denver-to-NJ lane tonight.",
      action:
        "Transfer 240 lbs of Brie de Meaux from Denver DC to NJ DC via GFI multi-temp fleet, dispatch 6 PM tonight, arrival 8 AM tomorrow.",
    },
    automationSummary:
      "Detects inter-DC perishable imbalance and drafts a temperature-aware transfer on GFI's own fleet.",
    automatedActions: [
      { what: "Confirm inventory imbalance", where: "WMS", detail: "Denver on-hand vs NJ 3-day forecast for the same SKU." },
      { what: "Verify perishability window", where: "WMS", detail: "Days-to-code-expiry on both ends of the move." },
      { what: "Check fleet availability", where: "TMS", detail: "Available multi-temp truck on the Denver-NJ lane tonight." },
      { what: "Draft dispatch plan", where: "TMS", detail: "Truck route, temperature setpoint, and estimated arrival." },
      { what: "Stage WMS transfer order", where: "WMS", detail: "Transfer order created in draft; held until Logistics Manager approves." },
    ],
    staysHuman: [
      "Logistics Manager approves the transfer before dispatch.",
      "DC associates confirm pick, pack, and multi-temp truck load.",
    ],
    journey: [
      { phase: "analyze", title: "Detect DC imbalance", detail: "Denver overstock vs NJ 3-day depletion for Brie." },
      { phase: "monitor", title: "Fleet & Inventory view", detail: "Available truck capacity confirms the move is feasible tonight." },
      { phase: "automate", title: "Draft transfer order", detail: "240 lbs, Denver to NJ, multi-temp fleet." },
      { phase: "automate", title: "Approval gate", detail: "Logistics Manager approves before dispatch." },
      { phase: "automate", title: "Dispatch and track", detail: "Truck departs 6 PM; WMS updated on arrival." },
    ],
    runSteps: [
      { label: "Confirm Denver overstock in WMS", duration: "0.5s" },
      { label: "Verify NJ 3-day depletion forecast", duration: "0.6s" },
      { label: "Check multi-temp fleet availability", duration: "0.7s" },
      { label: "Draft transfer order (Denver -> NJ)", duration: "0.4s" },
      { label: "Stage WMS transfer (held until approval)", duration: "0.5s" },
    ],
    impact: [
      { label: "Perishable loss avoided", value: "$4,320", sub: "240 lbs Brie" },
      { label: "NJ stockout prevented", value: "3 days", sub: "Time bought" },
      { label: "Transfer cost", value: "$620", sub: "Multi-temp fleet" },
    ],
    kpiTargets: [
      { name: "Perishable shrink rate", contributionType: "direct", targetDelta: { value: "-0.4 pts", horizon: "quarter" }, attribution: "Rebalancing avoids code-expiry write-offs at overstocked DCs." },
      { name: "Order Fill Rate", contributionType: "direct", targetDelta: { value: "+1.8 pts", horizon: "2 weeks" }, attribution: "Fewer regional stockouts on perishables improve fill." },
      { name: "Fleet utilization", contributionType: "indirect", targetDelta: { value: "+2 pts", horizon: "quarter" }, attribution: "Reuses empty return legs on the multi-temp fleet." },
    ],
  },
  {
    id: "G2",
    label: "Weekly supplier risk digest",
    catalogIds: ["gfi-uc-03-supplier-risk"],
    trigger: "Weekly supplier risk scan complete. 4 elevated-risk signals across 2,000+ suppliers.",
    analyzeTitle: "Supplier risk & disruption signals",
    monitorTitle: "Procurement",
    owner: "Procurement leadership",
    // No decisionCardId => no approval gate; the workflow is a digest.
    automationSummary:
      "Watches tariffs, weather, port disruption, and small-producer fragility signals across GFI's 2,000+ suppliers and assembles a weekly digest.",
    automatedActions: [
      { what: "Scan external risk feeds", where: "News · Weather · Trade data", detail: "Tariff changes, weather anomalies, port strikes, and small-producer news." },
      { what: "Match signals to supplier records", where: "Enterprise GPT", detail: "Signals linked to 2,000+ suppliers via ERP mapping." },
      { what: "Rank elevated-risk suppliers", where: "Enterprise GPT", detail: "Risk score by exposure value, sole-source flag, and lead time." },
      { what: "Draft weekly digest", where: "Enterprise GPT", detail: "One-page procurement summary plus a full at-risk supplier list." },
      { what: "Schedule Monday distribution", where: "Email", detail: "7am send to the procurement leadership distribution list." },
    ],
    staysHuman: [
      "Procurement leadership decides which suppliers need immediate action.",
      "Category buyers negotiate with alternates or bring risk to sourcing councils.",
    ],
    journey: [
      { phase: "analyze", title: "Scan risk feeds", detail: "External signal ingestion across news, weather, and trade data." },
      { phase: "monitor", title: "Procurement view", detail: "Elevated-risk suppliers surfaced on the procurement monitor." },
      { phase: "automate", title: "Weekly digest", detail: "Full at-risk list plus procurement recommendations." },
      { phase: "automate", title: "Distribute digest", detail: "Sent Monday 7am to procurement leadership." },
    ],
    runSteps: [
      { label: "Scan external risk feeds", duration: "1.4s" },
      { label: "Match signals to supplier records", duration: "0.9s" },
      { label: "Rank elevated-risk suppliers", duration: "0.6s" },
      { label: "Draft weekly digest", duration: "1.0s" },
      { label: "Schedule Monday 7am distribution", duration: "0.3s" },
    ],
    impact: [
      { label: "Suppliers monitored", value: "2,043", sub: "Full supplier base" },
      { label: "Elevated-risk this week", value: "4", sub: "Above threshold" },
      { label: "Exposure value", value: "$1.8M", sub: "At-risk inventory" },
    ],
    kpiTargets: [
      { name: "Supplier disruption count", contributionType: "indirect", targetDelta: { value: "Earlier signal", horizon: "quarter" }, attribution: "Earlier detection lets category buyers move to alternates before allocations hit." },
      { name: "Fill rate on specialty items", contributionType: "indirect", targetDelta: { value: "+0.7 pts", horizon: "quarter" }, attribution: "Fewer surprise supplier outages preserve fill on artisan and import lines." },
    ],
  },
];
