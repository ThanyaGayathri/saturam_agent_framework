/**
 * Turns a `KpiSuggestion` (`GET /kpis/suggestions`, `product/egpt_retail/api/openapi/schemas/
 * kpis.yaml`) into a discovery row. Row shape matches `flattenKpiIndexRows` so discovery
 * treats every source identically; the suggestion itself rides along untouched for the card to
 * read its reasons, evidence and confidence from.
 *
 * No static demo candidates here any more (issue #234/#222 track F): the "suggested" surface
 * used to be a hardcoded array badged as if it were live, with an "adopt" that only ever wrote
 * to in-session React state. `KpiDiscoveryPage.jsx` now calls `fetchKpiSuggestions` for real and
 * maps each result through `rowFromSuggestion`, so this file keeps just the one thing about a
 * suggestion that isn't obvious from the wire shape: how it becomes a row.
 */

/**
 * A `KpiSuggestion` as a discovery row. Row shape matches `flattenKpiIndexRows` so discovery
 * treats both inputs identically; the suggestion itself rides along untouched for the card to
 * read its reasons, evidence and confidence from.
 *
 * `kpiIndex` should be `suggestion.identifier`, not an array position: `categoryIndex`/
 * `sectionIndex` are both fixed at -1 for every suggestion, so `kpiIndex` is the only thing
 * that makes `KpiDiscovery.jsx`'s row key unique -- an array position shifts (and remounts every
 * later card) the moment an earlier suggestion is adopted and removed from the list.
 */
export function rowFromSuggestion(suggestion, kpiIndex) {
 return {
  suggested: true,
  suggestion,
  categoryIndex: -1,
  sectionIndex: -1,
  kpiIndex,
  categoryTitle: "Suggested",
  sectionName: suggestion.concept || "",
  kpi: {
   name: suggestion.name,
   desc: suggestion.description,
   subjectArea: suggestion.concept || undefined,
   businessAreaId: suggestion.business_area,
   inferred: true,
   // Evidence for a suggestion is the tables it cites, not workspace dataset ids.
   datasetIds: [],
  },
 };
}
