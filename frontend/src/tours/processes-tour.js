/**
 * Processes page tour. Walks each section:
 *   welcome → header (title + Add process) → filter toolbar
 *   (search + area chips + subject chips) → area sections → wrap.
 * Anchors target elements in Processes.jsx.
 */

export const PROCESSES_TOUR = {
  id: "processes",
  title: "Processes",
  subtitle: "Runs about 90 seconds.",
  steps: [
    {
      id: "welcome",
      route: "/processes",
      anchor: null,
      lines: [
        { text: "This is the Processes page. A process is a business activity your team runs, like Store Replenishment, Order Fulfillment, or Fraud Review.", rate: 0.97 },
        { text: "Every KPI in the Registry maps to one or more processes. Every use case runs against a process. Processes are how the rest of the product organizes work.", rate: 0.98, pauseAfter: 350 },
      ],
    },
    {
      id: "header",
      route: "/processes",
      anchor: "[data-tour-processes='header']",
      lines: [
        { text: "At the top, the count line tells you how many processes are defined and how many business areas they span. Add process on the right opens a form for a new one.", rate: 0.97, pauseAfter: 350 },
      ],
    },
    {
      id: "filters",
      route: "/processes",
      anchor: "[data-tour-processes='filters']",
      lines: [
        { text: "The toolbar has search on the left, then a row of business area chips. Click an area to narrow the list.", rate: 0.97 },
        { text: "Once an area is picked, a second row of subject chips appears so you can drill in further. Clear resets everything.", rate: 0.98, pauseAfter: 400 },
      ],
    },
    {
      id: "areas",
      route: "/processes",
      anchor: "[data-tour-processes='areas']",
      lines: [
        { text: "Below the toolbar, processes are grouped by business area, then by subject area, then listed as cards. Click any area header to collapse or expand its section.", rate: 0.97 },
        { text: "Each process card shows the name, description, the linked business area, and the use cases that run against it. Click a card to open the process detail page.", rate: 0.98, pauseAfter: 400 },
      ],
    },
    {
      id: "wrap",
      route: "/processes",
      anchor: null,
      lines: [
        { text: "That's Processes. Define here, then link them from the KPI Registry and Use Case Builder.", rate: 0.98 },
      ],
      advance: "manual",
    },
  ],
};
