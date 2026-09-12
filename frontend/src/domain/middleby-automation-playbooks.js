/**
 * Middleby automation playbooks: end-to-end Analyze → Monitor → Automate journeys.
 * Retail/GFI never loads these.
 */

import { MIDDLEBY_MONITOR_PIPELINE_HEALTH, MIDDLEBY_MONITOR_CUSTOMER_EXPERIENCE, MIDDLEBY_MONITOR_DEMAND_INTELLIGENCE } from "./middleby-monitor-manifest";

/** @typedef {'analyze' | 'monitor' | 'automate'} JourneyPhase */

/**
 * @typedef {{
 *   id: string,
 *   label: string,
 *   catalogIds: string[],
 *   monitorId?: string,
 *   trigger: string,
 *   analyzeTitle: string,
 *   monitorTitle: string,
 *   journey: { phase: JourneyPhase, title: string, detail: string }[],
 *   runSteps: { label: string, duration: string }[],
 *   impact: { label: string, value: string, sub?: string }[],
 *   decisionCardId?: string,
 *   owner: string,
 *   automationSummary: string,
 *   automatedActions: { what: string, where?: string, detail?: string }[],
 *   staysHuman: string[],
 * }} MiddlebyAutomationPlaybook
 */

/** @type {MiddlebyAutomationPlaybook[]} */
export const MIDDLEBY_AUTOMATION_PLAYBOOKS = [
  {
    id: "A1",
    label: "Weekly dealer worklist refresh",
    catalogIds: ["middleby-uc-04"],
    monitorId: MIDDLEBY_MONITOR_PIPELINE_HEALTH,
    trigger: "18 quotes in the 0-1 month window need near-term dealer follow-up.",
    analyzeTitle: "Quote follow-up priority",
    monitorTitle: "Pipeline Health",
    owner: "Dealer ops",
    decisionCardId: "mb-a1",
    automationSummary:
      "Builds the weekly prioritized quote follow-up list and creates HubSpot tasks for assigned reps.",
    automatedActions: [
      {
        what: "Pull urgent quotes",
        where: "HubSpot",
        detail: "Filter open deals in the 0-1 month follow-up window.",
      },
      {
        what: "Rank by priority",
        where: "Enterprise GPT",
        detail: "Sort by consultation flag and rep group workload.",
      },
      {
        what: "Draft Monday standup brief",
        where: "Enterprise GPT",
        detail: "One-page dealer follow-up summary for ops review.",
      },
      {
        what: "Create follow-up tasks",
        where: "HubSpot",
        detail: "Tasks with due dates linked to each deal and owner.",
      },
    ],
    staysHuman: [
      "Dealer ops approves the list before tasks are created.",
      "Sales reps make the calls and emails to buyers.",
    ],
    journey: [
      { phase: "analyze", title: "Detect aging quotes", detail: "HubSpot export flags deals in the 0-1 month window." },
      { phase: "monitor", title: "Roll up on Pipeline Health", detail: "SLA gauge and dealer workload confirm urgency." },
      { phase: "automate", title: "Build Monday worklist", detail: "Rank quotes by consultation flag and rep group." },
      { phase: "automate", title: "Approval gate", detail: "Dealer ops reviews before HubSpot tasks are created." },
      { phase: "automate", title: "Route and track", detail: "Tasks assigned; follow-up rate measured next week." },
    ],
    runSteps: [
      { label: "Refresh HubSpot view for quotes aged 0-1 months", duration: "0.6s" },
      { label: "Rank by consultation=yes and rep group load", duration: "0.4s" },
      { label: "Draft dealer follow-up brief for standup", duration: "1.0s" },
      { label: "Stage HubSpot tasks for assigned reps", duration: "0.5s" },
    ],
    impact: [
      { label: "Quotes in worklist", value: "18", sub: "Near-term follow-up" },
      { label: "Consultation priority", value: "6", sub: "Senior rep routing" },
      { label: "Target response lift", value: "+12%", sub: "Vs prior Monday cadence" },
    ],
    kpiTargets: [
      { name: "Total # of Quotes Generated", contributionType: "direct", targetDelta: { value: "+6%", horizon: "4 weeks" }, attribution: "Prioritized Monday outreach improves quote conversion on the 0-1 month cohort." },
      { name: "Closed-Won Rate", contributionType: "indirect", targetDelta: { value: "+2 pts", horizon: "quarter" }, attribution: "Consultation-flagged deals routed to senior reps have a historically higher close rate." },
      { name: "Quotes by Territory", contributionType: "direct", targetDelta: { value: "Balanced load", horizon: "2 weeks" }, attribution: "Rep-group balancing surfaces coverage gaps by territory." },
      { name: "Over-SLA quote count", contributionType: "direct", targetDelta: { value: "-25%", horizon: "4 weeks" }, attribution: "Monday worklist refresh brings aging quotes into the near-term follow-up queue before they breach SLA." },
      { name: "Quote volume", contributionType: "direct", targetDelta: { value: "+4%", horizon: "4 weeks" }, attribution: "Worklist-driven outreach lifts weekly HubSpot quote throughput." },
    ],
  },
  {
    id: "A2",
    label: "Dealer SLA escalation",
    catalogIds: ["middleby-uc-04", "middleby-uc-06"],
    monitorId: MIDDLEBY_MONITOR_PIPELINE_HEALTH,
    trigger: "12 quotes exceeded the 14-day SLA threshold in the aging sample.",
    analyzeTitle: "Quote aging vs SLA",
    monitorTitle: "Pipeline Health",
    owner: "Sales leadership",
    decisionCardId: "mb-a2",
    automationSummary:
      "Flags overdue quotes and sends an escalation package to sales leadership and rep groups.",
    automatedActions: [
      {
        what: "Find SLA breaches",
        where: "HubSpot",
        detail: "Deals open more than 14 days against policy.",
      },
      {
        what: "Attach dealer context",
        where: "HubSpot",
        detail: "Rep group, brand, and deal value on each row.",
      },
      {
        what: "Build escalation digest",
        where: "Enterprise GPT",
        detail: "Leadership summary with ranked at-risk pipeline.",
      },
      {
        what: "Queue rep notifications",
        where: "HubSpot",
        detail: "Held until leadership approves who is notified.",
      },
    ],
    staysHuman: [
      "Sales leadership approves notifications before they go out.",
      "Reps follow up with buyers on escalated deals.",
    ],
    journey: [
      { phase: "analyze", title: "Spot SLA breaches", detail: "Per-deal days open compared to 14-day policy." },
      { phase: "monitor", title: "Confirm dealer context", detail: "Pipeline Health shows rep group ownership." },
      { phase: "automate", title: "Escalation package", detail: "Leadership digest with deal list and owners." },
      { phase: "automate", title: "Approval gate", detail: "Sales leadership approves rep notifications." },
      { phase: "automate", title: "Notify and log", detail: "Escalations sent; SLA status tracked daily." },
    ],
    runSteps: [
      { label: "Filter deals open more than 14 days", duration: "0.5s" },
      { label: "Attach rep group and brand context", duration: "0.7s" },
      { label: "Build escalation summary for leadership", duration: "0.9s" },
      { label: "Queue rep notifications (held until approval)", duration: "0.4s" },
    ],
    impact: [
      { label: "Deals escalated", value: "12", sub: "Over 14-day SLA" },
      { label: "Rep groups involved", value: "8", sub: "In HubSpot sample" },
      { label: "Pipeline at risk", value: "23%", sub: "Share of active quotes" },
    ],
    kpiTargets: [
      { name: "Closed-Won Rate", contributionType: "direct", targetDelta: { value: "+3 pts", horizon: "quarter" }, attribution: "SLA breaches historically correlate with reduced close rate; recovering these quotes protects revenue." },
      { name: "Lost Reason Breakdown", contributionType: "indirect", targetDelta: { value: "Reduced 'no response'", horizon: "quarter" }, attribution: "Escalation reduces the 'no response' loss category, which is currently the largest bucket." },
      { name: "Quotes by Territory", contributionType: "indirect", targetDelta: { value: "Territory visibility", horizon: "2 weeks" }, attribution: "Weekly escalation exposure by territory reveals persistent SLA hotspots." },
      { name: "Over-SLA quote count", contributionType: "direct", targetDelta: { value: "-40%", horizon: "quarter" }, attribution: "Escalation is the primary lever for reducing the count of quotes over the SLA threshold." },
      { name: "Quotes by State", contributionType: "indirect", targetDelta: { value: "State-level SLA visibility", horizon: "2 weeks" }, attribution: "Escalation packet surfaces which US states have the highest concentration of SLA breaches." },
    ],
  },
  {
    id: "A3",
    label: "Friday executive briefing",
    catalogIds: ["middleby-uc-16", "middleby-uc-07"],
    monitorId: MIDDLEBY_MONITOR_DEMAND_INTELLIGENCE,
    trigger: "Weekly anomaly flags and executive headlines are ready for leadership review.",
    analyzeTitle: "Weekly anomaly flags",
    monitorTitle: "Demand Intelligence",
    owner: "Leadership",
    automationSummary:
      "Assembles the Friday leadership briefing from GA, HubSpot, and LogRocket weekly changes.",
    automatedActions: [
      {
        what: "Pull weekly deltas",
        where: "GA · HubSpot · LogRocket",
        detail: "Traffic, pipeline, and error trends vs prior week.",
      },
      {
        what: "Merge executive one-pager",
        where: "Enterprise GPT",
        detail: "Headline KPIs, anomalies, and monitor highlights.",
      },
      {
        what: "Attach monitor views",
        where: "Enterprise GPT",
        detail: "Top Demand Intelligence charts for context.",
      },
      {
        what: "Schedule Friday distribution",
        where: "Email",
        detail: "7am send to the leadership distribution list.",
      },
    ],
    staysHuman: [
      "Comms lead approves the recipient list before send.",
      "Leaders review insights and decide actions in meetings.",
    ],
    journey: [
      { phase: "analyze", title: "Compile signals", detail: "Cross-source weekly anomalies and headline KPIs." },
      { phase: "monitor", title: "Demand Intelligence view", detail: "Category and brand gaps summarized." },
      { phase: "automate", title: "Briefing pack", detail: "Friday deck and email digest assembled." },
      { phase: "automate", title: "Approval gate", detail: "Comms lead approves distribution list." },
      { phase: "automate", title: "Send briefing", detail: "Pack delivered; opens tracked." },
    ],
    runSteps: [
      { label: "Pull GA, HubSpot, and LogRocket weekly deltas", duration: "1.2s" },
      { label: "Merge into executive one-pager", duration: "0.8s" },
      { label: "Attach top 3 monitor screenshots", duration: "0.5s" },
      { label: "Schedule Friday 7am distribution", duration: "0.3s" },
    ],
    impact: [
      { label: "Signals in brief", value: "11", sub: "Cross-source" },
      { label: "Recipients", value: "6", sub: "Leadership distro" },
      { label: "Prep time saved", value: "~3 hrs", sub: "Vs manual pack" },
    ],
    kpiTargets: [
      { name: "Weekly anomaly flags", contributionType: "direct", targetDelta: { value: "Faster surface", horizon: "weekly" }, attribution: "Executive briefing delivers weekly anomaly summary in one artifact." },
      { name: "Estimated pipeline value", contributionType: "direct", targetDelta: { value: "Weekly executive line-of-sight", horizon: "weekly" }, attribution: "Pipeline dollars are the headline metric on the Friday leadership one-pager." },
      { name: "Quote volume", contributionType: "indirect", targetDelta: { value: "Weekly trend visibility", horizon: "weekly" }, attribution: "Weekly quote volume vs prior period is one of the three executive-attention headlines." },
      { name: "RFQ conversion rate", contributionType: "indirect", targetDelta: { value: "Weekly attribution", horizon: "weekly" }, attribution: "Briefing attributes the week's conversion movement to funnel + UX + campaign drivers." },
    ],
  },
  {
    id: "A4",
    label: "Brand gap nudge to marketing",
    catalogIds: ["middleby-uc-18"],
    monitorId: MIDDLEBY_MONITOR_DEMAND_INTELLIGENCE,
    trigger: "Globe shows the widest browse-to-quote gap: 2,236 sessions vs 3 quotes.",
    analyzeTitle: "Browse vs quote by brand",
    monitorTitle: "Demand Intelligence",
    owner: "Marketing",
    decisionCardId: "mb-a4",
    automationSummary:
      "Surfaces the worst browse-to-quote gap by brand and opens a marketing review task.",
    automatedActions: [
      {
        what: "Calculate browse-to-quote gap",
        where: "GA · HubSpot",
        detail: "Sessions vs quotes per brand. Export windows differ (GA week vs HubSpot snapshot).",
      },
      {
        what: "Attach top landing pages",
        where: "GA",
        detail: "Pages driving browse without quote conversion.",
      },
      {
        what: "Draft marketing nudge",
        where: "Enterprise GPT",
        detail: "Suggested owners and RFQ content review notes.",
      },
      {
        what: "Create review task",
        where: "Asana",
        detail: "RFQ content and dealer handoff follow-up.",
      },
    ],
    staysHuman: [
      "Marketing lead approves outreach copy before launch.",
      "Team updates content, campaigns, and dealer handoff.",
    ],
    journey: [
      { phase: "analyze", title: "Quantify gap", detail: "GA sessions compared to HubSpot quotes by brand." },
      { phase: "monitor", title: "Demand Intelligence", detail: "Treemap and gap chart confirm Globe lead." },
      { phase: "automate", title: "Marketing nudge", detail: "RFQ content and dealer handoff review requested." },
      { phase: "automate", title: "Approval gate", detail: "Marketing lead approves outreach copy." },
      { phase: "automate", title: "Launch nudge", detail: "Campaign task created; gap tracked weekly." },
    ],
    runSteps: [
      { label: "Calculate browse-to-quote ratio for Globe", duration: "0.4s" },
      { label: "Attach top landing pages from GA sample", duration: "0.6s" },
      { label: "Draft marketing nudge with suggested owners", duration: "1.0s" },
      { label: "Create Asana task for RFQ content review", duration: "0.4s" },
    ],
    impact: [
      { label: "Browse sessions", value: "2,236", sub: "Globe (GA sample)" },
      { label: "HubSpot quotes", value: "3", sub: "Sep export snapshot" },
      { label: "Gap rank", value: "#1", sub: "Among 20 brands" },
    ],
    kpiTargets: [
      { name: "Traffic by Category", contributionType: "indirect", targetDelta: { value: "Better conversion", horizon: "quarter" }, attribution: "Marketing nudge tightens landing pages that drive high browse and low quote conversion." },
      { name: "Quotes by Brand", contributionType: "direct", targetDelta: { value: "+8% Globe", horizon: "quarter" }, attribution: "Brand-specific content and dealer handoff improvements should lift quote volume on Globe." },
      { name: "Conversion rate", contributionType: "direct", targetDelta: { value: "+0.4 pts", horizon: "quarter" }, attribution: "Closing browse-to-quote gaps directly improves site-level conversion." },
      { name: "GA sessions", contributionType: "indirect", targetDelta: { value: "Session-mix visibility", horizon: "quarter" }, attribution: "Marketing nudge redirects paid + organic traffic toward brand pages with the largest browse-to-quote gap." },
      { name: "Browse-to-quote gap index", contributionType: "direct", targetDelta: { value: "Narrowed gap", horizon: "quarter" }, attribution: "Closing the observed browse-to-quote gap is the primary metric this playbook targets." },
      { name: "Quotes by Category", contributionType: "indirect", targetDelta: { value: "Category-level lift", horizon: "quarter" }, attribution: "Brand-gap fixes cascade into L1 category quote-volume improvement." },
    ],
  },
  {
    id: "A5",
    label: "SKU watchlist export",
    catalogIds: ["middleby-uc-19"],
    monitorId: MIDDLEBY_MONITOR_DEMAND_INTELLIGENCE,
    trigger: "High-intent SKUs crossed watch thresholds in the catalog sample.",
    analyzeTitle: "SKU watchlist",
    monitorTitle: "Demand Intelligence",
    owner: "Merchandising",
    automationSummary:
      "Exports a ranked SKU watchlist for merchandising and dealer teams.",
    automatedActions: [
      {
        what: "Rank SKUs by intent",
        where: "Enterprise GPT",
        detail: "Browse and quote signals combined into a watch score.",
      },
      {
        what: "Enrich with catalog data",
        where: "Akeneo",
        detail: "Price, category, and product attributes on each row.",
      },
      {
        what: "Generate CSV export",
        where: "Enterprise GPT",
        detail: "Dealer-ready watchlist file.",
      },
      {
        what: "Email download link",
        where: "Email",
        detail: "Sent to the merchandising distribution list.",
      },
    ],
    staysHuman: [
      "Merchandising confirms who receives the export.",
      "Team acts on catalog, pricing, and assortment changes.",
    ],
    journey: [
      { phase: "analyze", title: "Flag SKUs", detail: "Browse and quote signals per SKU from catalog." },
      { phase: "monitor", title: "Demand roll-up", detail: "Category performance validates priority list." },
      { phase: "automate", title: "Export watchlist", detail: "CSV for merchandising and dealer teams." },
      { phase: "automate", title: "Approval gate", detail: "Merchandising confirms distribution." },
      { phase: "automate", title: "Distribute list", detail: "Export shared; watch metrics updated." },
    ],
    runSteps: [
      { label: "Rank SKUs by intent score", duration: "0.7s" },
      { label: "Enrich with catalog price and category", duration: "0.5s" },
      { label: "Generate CSV export", duration: "0.3s" },
      { label: "Email link to merchandising distro", duration: "0.4s" },
    ],
    impact: [
      { label: "SKUs on watchlist", value: "14", sub: "Above threshold" },
      { label: "Categories covered", value: "6", sub: "L1 in sample" },
      { label: "Export format", value: "CSV", sub: "Dealer-ready" },
    ],
    kpiTargets: [
      { name: "Top Viewed PDPs", contributionType: "direct", targetDelta: { value: "Content coverage", horizon: "quarter" }, attribution: "Watchlist surfaces the SKUs merchandising should prioritize for spec sheets and comparison content." },
      { name: "Top Search Terms", contributionType: "indirect", targetDelta: { value: "Better mapping", horizon: "quarter" }, attribution: "Watchlist alignment improves search-term-to-SKU mapping in the shop." },
      { name: "Zero-result searches", contributionType: "indirect", targetDelta: { value: "-5%", horizon: "quarter" }, attribution: "Prioritized dealer follow-up on watchlist SKUs improves listing completeness." },
    ],
  },
  {
    id: "A6",
    label: "Consultation-yes priority routing",
    catalogIds: ["middleby-uc-10"],
    monitorId: MIDDLEBY_MONITOR_PIPELINE_HEALTH,
    trigger: "28 quotes requested consultation in the HubSpot sample.",
    analyzeTitle: "Consultation pipeline",
    monitorTitle: "Pipeline Health",
    owner: "Dealer ops",
    decisionCardId: "mb-a6",
    automationSummary:
      "Routes consultation=yes quotes to senior reps in priority order.",
    automatedActions: [
      {
        what: "Filter consultation quotes",
        where: "HubSpot",
        detail: "Open deals where consultation was requested.",
      },
      {
        what: "Match rep capacity",
        where: "HubSpot",
        detail: "Senior reps by region with current workload.",
      },
      {
        what: "Build priority worklist",
        where: "Enterprise GPT",
        detail: "Ranked queue for dealer ops review.",
      },
      {
        what: "Stage ownership updates",
        where: "HubSpot",
        detail: "Rep assignment held until ops confirms.",
      },
    ],
    staysHuman: [
      "Dealer ops confirms rep assignments before updates go live.",
      "Senior reps conduct consultation calls with buyers.",
    ],
    journey: [
      { phase: "analyze", title: "Identify consultation=yes", detail: "HubSpot consultation field on open quotes." },
      { phase: "monitor", title: "Pipeline context", detail: "Dealer workload and SLA on same monitor." },
      { phase: "automate", title: "Priority routing", detail: "Senior reps get ranked consultation queue." },
      { phase: "automate", title: "Approval gate", detail: "Dealer ops confirms rep assignments." },
      { phase: "automate", title: "Assign and notify", detail: "HubSpot ownership updated; reps alerted." },
    ],
    runSteps: [
      { label: "Filter consultation=yes quotes", duration: "0.4s" },
      { label: "Match to senior rep capacity by region", duration: "0.8s" },
      { label: "Build priority worklist", duration: "0.5s" },
      { label: "Stage ownership updates in HubSpot", duration: "0.4s" },
    ],
    impact: [
      { label: "Consultation quotes", value: "28", sub: "In sample" },
      { label: "Senior reps assigned", value: "12", sub: "Regional coverage" },
      { label: "Target first touch", value: "< 24h", sub: "After approval" },
    ],
    kpiTargets: [
      { name: 'Live chat "Can I buy" inquiries', contributionType: "direct", targetDelta: { value: "Same-day routing", horizon: "1 week" }, attribution: "Consultation-yes flags from the buy-intent chat are routed to senior reps within 24 hours." },
      { name: "Quotes by Customer Segment", contributionType: "indirect", targetDelta: { value: "Segment visibility", horizon: "2 weeks" }, attribution: "Priority routing surfaces segment concentration in the consultation queue." },
      { name: "Closed-Won Rate", contributionType: "direct", targetDelta: { value: "+4 pts", horizon: "quarter" }, attribution: "Senior-rep consultation is the highest-conversion path in the funnel." },
      { name: "RFQ conversion rate", contributionType: "direct", targetDelta: { value: "+0.6 pts", horizon: "quarter" }, attribution: "Consultation-flagged sessions convert to submitted RFQ at 38% vs 21% self-serve." },
      { name: "PO Reference Updates", contributionType: "indirect", targetDelta: { value: "Higher coverage", horizon: "quarter" }, attribution: "Senior-rep-managed consultation deals more consistently populate PO reference at closed-won." },
    ],
  },
  {
    id: "A7",
    label: "Post-deploy regression alert",
    catalogIds: ["middleby-uc-08", "middleby-uc-20"],
    monitorId: MIDDLEBY_MONITOR_CUSTOMER_EXPERIENCE,
    trigger: "Magento RequireJS errors spiked after the last site release marker.",
    analyzeTitle: "UX error impact",
    monitorTitle: "Customer Experience",
    owner: "Digital / commerce",
    decisionCardId: "mb-a7",
    automationSummary:
      "Detects post-release error spikes and alerts the digital team with error detail.",
    automatedActions: [
      {
        what: "Compare error rate around deploy",
        where: "LogRocket",
        detail: "24 hours before vs after the last release marker.",
      },
      {
        what: "Attach error breakdown",
        where: "LogRocket",
        detail: "Top error type and affected session count.",
      },
      {
        what: "Draft incident summary",
        where: "Enterprise GPT",
        detail: "Commerce team brief with estimated quote impact.",
      },
      {
        what: "Post to incident channel",
        where: "Slack",
        detail: "Held until commerce lead approves the message.",
      },
    ],
    staysHuman: [
      "Commerce lead approves the incident post before it is sent.",
      "Digital team investigates and fixes issues (e.g. JIRA tickets).",
    ],
    journey: [
      { phase: "analyze", title: "Detect error spike", detail: "LogRocket sessions vs release calendar." },
      { phase: "monitor", title: "Customer Experience", detail: "Deploy line chart confirms post-release regression." },
      { phase: "automate", title: "Regression alert", detail: "Digital team notified with error detail." },
      { phase: "automate", title: "Approval gate", detail: "Commerce lead approves incident channel post." },
      { phase: "automate", title: "Track recovery", detail: "Error rate monitored until baseline restored." },
    ],
    runSteps: [
      { label: "Compare error rate 24h before and after deploy", duration: "0.6s" },
      { label: "Attach top error type and affected sessions", duration: "0.5s" },
      { label: "Draft incident summary for digital team", duration: "0.9s" },
      { label: "Post to Slack #commerce-incidents (held until approval)", duration: "0.3s" },
    ],
    impact: [
      { label: "Sessions affected", value: "4,232", sub: "RequireJS failure" },
      { label: "Quotes at risk (est.)", value: "38", sub: "UX impact sample" },
      { label: "Deploy marker", value: "Mar 22", sub: "Last site release" },
    ],
    kpiTargets: [
      { name: "Drop off rate on get a quote", contributionType: "direct", targetDelta: { value: "Return to baseline", horizon: "24-48h" }, attribution: "Early alert lets the digital team fix the regression before it dents conversion." },
      { name: "Bounce rate by Entry Page", contributionType: "indirect", targetDelta: { value: "Contain impact", horizon: "24-48h" }, attribution: "Faster incident resolution limits bounce inflation across affected entry pages." },
      { name: "Guided Shopping Tool Conversion Rate", contributionType: "indirect", targetDelta: { value: "Protect", horizon: "24-48h" }, attribution: "Guided-tool flows are sensitive to script errors; fast recovery protects the funnel." },
      { name: "JS error sessions", contributionType: "direct", targetDelta: { value: "Contain post-release spike", horizon: "24-48h" }, attribution: "Post-deploy alert catches the LogRocket-error surge within the release window." },
      { name: "Error rate per 1K sessions", contributionType: "direct", targetDelta: { value: "Return to <10 target", horizon: "24-48h" }, attribution: "Normalized error rate is the primary post-deploy regression metric." },
      { name: "Page Depth / Session", contributionType: "indirect", targetDelta: { value: "Depth recovery", horizon: "24-48h" }, attribution: "Regressions typically depress session depth; fast rollback restores the pre-release baseline." },
      { name: "Time on site / session duration", contributionType: "indirect", targetDelta: { value: "Duration recovery", horizon: "24-48h" }, attribution: "UX regressions cut session duration; incident response restores engagement." },
      { name: "Traffic by device type", contributionType: "indirect", targetDelta: { value: "Device-scoped detection", horizon: "24-48h" }, attribution: "Many regressions are device-specific (e.g. iOS Safari); device-segmented visibility scopes the fix." },
    ],
  },
];

