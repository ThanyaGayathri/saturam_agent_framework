/**
 * KPI Registry tab tour.
 *
 * The KPI Registry is a read-only browse view of every metric your team
 * tracks, grouped by business area and subject area. Adding, editing, or
 * deleting KPIs happens in Configure > KPIs — the tour points there when
 * the user is ready to make changes.
 *
 * Anchors target elements in KpisHubLayout.jsx and KpiRegistry.jsx.
 */

export const KPI_REGISTRY_TOUR = {
  id: "kpi-registry",
  title: "KPI Registry",
  subtitle: "Runs about 3 minutes.",
  steps: [
    {
      id: "welcome",
      route: "/kpis/registry",
      anchor: null,
      lines: [
        { text: "This is the KPI Registry. It's the one place where every metric your team tracks is defined, and it's what the rest of the product refers back to.", rate: 0.97 },
        { text: "Every use case in the library maps back to one or more KPIs on this page, so when a workflow runs, you know exactly which metrics it should move.", rate: 0.98, pauseAfter: 300 },
      ],
    },
    {
      id: "actions",
      route: "/kpis/registry",
      anchor: "[data-tour-kpi='actions']",
      lines: [
        { text: "The Registry is read-only — a browse view of what already exists. To edit or delete a KPI, use Manage in Configure on the top right. That opens the KPIs tab in Configure, which is the primary edit surface.", rate: 0.97 },
        { text: "Add New KPI on the far right is a shortcut to create a single metric without leaving this page, and Import CSV brings in a batch at once when you're migrating a spreadsheet.", rate: 0.98, pauseAfter: 400 },
      ],
    },
    {
      id: "for-my-role",
      route: "/kpis/registry",
      anchor: "[data-tour-kpi-reg='for-my-role']",
      lines: [
        { text: "For my role is a soft filter. When you toggle it on, the registry narrows to the KPIs linked to the use cases assigned to your current role.", rate: 0.97 },
        { text: "Off by default because the Registry is the shared source of truth, and you sometimes need to see everything. Use this when you want a focused view.", rate: 0.97, pauseAfter: 350 },
      ],
    },
    {
      id: "filters",
      route: "/kpis/registry",
      anchor: "[data-tour-kpi-reg='filters']",
      lines: [
        { text: "The business area dropdown narrows the list to one area at a time, and the subject area dropdown further filters within the chosen business area.", rate: 0.97 },
        { text: "The search bar on the left matches KPI names, descriptions, and linked processes. It stacks on top of the dropdowns and the role filter.", rate: 0.98, pauseAfter: 400 },
      ],
    },
    {
      id: "tree",
      route: "/kpis/registry",
      anchor: "[data-tour-kpi-reg='tree']",
      lines: [
        { text: "The list is a tree. Enterprise groups all KPIs at the top, then business areas, then subject areas within each. Click any row to expand or collapse the group underneath.", rate: 0.97, pauseAfter: 350 },
      ],
    },
    {
      id: "rows",
      route: "/kpis/registry",
      anchor: "[data-tour-kpi-reg='tree']",
      lines: [
        { text: "Each KPI row has a handful of columns worth pointing out.", rate: 0.97 },
        { text: "KPI name is the canonical name used everywhere else in the product. Description explains what the metric actually measures.", rate: 0.97 },
        { text: "Maps to lists the business processes this KPI feeds into. Powered by lists every use case in the library that moves this KPI, so you can jump straight to a workflow from here.", rate: 0.98 },
        { text: "Status is the active toggle. Active KPIs feed the Dashboard tiles and the anomaly engine. Inactive KPIs stay defined but do not appear on live surfaces.", rate: 0.98 },
        { text: "Actions on the far right are the pencil and trash icons, for editing or removing the KPI — though the primary edit surface is Configure, KPIs.", rate: 0.98, pauseAfter: 400 },
      ],
    },
    {
      id: "wrap",
      route: "/kpis/registry",
      anchor: null,
      lines: [
        { text: "That's the Registry. Browse here, edit in Configure. Head to Data catalog to see the datasets that feed each KPI, or Dashboard to see them in action.", rate: 0.98 },
      ],
      advance: "manual",
    },
  ],
};
