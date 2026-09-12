/**
 * Operational alert seed for the demo. Each row is tied to a real business
 * area, process, and KPI so the Alerts inbox, bell dropdown, and Monitor
 * page stay consistent with the rest of the hierarchy.
 *
 * Timestamps are stored as negative millisecond offsets from "now" on first
 * load, then frozen to absolute timestamps in localStorage. This keeps the
 * demo feeling fresh when a user first opens the app.
 */

/** @typedef {"critical" | "warning" | "info"} AlertSeverity */
/** @typedef {"new" | "acknowledged" | "snoozed" | "resolved"} AlertStatus */

const MIN = 60 * 1000;
const HOUR = 60 * MIN;
const DAY = 24 * HOUR;

/** @type {Array<Omit<import('./alerts-types').AlertSeed, 'createdAt'> & { createdAtOffsetMs: number, resolvedAtOffsetMs?: number, snoozeUntilOffsetMs?: number }>} */
export const ALERT_SEED = [
  {
    id: "alert-stockout-ne-dc",
    severity: "critical",
    status: "new",
    title: "Stockout risk: 12 SKUs below safety stock in Northeast DC",
    summary:
      "Twelve fast-moving SKUs across grocery and frozen have dropped below their replenishment safety threshold in the Northeast distribution centre. If unaddressed, service-level will fall below 95% within 24 hours.",
    businessAreaId: "inventory",
    processId: "P1",
    useCaseId: "gallery-uc-01",
    kpi: { name: "Stockout Rate", value: "4.8%", delta: "+1.9pp", unit: "%" },
    rule: "Stockout Rate > 3% for more than 2 hours in any DC",
    recommendedActions: [
      "Open Smart Restock to draft emergency replenishment POs for the 12 SKUs.",
      "Reroute inbound shipments from Midwest DC to cover the next 48 hours.",
      "Notify the category manager for olive oils and pasta sauces.",
    ],
    createdAtOffsetMs: -8 * MIN,
  },
  {
    id: "alert-fraud-dach",
    severity: "critical",
    status: "new",
    title: "Fraud anomaly: 34 orders flagged in DACH region (last hour)",
    summary:
      "A burst of orders with matching card BINs and shipping addresses across DE, AT, and CH has tripped the fraud scoring model. Order Orchestrator is holding all 34 in manual review.",
    businessAreaId: "orders",
    processId: "P2",
    useCaseId: null,
    kpi: { name: "Fraud Detection Rate", value: "0.42%", delta: "+0.31pp", unit: "%" },
    rule: "Fraud Detection Rate > 0.3% over any rolling 60-minute window",
    recommendedActions: [
      "Review the 34 held orders and confirm or reject in bulk.",
      "Add the matched BIN range to the temporary block list for 24 hours.",
    ],
    createdAtOffsetMs: -22 * MIN,
  },
  {
    id: "alert-margin-olive-oils",
    severity: "warning",
    status: "new",
    title: "Margin drift: Olive Oils category down 2.4pp vs plan",
    summary:
      "Gross margin for the Olive Oils category has trended below planned margin for 4 consecutive days. Competitor price scraping shows two majors have cut shelf pricing 6-9%.",
    businessAreaId: "pricing",
    processId: "P4",
    useCaseId: null,
    kpi: { name: "Gross Margin by Category", value: "28.6%", delta: "-2.4pp", unit: "%" },
    rule: "Gross Margin by Category below plan by more than 1.5pp for 3+ days",
    recommendedActions: [
      "Open Pricing & Markdown to model a targeted price response.",
      "Validate vendor cost changes with procurement before adjusting shelf price.",
    ],
    createdAtOffsetMs: -45 * MIN,
  },
  {
    id: "alert-checkout-wait-s142",
    severity: "warning",
    status: "new",
    title: "Checkout wait time above target at Store 142 (4.6 min avg)",
    summary:
      "Queue dwell time at Store 142 has averaged 4.6 minutes across the last hour, 53% over the 3-minute target. Only 3 of 8 lanes are staffed against a forecast of 5.",
    businessAreaId: "pos",
    processId: "P8",
    useCaseId: null,
    kpi: { name: "Avg Checkout Wait Time", value: "4.6 min", delta: "+1.6 min", unit: "Minutes" },
    rule: "Avg Checkout Wait Time > 3 min for 30+ minutes",
    recommendedActions: [
      "Call additional cashiers from the floor for the next 60 minutes.",
      "Open self-checkout lane 6 if available.",
    ],
    createdAtOffsetMs: -1 * HOUR,
  },
  {
    id: "alert-return-proc-time",
    severity: "warning",
    status: "new",
    title: "Return processing time up to 6.2 days (target 5)",
    summary:
      "Average time from return initiation to refund has drifted above the 5-day SLA for the second week running. Delays are concentrated in the Atlanta returns hub.",
    businessAreaId: "support",
    processId: "P3",
    useCaseId: null,
    kpi: { name: "Return Processing Time", value: "6.2 days", delta: "+1.2 days", unit: "Days" },
    rule: "Return Processing Time > 5 days for more than 7 consecutive days",
    recommendedActions: [
      "Shift 2 headcount from inbound to returns triage at Atlanta hub for the week.",
      "Enable auto-refund for low-value apparel returns under $25.",
    ],
    createdAtOffsetMs: -4 * HOUR,
  },
  {
    id: "alert-conversion-up",
    severity: "info",
    status: "new",
    title: "Conversion rate up 0.6pp week-over-week on mobile web",
    summary:
      "Mobile web conversion rate has improved 0.6 percentage points vs. the prior 7 days, driven by the new product discovery tiles on category pages.",
    businessAreaId: "e-com",
    processId: "P7",
    useCaseId: null,
    kpi: { name: "Conversion Rate", value: "3.2%", delta: "+0.6pp", unit: "%" },
    rule: "Conversion Rate up more than 0.5pp week-over-week on any digital channel",
    recommendedActions: [
      "Promote the discovery tiles experiment to A/B on desktop.",
    ],
    createdAtOffsetMs: -2 * HOUR,
  },
  {
    id: "alert-inventory-turnover-up",
    severity: "info",
    status: "new",
    title: "Inventory turnover improved 8% in Apparel",
    summary:
      "Apparel inventory turnover is 8% above the 4-week trailing average, reflecting stronger sell-through on the Spring/Summer assortment.",
    businessAreaId: "inventory",
    processId: "P1",
    useCaseId: null,
    kpi: { name: "Inventory Turnover", value: "6.4x", delta: "+0.5x", unit: "Ratio" },
    rule: "Inventory Turnover up more than 5% vs 4-week trailing average",
    recommendedActions: [
      "Consider reallocating slow movers to fast stores using the same logic.",
    ],
    createdAtOffsetMs: -3 * HOUR,
  },
  {
    id: "alert-pos-failure-rate",
    severity: "warning",
    status: "acknowledged",
    title: "POS failure rate spiked to 1.8% on lanes 3-5 overnight",
    summary:
      "Lanes 3-5 at Store 41 logged elevated transaction failures between 23:00 and 04:00 local. Pattern is consistent with a pin pad firmware issue seen two weeks ago.",
    businessAreaId: "pos",
    processId: "P8",
    useCaseId: null,
    kpi: { name: "POS Transaction Failure Rate", value: "1.8%", delta: "+1.1pp", unit: "%" },
    rule: "POS Transaction Failure Rate > 1% at any store for 2+ hours",
    recommendedActions: [
      "Roll firmware 2024.4 on the affected pin pads.",
      "Flag the lanes as degraded in the ops dashboard until resolved.",
    ],
    createdAtOffsetMs: -3 * HOUR,
  },
  {
    id: "alert-overtime-s42",
    severity: "warning",
    status: "snoozed",
    title: "Overtime above 5% target at Store 42",
    summary:
      "Week-to-date overtime at Store 42 has reached 6.3% of paid hours. Root cause is back-of-house staffing shortfall on the overnight replenishment shift.",
    businessAreaId: "workforce",
    processId: "P5",
    useCaseId: null,
    kpi: { name: "Overtime Rate", value: "6.3%", delta: "+1.3pp", unit: "%" },
    rule: "Overtime Rate > 5% week-to-date at any store",
    recommendedActions: [
      "Request flex cover from neighbouring stores through Friday.",
      "Open the overnight replenishment role to part-time applicants.",
    ],
    createdAtOffsetMs: -5 * HOUR,
    snoozeUntilOffsetMs: 19 * HOUR,
  },
  {
    id: "alert-sla-contact-centre",
    severity: "critical",
    status: "resolved",
    title: "Contact centre SLA dropped below 80%, now recovered",
    summary:
      "Speed-to-answer dropped below the 85% service-level threshold for 40 minutes yesterday afternoon. An ad-hoc routing change brought it back to 91%.",
    businessAreaId: "support",
    processId: "P3",
    useCaseId: null,
    kpi: { name: "Contact Centre SLA Compliance", value: "91%", delta: "+11pp", unit: "%" },
    rule: "Contact Centre SLA < 85% for 30+ minutes",
    recommendedActions: [
      "Keep the ad-hoc routing change in place until Monday to confirm stability.",
    ],
    createdAtOffsetMs: -28 * HOUR,
    resolvedAtOffsetMs: -24 * HOUR,
  },
];

