import {
  LEGACY_USE_CASE_CATALOG_IDS,
  USE_CASE_BUSINESS_AREA_ORDER,
  USE_CASE_CATALOG_SEED,
  getUseCaseTier,
} from "../domain/use-case-catalog-seed";
import { DEFAULT_DEMO_PROFILE_ID, GOURMET_FOODS_PROFILE_ID, MEDIA_PROFILE_ID, MIDDLEBY_PROFILE_ID, normalizeDemoProfileId } from "../domain/demo-profiles";
import { GFI_USE_CASE_CATALOG_SEED } from "../domain/gfi-demo-seed";
import { MIDDLEBY_USE_CASE_CATALOG_SEED } from "../domain/middleby-demo-seed";
import { MEDIA_USE_CASE_CATALOG_SEED } from "../domain/media-demo-seed";
import { readLocalJson, writeLocalJson } from "./local-json";
import { useCaseCatalogEntrySnapshotKey } from "./keys";

// Bumped 6 → 7 (2026-08-14) so persisted media UCs re-normalize the tier
// field to empty (was defaulting to "tactical" via getUseCaseTier). Media
// has no strategic / operational / tactical tier concept.
const CATALOG_VERSION = 7;

export { USE_CASE_CATALOG_SEED };

export function writeCatalogEntryDraftSnapshot(catalogId, profileId, draft) {
  if (!catalogId || !draft) return;
  try {
    writeLocalJson(useCaseCatalogEntrySnapshotKey(catalogId, profileId), JSON.parse(JSON.stringify(draft)));
  } catch (e) {
    console.warn("[retailgpt] writeCatalogEntryDraftSnapshot failed", e);
  }
}

export function readCatalogEntryDraftSnapshot(catalogId, profileId) {
  if (!catalogId) return null;
  try {
    return readLocalJson(useCaseCatalogEntrySnapshotKey(catalogId, profileId), null);
  } catch {
    return null;
  }
}

function normalizeEntry(row) {
  if (!row || typeof row !== "object") return null;
  const go = row.galleryOrder;
  const galleryOrder =
    typeof go === "number" && !Number.isNaN(go)
      ? go
      : go != null && String(go).trim() !== ""
        ? Number(go)
        : undefined;
  return {
    id: row.id || `user-${crypto.randomUUID?.() ?? Date.now()}`,
    title: String(row.title || "Untitled"),
    items: Array.isArray(row.items) ? row.items.map(String) : [],
    tags: Array.isArray(row.tags) ? row.tags.map(String) : [],
    businessArea:
      row.businessArea != null && String(row.businessArea).trim() !== ""
        ? String(row.businessArea).trim()
        : undefined,
    problemSignal:
      row.problemSignal != null && String(row.problemSignal).trim() !== ""
        ? String(row.problemSignal).trim()
        : undefined,
    actionType:
      row.actionType != null && String(row.actionType).trim() !== ""
        ? String(row.actionType).trim()
        : undefined,
    process:
      row.process != null && String(row.process).trim() !== "" ? String(row.process).trim() : undefined,
    galleryOrder:
      typeof galleryOrder === "number" && !Number.isNaN(galleryOrder) ? galleryOrder : undefined,
    // Media profile use cases don't carry strategic / operational / tactical
    // tiers — media has no equivalent 3-tier categorisation, so we return an
    // empty tier and the card UI just hides the badge.
    tier:
      row.tier ||
      (String(row.id || "").startsWith("media-")
        ? ""
        : String(row.id || "").startsWith("middleby-")
          ? "tactical"
          : getUseCaseTier(row.id)),
    source: row.source === "seed" ? "seed" : "user",
    status: row.status === "draft" ? "draft" : "active",
    resumePath: row.resumePath || undefined,
    draftSnapshot:
      row.draftSnapshot != null && typeof row.draftSnapshot === "object" ? row.draftSnapshot : undefined,
    updatedAt: row.updatedAt || new Date().toISOString(),
    analyticsUcNumber:
      typeof row.analyticsUcNumber === "number" && !Number.isNaN(row.analyticsUcNumber)
        ? row.analyticsUcNumber
        : undefined,
    analyticsSheet: row.analyticsSheet != null ? String(row.analyticsSheet) : undefined,
    dataReadiness: row.dataReadiness != null ? String(row.dataReadiness) : undefined,
    sampleQuestion: row.sampleQuestion != null ? String(row.sampleQuestion) : undefined,
    linkedKpiKeys: Array.isArray(row.linkedKpiKeys) ? row.linkedKpiKeys.map(String) : undefined,
  };
}

