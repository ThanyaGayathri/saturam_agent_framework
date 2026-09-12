/**
 * Middleby automation hub tour.
 *
 * Anchors target elements in MiddlebyAutomationHub.jsx. Route stays under
 * /analytics/automation which is Middleby-safe.
 */

export const MIDDLEBY_AUTOMATION_TOUR = {
  id: "middleby-automation",
  title: "Business process automation",
  subtitle: "Runs about 90 seconds.",
  steps: [
    {
      id: "welcome",
      route: "/analytics/automation",
      anchor: null,
      lines: [
        { text: "This is the Automation hub. It lists exploratory workflow examples showing what Enterprise GPT could automate versus what your team still owns end to end.", rate: 0.97 },
        { text: "These are demo workflows, not validated Middleby production automation. Open one for the full three-step path with approval gates and impact numbers.", rate: 0.97, pauseAfter: 300 },
      ],
    },
    {
      id: "steps",
      route: "/analytics/automation",
      anchor: "[data-tour-automation='steps']",
      lines: [
        { text: "Every workflow follows the same three steps. Analyze gathers the insight evidence and signal. Monitor turns that into business KPIs and a hero chart. Automate lays out what runs, what needs approval, and the expected impact.", rate: 0.97, pauseAfter: 400 },
      ],
    },
    {
      id: "workflows",
      route: "/analytics/automation",
      anchor: "[data-tour-automation='workflows']",
      lines: [
        { text: "The workflow list shows each automation with its trigger, its Analyze to Monitor to Automate line, and a Needs approval or Runs on demand badge. Open one to walk the full path.", rate: 0.97, pauseAfter: 400 },
      ],
    },
    {
      id: "queue",
      route: "/analytics/automation",
      anchor: "[data-tour-automation='queue']",
      lines: [
        { text: "The Approval queue at the bottom collects every pending step across all workflows. Once a workflow flags a decision that needs a human, it lands here for review.", rate: 0.97, pauseAfter: 350 },
      ],
    },
    {
      id: "wrap",
      route: "/analytics/automation",
      anchor: null,
      lines: [
        { text: "That's the Automation hub. Open any workflow to see how Analyze, Monitor, and Automate come together.", rate: 0.98 },
      ],
      advance: "manual",
    },
  ],
};
