import { readLocalJson, writeLocalJson } from "./local-json";

const VERSION = 1;

/**
 * User-added KPI sources for the KPI Gallery.
 *
 * Shape mirrors KPI_DATA_CATALOG_DATASETS (see domain/kpi-data-catalog.js) so
 * entries can be merged into the same gallery grid alongside the seeded
 * catalog. A user source is more than a plain data-source connection: it also
 * carries the business context (dataset name, attributes, business areas,
 * linked KPIs) that turn raw rows into measurable metrics.
 */

export function loadKpiSources(storageKey) {
  const parsed = readLocalJson(storageKey, null);
  if (!parsed || typeof parsed !== "object") {
    return { version: VERSION, sources: [] };
  }
  return {
    version: parsed.version ?? VERSION,
    sources: Array.isArray(parsed.sources) ? parsed.sources : [],
  };
}

export function saveKpiSources(data, storageKey) {
  writeLocalJson(storageKey, {
    version: data?.version ?? VERSION,
    sources: Array.isArray(data?.sources) ? data.sources : [],
  });
}

export function newKpiSourceId() {
  return `user-src-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`;
}
