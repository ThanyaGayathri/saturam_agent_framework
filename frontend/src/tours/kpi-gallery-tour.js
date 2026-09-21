/**
 * Data catalog tab tour (formerly "KPI Gallery").
 *
 * The Data catalog is a read-only, live view of every table the semantic layer actually
 * exposes (`GET /semantic/tables`), grouped by schema (silver/gold) rather than by a
 * source-system label the backend does not track per table. Connecting a new source
 * happens in Configure > Data Sources; datasets appear here once the semantic layer has
 * harvested and modelled them.
 *
 * Anchors target elements in KpisHubLayout.jsx and KpiDataGallery.jsx.
 */

export const KPI_GALLERY_TOUR = {
  id: "kpi-gallery",
  title: "Data catalog",
  subtitle: "Runs about 2 minutes.",
  steps: [
    {
      id: "welcome",
      route: "/kpis/gallery",
      anchor: null,
      lines: [
        { text: "This is the Data catalog. It's a live, browse-only view of every table the semantic layer exposes.", rate: 0.97 },
        { text: "Every field here is real: which columns a table has, which business areas its linked KPIs serve, and which KPIs in the registry actually use it.", rate: 0.98, pauseAfter: 300 },
      ],
    },
    {
      id: "filters",
      route: "/kpis/gallery",
      anchor: "[data-tour-kpi-gal='filters']",
      lines: [
        { text: "The search bar on the left matches dataset names, attributes, business tags, and KPI names, so you can find any dataset from any angle.", rate: 0.97 },
        { text: "Schema narrows to silver or gold. Business area filters to datasets whose linked KPIs serve one area. Sort reorders by KPI count, row count, or name.", rate: 0.98, pauseAfter: 400 },
      ],
    },
    {
      id: "grid",
      route: "/kpis/gallery",
      anchor: "[data-tour-kpi-gal='grid']",
      lines: [
        { text: "Datasets are grouped by schema, so silver tables sit together and gold tables sit together.", rate: 0.97 },
        { text: "The count next to each schema shows how many datasets it contributes, plus how many attributes and KPI links it adds in total.", rate: 0.97, pauseAfter: 350 },
      ],
    },
    {
      id: "cards",
      route: "/kpis/gallery",
      anchor: "[data-tour-kpi-gal='grid']",
      lines: [
        { text: "Each dataset card has several fields worth pointing out.", rate: 0.97 },
        { text: "The header shows which schema the table lives in. Below it, the fully-qualified table name and its description from the semantic layer.", rate: 0.98 },
        { text: "Rows and Attributes give you a quick read on scale and shape.", rate: 0.98 },
        { text: "Just below that, the KPIs linked count tells you how many metrics in the registry actually declare this table as a source.", rate: 0.98 },
        { text: "Attribute chips list the columns exposed by the table. Below them, business area chips are drawn from the linked KPIs' own business areas.", rate: 0.98 },
        { text: "The KPIs linked count is the same relationship you see the other way around on a KPI's own detail panel, this is the dataset view of it.", rate: 0.98, pauseAfter: 400 },
      ],
    },
    {
      id: "wrap",
      route: "/kpis/gallery",
      anchor: null,
      lines: [
        { text: "That's the Data catalog. A table appears here once it's been connected and modelled in Configure, this page never shows a table that isn't real.", rate: 0.98 },
      ],
      advance: "manual",
    },
  ],
};