/** Media / OTT profile alert seed — trend, competitor, ad-yield, churn, piracy signals. */
export const MEDIA_ALERT_SEED = [
  {
    id: "media-alert-trend-mythology",
    severity: "info",
    status: "new",
    title: "Sustained trend: mythological drama reboots (4-week momentum)",
    summary:
      "Conversation around mythological drama reboots has grown for four consecutive weeks across open-web and social signals; strongest signal from Hindi-speaking Tier-2 audiences. Not a spike.",
    businessAreaId: "merchandise",
    processId: "media-trend-2",
    useCaseId: "media-uc-01-content-conversation-trends",
    kpi: { name: "Trend Momentum Score", value: "78", delta: "+12", unit: "Score" },
    rule: "Trend Momentum Score > 70 sustained for 4 consecutive weeks",
    recommendedActions: [
      "Open the trend-intelligence use case to review source evidence.",
      "Brief content-development on a mythology-adjacent slate exploration.",
    ],
    createdAtOffsetMs: -25 * MIN,
  },
  {
    id: "media-alert-churn-spike",
    severity: "critical",
    status: "new",
    title: "Churn risk spike on monthly cohort after marquee title finished",
    summary:
      "Renewal-window churn for the monthly cohort has climbed 3.4 pts week-over-week following the finish of the marquee release. Save-offer eligibility window closes in 48 hours.",
    businessAreaId: "support",
    processId: "media-sub-2",
    useCaseId: "media-uc-11-subscription-churn-predictor",
    kpi: { name: "Subscriber Churn Rate", value: "8.6%", delta: "+3.4pp", unit: "%" },
    rule: "Subscriber Churn Rate > 6% on any renewal cohort",
    recommendedActions: [
      "Stage save offers for at-risk cohort with content-affinity segmentation.",
      "Trigger the churn-intervention workflow before the 48-hour window closes.",
    ],
    createdAtOffsetMs: -1 * HOUR,
  },
  {
    id: "media-alert-competitor-price",
    severity: "warning",
    status: "new",
    title: "Competitor pricing move: -19% mobile-only plan (major OTT rival)",
    summary:
      "A major OTT competitor dropped their mobile-only plan by 19% mid-week; telco-bundle activity increased in parallel. ARPU compression risk on affected cohorts.",
    businessAreaId: "support",
    processId: "media-comp-pricing-1",
    useCaseId: "media-uc-12-plan-pricing-optimization",
    kpi: { name: "ARPU (Paid)", value: "112 INR", delta: "-6 INR", unit: "INR" },
    rule: "Competitor plan-price move > 15% in same tier",
    recommendedActions: [
      "Run a scenario in the plan & pricing optimization use case.",
      "Brief marketing on defensive messaging for retained subscribers.",
    ],
    createdAtOffsetMs: -3 * HOUR,
  },
  {
    id: "media-alert-ad-fill-drop",
    severity: "warning",
    status: "new",
    title: "Ad fill rate below target on connected-TV inventory (72%)",
    summary:
      "AVOD fill on connected-TV inventory has averaged 72% over the last 24 hours vs. an 80% target. Programmatic demand-partner mix has skewed after last week's floor adjustment.",
    businessAreaId: "finance",
    processId: "media-adops-2",
    useCaseId: "media-uc-13-ad-yield-optimization",
    kpi: { name: "Ad Fill Rate", value: "72%", delta: "-8pp", unit: "%" },
    rule: "Ad Fill Rate below 80% for more than 12 hours on any device class",
    recommendedActions: [
      "Audit header-bidding waterfall and demand-partner mix on connected TV.",
      "Restore prior floors if yield does not recover in the next 6 hours.",
    ],
    createdAtOffsetMs: -5 * HOUR,
  },
  {
    id: "media-alert-piracy-leak",
    severity: "critical",
    status: "new",
    title: "Piracy leak detected: marquee title within launch window",
    summary:
      "Unauthorized upload of the marquee premiere detected on two torrent indexes and a Telegram channel within 6 hours of release. Takedown draft prepared.",
    businessAreaId: "support",
    processId: "media-piracy-1",
    useCaseId: "media-uc-14-piracy-leak-monitoring",
    kpi: null,
    rule: "Any piracy signal on a marquee title inside the 24-hour launch window",
    recommendedActions: [
      "Approve the auto-drafted takedown notices to legal-authorized recipients.",
      "Notify the content-protection team; trigger escalation if leak persists after 6h.",
    ],
    createdAtOffsetMs: -6 * HOUR,
  },
  {
    id: "media-alert-aeo-gap",
    severity: "info",
    status: "new",
    title: "AEO / GEO visibility widening vs. category leader (9-pt gap)",
    summary:
      "AI-answer presence score on tracked prompts has held at 34% for two weeks while the category leader climbed to 43%. Gap widened from 5 pts to 9 pts.",
    businessAreaId: "e-com",
    processId: "media-mkt-2",
    useCaseId: "media-uc-10-aeo-geo-visibility",
    kpi: { name: "AI Answer Presence Score", value: "34%", delta: "-9pt gap", unit: "%" },
    rule: "AEO presence gap vs. category leader > 5 pts sustained for 2 weeks",
    recommendedActions: [
      "Brief SEO on title-metadata gaps surfaced by the AEO tracker.",
      "Prioritize long-form editorial on the top 5 under-cited titles this month.",
    ],
    createdAtOffsetMs: -1 * DAY,
  },
  {
    id: "media-alert-regional-hindi-share",
    severity: "info",
    status: "new",
    title: "Regional-language watch share up 4 pts (Marathi + Tamil)",
    summary:
      "Regional-language share of watch hours climbed 4 pts week-over-week; Marathi and Tamil catalogs drove the majority of the gain.",
    businessAreaId: "merchandise",
    processId: "media-cnt-2",
    useCaseId: "media-uc-08-regional-language-performance",
    kpi: { name: "Regional-Language Watch Share", value: "38%", delta: "+4pp", unit: "%" },
    rule: "Regional-Language Watch Share up more than 3 pts week-over-week",
    recommendedActions: [
      "Reallocate marketing spend to regional-language originals for the next 2 weeks.",
      "Revisit commissioning shortlist for Marathi and Tamil titles.",
    ],
    createdAtOffsetMs: -8 * HOUR,
  },
  {
    id: "media-alert-content-gap-thriller",
    severity: "warning",
    status: "acknowledged",
    title: "Content gap widened: thriller genre on Hindi catalog (-3 titles)",
    summary:
      "Thriller-genre depth on our Hindi catalog is now 3 titles behind the top two competitor platforms. Competitive title-overlap climbed to 28% this month.",
    businessAreaId: "merchandise",
    processId: "media-mkt-1",
    useCaseId: "media-uc-05-content-gap-analysis",
    kpi: { name: "Competitor Title Overlap", value: "28%", delta: "+3pp", unit: "%" },
    rule: "Competitor Title Overlap > 25% on top 500 titles",
    recommendedActions: [
      "Brief acquisitions on Hindi thriller-catalog opportunities.",
      "Open the content-gap analysis use case for the deep dive.",
    ],
    createdAtOffsetMs: -18 * HOUR,
  },
];

