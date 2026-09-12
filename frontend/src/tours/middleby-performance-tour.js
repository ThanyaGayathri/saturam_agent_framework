/**
 * Middleby portfolio performance tour.
 *
 * Anchors target elements in MiddlebyPortfolioPerformance.jsx. All steps
 * stay on /analytics/performance which is Middleby-safe.
 */

export const MIDDLEBY_PERFORMANCE_TOUR = {
  id: "middleby-performance",
  title: "Portfolio performance",
  subtitle: "Runs about 2 minutes.",
  steps: [
    {
      id: "welcome",
      route: "/analytics/performance",
      anchor: null,
      lines: [
        { text: "This is the Portfolio performance board. It's the leadership view of the Middleby portfolio, blending real quote pipeline and demand signals with an illustrative commerce sample.", rate: 0.97 },
        { text: "Data comes from HubSpot for quotes, GA for demand, and a catalog median price for estimated pipeline value.", rate: 0.97, pauseAfter: 300 },
      ],
    },
    {
      id: "filters",
      route: "/analytics/performance",
      anchor: "[data-tour-perf='filters']",
      lines: [
        { text: "Filters at the top control the window. Year and quarter set the time range. Category L1 narrows to one product family, so you can see what pipeline looks like for ovens, or for beverage, or the whole book.", rate: 0.97, pauseAfter: 400 },
      ],
    },
    {
      id: "kpis",
      route: "/analytics/performance",
      anchor: "[data-tour-perf='kpis']",
      lines: [
        { text: "The KPI strip is the pipeline and demand read. Quote volume, estimated pipeline value, win-rate signals, and demand indicators, each with a delta versus the prior period and a sparkline.", rate: 0.97, pauseAfter: 350 },
      ],
    },
    {
      id: "trends",
      route: "/analytics/performance",
      anchor: "[data-tour-perf='trends']",
      lines: [
        { text: "Operational trends charts the same metrics month by month. The tab switcher on the right cycles through quote count, pipeline value, and demand series.", rate: 0.97, pauseAfter: 350 },
      ],
    },
    {
      id: "commerce",
      route: "/analytics/performance",
      anchor: "[data-tour-perf='commerce']",
      lines: [
        { text: "The Commerce metrics section is illustrative. GMV, margin, orders, and average order value are sample values until the Adobe Commerce order export is wired in for production numbers.", rate: 0.97, pauseAfter: 400 },
      ],
    },
    {
      id: "category-demand",
      route: "/analytics/performance",
      anchor: "[data-tour-perf='category']",
      lines: [
        { text: "Category demand is the treemap at the bottom. Estimated revenue by product family, sized by quote count times catalog median list price. Handy for spotting where interest is concentrated.", rate: 0.97, pauseAfter: 350 },
      ],
    },
    {
      id: "category-mix",
      route: "/analytics/performance",
      anchor: "[data-tour-perf='category-mix']",
      lines: [
        { text: "Category mix breaks the same view into a table. Quote count, estimated pipeline, and the share each category takes of both pipeline and quotes, so you can see mix and growth side by side.", rate: 0.97, pauseAfter: 400 },
      ],
    },
    {
      id: "wrap",
      route: "/analytics/performance",
      anchor: null,
      lines: [
        { text: "That's Portfolio performance. Head to Monitors for the cross-use-case operational view, or Use Cases to drill into a specific analytics question.", rate: 0.98 },
      ],
      advance: "manual",
    },
  ],
};
