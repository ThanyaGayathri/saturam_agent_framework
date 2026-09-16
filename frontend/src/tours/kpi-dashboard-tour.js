/**
 * KPI Dashboard tab tour. Walks each section of the Dashboard page itself:
 *   welcome → range toggle → summary tile fields → trend chart fields → wrap.
 * Skips explaining Registry and Gallery — those have their own tours now.
 * Anchors target elements in KpiHealthDashboard.jsx.
 */

export const KPI_DASHBOARD_TOUR = {
  id: "kpi-dashboard",
  title: "KPI Dashboard",
  subtitle: "Runs about 3 minutes.",
  steps: [
    {
      id: "welcome",
      route: "/kpis/dashboard",
      anchor: null,
      lines: [
        { text: "This is the KPI Dashboard.", rate: 0.97 },
        { text: "The whole page is scoped to your role. The tiles you see and the KPI in the trend chart reflect the metrics linked to the use cases assigned to you. Switch role from the profile switcher and everything on this page changes.", rate: 0.97, pauseAfter: 300 },
        { text: "You track your KPIs at the top, and drill into any one of them in the trend chart below.", rate: 0.97, pauseAfter: 350 },
      ],
    },
    {
      id: "range",
      route: "/kpis/dashboard",
      anchor: "[data-tour-kpi-dash='range']",
      lines: [
        { text: "This is the time range. Switch between 7 days and 30 days. Everything on this page, tile deltas, sparklines, and the trend chart, reflows to the selected window.", rate: 0.97, pauseAfter: 350 },
      ],
    },
    {
      id: "tiles",
      route: "/kpis/dashboard",
      anchor: "[data-tour-kpi-dash='tiles']",
      lines: [
        { text: "These are the summary tiles. Only KPIs marked active in the Registry and linked to your role's use cases show up here, so the page stays relevant.", rate: 0.97 },
        { text: "Each tile has several fields. The name and icon at the top identify the KPI. The big number is the current value. Below it, the delta shows the movement, either versus the prior week or versus the prior four weeks, depending on the range.", rate: 0.98 },
        { text: "The sparkline gives you a shape-of-the-curve read. The small text under it names the subject area the KPI belongs to.", rate: 0.98, pauseAfter: 400 },
      ],
    },
    {
      id: "trend",
      route: "/kpis/dashboard",
      anchor: "[data-tour-kpi-dash='trend']",
      lines: [
        { text: "Below the tiles, the trend chart plots one KPI at a time over the selected range.", rate: 0.97 },
        { text: "The picker on the right switches KPIs. The list matches the tiles above, so you can drill into whichever one caught your attention.", rate: 0.97 },
        { text: "The big number and delta pill on the left show the current value and total movement across the window. The line underneath is the daily or weekly curve, with a Live badge if the feed is streaming.", rate: 0.98 },
        { text: "The footer note links to Monitor for the live ops view if you want to see the KPI beat in real time.", rate: 0.98, pauseAfter: 400 },
      ],
    },
    {
      id: "wrap",
      route: "/kpis/dashboard",
      anchor: null,
      lines: [
        { text: "That's the Dashboard. Tiles up top for at-a-glance health, and the trend chart for a deeper read.", rate: 0.98 },
      ],
      advance: "manual",
    },
  ],
};