/**
 * Materialize the seed with real timestamps relative to the given "now".
 * Used once, when localStorage has no alerts yet for a profile.
 */
export function materializeAlertsSeed(nowMs = Date.now()) {
  return ALERT_SEED.map((row) => {
    const createdAt = nowMs + row.createdAtOffsetMs;
    const base = {
      id: row.id,
      severity: row.severity,
      status: row.status,
      title: row.title,
      summary: row.summary,
      businessAreaId: row.businessAreaId,
      processId: row.processId,
      useCaseId: row.useCaseId ?? null,
      kpi: row.kpi,
      rule: row.rule,
      recommendedActions: row.recommendedActions ?? [],
      createdAt,
      acknowledgedAt: null,
      resolvedAt: null,
      snoozeUntil: null,
      seed: true,
    };
    if (row.status === "resolved" && typeof row.resolvedAtOffsetMs === "number") {
      base.resolvedAt = nowMs + row.resolvedAtOffsetMs;
    }
    if (row.status === "acknowledged") {
      base.acknowledgedAt = createdAt + 30 * MIN;
    }
    if (row.status === "snoozed" && typeof row.snoozeUntilOffsetMs === "number") {
      base.snoozeUntil = nowMs + row.snoozeUntilOffsetMs;
    }
    return base;
  });
}

/** Same materialization logic but keyed off the media alert seed. */
export function materializeMediaAlertsSeed(nowMs = Date.now()) {
  return MEDIA_ALERT_SEED.map((row) => {
    const createdAt = nowMs + row.createdAtOffsetMs;
    const base = {
      id: row.id,
      severity: row.severity,
      status: row.status,
      title: row.title,
      summary: row.summary,
      businessAreaId: row.businessAreaId,
      processId: row.processId,
      useCaseId: row.useCaseId ?? null,
      kpi: row.kpi,
      rule: row.rule,
      recommendedActions: row.recommendedActions ?? [],
      createdAt,
      acknowledgedAt: null,
      resolvedAt: null,
      snoozeUntil: null,
      seed: true,
    };
    if (row.status === "resolved" && typeof row.resolvedAtOffsetMs === "number") {
      base.resolvedAt = nowMs + row.resolvedAtOffsetMs;
    }
    if (row.status === "acknowledged") {
      base.acknowledgedAt = createdAt + 30 * MIN;
    }
    if (row.status === "snoozed" && typeof row.snoozeUntilOffsetMs === "number") {
      base.snoozeUntil = nowMs + row.snoozeUntilOffsetMs;
    }
    return base;
  });
}
