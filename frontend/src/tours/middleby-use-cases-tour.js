/**
 * Middleby analytics catalog tour.
 *
 * Anchors target elements in MiddlebyAnalyticsCatalog.jsx. Routes stay under
 * /analytics/* which is Middleby-safe per ProfileRouteGuard.
 */

export const MIDDLEBY_USE_CASES_TOUR = {
  id: "middleby-use-cases",
  title: "Use cases",
  subtitle: "Runs about 2 minutes.",
  steps: [
    {
      id: "welcome",
      route: "/analytics",
      anchor: null,
      lines: [
        { text: "This is the Use Cases library. Each card is a ready-made analytics question tailored to Middleby, backed by real HubSpot, GA, and LogRocket samples.", rate: 0.97 },
        { text: "Cards are grouped by priority, by funnel stage, and by supporting or roadmap status. Open one to jump straight into an Insight Session.", rate: 0.97, pauseAfter: 300 },
      ],
    },
    {
      id: "search",
      route: "/analytics",
      anchor: "[data-tour-catalog='search']",
      lines: [
        { text: "The search bar matches titles, sample questions, and problem signals. The period chip below narrows every card to a specific date range, so numbers reflect the window you care about.", rate: 0.97, pauseAfter: 350 },
      ],
    },
    {
      id: "create",
      route: "/analytics",
      anchor: "[data-tour-catalog='create']",
      lines: [
        { text: "Create use case opens the analytics builder. Four steps: pick a question, pick the data, define the chart, and save it back to this library so your team can reuse it.", rate: 0.97, pauseAfter: 350 },
      ],
    },
    {
      id: "sections",
      route: "/analytics",
      anchor: null,
      lines: [
        { text: "Use cases of interest are the demo-priority set, tagged with an automation-ready badge where a workflow already exists.", rate: 0.98 },
        { text: "Analytics across the buyer funnel groups cards by stage, from awareness through post-sale, so leadership can trace the whole journey.", rate: 0.98 },
        { text: "Additional and On the roadmap sections cover the supporting and future cases. Handy for scoping conversations.", rate: 0.98, pauseAfter: 400 },
      ],
    },
    {
      id: "wrap",
      route: "/analytics",
      anchor: null,
      lines: [
        { text: "That's the catalog. Open a card to run its Insight Session, or head to Monitors for the cross-use-case view.", rate: 0.98 },
      ],
      advance: "manual",
    },
  ],
};
