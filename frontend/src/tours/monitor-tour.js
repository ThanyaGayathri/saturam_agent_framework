/**
 * Monitor page tour. Walks each section of the operations dashboard:
 *   welcome → header (title + View all alerts) → stat strip
 *   → needs-attention band → use case runs → KPI drift + activity feed
 *   → data source health → wrap.
 * Anchors target elements in Monitor.jsx.
 */

export const MONITOR_TOUR = {
  id: "monitor",
  title: "Monitor",
  subtitle: "Runs about 2 minutes.",
  steps: [
    {
      id: "welcome",
      route: "/monitor",
      anchor: null,
      lines: [
        { text: "This is Monitor. It's the operational health page. If a process is running slow, a use case is failing, or a data source is stale, it shows up here.", rate: 0.97 },
        { text: "Anything that needs your attention becomes an alert. The Dashboard shows KPIs; Monitor shows operations behind the KPIs.", rate: 0.98, pauseAfter: 350 },
      ],
    },
    {
      id: "stats",
      route: "/monitor",
      anchor: "[data-tour-monitor='stats']",
      lines: [
        { text: "Up top, five tiles summarize the workspace at a glance.", rate: 0.97 },
        { text: "Launched use cases and Tasks today show workload. Avg response is the median request latency across processes. Critical alerts flags anything open and unresolved. Data sources shows how many connected feeds are healthy.", rate: 0.98 },
        { text: "The Critical alerts tile and the Data sources tile are clickable, and take you straight to those views.", rate: 0.98, pauseAfter: 400 },
      ],
    },
    {
      id: "attention",
      route: "/monitor",
      anchor: "[data-tour-monitor='attention']",
      lines: [
        { text: "Needs attention lists the most severe unresolved alerts, sorted so critical ones surface first. Click any row to open the alert detail.", rate: 0.97, pauseAfter: 400 },
      ],
    },
    {
      id: "use-cases",
      route: "/monitor",
      anchor: "[data-tour-monitor='use-cases']",
      lines: [
        { text: "Below the alerts, each launched use case gets a card. The pill on the right shows its status. The sparkline shows how the KPI it moves has trended.", rate: 0.97 },
        { text: "Open impact drills into the use case's own impact dashboard. Manage use cases at the top of the section takes you to the library.", rate: 0.98, pauseAfter: 400 },
      ],
    },
    {
      id: "drift-activity",
      route: "/monitor",
      anchor: "[data-tour-monitor='drift-activity']",
      lines: [
        { text: "Two panels here. KPI drift on the left lists metrics that have moved beyond their healthy band and are still open as alerts, with a link back to the Registry.", rate: 0.97 },
        { text: "Activity feed on the right is a rolling list of what the workspace has done recently, alerts fired, processes that ran, use cases that produced answers.", rate: 0.98, pauseAfter: 400 },
      ],
    },
    {
      id: "sources",
      route: "/monitor",
      anchor: "[data-tour-monitor='sources']",
      lines: [
        { text: "Data source health lists every connected feed, both internal sources and active external integrations, with latency and time since last sync.", rate: 0.97 },
        { text: "A degraded source here usually means an upstream issue, not something Enterprise GPT can fix. Manage connections from Configure.", rate: 0.98, pauseAfter: 400 },
      ],
    },
    {
      id: "wrap",
      route: "/monitor",
      anchor: null,
      lines: [
        { text: "That's Monitor. Stats up top, alerts in the middle, use cases and drift below, sources at the bottom. Come here first when something feels off.", rate: 0.98 },
      ],
      advance: "manual",
    },
  ],
};
