/**
 * Maps Playground hub category pills to URL slugs and catalog business-area
 * labels. Derived from the canonical `BUSINESS_AREAS` registry so the Playground
 * home, the Business Areas catalog, Processes, Use Cases, and KPI Registry all
 * stay in lockstep without local copies drifting.
 */
import { BUSINESS_AREAS } from "./business-areas";
import { getBusinessAreasSync } from "../storage/business-areas";
import { storageKeysForProfile } from "../storage/keys";
import { readActiveDemoProfileId } from "../storage/demo-profile-preference";

/** How many business-area pills show before “See more” on the Playground hub. */
export const PLAYGROUND_HUB_BUSINESS_AREAS_INITIAL_COUNT = 5;

/** Ordered list of business-area labels, for rendering pills on the hub. */
export const PLAYGROUND_HUB_CATEGORY_LABELS = BUSINESS_AREAS.map((b) => b.label);

/**
 * Label → URL slug. Derived from BUSINESS_AREAS; extra aliases (e.g. the
 * library sidebar says "E-Commerce") are tacked on after.
 */
export const PLAYGROUND_HUB_CATEGORY_TO_SLUG = {
  ...Object.fromEntries(BUSINESS_AREAS.map((b) => [b.label, b.id])),
  "E-Commerce": "e-com",
};

/** URL slug → canonical label. Derived from BUSINESS_AREAS. */
export const PLAYGROUND_AREA_SLUG_TO_LABEL = Object.fromEntries(
  BUSINESS_AREAS.map((b) => [b.id, b.label])
);

export function playgroundCategoryToSlug(label) {
  return PLAYGROUND_HUB_CATEGORY_TO_SLUG[label] ?? null;
}

export function playgroundSlugToAreaLabel(slug) {
  if (!slug || typeof slug !== "string") return null;
  // Prefer the live persisted store so renames on the Business hierarchy tab
  // reflect on the /playground/area/:slug page. Falls back to the seed map.
  try {
    const profileId = readActiveDemoProfileId();
    const key = storageKeysForProfile(profileId).BUSINESS_AREAS;
    const areas = getBusinessAreasSync(key, profileId);
    const hit = areas.find((a) => a.id === slug);
    if (hit) return hit.label;
  } catch {
    /* fall through to seed */
  }
  return PLAYGROUND_AREA_SLUG_TO_LABEL[slug] ?? null;
}

export function isPlaygroundAreaSlug(slug) {
  return Boolean(slug && Object.prototype.hasOwnProperty.call(PLAYGROUND_AREA_SLUG_TO_LABEL, slug));
}

/**
 * Optional deep links shown on the business-area use-case page (primary grid = catalog).
 * @type {Record<string, Array<{ to: string, label: string, description: string }>>}
 */
export const PLAYGROUND_AREA_EXTRA_LINKS = {
  inventory: [
    {
      to: "/playground/inventory",
      label: "Inventory overview",
      description: "Stock health, turnover, and replenishment signals across locations and SKUs.",
    },
  ],
  "store-ops": [
    {
      to: "/playground/store-ops",
      label: "Store operations",
      description: "Labor, door-level tasks, and operational exceptions in one workspace.",
    },
  ],
};
