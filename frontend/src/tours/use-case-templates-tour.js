/**
 * Use Case Templates (Pre-defined) tab tour.
 * Focus: what pre-defined use cases are, how to launch one.
 */

export const USE_CASE_TEMPLATES_TOUR = {
  id: "use-case-templates",
  title: "Pre-defined Use Cases",
  subtitle: "Runs about 90 seconds.",
  steps: [
    {
      id: "welcome",
      route: "/use-cases/templates",
      anchor: null,
      lines: [
        { text: "This is the Pre-defined Use Cases tab. These are ready-to-run AI workflows that come with the product.", rate: 0.97 },
        { text: "Each one is tied to a KPI so its impact on the business is measurable, and every action it takes is logged.", rate: 0.98, pauseAfter: 300 },
      ],
    },
    {
      id: "cards",
      route: "/use-cases/templates",
      anchor: "[data-tour-uc='cards']",
      lines: [
        { text: "Each card is a template. Click any one to see what it does, what data it needs, and which KPIs it moves. Most templates affect one or more.", rate: 0.97 },
        { text: "The purple badge at the top left shows the role the template is aimed at: Executive, Operations, or Analyst.", rate: 0.97 },
        { text: "Next to it, the coloured pill is the tier: Strategic, Operational, or Tactical. Use the filter on the right to narrow the list.", rate: 0.97 },
        { text: "The green chips near the bottom, marked Moves, list the KPIs this template affects when it runs.", rate: 0.98, pauseAfter: 350 },
      ],
    },
    {
      id: "create",
      route: "/use-cases/templates",
      anchor: "[data-tour-uc='create']",
      lines: [
        { text: "Want a workflow that's not in the templates list? Build your own. A ten-step wizard walks through the question, the data, the plan, and the guardrails.", rate: 0.98, pauseAfter: 350 },
      ],
    },
    {
      id: "wrap",
      route: "/use-cases/templates",
      anchor: null,
      lines: [
        { text: "That's the Pre-defined tab. Click any card to launch, or switch to User built use cases to see what your team has already created.", rate: 0.98 },
      ],
      advance: "manual",
    },
  ],
};