function catalogSeedForProfile(profileId) {
  const id = normalizeDemoProfileId(profileId);
  if (id === GOURMET_FOODS_PROFILE_ID) {
    return [...GFI_USE_CASE_CATALOG_SEED];
  }
  if (id === MIDDLEBY_PROFILE_ID) {
    return [...MIDDLEBY_USE_CASE_CATALOG_SEED];
  }
  if (id === MEDIA_PROFILE_ID) {
    return [...MEDIA_USE_CASE_CATALOG_SEED];
  }
  return [...USE_CASE_CATALOG_SEED];
}

function mergeWithSeed(stored, profileId = DEFAULT_DEMO_PROFILE_ID) {
  const seedRows = catalogSeedForProfile(profileId);
  const seedIds = new Set(seedRows.map((s) => s.id));
  const out = seedRows.map((s) => normalizeEntry({ ...s, source: "seed" }));
  for (const row of stored) {
    if (LEGACY_USE_CASE_CATALOG_IDS.has(row.id)) continue;
    const n = normalizeEntry(row);
    if (!n) continue;
    // Previously we skipped every stored entry marked source="seed" so the
    // static seed always won. That silently dropped user-added fields
    // (draftSnapshot from Launch, updated status, etc.) on a seed use case.
    // Now we only skip when the stored entry adds nothing beyond what the
    // seed already carries.
    const isSeedShadow = n.source === "seed" && seedIds.has(n.id);
    if (isSeedShadow && !n.draftSnapshot && n.status !== "draft") continue;
    const idx = out.findIndex((e) => e.id === n.id);
    if (idx >= 0) {
      // Merge: keep seed metadata (galleryOrder, tier, process, etc.) but
      // overlay the stored fields the user actually changed.
      out[idx] = { ...out[idx], ...n };
    } else {
      out.push(n);
    }
  }
  return out;
}

/** Seed-only rows (e.g. after clear storage or failed merge). */
export function getDefaultCatalogEntries(profileId = DEFAULT_DEMO_PROFILE_ID) {
  return mergeWithSeed([], profileId);
}

/**
 * Search across title, problem signal, description (items), tags, and facet fields (demo catalog grid).
 * Multi-word queries require every token to appear somewhere in the combined text.
 */
export function entryMatchesSearchQuery(entry, rawQuery) {
  const q = (rawQuery || "").trim().toLowerCase();
  if (!q) return true;
  const parts = [
    entry?.title,
    entry?.problemSignal,
    ...(Array.isArray(entry?.items) ? entry.items : []),
    ...(Array.isArray(entry?.tags) ? entry.tags : []),
    entry?.businessArea,
    entry?.actionType,
    entry?.process,
    entry?.tier,
  ]
    .filter(Boolean)
    .map((s) => String(s).toLowerCase());
  const blob = parts.join(" ");
  const tokens = q.split(/\s+/).filter(Boolean);
  if (tokens.length === 0) return true;
  return tokens.every((t) => blob.includes(t));
}

/**
 * @param {string} storageKey
 * @param {string} [profileId]
 */
export function loadUseCaseCatalog(storageKey, profileId = DEFAULT_DEMO_PROFILE_ID) {
  const parsed = readLocalJson(storageKey, null);
  if (!parsed || typeof parsed !== "object") {
    const initial = { version: CATALOG_VERSION, entries: mergeWithSeed([], profileId) };
    writeLocalJson(storageKey, initial);
    return initial;
  }
  const storedVersion = parsed.version ?? 0;
  const prevEntries = Array.isArray(parsed.entries) ? parsed.entries : [];
  const seedIds = new Set(catalogSeedForProfile(profileId).map((s) => String(s.id)));
  const baseForMerge =
    storedVersion < CATALOG_VERSION
      ? prevEntries.filter((row) => row?.id && !seedIds.has(String(row.id)))
      : prevEntries;
  let entries = mergeWithSeed(baseForMerge, profileId);
  if (!entries.length) {
    entries = mergeWithSeed([], profileId);
  }
  const out = { version: CATALOG_VERSION, entries };
  writeLocalJson(storageKey, out);
  return out;
}

