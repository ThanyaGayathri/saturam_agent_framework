/**
 * Per-customer profile capabilities and route guards.
 *
 * Isolation contract (no cross-profile mixing):
 * - Each profile has its own localStorage keys (`storageKeysForProfile`).
 * - Seeds are selected only via helpers here and in domain storage modules.
 * - Default and GFI never load Middleby catalog/process/KPI/alert seeds.
 * - Middleby never loads retail or GFI operational seeds.
 * - Featured use case IDs are scoped per profile (no fallback to default retail).
 */

import {
  DEFAULT_DEMO_PROFILE_ID,
  GOURMET_FOODS_PROFILE_ID,
  MEDIA_PROFILE_ID,
  MIDDLEBY_PROFILE_ID,
  normalizeDemoProfileId,
} from "./demo-profiles";

/** Routes that belong to the retail automation demo (default + GFI). */
export const RETAIL_ONLY_ROUTE_PREFIXES = [
  // "/use-cases" removed — the use-case platform (builder, gallery, runs, dashboards) is the
  // same product for every customer; Middleby's own analytics use cases are built there.
  // "/kpis" removed — Middleby now shares the same KPI hub at /kpis/*.
  "/processes",
  "/monitor",
  "/alerts",
  "/business-areas",
  "/hierarchy",
  "/copilot",
];

/** Routes reserved for Middleby analytics (not used by default/GFI). */
export const MIDDLEBY_ONLY_ROUTE_PREFIXES = ["/analytics", "/playground/insight"];

// Canonical top-nav order across all profiles:
//   Playground · Configure · KPIs · Use Cases · Processes · Monitor · Alerts
// Profile-specific items (Middleby has Performance and Automation instead of
// Processes and Alerts) slot into the equivalent position.
// "Impact" has been folded into Monitor - the /impact page is reachable via
// the Monitor surface now, not as its own top-level nav item.
export const PROFILE_NAV_ITEMS = {
  [DEFAULT_DEMO_PROFILE_ID]: [
    { label: "Playground", path: "/playground", activePrefix: "/playground" },
    { label: "Configure", path: "/configure/internal-sources", activePrefix: "/configure" },
    { label: "KPIs", path: "/kpis" },
    { label: "Use Cases", path: "/use-cases", activePrefix: "/use-cases" },
    { label: "Processes", path: "/processes" },
    {
      label: "Monitor",
      path: "/monitor",
      activePrefix: "/monitor",
      activeAlsoPrefixes: ["/impact"],
    },
    { label: "Alerts", path: "/alerts" },
  ],
  [GOURMET_FOODS_PROFILE_ID]: [
    { label: "Playground", path: "/playground", activePrefix: "/playground" },
    { label: "Configure", path: "/configure/internal-sources", activePrefix: "/configure" },
    { label: "KPIs", path: "/kpis" },
    { label: "Use Cases", path: "/use-cases", activePrefix: "/use-cases" },
    { label: "Processes", path: "/processes" },
    {
      label: "Monitor",
      path: "/monitor",
      activePrefix: "/monitor",
      activeAlsoPrefixes: ["/impact"],
    },
    { label: "Alerts", path: "/alerts" },
  ],
  [MEDIA_PROFILE_ID]: [
    { label: "Playground", path: "/playground", activePrefix: "/playground" },
    { label: "Configure", path: "/configure/internal-sources", activePrefix: "/configure" },
    { label: "KPIs", path: "/kpis" },
    { label: "Use Cases", path: "/use-cases", activePrefix: "/use-cases" },
    { label: "Processes", path: "/processes" },
    {
      label: "Monitor",
      path: "/monitor",
      activePrefix: "/monitor",
      activeAlsoPrefixes: ["/impact"],
    },
    { label: "Alerts", path: "/alerts" },
  ],
  [MIDDLEBY_PROFILE_ID]: [
    {
      label: "Playground",
      path: "/playground",
      activePrefix: "/playground",
      activeExcludePrefixes: ["/playground/insight"],
    },
    { label: "Configure", path: "/configure/internal-sources", activePrefix: "/configure" },
    { label: "KPIs", path: "/kpis", activePrefix: "/kpis" },
    {
      // The use-case platform, as for every profile: the at-risk pipeline and the
      // diagnostic are built, rehearsed and run here. The prototype's Insight Sessions
      // catalog stays reachable at /analytics, not from the nav.
      label: "Use Cases",
      path: "/use-cases",
      activePrefix: "/use-cases",
      activeAlsoPrefixes: ["/analytics", "/playground/insight"],
      activeExcludePrefixes: [
        "/analytics/performance",
        "/analytics/monitor",
        "/analytics/automation",
      ],
    },
    { label: "Performance", path: "/analytics/performance", activePrefix: "/analytics/performance" },
    {
      label: "Monitor",
      path: "/analytics/monitor",
      activePrefix: "/analytics/monitor",
      activeAlsoPrefixes: ["/impact"],
    },
    { label: "Automation", path: "/analytics/automation", activePrefix: "/analytics/automation" },
  ],
};

export function navItemsForProfile(profileId) {
  const id = normalizeDemoProfileId(profileId);
  return PROFILE_NAV_ITEMS[id] ?? PROFILE_NAV_ITEMS[DEFAULT_DEMO_PROFILE_ID];
}

/** Whether a primary nav item should show as active for the current route. */
export function isNavItemActive(item, pathname) {
  const path = pathname || "/";
  const also = item.activeAlsoPrefixes;
  if (Array.isArray(also) && also.some((prefix) => path.startsWith(prefix))) {
    return true;
  }
  const exclude = item.activeExcludePrefixes;
  if (Array.isArray(exclude) && exclude.some((prefix) => path.startsWith(prefix))) {
    return false;
  }
  if (item.activePrefix) {
    return path === item.activePrefix || path.startsWith(`${item.activePrefix}/`);
  }
  return path === item.path || path.startsWith(`${item.path}/`);
}

export function isMiddlebyProfile(profileId) {
  return normalizeDemoProfileId(profileId) === MIDDLEBY_PROFILE_ID;
}

export function isGfiProfile(profileId) {
  return normalizeDemoProfileId(profileId) === GOURMET_FOODS_PROFILE_ID;
}

export function isRetailAutomationProfile(profileId) {
  const id = normalizeDemoProfileId(profileId);
  return (
    id === DEFAULT_DEMO_PROFILE_ID ||
    id === GOURMET_FOODS_PROFILE_ID ||
    id === MEDIA_PROFILE_ID
  );
}

export function isMediaProfile(profileId) {
  return normalizeDemoProfileId(profileId) === MEDIA_PROFILE_ID;
}

export function isAnalyticsProfile(profileId) {
  return isMiddlebyProfile(profileId);
}

export function homePathForProfile(profileId) {
  return "/playground";
}

function pathMatchesPrefix(pathname, prefixes) {
  return prefixes.some((p) => pathname === p || pathname.startsWith(`${p}/`));
}

/** Redirect target when the active profile cannot use the current route. */
export function profileRouteRedirect(profileId, pathname) {
  const id = normalizeDemoProfileId(profileId);
  const path = pathname || "/";

  if (id === MIDDLEBY_PROFILE_ID && pathMatchesPrefix(path, RETAIL_ONLY_ROUTE_PREFIXES)) {
    return homePathForProfile(id);
  }

  if (id !== MIDDLEBY_PROFILE_ID && pathMatchesPrefix(path, MIDDLEBY_ONLY_ROUTE_PREFIXES)) {
    return homePathForProfile(id);
  }

  return null;
}
