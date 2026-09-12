/**
 * Playground page tour.
 *
 * Walks the page in the order the user reads it:
 *   welcome → composer → business area pills → All queries (master list)
 *   → role card shortlist (heading + carousel) → wrap.
 * Anchors target elements marked with data-tour-pg attributes in
 * PlaygroundHomeAlt.jsx.
 */

export const PLAYGROUND_TOUR = {
  id: "playground",
  title: "Playground",
  subtitle: "Runs about 2 minutes.",
  steps: [
    {
      id: "welcome",
      route: "/playground",
      anchor: null,
      lines: [
        { text: "This is the Playground. It's where you ask business questions in plain language and get answers grounded in your data.", rate: 0.95 },
        { text: "Every answer traces back to the source, so nothing is guessed and every claim is auditable.", rate: 0.98, pauseAfter: 300 },
      ],
    },
    {
      id: "composer",
      route: "/playground",
      anchor: "[data-tour-pg='composer']",
      lines: [
        { text: "This is the composer. Type any business question here, or dictate it with the mic on the right.", rate: 0.97 },
        { text: "The icons on the left let you attach files or scope the answer to a specific data source or connector.", rate: 0.98, pauseAfter: 350 },
      ],
    },
    {
      id: "areas",
      route: "/playground",
      anchor: "[data-tour-pg='areas']",
      lines: [
        { text: "Below the composer, these pills are business areas: Inventory, Orders, Store Ops, and so on.", rate: 0.97 },
        { text: "Click any area to open a page filtered to just that area's questions and use cases. It's a way to narrow down when you already know where the question lives.", rate: 0.98, pauseAfter: 400 },
      ],
    },
    {
      id: "all-queries",
      route: "/playground",
      anchor: "[data-tour-pg='all-queries']",
      lines: [
        { text: "All queries opens the master list of frequent questions across every business area.", rate: 0.98, pauseAfter: 350 },
      ],
    },
    {
      id: "role-cards",
      route: "/playground",
      anchor: "[data-tour-pg='use-cases']",
      lines: [
        { text: "What do you need help with is a short list of use cases picked to match your role, and the kinds of questions someone in your role usually asks, and each card lists a couple of starting questions framed from different angles like This week or This quarter, so you can pick the timeframe that fits.", rate: 0.97 },
        { text: "Click any question to load it into the composer. The action button at the bottom of the card, labelled Analyze or Improve depending on the use case, also loads a question, defaulting to the This week angle. From the composer you can tweak the wording and send.", rate: 0.98, pauseAfter: 400 },
      ],
    },
    {
      id: "wrap",
      route: "/playground",
      anchor: null,
      lines: [
        { text: "That's the Playground. Ask directly, pick an area, browse all queries, or start from a role-scoped use case card. Every path leads to a grounded answer.", rate: 0.98 },
      ],
      advance: "manual",
    },
  ],
};
