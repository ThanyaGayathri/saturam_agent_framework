// Demo-only metadata derived from a process id. No real backend; values are
// stable per id so the same process always shows the same status, trigger,
// run history, and insights across page loads.

const STATUS_META = {
  monitored: { label: "Monitored", dot: "#12b76a", text: "#027a48", bg: "#ecfdf3", border: "#abefc6" },
  "not-configured": { label: "Not configured", dot: "#98a2b3", text: "#475467", bg: "#f2f4f7", border: "#d0d5dd" },
};

function hashId(id) {
  let h = 0;
  const s = String(id || "");
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
  return Math.abs(h);
}

/** Derived from real data: a process is "monitored" if 1+ use cases reference it, otherwise "not configured". */
export function getProcessAutomationStatus(linkedUseCaseCount) {
  return linkedUseCaseCount > 0 ? "monitored" : "not-configured";
}

export function getStatusMeta(status) {
  return STATUS_META[status] || STATUS_META["not-configured"];
}

const RELATIVE_TIMES = ["12 min ago", "47 min ago", "2 hours ago", "5 hours ago", "Yesterday, 18:42", "Yesterday, 09:15", "2 days ago"];

export function getProcessLastRunLabel(processId) {
  return RELATIVE_TIMES[hashId(`${processId}last`) % RELATIVE_TIMES.length];
}

export function getProcessTotalRuns(processId) {
  return 28 + (hashId(`${processId}runs`) % 180);
}

export function getProcessSuccessRate(processId) {
  return 88 + (hashId(`${processId}succ`) % 11);
}

export function getProcessExceptionsCount(processId) {
  return hashId(`${processId}exc`) % 5;
}

const RUN_OUTCOMES = [
  { status: "success", label: "Success" },
  { status: "success", label: "Success" },
  { status: "success", label: "Success" },
  { status: "warning", label: "Flagged for review" },
  { status: "success", label: "Success" },
  { status: "error", label: "Exception" },
  { status: "success", label: "Success" },
];

const RUN_SUMMARIES_BY_AREA = {
  inventory: [
    "Forecast accuracy 92% across 1,240 SKUs",
    "Detected 3 SKUs trending below safety stock",
    "Replenishment plan generated for 47 stores",
    "Variance flagged: 2 SKUs above threshold",
    "Stock-out risk on SKU-4429 escalated",
    "All checks passed",
    "Demand spike detected for category Snacks",
  ],
  orders: [
    "Order fill rate 96.4% across the day",
    "12 orders flagged for delivery exception",
    "All 3,420 orders routed without manual review",
    "Fraud signal on 2 orders escalated",
    "Returns batch processed",
    "Backorder queue cleared",
    "SLA breach risk on 4 in-transit shipments",
  ],
  merchandise: [
    "Planogram compliance 94% across 47 stores",
    "Assortment gap detected in Beverages, Store 12",
    "Category performance review complete",
    "Vendor scorecard updated",
    "3 SKUs flagged for delisting",
    "All checks passed",
    "Promo lift exceeded baseline by 18%",
  ],
  "store-ops": [
    "Labor schedule adherence 91%",
    "Foot traffic trended +6% vs prior week",
    "Task compliance review complete",
    "2 stores below KPI threshold flagged",
    "Shift handover anomaly detected at Store 8",
    "All checks passed",
    "Overtime risk in Region North flagged",
  ],
  "e-com": [
    "Conversion rate 3.4%, within band",
    "Cart abandonment up 1.2%, flagged",
    "Site performance: P95 LCP 2.1s",
    "Top traffic source: organic search 41%",
    "Product discovery: 8 SKUs missing imagery",
    "All checks passed",
    "Checkout drop-off detected on payment step",
  ],
  pos: [
    "Transaction throughput nominal",
    "Checkout efficiency: avg 92s per transaction",
    "Payment method failure rate 0.4%",
    "Staff performance review complete",
    "Lane 3 at Store 12 flagged for slow checkout",
    "All checks passed",
    "Loyalty lookup latency exceeded SLA",
  ],
  support: [
    "CSAT trending at 4.6 / 5",
    "Returns volume +14% vs last week, flagged",
    "Wait times within SLA",
    "3 escalations resolved",
    "Contact centre staffing gap detected",
    "All checks passed",
    "Repeat-contact rate up 2.1%",
  ],
  pricing: [
    "Markdown optimisation: 124 SKUs adjusted",
    "Competitor undercut detected on 8 SKUs",
    "Sell-through above plan in Apparel",
    "Promo conflict resolved",
    "Margin erosion flagged in Electronics",
    "All checks passed",
    "Price elasticity model retrained",
  ],
  finance: [
    "Gross margin within budget band",
    "Shrinkage variance flagged in 2 stores",
    "Budget vs actuals review complete",
    "Cost-per-unit trending +1.8%",
    "Cash reconciliation complete",
    "All checks passed",
    "Audit anomaly escalated to controller",
  ],
  workforce: [
    "Labor cost 18.4% of sales, on target",
    "Overtime hours +12% vs plan, flagged",
    "Schedule compliance 94%",
    "Productivity review complete",
    "Training completion at 87%",
    "All checks passed",
    "Shift coverage gap detected for Saturday",
  ],
};

