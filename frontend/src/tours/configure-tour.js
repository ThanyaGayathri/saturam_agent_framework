/**
 * Configure workspace tour.
 *
 * One tour covers all five Configure tabs. Each tab-specific step sets its
 * own `route` so the TourProvider navigates to that tab before speaking —
 * the tab is selected and its content visible while the voice describes it.
 *
 * Anchors target elements in shared/ConfigureLayout.jsx (tabs, body, footer).
 */

export const CONFIGURE_TOUR = {
  id: "configure",
  title: "Configure",
  subtitle: "Runs about 5 minutes.",
  steps: [
    {
      id: "welcome",
      route: "/configure/hierarchy",
      anchor: null,
      lines: [
        { text: "This is Configure. It's the admin surface for the workspace, where you set up the business shape, connect data, and manage KPIs.", rate: 0.97 },
        { text: "You'll usually walk through it once during onboarding, then only visit when something changes.", rate: 0.98, pauseAfter: 400 },
      ],
    },
    {
      id: "tabs",
      route: "/configure/hierarchy",
      anchor: "[data-tour-configure='tabs']",
      lines: [
        { text: "Five tabs across the top, walked left to right in the order you'd usually set them up.", rate: 0.97, pauseAfter: 300 },
      ],
    },
    {
      id: "hierarchy",
      route: "/configure/hierarchy",
      anchor: "[data-tour-configure='body']",
      lines: [
        { text: "Business hierarchy is the first tab. Every business area and subject area used across the product lives here. Playground pills, Processes filter, Alerts filter, and the KPI Registry dropdowns all read from this list.", rate: 0.97 },
        { text: "Add a new business area from the button on the top right. Click into any area to expand and manage its subject areas. Rename and delete are inline actions on each row.", rate: 0.98 },
        { text: "Delete is blocked when an area still has KPIs tagged to it, so you can't accidentally break other pages. The confirmation dialog lists everywhere the change will show up.", rate: 0.98, pauseAfter: 400 },
      ],
    },
    {
      id: "sources",
      route: "/configure/internal-sources",
      anchor: "[data-tour-configure='body']",
      lines: [
        { text: "Data Sources is the second tab. It lists the internal systems your workspace pulls data from, databases, warehouses, and analytics stores like Snowflake, SAP, or PostgreSQL.", rate: 0.97 },
        { text: "Each row is one connection. Click a row to edit credentials, refresh cadence, or the schema being pulled. Add source at the top-right opens the connection form.", rate: 0.98, pauseAfter: 400 },
      ],
    },
    {
      id: "integrations",
      route: "/configure/external",
      anchor: "[data-tour-configure='body']",
      lines: [
        { text: "Integrations is the third tab. It connects external SaaS tools your team already uses.", rate: 0.97 },
        { text: "Think of the shops that live outside your data warehouse. Shopify or Salesforce Commerce for e-commerce, Salesforce CRM for accounts, Klaviyo or Braze for marketing, Stripe for payments, FedEx for shipping.", rate: 0.98 },
        { text: "When these are connected, use cases can pull live status, and Co-Pilot workflows can send actions back out, like triggering a recovery campaign or updating a case.", rate: 0.98, pauseAfter: 450 },
      ],
    },
    {
      id: "kpis-header",
      route: "/configure/domain-mapping",
      anchor: "[data-tour-configure='body']",
      lines: [
        { text: "KPIs is the fourth tab, and the primary place to manage every KPI in the workspace. Add new KPIs, rename them, change their business area or subject area, and link them to datasets and processes, all from here.", rate: 0.97 },
        { text: "The Add KPI button top-right creates a new metric. Click any row to open its detail panel; the Edit KPI button opens the full form to change everything about it.", rate: 0.98, pauseAfter: 350 },
        { text: "You may have seen the KPI Registry and Data catalog under the KPIs hub. Those are read-only browse surfaces. This tab is where the actual edits happen.", rate: 0.98, pauseAfter: 400 },
      ],
    },
    {
      id: "kpis-panes",
      route: "/configure/domain-mapping",
      anchor: "[data-tour-configure='body']",
      lines: [
        { text: "The strip at the top shows three counts. Assets mapped automatically is how many KPIs already have datasets linked. Need review are ones the system has a guess for but wants a human to confirm. Missing a dataset are KPIs with no dataset yet.", rate: 0.98 },
        { text: "Run Auto-Mapper on the right kicks off a scan that matches KPI names and keywords against your dataset catalog and suggests links. It's a one-shot, and every suggestion still shows up in the table below for a human to review or override.", rate: 0.98, pauseAfter: 400 },
        { text: "The left pane is the business hierarchy tree. Clicking any node filters the table to the KPIs under it. Two chips above the tree let you see all KPIs, or narrow to just the ones still missing a dataset.", rate: 0.97 },
        { text: "Each row on the right shows the KPI name with its area and subject as a small chip, all mapped datasets as chips, a compact Used by summary counting linked use cases and processes, a Confidence badge, and an Enable toggle. Click a row for the detail panel with the full context.", rate: 0.98, pauseAfter: 450 },
      ],
    },
    {
      id: "model",
      route: "/configure/models",
      anchor: "[data-tour-configure='body']",
      lines: [
        { text: "Model is the fifth and last tab. This is where a general-purpose language model gets connected to the workspace.", rate: 0.97 },
        { text: "You only need to configure a model here when your business does not already have its own dedicated model. Many workspaces run on a private model tuned to their own data, in that case this tab shows the existing connection and you don't add anything.", rate: 0.98, pauseAfter: 350 },
        { text: "Only one model is Active at a time. The card with the green Active pill is the one powering reasoning right now. Others show Available with a Make active button so you can swap.", rate: 0.98, pauseAfter: 400 },
      ],
    },
    {
      id: "running-on",
      route: "/configure/hierarchy",
      anchor: "[data-tour-configure='running-on']",
      lines: [
        { text: "At the bottom of Configure, the Running on line shows where the workspace is deployed and the cloud region it runs in. View cloud details opens the full deployment page.", rate: 0.98, pauseAfter: 350 },
      ],
    },
    {
      id: "wrap",
      route: "/configure/hierarchy",
      anchor: null,
      lines: [
        { text: "That's Configure. Business hierarchy, data sources, integrations, KPIs, and model, set once, and the rest of the product wires up on top of what you defined here.", rate: 0.98 },
      ],
      advance: "manual",
    },
  ],
};
