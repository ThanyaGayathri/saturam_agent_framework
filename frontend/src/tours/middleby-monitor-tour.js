/**
 * Middleby monitors hub tour.
 *
 * Anchors target elements in MiddlebyMonitorHub.jsx. Route stays under
 * /analytics/monitor which is Middleby-safe.
 */

export const MIDDLEBY_MONITOR_TOUR = {
  id: "middleby-monitor",
  title: "Business monitors",
  subtitle: "Runs about 90 seconds.",
  steps: [
    {
      id: "welcome",
      route: "/analytics/monitor",
      anchor: null,
      lines: [
        { text: "This is the Business monitors hub. Each monitor combines several analytics use cases into a single cross-cutting view for a persona.", rate: 0.97 },
        { text: "Use these when a single use case doesn't tell the whole story: pipeline health, customer experience, brand demand, and portfolio all sit above the individual cards in the catalog.", rate: 0.97, pauseAfter: 350 },
      ],
    },
    {
      id: "portfolio",
      route: "/analytics/monitor",
      anchor: "[data-tour-monitor='portfolio']",
      lines: [
        { text: "Portfolio performance is the featured leadership monitor. Real pipeline and demand at the top, illustrative commerce metrics below, quarter and category filters throughout.", rate: 0.97, pauseAfter: 350 },
      ],
    },
    {
      id: "grid",
      route: "/analytics/monitor",
      anchor: "[data-tour-monitor='grid']",
      lines: [
        { text: "Below the featured monitor sits the persona grid. Pipeline for sales, experience for CX, demand for merchandise, brand portfolio for marketing. Each card lists its hero question and how many use cases it stitches together.", rate: 0.97, pauseAfter: 400 },
      ],
    },
    {
      id: "wrap",
      route: "/analytics/monitor",
      anchor: null,
      lines: [
        { text: "That's the Monitors hub. Open a monitor for the cross-use-case view, or Use Cases to browse the individual analytics cards.", rate: 0.98 },
      ],
      advance: "manual",
    },
  ],
};
