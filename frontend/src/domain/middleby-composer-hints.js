/**
 * Contextual grey helper text for Middleby composers (Insights hub, chat, insight follow-up).
 */

import { DEMO_ROLE_ANALYST, DEMO_ROLE_EXECUTIVE, DEMO_ROLE_OPERATIONS, normalizeDemoRoleId } from "./demo-profiles";
import { getMiddlebyKpiDerivedHint } from "./middleby-kpi-facts";

/** Default follow-up drill-down per analytics use case. */
const FOLLOW_UP_BY_CATALOG = {
  "middleby-uc-01":
    "How many high-intent sessions appeared in the last 15 days, and which brands lead?",
  "middleby-uc-02": "Which funnel step lost the most sessions, and do LogRocket errors line up with that drop?",
  "middleby-uc-03": "Which categories show rising GA traffic but weak quote conversion?",
  "middleby-uc-04": "Which 0-1 month quotes requested consultation and still need a dealer touch today?",
  "middleby-uc-05": "Walk the Pitco fryer path: where did high-intent browse stop before the quote?",
  "middleby-uc-06": "Which dealer regions have the largest quote pipeline gaps in HubSpot?",
  "middleby-uc-07": "What changed this week across traffic, errors, and submitted quotes?",
  "middleby-uc-08": "Which error types hit the most sessions on quote and PDP paths?",
  "middleby-uc-09": "What share of new quotes want 0-1 month delivery versus consultation?",
  "middleby-uc-10":
    "Do consultation quotes get faster dealer follow-up than self-serve RFQs in the sample?",
  "middleby-uc-11": "Which brands skew repeat-buyer versus new-to-brand in quote volume?",
  "middleby-uc-12": "Which regions show the most install-heavy or delivery-sensitive quote demand?",
  "middleby-uc-13": "Where are deals stalling by stage, and which brands concentrate the backlog?",
  "middleby-uc-14": "Which channels drive engaged sessions versus quick bounces?",
  "middleby-uc-15": "How many sessions browsed more than one Middleby brand before quoting?",
  "middleby-uc-16": "Flag the biggest weekly moves in traffic, LogRocket errors, and quote volume.",
  "middleby-uc-17": "How many brand sessions appeared in the sample window, and which brands led traffic?",
  "middleby-uc-18": "Which brands have the largest gap between GA sessions and HubSpot quotes?",
  "middleby-uc-19": "Which SKUs get strong browse interest but almost no quote requests?",
  "middleby-uc-20": "Did the latest site release line up with more errors and fewer quotes?",
  "middleby-uc-21": "Which PDP or category pages convert views to quotes best in the sample?",
  "middleby-uc-22": "Which session patterns look like buyers but never reached quote submit?",
  "middleby-uc-23": "Why did quotes soften this month when traffic was up?",
  "middleby-uc-24": "Which example session links tie a LogRocket replay to a HubSpot deal (stitch not in production)?",
  "middleby-uc-25": "Which products are most often quoted together on the same RFQ?",
  "middleby-uc-26": "How do guest, registered, and rep-assisted quote paths compare by brand?",
  "middleby-uc-27": "What add-on SKUs should we suggest before this quote is submitted?",
  "middleby-ex-04": "Which zero-result search terms should we fix first in the catalog?",
  "middleby-kpi-01": "Which channel and device pairs convert best to quotes?",
  "middleby-kpi-02": "What is the closed-won rate, and which lost reasons dominate?",
  "middleby-kpi-03": "How many Can I buy chats converted to quotes this week?",
  "middleby-kpi-04": "Which guided shopping tool has the weakest quote conversion?",
  "middleby-kpi-05": "Which entry pages have the highest bounce rate?",
  "middleby-kpi-06": "Which brands are missing PO references on closed-won deals?",
  "middleby-kpi-07": "Which sales territory generated the most quotes?",
  "middleby-kpi-08": "Which customer segment has the best closed-won rate?",
  "middleby-kpi-09": "Which product pages had the most views this week?",
  "middleby-kpi-10": "Which L1 categories drive the most quote volume?",
  "middleby-kpi-11": "Which US states generated the most quotes, and where is win rate strongest?",
  "middleby-kpi-12": "Which brand-territory pairs show the strongest and weakest quote conversion?",
  "middleby-kpi-13": "Which customer segments concentrate on which product categories?",
};

/** Role-aware overrides where the same chart needs a different angle. */
const FOLLOW_UP_BY_ROLE = {
  [DEMO_ROLE_EXECUTIVE]: {
    "middleby-uc-01": "How many quote-ready buyers surfaced this week, and which brands drive the most pipeline?",
    "middleby-uc-04": "How many urgent quotes need executive visibility on dealer follow-up this week?",
    "middleby-uc-07": "Summarize the top weekly moves in traffic, errors, and quotes for leadership.",
    "middleby-uc-18": "Which brands show the biggest browse-to-quote gap worth a leadership review?",
  },
  [DEMO_ROLE_OPERATIONS]: {
    "middleby-uc-01": "Which high-intent sessions should dealers contact first in the next 48 hours?",
    "middleby-uc-04": "Which consultation quotes are still unassigned to a regional dealer today?",
    "middleby-uc-06": "Which dealer regions need outreach to close the largest RFQ gaps?",
    "middleby-uc-10": "Are consultation RFQs getting first dealer touch faster than self-serve quotes?",
  },
  [DEMO_ROLE_ANALYST]: {
    "middleby-uc-01": "Break down high-intent sessions by brand and pages viewed (illustrative intent model).",
    "middleby-uc-08": "Which LogRocket error signatures cluster on RFQ form and PDP paths?",
    "middleby-uc-18": "Show browse vs quote volume by brand and flag the largest session gaps.",
    "middleby-uc-22": "Which behavioral patterns separate quote submitters from high-intent drop-offs?",
  },
};

