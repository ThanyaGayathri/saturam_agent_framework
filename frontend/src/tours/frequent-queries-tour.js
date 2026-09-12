/**
 * Frequent queries page tour. Reached from Playground's "All queries" pill or
 * from a business area click on the Playground hub. Walks:
 *   welcome → header (title + counts) → search → area sections → wrap.
 * Anchors target elements in FrequentQueries.jsx.
 */

export const FREQUENT_QUERIES_TOUR = {
  id: "frequent-queries",
  title: "Frequent queries",
  subtitle: "Runs about 90 seconds.",
  steps: [
    {
      id: "welcome",
      route: "/playground/queries",
      anchor: null,
      lines: [
        { text: "This is Frequent queries. It's the master catalog of sample questions every use case in your library can answer.", rate: 0.97 },
        { text: "You can reach it two ways: All queries on the Playground hub brings you here unfiltered, or clicking a business area on the hub jumps you to the same list narrowed to that area.", rate: 0.98, pauseAfter: 400 },
      ],
    },
    {
      id: "search",
      route: "/playground/queries",
      anchor: "[data-tour-freq='search']",
      lines: [
        { text: "The search bar matches by keyword, use case name, or business area, so you can find a starting question from any angle.", rate: 0.98, pauseAfter: 350 },
      ],
    },
    {
      id: "areas",
      route: "/playground/queries",
      anchor: "[data-tour-freq='areas']",
      lines: [
        { text: "Questions are grouped by business area, then by the use case they belong to. Each use case shows its title and impact line, and lists its starter questions below.", rate: 0.97 },
        { text: "Each question is tagged with a perspective, like This week or This quarter, so you can pick the timeframe that fits your ask.", rate: 0.98 },
        { text: "Click any question and it loads into the Playground composer. You can then tweak the wording and send.", rate: 0.98, pauseAfter: 450 },
      ],
    },
    {
      id: "wrap",
      route: "/playground/queries",
      anchor: null,
      lines: [
        { text: "That's Frequent queries. Search or browse, pick a starter, and let Playground handle the rest.", rate: 0.98 },
      ],
      advance: "manual",
    },
  ],
};
