import { KPI_DATA_CATALOG_DATASETS, getDatasetById } from "./kpi-data-catalog";

// KPI names in the data catalog use title case ("Gross Revenue") but some
// seeded registries lowercase them ("Gross revenue"), which used to leave
// every KPI in that profile unmapped. Normalize both sides.
function normalizeKpiNameKey(name) {
 return String(name || "").trim().toLowerCase();
}

/** @returns {Map<string, string[]>} normalized KPI name → dataset ids (from gallery `linkedKpiNames`). */
export function datasetIdsByLinkedKpiName() {
 const map = new Map();
 for (const d of KPI_DATA_CATALOG_DATASETS) {
 for (const name of d.linkedKpiNames || []) {
 const key = normalizeKpiNameKey(name);
 if (!key) continue;
 if (!map.has(key)) map.set(key, []);
 map.get(key).push(d.id);
 }
 }
 return map;
}

/** One-shot: set `datasetIds` where KPI `name` appears in a catalog dataset's `linkedKpiNames`. */
export function autoMapDatasetIdsForRegistry(categories) {
 const nameMap = datasetIdsByLinkedKpiName();
 const next = structuredClone(categories);
 for (const cat of next) {
 for (const sec of cat.sections || []) {
 for (const kpi of sec.kpis || []) {
 const key = normalizeKpiNameKey(kpi.name);
 const ids = nameMap.get(key);
 if (ids?.length) {
 kpi.datasetIds = [...new Set(ids)];
 }
 }
 }
 }
 return next;
}

/** @returns {"none" | "review" | "linked"} */
export function mappingStatusForKpi(kpi) {
 const ids = Array.isArray(kpi.datasetIds) ? kpi.datasetIds : [];
 if (ids.length === 0) return "none";
 let anyMissing = false;
 let anyStale = false;
 for (const id of ids) {
 const d = getDatasetById(id);
 if (!d) anyMissing = true;
 else if (d.status === "stale") anyStale = true;
 }
 if (anyMissing || anyStale) return "review";
 return "linked";
}

export function mappingHubStats(categories) {
 let none = 0;
 let onePlus = 0;
 let staleBacked = 0;
 for (const cat of categories || []) {
 for (const sec of cat.sections || []) {
 for (const kpi of sec.kpis || []) {
 const ids = Array.isArray(kpi.datasetIds) ? kpi.datasetIds : [];
 if (ids.length === 0) none++;
 else {
 onePlus++;
 const hasStale = ids.some((id) => getDatasetById(id)?.status === "stale");
 if (hasStale) staleBacked++;
 }
 }
 }
 }
 return { none, onePlus, staleBacked };
}

export function flattenKpiIndexRows(categories) {
 const rows = [];
 (categories || []).forEach((cat, categoryIndex) => {
 (cat.sections || []).forEach((sec, sectionIndex) => {
 (sec.kpis || []).forEach((kpi, kpiIndex) => {
 rows.push({
 kpi,
 categoryIndex,
 sectionIndex,
 kpiIndex,
 categoryTitle: cat.category || "",
 sectionName: sec.name || "",
 });
 });
 });
 });
 return rows;
}

function sortedIds(ids) {
 return [...ids].sort();
}

function idSetsEqual(a, b) {
 const sa = sortedIds(a);
 const sb = sortedIds(b);
 if (sa.length !== sb.length) return false;
 return sa.every((v, i) => v === sb[i]);
}

/** Heuristic confidence for the mappings slide-over (gallery lineage vs registry). */
export function mappingConfidenceForKpi(kpi, nameToIdsMap) {
 const ids = Array.isArray(kpi.datasetIds) ? kpi.datasetIds : [];
 const key = (kpi.name || "").trim();
 const catalogIds = nameToIdsMap.get(key) || [];

 if (ids.length === 0) {
 return {
 label: "Low",
 score: 18,
 // The Auto-map button was removed: binding runs when a source finishes modelling and
   // whenever a metric is added or enabled, so the only manual route left is Edit KPI.
   detail: "No enterprise datasets linked. Assign datasets in Edit KPI, or connect a source that carries them.",
 };
 }

 const resolved = ids.map((id) => getDatasetById(id)).filter(Boolean);
 const anyStale = resolved.some((d) => d.status === "stale");
 const anyMissing = ids.length > resolved.length;

 if (anyMissing) {
 return {
 label: "Low",
 score: 32,
 detail: "Some mapped ids are not in the gallery seed - verify in Configure or Gallery.",
 };
 }

 if (catalogIds.length && idSetsEqual(ids, catalogIds)) {
 if (anyStale) {
 return {
 label: "Medium",
 score: 71,
 detail: "Lineage matches gallery KPI names, but a backing dataset is marked stale.",
 };
 }
 return {
 label: "High",
 score: 94,
 detail: "Mappings align with gallery lineage for this KPI name; all linked assets are healthy.",
 };
 }

 if (resolved.length > 0) {
 return {
 label: "Medium",
 score: 56,
 detail: "Datasets are linked, but the set differs from gallery auto-map for this KPI name - worth a quick review.",
 };
 }

 return { label: "Low", score: 28, detail: "Unable to resolve mapping confidence from gallery data." };
}