export function saveUseCaseCatalog(data, storageKey) {
  writeLocalJson(storageKey, {
    version: data.version ?? CATALOG_VERSION,
    entries: (data.entries || []).map((e) => normalizeEntry(e)).filter(Boolean),
  });
}

/** Legacy helper: sorted gallery-shaped rows, optionally capped. The live gallery uses the full catalog per area. */
export function selectGalleryEntries(entries, limit = Number.POSITIVE_INFINITY) {
  const sorted = (entries || [])
    .filter((e) => e.businessArea && e.galleryOrder != null)
    .sort((a, b) => (a.galleryOrder || 0) - (b.galleryOrder || 0));
  return Number.isFinite(limit) ? sorted.slice(0, limit) : sorted;
}

/**
 * Business area for gallery grouping: stored field, else first matching tag, else "Other".
 * Keeps templates without `businessArea` visible alongside seeded rows.
 */
export function resolveGallerySectionArea(entry, areaOrder = USE_CASE_BUSINESS_AREA_ORDER) {
  const raw = entry?.businessArea != null && String(entry.businessArea).trim() !== "" ? String(entry.businessArea).trim() : "";
  if (raw) return raw;
  const tags = Array.isArray(entry?.tags) ? entry.tags.map((t) => String(t).trim()).filter(Boolean) : [];
  const order = areaOrder || USE_CASE_BUSINESS_AREA_ORDER;
  const lower = tags.map((t) => t.toLowerCase());
  for (const label of order) {
    if (lower.includes(label.toLowerCase())) return label;
  }
  return "Other";
}

export function groupGalleryEntriesByArea(entries, areaOrder, getArea) {
  const map = new Map();
  const resolve =
    typeof getArea === "function"
      ? getArea
      : (e) =>
          e?.businessArea != null && String(e.businessArea).trim() !== ""
            ? String(e.businessArea).trim()
            : "Other";
  for (const e of entries || []) {
    const area = resolve(e);
    if (!map.has(area)) map.set(area, []);
    map.get(area).push(e);
  }
  const order = areaOrder || [];
  const keys = [...map.keys()].sort((x, y) => {
    const ix = order.indexOf(x);
    const iy = order.indexOf(y);
    if (ix === -1 && iy === -1) return x.localeCompare(y);
    if (ix === -1) return 1;
    if (iy === -1) return -1;
    return ix - iy;
  });
  return keys.map((area) => ({ area, entries: map.get(area) }));
}

/** Catalog gallery `businessArea` label → builder step1 `category` slug. */
const SEED_BUSINESS_AREA_TO_CATEGORY = {
  Inventory: "inventory",
  Orders: "orders",
  "Store Ops": "store-ops",
  "E-Com": "ecom",
  "E-Commerce": "ecom",
  POS: "pos",
  Support: "support",
  Pricing: "pricing",
  Merchandise: "merchandise",
  Finance: "finance",
  Workforce: "workforce",
  // Media profile labels — map to the same canonical categories so use-case
  // draft prefill and Step 3 data-scope pick the right area on both sides.
  "Content Library": "inventory",
  "Content Publishing": "orders",
  Content: "merchandise",
  "Broadcast Ops": "store-ops",
  "OTT Platform": "e-com",
  "Signup & Checkout": "pos",
  "Subscriber Care": "support",
  "Plans & Pricing": "pricing",
  Monetization: "finance",
  "Content Teams": "workforce",
};

/**
 * Partial draft prefill from a seeded catalog row plus optional primary question (e.g. last Playground message).
 */