const HUB_FALLBACK_BY_ROLE = {
  [DEMO_ROLE_EXECUTIVE]: "What changed this week across traffic, errors, and quote volume?",
  [DEMO_ROLE_OPERATIONS]: "Which quotes need dealer follow-up today?",
  [DEMO_ROLE_ANALYST]: "How many high-intent sessions appeared in the last 15 days?",
};

function catalogIdKey(catalogId) {
  return String(catalogId || "").trim();
}

/**
 * Insight session follow-up composer (grey text in the Ask box).
 */
export function getMiddlebyInsightFollowUpPlaceholder(catalogId, role = DEMO_ROLE_EXECUTIVE) {
  const id = catalogIdKey(catalogId);
  const roleKey = normalizeDemoRoleId(role);
  if (!id) return HUB_FALLBACK_BY_ROLE[roleKey] || HUB_FALLBACK_BY_ROLE[DEMO_ROLE_EXECUTIVE];

  const roleText = FOLLOW_UP_BY_ROLE[roleKey]?.[id];
  if (roleText) return roleText;

  // Client KPI hints derive numbers from the fixture arrays so placeholders
  // always cite the live top-lost-reason, worst-converting tool, etc.
  const derived = getMiddlebyKpiDerivedHint(id);
  if (derived) return derived;

  return (
    FOLLOW_UP_BY_CATALOG[id] ||
    "Ask about a brand, timeframe, funnel step, or dealer action tied to this chart…"
  );
}

/**
 * Insights home composer: top priority use case for the active role, else role default.
 */
export function getMiddlebyInsightsHubPlaceholder(role, primaryCatalogId = null) {
  const roleKey = normalizeDemoRoleId(role);
  const id = catalogIdKey(primaryCatalogId);
  if (id) return getMiddlebyInsightFollowUpPlaceholder(id, roleKey);
  return HUB_FALLBACK_BY_ROLE[roleKey] || HUB_FALLBACK_BY_ROLE[DEMO_ROLE_EXECUTIVE];
}

/**
 * Playground active chat when Middleby profile (optionally in a use-case thread).
 */
export function getMiddlebyPlaygroundChatPlaceholder(catalogId, role = DEMO_ROLE_EXECUTIVE) {
  const id = catalogIdKey(catalogId);
  if (id) {
    return `Follow up on ${shortUseCaseHint(id)}…`;
  }
  return getMiddlebyInsightsHubPlaceholder(role);
}

/** Subtitle under "Ask a follow-up" on insight sessions. */
export function getMiddlebyInsightFollowUpSectionHint(useCaseTitle) {
  const title = String(useCaseTitle || "").trim();
  if (!title) {
    return "Ask about the chart above. Answers stay in this use case with GA4, HubSpot, and LogRocket context.";
  }
  return `Ask a drill-down on ${title}. Answers stay in this session with the chart and evidence above.`;
}

/** Analytics catalog search helper. */
export function getMiddlebyAnalyticsSearchPlaceholder(role, primaryCatalogId = null) {
  const roleKey = normalizeDemoRoleId(role);
  const id = catalogIdKey(primaryCatalogId);
  const derived = id ? getMiddlebyKpiDerivedHint(id) : null;
  const source = derived || FOLLOW_UP_BY_CATALOG[id];
  if (id && source) {
    const snippet = source.slice(0, 72);
    return `Search use cases, or try: ${snippet}${source.length > 72 ? "…" : ""}`;
  }
  const fallbacks = {
    [DEMO_ROLE_EXECUTIVE]: "Search analytics use cases, e.g. weekly headlines or browse vs quote",
    [DEMO_ROLE_OPERATIONS]: "Search analytics use cases, e.g. dealer follow-up or RFQ funnel",
    [DEMO_ROLE_ANALYST]: "Search analytics use cases, e.g. intent score or LogRocket errors",
  };
  return fallbacks[roleKey] || fallbacks[DEMO_ROLE_EXECUTIVE];
}

function shortUseCaseHint(catalogId) {
  const hints = {
    "middleby-uc-01": "high-intent sessions",
    "middleby-uc-04": "dealer follow-up quotes",
    "middleby-uc-09": "buyer intent mix",
    "middleby-uc-11": "repeat vs new brand quotes",
    "middleby-uc-18": "browse vs quote gaps",
  };
  return hints[catalogId] || "this analytics use case";
}

/** @deprecated use getMiddlebyInsightFollowUpPlaceholder */
export const MIDDLEBY_INSIGHT_FOLLOW_UP_PLACEHOLDERS = FOLLOW_UP_BY_CATALOG;

export function getMiddlebyInsightFollowUpPlaceholderLegacy(catalogId) {
  return getMiddlebyInsightFollowUpPlaceholder(catalogId);
}