const USE_CASE_FALLBACK_NAMES = ["Daily analysis", "Trend detector", "Anomaly scan", "Threshold monitor", "Trigger review"];

/**
 * Build a fake run history of 6 entries for a process. Each entry includes a
 * timestamp, the use case that "ran" (rotated through linked use cases), an
 * outcome, and a summary line specific to the business area.
 */
export function getProcessRunHistory(process, linkedUseCases) {
  const summaries = RUN_SUMMARIES_BY_AREA[process.businessAreaId] || RUN_SUMMARIES_BY_AREA.inventory;
  const ucNames = (linkedUseCases || []).map((u) => u.title).filter(Boolean);
  const pool = ucNames.length ? ucNames : USE_CASE_FALLBACK_NAMES;
  const baseHash = hashId(process.id);
  const rows = [];
  for (let i = 0; i < 6; i++) {
    const summary = summaries[(baseHash + i * 3) % summaries.length];
    const outcome = RUN_OUTCOMES[(baseHash + i) % RUN_OUTCOMES.length];
    const useCase = pool[(baseHash + i * 2) % pool.length];
    rows.push({
      id: `run-${process.id}-${i}`,
      time: RELATIVE_TIMES[i % RELATIVE_TIMES.length],
      useCase,
      outcomeStatus: outcome.status,
      outcomeLabel: outcome.label,
      summary,
    });
  }
  return rows;
}

