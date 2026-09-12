// Synthetic data for the extended Portfolio Performance sections. Mirrors the
// KPIs that appear in McFadyen's Power BI screenshots (Form Starts / Form
// Abandonment / Consultation Requests / Traffic by channel / Category
// Performance) so audiences see the same shape of report they're used to.

export const PORTFOLIO_TRAFFIC_BY_CHANNEL = [
  { channel: "Organic search", sessions: 31200, share: 42, rfqs: 540, conversion: 1.73 },
  { channel: "Direct", sessions: 17800, share: 24, rfqs: 328, conversion: 1.84 },
  { channel: "Paid campaigns", sessions: 13400, share: 18, rfqs: 195, conversion: 1.46 },
  { channel: "Referral", sessions: 7300, share: 10, rfqs: 168, conversion: 2.30 },
  { channel: "Email", sessions: 4300, share: 6, rfqs: 53, conversion: 1.23 },
];

export const PORTFOLIO_RFQ_FORM_FUNNEL = [
  { stage: "Product page views", value: 42800, prev: 40200 },
  { stage: "Form starts", value: 3120, prev: 3260 },
  { stage: "Form abandoned", value: 1836, prev: 1780 },
  { stage: "RFQs submitted", value: 1284, prev: 1480 },
];

export const PORTFOLIO_CONSULTATION_MIX = [
  { intent: "Consultation-led", quotes: 462, avg_deal_size: 8400, win_rate: 34, follow_up_hours: 6.2 },
  { intent: "Self-serve", quotes: 822, avg_deal_size: 3900, win_rate: 21, follow_up_hours: 18.4 },
];

export const PORTFOLIO_CATEGORY_LEADERBOARD = [
  { category: "Commercial Ovens", views: 12820, rfqs: 342, pipeline_k: 4820, wow_pct: 8.2 },
  { category: "Fryers", views: 9160, rfqs: 251, pipeline_k: 3110, wow_pct: 4.6 },
  { category: "Refrigeration", views: 8420, rfqs: 197, pipeline_k: 2680, wow_pct: -3.1 },
  { category: "Ranges", views: 7010, rfqs: 174, pipeline_k: 2140, wow_pct: 6.9 },
  { category: "Warewashing", views: 5480, rfqs: 118, pipeline_k: 1420, wow_pct: 2.1 },
  { category: "Cooking Suites", views: 3910, rfqs: 89, pipeline_k: 1180, wow_pct: 11.4 },
  { category: "Beverage", views: 2860, rfqs: 62, pipeline_k: 720, wow_pct: -6.7 },
  { category: "Bakery Equipment", views: 2620, rfqs: 51, pipeline_k: 640, wow_pct: 1.8 },
];

export const PORTFOLIO_EXTENDED_KPIS = {
  trafficTotal: PORTFOLIO_TRAFFIC_BY_CHANNEL.reduce((s, r) => s + r.sessions, 0),
  formStartsTotal: 3120,
  formAbandonRate: 58.8, // 1836 / 3120
  consultationShare: 36, // 462 / (462+822)
  fastestFollowUpChannel: "Consultation-led",
  slowestFollowUpChannel: "Self-serve",
};
