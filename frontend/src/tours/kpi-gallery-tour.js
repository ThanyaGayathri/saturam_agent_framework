/**
 * Data catalog tab tour (formerly "KPI Gallery").
 *
 * The Data catalog is a read-only browse view of every dataset that
 * powers the KPIs in the registry, grouped by source system. Adding
 * or editing datasets happens elsewhere (Configure > Data Sources for
 * connections, or the Add KPI source button for new dataset entries).
 *
 * Anchors target elements in KpisHubLayout.jsx and KpiDataGallery.jsx.
 */

export const KPI_GALLERY_TOUR = {
  id: "kpi-gallery",
  title: "Data catalog",
  subtitle: "Runs about 3 minutes.",
  steps: [
    {
      id: "welcome",
      route: "/kpis/gallery",
      anchor: null,
      lines: [
        { text: "This is the Data catalog. It's a browse-only view of every dataset that powers the KPIs in your registry.", rate: 0.97 },
        { text: "A dataset here is more than a raw connection. It carries the business context — which columns matter, which business areas it serves, and which KPIs it feeds.", rate: 0.98, pauseAfter: 300 },
      ],
    },
    {
      id: "connect",
      route: "/kpis/gallery",
      anchor: "[data-tour-kpi='connect']",
      lines: [
        { text: "Add KPI source opens a form for registering a new dataset. Pick the underlying data source, name the dataset, list its attributes, tag the business areas, and link the KPIs it powers.", rate: 0.97, pauseAfter: 400 },
        { text: "This is separate from adding a raw data source in Configure. A KPI source lives here because it carries metric-specific context that a bare connection doesn't.", rate: 0.98, pauseAfter: 350 },
      ],
    },
    {
      id: "filters",
      route: "/kpis/gallery",
      anchor: "[data-tour-kpi-gal='filters']",
      lines: [
        { text: "The search bar on the left matches dataset names, attributes, business tags, and KPI names, so you can find any dataset from any angle.", rate: 0.97 },
        { text: "Four dropdowns on the right. Source narrows to one system, like SAP or Snowflake. Business area filters to datasets serving one area. Status shows only healthy or only stale feeds. Sort reorders by recent sync, name, or KPI count.", rate: 0.98, pauseAfter: 400 },
      ],
    },
    {
      id: "grid",
      route: "/kpis/gallery",
      anchor: "[data-tour-kpi-gal='grid']",
      lines: [
        { text: "Datasets are grouped by the source system they live in, so all the SAP tables sit together, all the Snowflake views sit together, and so on.", rate: 0.97 },
        { text: "The count next to each source shows how many datasets it contributes, plus how many attributes and KPI links it adds in total.", rate: 0.97, pauseAfter: 350 },
      ],
    },
    {
      id: "cards",
      route: "/kpis/gallery",
      anchor: "[data-tour-kpi-gal='grid']",
      lines: [
        { text: "Each dataset card has several fields worth pointing out.", rate: 0.97 },
        { text: "The header shows the source icon and a Healthy or Stale pill. A green Healthy pill means the sync is current; a yellow Stale pill means the last sync is behind schedule.", rate: 0.98 },
        { text: "Under the header, the fully-qualified name of the table or view, and a one-line description of what the dataset holds.", rate: 0.98 },
        { text: "Rows and Last sync give you a quick read on scale and freshness.", rate: 0.98 },
        { text: "Just below that, the KPIs linked count tells you how many metrics in the Registry this dataset feeds.", rate: 0.98 },
        { text: "Attribute chips list the columns exposed by the dataset. Below them, business area chips tag which parts of the business the dataset serves.", rate: 0.98 },
        { text: "At the very bottom, Edit and Remove buttons let you update or delete the source.", rate: 0.98 },
        { text: "The KPIs linked count on each card is the same relationship you configure the other way around in Configure, under the KPIs tab. Data catalog is the dataset view. Configure KPIs is the KPI view.", rate: 0.98, pauseAfter: 400 },
      ],
    },
    {
      id: "wrap",
      route: "/kpis/gallery",
      anchor: null,
      lines: [
        { text: "That's the Data catalog. Every KPI needs at least one dataset here to have a real value. Add sources as your data grows, and edit KPI to dataset mappings from Configure.", rate: 0.98 },
      ],
      advance: "manual",
    },
  ],
};