const INSIGHTS_BY_AREA = {
  inventory: [
    { tone: "warning", title: "3 SKUs trending below safety stock", body: "Snacks category, projected stock-out within 4 days based on current depletion velocity. Suggested PO trigger by EOD." },
    { tone: "info", title: "Forecast accuracy improved 4.2% this week", body: "Model retraining on Q1 data lifted MAPE from 14.8% to 10.6%. Largest gains in Beverages." },
    { tone: "success", title: "Replenishment cycle on track", body: "47 stores received scheduled drops within SLA. No exceptions." },
  ],
  orders: [
    { tone: "warning", title: "Delivery exception spike in Region East", body: "12 orders flagged in last 6 hours, 3x baseline. Likely carrier issue with FedEx routes 4002 / 4015." },
    { tone: "info", title: "Order routing automation hit 98%", body: "Only 2.1% of today's 3,420 orders required manual review, down from 5.4% last month." },
    { tone: "success", title: "Fraud detection caught 2 high-risk orders", body: "Both held for review. Pattern matched recent carding incident; score 0.94." },
  ],
  merchandise: [
    { tone: "warning", title: "Planogram drift detected at Store 12", body: "Beverages aisle 4 SKUs missing from planned set. Compliance score dropped to 81%." },
    { tone: "info", title: "Promo lift exceeded baseline by 18%", body: "Snacks promo outperforming forecast. Consider extending campaign by 1 week." },
    { tone: "success", title: "Vendor scorecard refreshed", body: "All 23 active vendors evaluated. 2 promoted to preferred tier." },
  ],
  "store-ops": [
    { tone: "warning", title: "Overtime risk in Region North", body: "5 stores trending +18% over plan. Suggested shift swap recommendations attached." },
    { tone: "info", title: "Foot traffic +6% vs prior week", body: "Driven by weekend uplift. Conversion held steady; no staffing adjustment needed." },
    { tone: "success", title: "Task compliance at 94%", body: "Above target across all 47 stores. Top performer: Store 22 at 99%." },
  ],
  "e-com": [
    { tone: "warning", title: "Checkout drop-off on payment step", body: "Conversion fell 1.4% over last 24 hours. Drop concentrated in mobile Safari users." },
    { tone: "info", title: "Top traffic source shifted to organic search", body: "Organic now 41% (up from 36%). Paid social down 4%. Likely seasonal." },
    { tone: "success", title: "Site P95 LCP held at 2.1s", body: "Within performance SLA. No regressions detected after this week's deploy." },
  ],
  pos: [
    { tone: "warning", title: "Loyalty lookup latency exceeded SLA", body: "Avg lookup time 4.2s on lanes 3-5, Store 12. POS terminal reboot recommended." },
    { tone: "info", title: "Avg checkout time 92s", body: "Within target band. Slight uptick on Friday afternoons; expected pattern." },
    { tone: "success", title: "Payment method failure rate at 0.4%", body: "All providers within tolerance. Apple Pay leading at 99.8% success." },
  ],
  support: [
    { tone: "warning", title: "Returns volume +14% vs last week", body: "Concentrated in Apparel; sizing complaints. Suggested vendor flag on supplier SKU-2240." },
    { tone: "info", title: "CSAT trending at 4.6 / 5", body: "Slight uplift from chat channel. Voice channel held flat at 4.4." },
    { tone: "success", title: "All escalations resolved within SLA", body: "3 escalations closed today, avg time-to-resolve 4.2 hours." },
  ],
  pricing: [
    { tone: "warning", title: "Margin erosion flagged in Electronics", body: "Competitor undercut on 8 SKUs. Suggested matched pricing or category swap." },
    { tone: "info", title: "Markdown optimisation adjusted 124 SKUs", body: "Avg margin recovery +2.4% on adjusted set. No sell-through impact yet." },
    { tone: "success", title: "Promo conflict resolved", body: "Overlapping discount on category Beverages auto-corrected. Customer-facing prices consistent." },
  ],
  finance: [
    { tone: "warning", title: "Shrinkage variance in 2 stores", body: "Stores 8 and 19 showing higher than expected loss in Q1. Audit recommended." },
    { tone: "info", title: "Cost-per-unit trending +1.8%", body: "Within tolerance band but worth monitoring. Suppliers Beverages and Dairy showing the largest moves." },
    { tone: "success", title: "Cash reconciliation complete", body: "All 47 stores reconciled. Variance under 0.05%." },
  ],
  workforce: [
    { tone: "warning", title: "Overtime +12% vs plan", body: "Concentrated in Region South. Suggested schedule rebalance attached." },
    { tone: "info", title: "Training completion at 87%", body: "Up 4 points from last month. 3 stores still below 75%; follow-up scheduled." },
    { tone: "success", title: "Schedule adherence at 94%", body: "Above target. Top performer: Store 22 at 99%." },
  ],
};

export function getProcessInsights(process) {
  return INSIGHTS_BY_AREA[process.businessAreaId] || INSIGHTS_BY_AREA.inventory;
}

const SOURCES_BY_AREA = {
  inventory: ["SAP S/4HANA · sap_prod", "Snowflake · RETAIL_DW", "PostgreSQL · retail_db"],
  orders: ["SAP S/4HANA · sap_prod", "Snowflake · RETAIL_DW"],
  merchandise: ["SAP S/4HANA · sap_prod", "Snowflake · RETAIL_DW"],
  "store-ops": ["Snowflake · RETAIL_DW", "PostgreSQL · retail_db"],
  "e-com": ["PostgreSQL · retail_db", "Snowflake · RETAIL_DW"],
  pos: ["PostgreSQL · retail_db", "Snowflake · RETAIL_DW"],
  support: ["Salesforce · Service Cloud", "Snowflake · RETAIL_DW"],
  pricing: ["SAP S/4HANA · sap_prod", "Snowflake · RETAIL_DW"],
  finance: ["SAP S/4HANA · sap_prod", "Snowflake · RETAIL_DW"],
  workforce: ["PostgreSQL · retail_db", "Snowflake · RETAIL_DW"],
};

export function getProcessConnectedSources(process) {
  return SOURCES_BY_AREA[process.businessAreaId] || ["Snowflake · RETAIL_DW"];
}

/** Aggregate stats for the Processes page header strip. Monitored count is real; analyses/exceptions are demo numbers scaled to monitored. */
export function getProcessesAggregateStats(totalProcesses, monitoredProcesses) {
  const analysesToday = monitoredProcesses * 7 + 12;
  const exceptions = Math.max(0, Math.round(monitoredProcesses * 0.12));
  return {
    active: totalProcesses,
    monitored: monitoredProcesses,
    analysesToday,
    exceptions,
  };
}