export function seedEntryToDraftPrefill(entry, primaryQuestion) {
  const title = String(entry?.title || "").trim() || "Untitled use case";
  const desc =
    String(entry?.problemSignal || "").trim() ||
    (Array.isArray(entry?.items) && entry.items[0] ? String(entry.items[0]).trim() : "") ||
    `Exploratory use case based on the catalog template "${title}".`;
  let category = "";
  const ba = entry?.businessArea != null ? String(entry.businessArea).trim() : "";
  if (ba && SEED_BUSINESS_AREA_TO_CATEGORY[ba]) {
    category = SEED_BUSINESS_AREA_TO_CATEGORY[ba];
  } else if (Array.isArray(entry?.tags)) {
    const lower = entry.tags.map((t) => String(t).trim().toLowerCase());
    const slugs = ["inventory", "orders", "store-ops", "ecom", "pos", "support", "pricing", "merchandise", "finance", "workforce"];
    for (const slug of slugs) {
      if (lower.includes(slug)) {
        category = slug;
        break;
      }
      const spaced = slug.replace("-", " ");
      if (lower.some((t) => t === spaced || t.includes(spaced))) {
        category = slug;
        break;
      }
    }
  }
  const q =
    String(primaryQuestion || "").trim() ||
    (Array.isArray(entry?.items) && entry.items[0] ? String(entry.items[0]).trim() : "") ||
    "";
  return {
    step1: { title, description: desc, category },
    step2: { primaryQuestion: q },
    builderSession: {
      hideTemplatesDraftBanner: false,
      reopenedCatalogId: null,
      fromSeedTemplateId: entry.id,
    },
  };
}

/** Save or update a draft entry in the catalog without activating. Returns { catalog, entry }. */
export function saveDraftEntryToCatalog(draft, catalogState, resumePath, profileId) {
  const title = draft.step1?.title?.trim() || "Untitled use case";
  const primary = draft.step2?.primaryQuestion?.trim();
  const items = primary ? [primary] : ["Use case draft in progress"];
  const CATEGORY_TO_BUSINESS_AREA = {
    inventory: "Inventory", orders: "Orders", "store-ops": "Store Ops",
    ecom: "E-Com", pos: "POS", support: "Support",
  };
  const businessArea = CATEGORY_TO_BUSINESS_AREA[draft.step1?.category] || undefined;
  const tags = (draft.step1?.tags || "").split(",").map((t) => t.trim()).filter(Boolean);

  // Prefer the reopenedCatalogId when the user is editing an existing entry
  // (seed or user-created). Falls back to matching an existing user draft by
  // title. Never creates a new "user-..." entry when we are editing a seed
  // entry - that would duplicate the seed as a user-defined use case.
  const reopenedId = draft.builderSession?.reopenedCatalogId || null;
  const existingByReopen = reopenedId
    ? (catalogState.entries || []).find((e) => e.id === reopenedId) ?? null
    : null;
  const existingDraft = existingByReopen ??
    (catalogState.entries || []).find(
      (e) => e.source === "user" && e.status === "draft" && e.title === title
    );
  const id = existingDraft?.id || `user-${crypto.randomUUID?.() ?? Date.now()}`;

  let draftSnapshot;
  try {
    draftSnapshot = JSON.parse(JSON.stringify(draft));
  } catch {
    draftSnapshot = undefined;
  }
  const entry = normalizeEntry({
    // Preserve seed-specific fields (source, process, problemSignal, tier,
    // galleryOrder, etc.) so a saved-as-draft seed use case stays a seed.
    ...(existingDraft || {}),
    id,
    title,
    items,
    tags,
    businessArea,
    // If the existing entry was a seed, keep source="seed" but mark it as a
    // draft so the Builder can pick it back up. Otherwise it's a user entry.
    source: existingDraft?.source || "user",
    status: "draft",
    resumePath,
    draftSnapshot,
    updatedAt: new Date().toISOString(),
  });

  const entries = existingDraft
    ? (catalogState.entries || []).map((e) => (e.id === id ? entry : e))
    : [...(catalogState.entries || []), entry];

  if (profileId) writeCatalogEntryDraftSnapshot(entry.id, profileId, draft);
  return { catalog: { ...catalogState, entries }, entry };
}

