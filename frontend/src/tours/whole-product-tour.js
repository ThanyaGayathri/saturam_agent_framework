/**
 * Whole-product guided tour.
 *
 * Four acts, roughly 5 minutes end-to-end:
 *
 *   Act I  — How Enterprise GPT works       (/how-it-works,    5 steps)
 *   Act II — Where the data comes in        (/configure/*,     2 steps)
 *   Act III — The building blocks           (/hierarchy,       2 steps)
 *   Act IV — Where it happens               (product screens,  5 steps)
 *   Wrap                                    (1 step)
 *
 * Voice tone rules (see feedback memories):
 *   - No em-dashes.
 *   - Plain language: no ontology / hypergraph / ODKE / SLM / Guard.
 *   - "Automation workflow" not "playbook".
 *   - Neutral business language. No fluff, no metaphor beyond what the
 *     on-screen visuals already show.
 *   - Rate varies per line so speech isn't monotone.
 *
 * Each step has multiple `lines` because Web Speech gives natural pauses
 * between separate utterances (see useSpeechQueue).
 */

export const WHOLE_PRODUCT_TOUR = {
  id: "whole-product",
  title: "How Enterprise GPT works",
  subtitle: "Runs about 5 minutes.",
  steps: [
    // ─────────────────────────── Act I ────────────────────────────
    //          How Enterprise GPT works (on /how-it-works)

    {
      id: "welcome",
      route: "/how-it-works",
      anchor: null,
      lines: [
        { text: "Welcome. This tour walks through how Enterprise GPT works and how the product is organized.", rate: 0.95 },
        { text: "About five minutes. Pause any time. Use next and back whenever you need.", rate: 1.0, pauseAfter: 300 },
      ],
    },
    {
      id: "howit-cabinet-vs-brain",
      route: "/how-it-works",
      anchor: "[data-tour-how='brain']",
      lines: [
        { text: "Here's how Enterprise GPT is different from a generic AI. A generic AI is like a filing cabinet with sticky notes. It finds text that looks relevant to your question.", rate: 0.97 },
        { text: "Enterprise GPT is a brain that knows your business. It learns your terms and rules on Day one, and every answer references the specific rule it used.", rate: 0.98, pauseAfter: 400 },
      ],
    },
    {
      id: "howit-subway-map",
      route: "/how-it-works",
      anchor: "[data-tour-how='subway']",
      lines: [
        { text: "Now consider how each one handles reasoning. A generic AI hands you a street map. You still have to figure out the route yourself.", rate: 0.97 },
        { text: "Enterprise GPT shows you the route. It follows the connections in your business the way a subway map connects stations.", rate: 0.98, pauseAfter: 400 },
      ],
    },
    {
      id: "howit-two-brains",
      route: "/how-it-works",
      anchor: "[data-tour-how='two-brains']",
      lines: [
        { text: "Enterprise GPT uses two brains on every decision.", rate: 0.95 },
        { text: "One brain suggests what to do. A second, independent brain checks every claim against your data before you see it. Nothing reaches you unless both brains agree.", rate: 0.98, pauseAfter: 400 },
      ],
    },
    {
      id: "howit-private-brain",
      route: "/how-it-works",
      anchor: "[data-tour-how='private']",
      lines: [
        { text: "Your model is yours alone.", rate: 0.95 },
        { text: "Each business gets its own dedicated model. What yours learns from your data never touches anyone else's.", rate: 0.98, pauseAfter: 400 },
      ],
    },

    // ─────────────────────────── Act II ───────────────────────────
    //             Where the data comes in (Configure)

    {
      id: "configure-sources",
      route: "/configure/internal-sources",
      anchor: null,
      lines: [
        { text: "Now let's see how the AI gets connected to your data.", rate: 0.95 },
        { text: "This is where you set up internal data sources. Databases, warehouses, whatever your business runs on.", rate: 0.98 },
        { text: "The green pill above confirms your data stays in your workspace. Never shared with other tenants.", rate: 0.98, pauseAfter: 400 },
      ],
    },
    {
      id: "configure-models-integrations",
      route: "/configure/external",
      anchor: null,
      lines: [
        { text: "External integrations sit next to internal sources. Shopify, Salesforce, whichever tools your team already uses.", rate: 0.97 },
        { text: "Models and cloud settings live in adjacent tabs. Once configured, the AI has everything it needs to reason across your business.", rate: 0.98, pauseAfter: 400 },
      ],
    },

    // ─────────────────────────── Act III ──────────────────────────
    //             The building blocks (on /hierarchy)

    {
      id: "concepts-overview",
      route: "/hierarchy",
      anchor: "[data-tour-hierarchy='building-blocks']",
      lines: [
        { text: "Every entity in Enterprise GPT is one of six building blocks. These cards are those blocks.", rate: 0.95 },
        { text: "Business Areas contain Processes. Processes are measured by KPIs. Use Cases run across business areas and move KPIs. Alerts fire when a KPI needs attention. Subject Areas group related KPIs.", rate: 0.98, pauseAfter: 400 },
      ],
    },
    {
      id: "concepts-cards",
      route: "/hierarchy",
      anchor: "[data-tour-hierarchy='connections']",
      lines: [
        { text: "Below the cards, the connections diagram shows how the six concepts relate to each other. Read it as the map of how the product organizes work.", rate: 0.97, pauseAfter: 400 },
      ],
    },

    // ─────────────────────────── Act IV ───────────────────────────
    //              Where it happens (product screens)

    {
      id: "walk-kpi-registry",
      route: "/kpis/registry",
      anchor: null,
      lines: [
        { text: "Now to the actual product. This is the KPI Registry.", rate: 0.97 },
        { text: "Every metric your team tracks is defined here, and every use case in the library maps back to one or more KPIs on this page.", rate: 0.98, pauseAfter: 400 },
      ],
    },
    {
      id: "walk-processes",
      route: "/processes",
      anchor: null,
      lines: [
        { text: "This is the Processes page.", rate: 0.95 },
        { text: "Processes are grouped by business area. Each row shows the KPIs it moves and the use cases that reference it.", rate: 0.98, pauseAfter: 400 },
      ],
    },
    {
      id: "walk-use-cases",
      route: "/use-cases",
      anchor: null,
      lines: [
        { text: "This is the Use Cases gallery.", rate: 0.97 },
        { text: "Every action a use case takes is logged. An auditor can trace every decision the AI took, end to end.", rate: 0.98, pauseAfter: 400 },
      ],
    },
    {
      id: "walk-playground",
      route: "/playground",
      anchor: null,
      lines: [
        { text: "This is the Playground. It's where you ask business questions.", rate: 0.97 },
        { text: "Every answer traces back to your data. Ask a question, get a reasoned answer with references to the underlying facts.", rate: 0.98, pauseAfter: 400 },
      ],
    },
    {
      id: "walk-monitor",
      route: "/monitor",
      anchor: null,
      lines: [
        { text: "This is Monitor.", rate: 0.95 },
        { text: "Once use cases are running, this page shows their status. KPIs, completed actions, and flagged anomalies in one view.", rate: 0.98, pauseAfter: 400 },
      ],
    },

    // ─────────────────────────── Wrap ─────────────────────────────

    {
      id: "wrap",
      route: "/how-it-works",
      anchor: null,
      lines: [
        { text: "That's Enterprise GPT.", rate: 0.9 },
        { text: "You've seen how it thinks, how it stays safe, how it stays private, and how the product is organized.", rate: 0.97 },
        { text: "From here, open the Playground and try a business question. Or open a use case and see it run. Thanks for taking the tour.", rate: 1.0, pauseAfter: 300 },
      ],
      advance: "manual",
    },
  ],
};
