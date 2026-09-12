/**
 * Two-way links between KPIs and use cases.
 *
 * Playbook layer (automation workflows):
 *   catalogId --(playbook.catalogIds)--> playbook --(playbook.kpiTargets)--> KPI name
 *
 * Direct-map layer (analytics use cases without a playbook):
 *   catalogId --(KPI_LINKS_BY_CATALOG_ID)--> KPI name
 *
 * Surface 2 (KPI chips on catalog cards) uses `getKpiNamesForCatalogId`,
 * which merges both layers (union, capped at 3).
 * Surface 3 ("Powered by" column in the KPI registry) uses
 * `getCatalogEntriesForKpiName`, which also unions both layers.
 *
 * Pure computations over the automation registry, demo catalog seeds, and
 * the direct-map. No side effects.
 */

import { getAllPlaybooks, playbooksTargetingKpiName } from "./automation-registry";
import { USE_CASE_CATALOG_SEED } from "./use-case-catalog-seed";
import { GFI_USE_CASE_CATALOG_SEED } from "./gfi-demo-seed";
import { MIDDLEBY_USE_CASE_CATALOG_SEED } from "./middleby-demo-seed";
import { KPI_LINKS_BY_CATALOG_ID } from "./kpi-links-by-catalog-id";
import {
  DEFAULT_DEMO_PROFILE_ID,
  GOURMET_FOODS_PROFILE_ID,
  MIDDLEBY_PROFILE_ID,
} from "./demo-profiles";

let CATALOG_INDEX = null;

function buildCatalogIndex() {
  const idx = new Map();
  for (const seed of [
    ...USE_CASE_CATALOG_SEED,
    ...GFI_USE_CASE_CATALOG_SEED,
    ...MIDDLEBY_USE_CASE_CATALOG_SEED,
  ]) {
    if (seed?.id) idx.set(seed.id, seed);
  }
  return idx;
}

function getCatalogIndex() {
  if (!CATALOG_INDEX) CATALOG_INDEX = buildCatalogIndex();
  return CATALOG_INDEX;
}

/**
 * Return the unique KPI names that this catalog id contributes to.
 * Merges two sources (union):
 *   1. Direct map from KPI_LINKS_BY_CATALOG_ID (analytics use cases)
 *   2. Walked via playbook.catalogIds -> playbook.kpiTargets (workflow use cases)
 * Capped at `limit` for chip UI.
 *
 * @param {string} catalogId
 * @param {number} [limit=3]
 * @returns {string[]}
 */
export function getKpiNamesForCatalogId(catalogId, limit = 3) {
  if (!catalogId) return [];
  const names = new Set();

  // Layer 1: direct-map (analytics use cases without a playbook)
  for (const n of KPI_LINKS_BY_CATALOG_ID[catalogId] || []) {
    const s = String(n).trim();
    if (s) names.add(s);
  }

  // Layer 2: playbook-derived (workflow use cases)
  const playbooks = getAllPlaybooks().filter(
    (p) => Array.isArray(p.catalogIds) && p.catalogIds.includes(catalogId)
  );
  for (const p of playbooks) {
    for (const t of p.kpiTargets || []) {
      const n = String(t?.name || "").trim();
      if (n) names.add(n);
    }
  }

  return Array.from(names).slice(0, limit);
}

/**
 * Return the catalog entries that power a given KPI. Merges playbook-derived
 * targets with the direct KPI_LINKS_BY_CATALOG_ID map. Optionally scope by
 * profileId (via `playbooksTargetingKpiName`'s internal profile filter).
 *
 * The profileId filter uses the playbook prefix map to scope the playbook
 * layer. For the direct-map layer we scope by catalog-id prefix on the
 * seed source (middleby-*, gfi-*, gallery-uc-*) so retail/GFI Powered-by
 * columns don't leak Middleby analytics.
 *
 * @param {string} kpiName
 * @param {string} [profileId]
 * @returns {Array<{ id: string, title: string }>}
 */
export function getCatalogEntriesForKpiName(kpiName, profileId = null) {
  if (!kpiName) return [];
  const needle = String(kpiName).trim();
  const seenIds = new Set();
  const idx = getCatalogIndex();
  const out = [];

  const pushEntry = (cid) => {
    if (seenIds.has(cid)) return;
    seenIds.add(cid);
    const entry = idx.get(cid);
    out.push({ id: cid, title: entry ? String(entry.title || cid) : cid });
  };

  // Filter direct-map entries by profile via catalog-id prefix. Use the
  // exported profile ID constants (not string literals) so this stays in
  // sync with demo-profiles.js — e.g. GFI is "gourmet-foods-intl", not
  // "gourmet-foods".
  const profileMatchesId = (cid) => {
    if (!profileId) return true;
    if (profileId === MIDDLEBY_PROFILE_ID) return cid.startsWith("middleby-");
    if (profileId === GOURMET_FOODS_PROFILE_ID) return cid.startsWith("gfi-");
    if (profileId === DEFAULT_DEMO_PROFILE_ID) return cid.startsWith("gallery-uc-");
    return true;
  };

  // Layer 1: direct-map
  for (const [cid, kpis] of Object.entries(KPI_LINKS_BY_CATALOG_ID)) {
    if (!profileMatchesId(cid)) continue;
    if (kpis.some((n) => String(n).trim() === needle)) pushEntry(cid);
  }

  // Layer 2: playbook-derived
  const playbooks = playbooksTargetingKpiName(kpiName, profileId);
  for (const p of playbooks) {
    for (const cid of p.catalogIds || []) pushEntry(cid);
  }

  return out;
}