export function appendCatalogFromDraft(draft, catalogState, profileId) {
  const title = draft.step1?.title?.trim() || "New use case";
  const primary = draft.step2?.primaryQuestion?.trim();
  const items = [];
  if (primary) items.push(primary);
  const supporting = draft.step2?.supportingQuestions || [];
  items.push(...supporting);
  if (items.length === 0) {
    items.push("Configured use case from builder");
  }

  // Builder draft categories map to gallery business-area labels.
  const CATEGORY_TO_BUSINESS_AREA = {
    inventory: "Inventory",
    orders: "Orders",
    "store-ops": "Store Ops",
    ecom: "E-Com",
    pos: "POS",
    support: "Support",
  };
  const businessArea = CATEGORY_TO_BUSINESS_AREA[draft.step1?.category] || undefined;

  const tags = (draft.step1?.tags || "")
    .split(",")
    .map((t) => t.trim())
    .filter(Boolean);
  let draftSnapshot;
  try {
    draftSnapshot = JSON.parse(JSON.stringify(draft));
  } catch {
    draftSnapshot = undefined;
  }

  // If this launch is a re-activation of an existing use case (opened via Edit
  // with ?reopenCatalogId=X), update that entry in place instead of appending
  // a duplicate. Same title without a reopenedCatalogId still appends a new
  // entry, since two use cases can legitimately share a default title.
  const reopenedId = draft.builderSession?.reopenedCatalogId || null;
  const existingEntry = reopenedId
    ? (catalogState.entries || []).find((e) => e.id === reopenedId) ?? null
    : null;

  const entry = normalizeEntry({
    // Preserve seed-specific fields (source, process, problemSignal, tier,
    // galleryOrder, etc.) when relaunching a seed use case. Without this
    // spread, the seed entry would be replaced by a copy with source="user"
    // and effectively become a user-defined use case.
    ...(existingEntry || {}),
    id: existingEntry?.id || `user-${crypto.randomUUID?.() ?? Date.now()}`,
    title,
    items,
    tags,
    businessArea,
    // Keep source="seed" for reopened seed entries. Only assign "user" to
    // brand-new entries.
    source: existingEntry?.source || "user",
    status: "activated",
    draftSnapshot,
    updatedAt: new Date().toISOString(),
  });
  const entries = existingEntry
    ? (catalogState.entries || []).map((e) => (e.id === entry.id ? entry : e))
    : [...(catalogState.entries || []), entry];
  const catalog = { ...catalogState, entries };
  if (profileId) writeCatalogEntryDraftSnapshot(entry.id, profileId, draft);
  return { catalog, newEntry: entry };
}

/** Launch a Middleby analytics use case from the shortened builder (4 steps). */
export function appendAnalyticsCatalogFromDraft(draft, catalogState, profileId) {
  const title = draft.step1?.title?.trim() || "New analytics use case";
  const primary = draft.step2?.primaryQuestion?.trim();
  const description = draft.step1?.description?.trim();
  const category = draft.step1?.category || "";

  const ANALYTICS_CATEGORY_TO_BUSINESS_AREA = {
    rfq: "Digital & RFQ",
    ux: "UX & Quality",
    demand: "Digital & RFQ",
    executive: "Executive",
    dealer: "Dealer & Sales",
    marketing: "Marketing",
  };
  const ANALYTICS_CATEGORY_ACTION_TYPE = {
    rfq: "Analyze",
    ux: "Diagnose",
    demand: "Analyze",
    executive: "Summarize",
    dealer: "Prioritize",
    marketing: "Analyze",
  };

  const tags = (draft.step1?.tags || "")
    .split(",")
    .map((t) => t.trim())
    .filter(Boolean);
  if (!tags.includes("Middleby")) tags.unshift("Middleby");
  if (!tags.includes("Analytics")) tags.push("Analytics");

  const enabledSources = (draft.step3?.confirmedSources ?? []).filter((s) => s.enabled);
  const sourceNote =
    enabledSources.length > 0
      ? `Data: ${enabledSources.map((s) => s.name).join(", ")}.`
      : "Custom analytics use case.";

  let draftSnapshot;
  try {
    draftSnapshot = JSON.parse(JSON.stringify(draft));
  } catch {
    draftSnapshot = undefined;
  }

  const entry = normalizeEntry({
    id: `user-${crypto.randomUUID?.() ?? Date.now()}`,
    title,
    items: [description || sourceNote],
    tags,
    businessArea: ANALYTICS_CATEGORY_TO_BUSINESS_AREA[category] || "Digital & RFQ",
    problemSignal: description || sourceNote,
    actionType: ANALYTICS_CATEGORY_ACTION_TYPE[category] || "Analyze",
    analyticsSheet: "Custom",
    dataReadiness: "Partly Ready",
    sampleQuestion: primary || title,
    linkedKpiKeys: draft.analytics?.linkedKpiKeys ?? [],
    source: "user",
    draftSnapshot,
    updatedAt: new Date().toISOString(),
  });
  const entries = [...(catalogState.entries || []), entry];
  const catalog = { ...catalogState, entries };
  if (profileId) writeCatalogEntryDraftSnapshot(entry.id, profileId, draft);
  return { catalog, newEntry: entry };
}
