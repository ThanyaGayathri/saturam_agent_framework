/** In-product demo customer profiles (browser-local; no backend). */

export const DEFAULT_DEMO_PROFILE_ID = "default";

/** Gourmet Foods International - GFI-only seeds + chat when this profile is active (see `gfi-demo-seed.js`). */
export const GOURMET_FOODS_PROFILE_ID = "gourmet-foods-intl";

/** Middleby Corporation - analytics Insight Sessions only (see `middleby-demo-seed.js`). */
export const MIDDLEBY_PROFILE_ID = "middleby";

/** Default Media - digital OTT / broadcast media & entertainment demo (see `media-demo-seed.js`). */
export const MEDIA_PROFILE_ID = "default-media";

// ── Demo roles ───────────────────────────────────────────────────────────────

export const DEMO_ROLE_EXECUTIVE  = "executive";
export const DEMO_ROLE_OPERATIONS = "operations";
export const DEMO_ROLE_ANALYST    = "analyst";
export const DEFAULT_DEMO_ROLE    = DEMO_ROLE_EXECUTIVE;

export const DEMO_ROLES = [
  {
    id: DEMO_ROLE_EXECUTIVE,
    label: "Executive",
    description: "ROI, margin, and strategic outcomes",
    persona: {
      name: "Alex Morgan",
      title: "VP Retail Strategy",
      initials: "AM",
      email: "alex.morgan@retailco.com",
      timezone: "America/New_York",
      timezoneLabel: "Eastern Time (ET)",
      locale: "en-US",
      memberSince: "Jan 2024",
      assignedAreas: ["inventory", "orders", "merchandise", "store-ops", "e-com", "pos", "support", "pricing", "finance", "workforce"],
    },
  },
  {
    id: DEMO_ROLE_OPERATIONS,
    label: "Operations",
    description: "Process efficiency, SLAs, and execution",
    persona: {
      name: "Jordan Lee",
      title: "Head of Operations",
      initials: "JL",
      email: "jordan.lee@retailco.com",
      timezone: "America/Chicago",
      timezoneLabel: "Central Time (CT)",
      locale: "en-US",
      memberSince: "Mar 2024",
      assignedAreas: ["inventory", "orders", "store-ops", "workforce"],
    },
  },
  {
    id: DEMO_ROLE_ANALYST,
    label: "Analyst",
    description: "Diagnostics, forecasting, and insights",
    persona: {
      name: "Sam Chen",
      title: "Senior Data Analyst",
      initials: "SC",
      email: "sam.chen@retailco.com",
      timezone: "America/Los_Angeles",
      timezoneLabel: "Pacific Time (PT)",
      locale: "en-US",
      memberSince: "Jun 2024",
      assignedAreas: ["inventory", "e-com", "pos", "pricing", "finance"],
    },
  },
];

/** Returns the demo persona for the given role. */
export function demoPersona(roleId) {
  return DEMO_ROLES.find((r) => r.id === roleId)?.persona ?? DEMO_ROLES[0].persona;
}

export function isDemoRoleId(value) {
  return DEMO_ROLES.some((r) => r.id === value);
}

export function normalizeDemoRoleId(value) {
  return isDemoRoleId(value) ? value : DEFAULT_DEMO_ROLE;
}

export function demoRoleLabel(roleId) {
  return DEMO_ROLES.find((r) => r.id === roleId)?.label ?? roleId;
}

export const DEMO_PROFILES = [
  {
    id: DEFAULT_DEMO_PROFILE_ID,
    label: "Default (generic retail)",
    companyName: "RetailCo",
    industry: "General Retail",
    region: "North America",
    plan: "Enterprise",
  },
  {
    id: GOURMET_FOODS_PROFILE_ID,
    label: "Gourmet Foods International",
    companyName: "Gourmet Foods International",
    industry: "Specialty Food & Beverage",
    region: "Multi-region",
    plan: "Enterprise",
  },
  {
    id: MIDDLEBY_PROFILE_ID,
    label: "Middleby (Analytics only)",
    companyName: "Middleby Corporation",
    industry: "Commercial Foodservice Equipment",
    region: "Global",
    plan: "Enterprise",
  },
  {
    id: MEDIA_PROFILE_ID,
    label: "Default Media",
    companyName: "MediaCo",
    industry: "Media & Entertainment (OTT + Broadcast)",
    region: "India",
    plan: "Enterprise",
  },
];

/** Returns the customer-facing company name (used on profile page, not the internal demo label). */
export function demoCompanyName(profileId) {
  return DEMO_PROFILES.find((p) => p.id === profileId)?.companyName ?? "RetailCo";
}

/** Returns workspace metadata for the profile page. */
export function demoWorkspace(profileId) {
  const p = DEMO_PROFILES.find((x) => x.id === profileId) ?? DEMO_PROFILES[0];
  return { companyName: p.companyName, industry: p.industry, region: p.region, plan: p.plan };
}

export function isDemoProfileId(value) {
  return DEMO_PROFILES.some((p) => p.id === value);
}

export function normalizeDemoProfileId(value) {
  return isDemoProfileId(value) ? value : DEFAULT_DEMO_PROFILE_ID;
}

export function demoProfileLabel(profileId) {
  return DEMO_PROFILES.find((p) => p.id === profileId)?.label ?? profileId;
}

/** Isolates IndexedDB chat threads per profile. */
export function scopedChatSessionId(baseSessionId, profileId) {
  const id = normalizeDemoProfileId(profileId);
  if (id === DEFAULT_DEMO_PROFILE_ID) return baseSessionId;
  return `${baseSessionId}__${id}`;
}
