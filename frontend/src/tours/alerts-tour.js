/**
 * Alerts page tour. Walks the two-panel layout:
 *   welcome → header (title + Mark all read) → filter rail (status,
 *   severity, business area) → alerts list (rows with severity chip,
 *   KPI/process context, actions) → wrap.
 * Anchors target elements in Alerts.jsx.
 */

export const ALERTS_TOUR = {
  id: "alerts",
  title: "Alerts",
  subtitle: "Runs about 90 seconds.",
  steps: [
    {
      id: "welcome",
      route: "/alerts",
      anchor: null,
      lines: [
        { text: "This is Alerts. Every operational signal that needs attention lands here, whether it comes from a KPI drifting, a process failing, or a data source going stale.", rate: 0.97 },
        { text: "Each alert traces back to the business area it came from, and to the process or KPI that triggered it.", rate: 0.98, pauseAfter: 350 },
      ],
    },
    {
      id: "rail",
      route: "/alerts",
      anchor: "[data-tour-alerts='rail']",
      lines: [
        { text: "On the left, the filter rail. Status at the top switches between Open, Snoozed, and Resolved. Each has a live count so you can see the queue at a glance.", rate: 0.97 },
        { text: "Below, Severity lets you narrow to Critical, Warning, or Info. Business area filters to alerts from one area at a time. Combine any of the three to slice the list.", rate: 0.98, pauseAfter: 400 },
      ],
    },
    {
      id: "list",
      route: "/alerts",
      anchor: "[data-tour-alerts='list']",
      lines: [
        { text: "On the right, the alerts list. The count above shows how many match the current filters.", rate: 0.97 },
        { text: "Each row shows the severity chip, the alert title, the business area and process it came from, and how long ago it fired. If the alert is tied to a KPI, the row also shows the current value and the movement that triggered it.", rate: 0.98 },
        { text: "Row actions let you snooze, resolve, or open the alert in Playground to ask a follow-up. Snoozed and resolved alerts stay visible from the rail filter for audit.", rate: 0.98, pauseAfter: 450 },
      ],
    },
    {
      id: "wrap",
      route: "/alerts",
      anchor: null,
      lines: [
        { text: "That's Alerts. Filter on the left, work the list on the right. The most critical items also surface on Monitor's Needs attention band.", rate: 0.98 },
      ],
      advance: "manual",
    },
  ],
};
