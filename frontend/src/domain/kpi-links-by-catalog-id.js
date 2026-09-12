/**
 * Direct KPI attribution per catalog id.
 *
 * `getKpiNamesForCatalogId` derives KPIs by walking playbook.kpiTargets, but
 * many Middleby seed use cases are analytics views (no playbook) rather than
 * automation workflows - they still measurably move KPIs. This map fills the
 * gap by declaring which KPIs each analytics use case reports on.
 *
 * The KPI-usecase-links resolver merges this with the playbook-derived list
 * so a use case that has both a playbook AND a direct mapping shows the
 * union (capped at 3 chips per card).
 *
 * KPI names must match the canonical registry names in
 * frontend/src/domain/middleby-kpi-seed.js.
 */

export const KPI_LINKS_BY_CATALOG_ID = {
  // ── Middleby (all 50 use cases) ──────────────────────────────────────────
  // Every use case has a direct entry here so KPI chips render from the same
  // source of truth. The 9 use cases that are also covered by a real
  // automation workflow (A1-A7) still get workflow-derived KPI targets via
  // getKpiNamesForCatalogId's union, but their direct map entry is the
  // canonical "which KPIs does this move" declaration.
  "middleby-uc-01": [
    "Conversion rate",
    'Live chat "Can I buy" inquiries',
    "GA sessions",
  ],
  "middleby-uc-02": [
    "RFQ conversion rate",
    "Drop off rate on get a quote",
    "Conversion rate",
  ],
  "middleby-uc-03": ["Traffic by Category", "Quotes by Category"],
  "middleby-uc-04": [
    "Over-SLA quote count",
    "Closed-Won Rate",
    "Quotes by Territory",
  ],
  "middleby-uc-05": ["Conversion rate", "GA sessions"],
  "middleby-uc-06": [
    "Closed-Won Rate",
    "Lost Reason Breakdown",
    "Quotes by Territory",
  ],
  "middleby-uc-07": [
    "Weekly anomaly flags",
    "Estimated pipeline value",
    "RFQ conversion rate",
  ],
  "middleby-uc-08": [
    "JS error sessions",
    "Error rate per 1K sessions",
    "Drop off rate on get a quote",
  ],
  "middleby-uc-09": ["Total # of Quotes Generated", "Quote volume"],
  "middleby-uc-10": [
    "Closed-Won Rate",
    "RFQ conversion rate",
    'Live chat "Can I buy" inquiries',
  ],
  "middleby-uc-11": ["Closed-Won Rate", "Quotes by Customer Segment"],
  "middleby-uc-12": ["Quotes by State", "Quotes by Territory"],
  "middleby-uc-13": [
    "Quotes by Brand",
    "Quotes by Territory",
    "Estimated pipeline value",
  ],
  "middleby-uc-14": [
    "GA sessions",
    "Traffic by device type",
    "Bounce rate by Entry Page",
  ],
  "middleby-uc-15": ["Quotes by Brand", "GA sessions"],
  "middleby-uc-16": [
    "Weekly anomaly flags",
    "Estimated pipeline value",
    "Quote volume",
  ],
  "middleby-uc-17": ["GA sessions", "Traffic by Category"],
  "middleby-uc-18": [
    "Browse-to-quote gap index",
    "Quotes by Brand",
    "GA sessions",
  ],
  "middleby-uc-19": [
    "Top Viewed PDPs",
    "Zero-result searches",
    "Top Search Terms",
  ],
  "middleby-uc-20": [
    "JS error sessions",
    "Error rate per 1K sessions",
    "Bounce rate by Entry Page",
  ],
  "middleby-uc-21": [
    "Top Viewed PDPs",
    "Bounce rate by Entry Page",
    "Conversion rate",
  ],
  "middleby-uc-22": ["GA sessions", "Conversion rate"],
  "middleby-uc-23": ["Weekly anomaly flags", "Total # of Quotes Generated"],
  "middleby-uc-24": ["Total # of Quotes Generated", "Closed-Won Rate"],
  "middleby-uc-25": ["Quote volume", "Quotes by Category"],
  "middleby-uc-26": ["Total # of Quotes Generated", "Closed-Won Rate"],
  "middleby-uc-27": ["Quote volume", "Quotes by Category"],

  // ── Middleby executive-scoped analytics (ex-01..06) ──────────────────────
  "middleby-ex-01": [
    "RFQ conversion rate",
    "Drop off rate on get a quote",
    "Weekly anomaly flags",
  ],
  "middleby-ex-02": ["Traffic by Category", "Estimated pipeline value"],
  "middleby-ex-03": ["Closed-Won Rate", "Over-SLA quote count"],
  "middleby-ex-04": ["Zero-result searches", "Top Search Terms"],
  "middleby-ex-05": ["Estimated pipeline value", "Quotes by Category"],
  "middleby-ex-06": [
    "Estimated pipeline value",
    "Weekly anomaly flags",
    "GA sessions",
  ],

  // ── Next-step / aspirational analytics (ns-01..04) ───────────────────────
  "middleby-ns-01": ["Estimated pipeline value", "RFQ conversion rate"],
  "middleby-ns-02": ["Closed-Won Rate", "Over-SLA quote count"],
  "middleby-ns-03": ["Quotes by Brand", "GA sessions"],
  "middleby-ns-04": ["Estimated pipeline value", "Quote volume"],

  // ── Client-KPI-mirrored use cases (kpi-01..13) ───────────────────────────
  "middleby-kpi-01": ["Traffic by device type", "GA sessions"],
  "middleby-kpi-02": ["Closed-Won Rate", "Lost Reason Breakdown"],
  "middleby-kpi-03": ['Live chat "Can I buy" inquiries'],
  "middleby-kpi-04": ["Guided Shopping Tool Conversion Rate"],
  "middleby-kpi-05": [
    "Bounce rate by Entry Page",
    "Page Depth / Session",
    "Time on site / session duration",
  ],
  "middleby-kpi-06": ["PO Reference Updates", "Closed-Won Rate"],
  "middleby-kpi-07": ["Quotes by Territory"],
  "middleby-kpi-08": ["Quotes by Customer Segment"],
  "middleby-kpi-09": ["Top Viewed PDPs"],
  "middleby-kpi-10": ["Quotes by Category"],
  "middleby-kpi-11": ["Quotes by State"],
  "middleby-kpi-12": ["Quotes by Brand", "Quotes by Territory"],
  "middleby-kpi-13": ["Quotes by Customer Segment", "Quotes by Category"],

  // ── Retail (default) - all 82 gallery use cases ──────────────────────────
  "gallery-uc-01": ["Stockout Rate", "Days of Stock Remaining"],
  "gallery-uc-02": ["Order Fill Rate", "On-Time Delivery Rate"],
  "gallery-uc-03": ["Sell-Through Rate", "Gross Margin by Category"],
  "gallery-uc-04": ["Sales per Labor Hour", "Labor Cost as % of Revenue"],
  "gallery-uc-05": ["Cart Abandonment Rate", "Conversion Rate"],
  "gallery-uc-06": ["POS Transaction Failure Rate", "Avg Checkout Wait Time"],
  "gallery-uc-07": ["Return Rate", "Return Processing Time"],
  "gallery-uc-08": ["Markdown Rate", "Sell-Through Rate"],
  "gallery-uc-09": ["Gross Margin Rate", "Gross Margin by Category"],
  "gallery-uc-10": ["Labor Cost as % of Revenue", "Sales per Labor Hour"],
  "gallery-uc-11": ["Inventory Turnover", "Stockout Rate"],
  "gallery-uc-12": ["Inventory Turnover", "Days of Stock Remaining"],
  "gallery-uc-13": ["Order Fill Rate", "Stockout Rate"],
  "gallery-uc-14": ["Order Fill Rate", "On-Time Delivery Rate"],
  "gallery-uc-15": ["On-Time Delivery Rate", "Order Fill Rate"],
  "gallery-uc-16": ["Order Fill Rate", "On-Time Delivery Rate"],
  "gallery-uc-17": ["Sell-Through Rate", "Foot Traffic"],
  "gallery-uc-18": ["Sell-Through Rate", "Gross Margin by Category"],
  "gallery-uc-19": ["Markdown Rate", "Sell-Through Rate"],
  "gallery-uc-20": ["Sales per Labor Hour"],
  "gallery-uc-21": ["Shrinkage Rate"],
  "gallery-uc-22": ["Sales per Labor Hour", "Foot Traffic"],
  "gallery-uc-23": ["Conversion Rate", "Cart Abandonment Rate"],
  "gallery-uc-24": ["Fraud Detection Rate", "Conversion Rate"],
  "gallery-uc-25": ["Cart Abandonment Rate", "Conversion Rate"],
  "gallery-uc-26": ["POS Transaction Failure Rate", "Avg Checkout Wait Time"],
  "gallery-uc-27": ["Avg Checkout Wait Time", "Conversion Rate"],
  "gallery-uc-28": ["Return Rate", "Gross Margin Rate"],
  "gallery-uc-29": ["Contact Centre SLA Compliance", "Net Promoter Score (NPS)"],
  "gallery-uc-30": ["Contact Centre SLA Compliance", "Net Promoter Score (NPS)"],
  "gallery-uc-31": ["Contact Centre SLA Compliance"],
  "gallery-uc-32": ["Gross Margin Rate", "Conversion Rate"],
  "gallery-uc-33": ["Gross Margin Rate", "Sell-Through Rate"],
  "gallery-uc-34": ["Gross Margin Rate", "Conversion Rate"],
  "gallery-uc-35": ["Shrinkage Rate"],
  "gallery-uc-36": ["Shrinkage Rate", "Fraud Detection Rate"],
  "gallery-uc-37": ["Gross Margin by Category", "Gross Margin Rate"],
  "gallery-uc-38": ["Overtime Rate", "Labor Cost as % of Revenue"],
  "gallery-uc-39": ["Sales per Labor Hour", "Labor Cost as % of Revenue"],
  "gallery-uc-40": ["Sales per Labor Hour"],
  "gallery-uc-41": ["Safety Stock Coverage", "Stockout Rate"],
  "gallery-uc-42": ["Order Fill Rate"],
  "gallery-uc-43": ["Order Fill Rate", "Supplier Lead Time"],
  "gallery-uc-44": ["Labor Cost as % of Revenue"],
  "gallery-uc-45": ["Conversion Rate", "Foot Traffic"],
  "gallery-uc-46": ["Return Rate", "POS Transaction Failure Rate"],
  "gallery-uc-47": ["Return Rate", "Return Processing Time"],
  "gallery-uc-48": ["Gross Margin Rate", "Markdown Rate"],
  "gallery-uc-49": ["Gross Margin Rate"],
  "gallery-uc-50": ["Overtime Rate", "Labor Cost as % of Revenue"],
  "gallery-uc-51": ["Order Fill Rate", "Days of Stock Remaining"],
  "gallery-uc-52": ["On-Time Delivery Rate", "Order Fill Rate"],
  "gallery-uc-53": ["Sell-Through Rate", "Gross Margin by Category"],
  "gallery-uc-54": ["Foot Traffic", "Net Promoter Score (NPS)"],
  "gallery-uc-55": ["Fraud Detection Rate", "Conversion Rate"],
  "gallery-uc-56": ["POS Transaction Failure Rate", "Shrinkage Rate"],
  "gallery-uc-57": ["Net Promoter Score (NPS)", "Return Rate"],
  "gallery-uc-58": ["Gross Margin Rate", "Return Rate"],
  "gallery-uc-59": ["Gross Margin Rate", "Gross Margin by Category"],
  "gallery-uc-60": ["Sales per Labor Hour", "Labor Cost as % of Revenue"],
  "gallery-uc-61": ["Supplier Lead Time", "Stockout Rate"],
  "gallery-uc-62": ["On-Time Delivery Rate", "Order Fill Rate"],
  "gallery-uc-63": ["Markdown Rate", "Sell-Through Rate"],
  "gallery-uc-64": ["Shrinkage Rate", "Return Rate"],
  "gallery-uc-65": ["Cart Abandonment Rate", "Conversion Rate"],
  "gallery-uc-66": ["Gross Revenue", "Stockout Rate"],
  "gallery-uc-67": ["Order Fill Rate", "Supplier Lead Time"],
  "gallery-uc-68": ["Fraud Detection Rate", "Return Rate"],
  "gallery-uc-69": ["Conversion Rate", "Foot Traffic"],
  "gallery-uc-70": ["Conversion Rate", "Cart Abandonment Rate"],
  "gallery-uc-71": ["Conversion Rate", "Foot Traffic"],
  "gallery-uc-72": ["Cart Abandonment Rate", "Conversion Rate"],
  "gallery-uc-73": ["Avg Checkout Wait Time"],
  "gallery-uc-74": ["POS Transaction Failure Rate", "Fraud Detection Rate"],
  "gallery-uc-75": ["Net Promoter Score (NPS)", "Contact Centre SLA Compliance"],
  "gallery-uc-76": ["Contact Centre SLA Compliance"],
  "gallery-uc-77": ["Contact Centre SLA Compliance"],
  "gallery-uc-78": ["Contact Centre SLA Compliance", "Net Promoter Score (NPS)"],
  "gallery-uc-79": ["Gross Margin Rate", "Conversion Rate"],
  "gallery-uc-80": ["Sell-Through Rate", "Markdown Rate"],
  "gallery-uc-81": ["Labor Cost as % of Revenue", "Gross Margin Rate"],
  "gallery-uc-82": ["Overtime Rate", "Labor Cost as % of Revenue"],

  // ── GFI - all 15 seed use cases ──────────────────────────────────────────
  // KPI names must match the GFI KPI registry (gfi-demo-seed.js) exactly —
  // GFI uses lowercase phrasing plus specialty KPIs like "Food waste / spoilage
  // rate", "Supplier fill rate", "FSMA compliance coverage", "Picks per labor
  // hour", "Sell-through rate (short code)", "New item sell-through (8-week)".
  "gfi-uc-01-perishable-forecast": [
    "Stockout rate",
    "Inventory turnover",
    "Food waste / spoilage rate",
  ],
  "gfi-uc-02-inter-dc-rebalance": ["Order fill rate", "Days of stock remaining"],
  "gfi-uc-03-supplier-risk": ["Supplier lead time", "Supplier fill rate"],
  "gfi-uc-04-markdown-optimization": [
    "Markdown rate",
    "Food waste / spoilage rate",
  ],
  "gfi-uc-05-labor-optimization": ["Picks per labor hour", "Overtime rate"],
  "gfi-uc-06-multi-temp-routing": ["On-time delivery rate", "Order fill rate"],
  "gfi-uc-07-vendor-scorecards": ["Supplier fill rate", "Supplier lead time"],
  "gfi-uc-08-npi-tracking": [
    "New item sell-through (8-week)",
    "Sell-through rate (short code)",
  ],
  "gfi-uc-09-fsma-traceability": ["FSMA compliance coverage", "Shrinkage rate"],
  "gfi-uc-10-order-routing": ["Order fill rate", "On-time delivery rate"],
  "gfi-uc-11-infra-curated-programs": [
    "Sell-through rate (short code)",
    "Gross revenue",
  ],
  "gfi-uc-12-customer-demand-intelligence": [
    "Gross revenue",
    "Sell-through rate (short code)",
  ],
  "gfi-uc-13-shrinkage-detection": [
    "Shrinkage rate",
    "Food waste / spoilage rate",
  ],
  "gfi-uc-14-dynamic-specialty-pricing": ["Gross margin rate", "Markdown rate"],
  "gfi-uc-15-competitive-positioning": ["Gross revenue", "Gross margin rate"],
};
