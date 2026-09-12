/**
 * Middleby Insights home tour.
 *
 * Anchors target elements in MiddlebyInsightsHome.jsx. All steps stay on
 * /playground which is Middleby-safe (Playground routes are not blocked).
 */

export const MIDDLEBY_HOME_TOUR = {
  id: "middleby-home",
  title: "Insights home",
  subtitle: "Runs about 90 seconds.",
  steps: [
    {
      id: "welcome",
      route: "/playground",
      anchor: null,
      lines: [
        { text: "Welcome to your Insights home. This is the chat-first hub for Middleby analytics: ask a question in plain language, and EnterpriseGPT routes it to the right use case.", rate: 0.97 },
        { text: "Behind the scenes it's grounded in your HubSpot quotes, GA traffic, and LogRocket experience data.", rate: 0.97, pauseAfter: 300 },
      ],
    },
    {
      id: "composer",
      route: "/playground",
      anchor: "[data-tour-mb-home='composer']",
      lines: [
        { text: "The composer is where every conversation starts. Type your question, attach a file, or use voice dictation. The period chip below the box narrows the answer to a specific date window.", rate: 0.97, pauseAfter: 400 },
      ],
    },
    {
      id: "carousel",
      route: "/playground",
      anchor: "[data-tour-mb-home='carousel']",
      lines: [
        { text: "The priority use cases carousel below the composer is your fast lane. Click any card and the composer prefills with a ready-made question, so you can send it as-is or tweak the wording first.", rate: 0.97, pauseAfter: 400 },
      ],
    },
    {
      id: "browse",
      route: "/playground",
      anchor: "[data-tour-mb-home='browse']",
      lines: [
        { text: "Browse all use cases jumps to the full library. There you'll find every analytics use case tailored to Middleby, grouped by priority and funnel stage.", rate: 0.97, pauseAfter: 350 },
      ],
    },
    {
      id: "wrap",
      route: "/playground",
      anchor: null,
      lines: [
        { text: "That's the home. Ask a question, pick a priority card, or head to Use Cases for the full library. Every top-nav page also has its own help icon if you want a tour of that surface.", rate: 0.98 },
      ],
      advance: "manual",
    },
  ],
};
