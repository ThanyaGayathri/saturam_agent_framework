import { readLocalJson, writeLocalJson } from "./local-json";
import { BUSINESS_AREAS as RETAIL_SEED, MEDIA_BUSINESS_AREAS } from "../domain/business-areas";
import { MIDDLEBY_ANALYTICS_AREAS } from "../domain/middleby-analytics-areas";
import { MEDIA_PROFILE_ID, MIDDLEBY_PROFILE_ID } from "../domain/demo-profiles";

/**
 * Persisted business hierarchy — the canonical list of business areas and
 * their subject areas used across the product (Playground pills,
 * Processes filter, Alerts filter, Business Areas catalog, KPI Registry
 * dropdowns, and the Configure > Business hierarchy tab).
 *
 * The seed is per-profile so each customer starts with areas that match
 * their business:
 *   - Retail (default) and GFI: retail areas (Inventory, Orders, Store Ops, ...).
 *   - Middleby: analytics areas (RFQ, Traffic, UX, Demand, Executive).
 *
 * After the first write, the persisted copy per profile takes over.
 * Deleting the localStorage key resets to the profile's seed.
 *
 * Shape: `{ version: 1, areas: [{ id, label, subjectAreas: string[], description? }] }`
 */

// Bumped 4 → 5 (2026-08-14) after Media business areas were further
// consolidated from 7 to 5 — Signup & Checkout + Subscriber Care +
// Plans & Pricing all collapsed into a single "Subscribers" area
// (reflecting how OTT orgs typically own subscriber lifecycle).
const VERSION = 5;

function slugify(text) {
  return String(text || "")
    .toLowerCase()
    .trim()
    .replace(/&/g, "and")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

function ensureUniqueId(baseId, existingIds) {
  if (!existingIds.has(baseId)) return baseId;
  let n = 2;
  while (existingIds.has(`${baseId}-${n}`)) n += 1;
  return `${baseId}-${n}`;
}

function normalizeArea(raw, existingIds = new Set()) {
  if (!raw || typeof raw !== "object") return null;
  const label = String(raw.label || "").trim();
  if (!label) return null;
  let id = String(raw.id || slugify(label)).trim() || slugify(label);
  id = ensureUniqueId(id, existingIds);
  existingIds.add(id);
  const subjectAreas = Array.isArray(raw.subjectAreas)
    ? raw.subjectAreas.map((s) => String(s || "").trim()).filter(Boolean)
    : [];
  const out = { id, label, subjectAreas };
  if (raw.description != null && String(raw.description).trim() !== "") {
    out.description = String(raw.description).trim();
  }
  return out;
}

function seedForProfile(profileId) {
  let src;
  if (profileId === MIDDLEBY_PROFILE_ID) {
    src = MIDDLEBY_ANALYTICS_AREAS;
  } else if (profileId === MEDIA_PROFILE_ID) {
    src = MEDIA_BUSINESS_AREAS;
  } else {
    src = RETAIL_SEED;
  }
  const seen = new Set();
  return src.map((s) => normalizeArea(s, seen)).filter(Boolean);
}

export function loadBusinessAreas(storageKey, profileId) {
  const parsed = readLocalJson(storageKey, null);
  const storedVersion = parsed?.version ?? 0;
  if (!parsed || typeof parsed !== "object" || !Array.isArray(parsed.areas) || storedVersion < VERSION) {
    // Re-seed on first load, missing data, or older schema. VERSION bumps
    // (e.g. renaming Middleby labels) force existing browsers to pick up the
    // new defaults instead of showing stale cached labels.
    const initial = { version: VERSION, areas: seedForProfile(profileId) };
    writeLocalJson(storageKey, initial);
    return initial;
  }
  const seen = new Set();
  const areas = parsed.areas.map((a) => normalizeArea(a, seen)).filter(Boolean);
  if (areas.length === 0) {
    const initial = { version: VERSION, areas: seedForProfile(profileId) };
    writeLocalJson(storageKey, initial);
    return initial;
  }
  return { version: VERSION, areas };
}

export function saveBusinessAreas(data, storageKey) {
  const seen = new Set();
  writeLocalJson(storageKey, {
    version: data?.version ?? VERSION,
    areas: (data?.areas || []).map((a) => normalizeArea(a, seen)).filter(Boolean),
  });
}

/** Synchronous read for non-React callers. Falls back to profile seed if storage is empty. */
export function getBusinessAreasSync(storageKey, profileId) {
  const parsed = readLocalJson(storageKey, null);
  if (parsed && Array.isArray(parsed.areas) && parsed.areas.length > 0) {
    const seen = new Set();
    return parsed.areas.map((a) => normalizeArea(a, seen)).filter(Boolean);
  }
  return seedForProfile(profileId);
}
