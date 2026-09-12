/**
 * User built use cases tab tour.
 * Focus: what this tab is, that these were built by the team, how to
 * launch or edit them.
 */

export const USE_CASE_GALLERY_TOUR = {
  id: "use-case-gallery",
  title: "User built use cases",
  subtitle: "Runs about 90 seconds.",
  steps: [
    {
      id: "welcome",
      route: "/use-cases/gallery",
      anchor: null,
      lines: [
        { text: "This is the User-built Use Cases tab. These are workflows your team has created and launched.", rate: 0.97 },
        { text: "Every action a use case takes is logged, so an auditor can trace every decision the AI made end to end.", rate: 0.98, pauseAfter: 300 },
      ],
    },
    {
      id: "cards",
      route: "/use-cases/gallery",
      anchor: "[data-tour-uc='cards']",
      lines: [
        { text: "Each card is one of your launched use cases. Click a card to open its details or resume editing.", rate: 0.97 },
        { text: "The purple pill at the top shows the action type: Analyze, Automate, or Explore. Next to it, the grey pill is the business process it runs against.", rate: 0.97 },
        { text: "The green chips marked Moves list the KPIs this use case affects. A yellow Draft badge means the use case hasn't been launched yet.", rate: 0.98, pauseAfter: 350 },
      ],
    },
    {
      id: "create",
      route: "/use-cases/gallery",
      anchor: "[data-tour-uc='create']",
      lines: [
        { text: "Build a new use case here. A ten-step wizard walks you through the question, the data, the plan, and the guardrails before launch.", rate: 0.98, pauseAfter: 350 },
      ],
    },
    {
      id: "wrap",
      route: "/use-cases/gallery",
      anchor: null,
      lines: [
        { text: "That's the User built use cases tab. Open any card to see its details, or use Create Use Case to start a new one.", rate: 0.98 },
      ],
      advance: "manual",
    },
  ],
};