/** Demo order for automation-ready use cases in catalog strips. */
export const POC_AUTOMATION_CATALOG_PRIORITY = [
  "middleby-uc-04",
  "middleby-uc-06",
  "middleby-uc-10",
  "middleby-uc-08",
  "middleby-uc-20",
  "middleby-uc-18",
  "middleby-uc-19",
  "middleby-uc-16",
  "middleby-uc-07",
];

const POC_PLAYBOOK_IDS = new Set(["A1", "A2", "A3", "A4", "A5", "A6", "A7"]);

export function getAutomationPlaybook(patternId) {
  return MIDDLEBY_AUTOMATION_PLAYBOOKS.find((p) => p.id === patternId) ?? null;
}

export function getPlaybooksForCatalogId(catalogId) {
  return MIDDLEBY_AUTOMATION_PLAYBOOKS.filter((p) => p.catalogIds.includes(catalogId));
}

/** POC playbooks linked to a catalog use case (for Automate tab + badges). */
export function getPocPlaybooksForCatalogId(catalogId) {
  return getPlaybooksForCatalogId(catalogId).filter((p) => POC_PLAYBOOK_IDS.has(p.id));
}

export function catalogHasPocAutomation(catalogId) {
  return getPocPlaybooksForCatalogId(catalogId).length > 0;
}

export function getPocAutomationCatalogIds() {
  const ids = new Set();
  for (const p of MIDDLEBY_AUTOMATION_PLAYBOOKS) {
    if (!POC_PLAYBOOK_IDS.has(p.id)) continue;
    for (const id of p.catalogIds) ids.add(id);
  }
  return POC_AUTOMATION_CATALOG_PRIORITY.filter((id) => ids.has(id));
}

export function getPocPlaybooks() {
  return MIDDLEBY_AUTOMATION_PLAYBOOKS.filter((p) =>
    ["A1", "A2", "A3", "A4", "A5", "A6", "A7"].includes(p.id)
  );
}

/** Playbooks with a human approval gate in the POC (vs simulate-only digest/export). */
export function playbookRequiresApproval(playbook) {
  return Boolean(playbook?.decisionCardId);
}
