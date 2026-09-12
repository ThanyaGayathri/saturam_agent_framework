/**
 * Middleby analytics scripted replies for Playground chat (demo, no backend).
 * Tone: GA4 + HubSpot quotes + LogRocket errors, dealer follow-up, browse vs quote gaps.
 */

import { MIDDLEBY_USE_CASE_CATALOG_SEED } from "./middleby-demo-seed";
import { resolveMiddlebyCatalogWithConfidence } from "./middleby-nl-router";

export { resolveMiddlebyCatalogWithConfidence } from "./middleby-nl-router";

function textMatchesKeyword(lower, keyword) {
  const k = keyword.trim().toLowerCase();
  if (!k) return false;
  if (k.includes(" ")) return lower.includes(k);
  try {
    return new RegExp(`\\b${k.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\b`, "i").test(lower);
  } catch {
    return lower.includes(k);
  }
}

export const PLAYGROUND_MIDDLEBY_DEFAULT_ACK =
  "I can analyze Middleby digital and RFQ signals across GA4 sessions, HubSpot quotes, and LogRocket errors. Ask how many potential buyers showed high intent, which quotes need dealer follow-up, browse vs quote gaps, funnel drop-offs, or LogRocket error impact.";

/** Rich KPI tiles + actions for analytics chat (keys used in meta.topic or catalog routing). */
export const MIDDLEBY_TOPIC_PANELS = {
  analytics: {
    findings: [
      {
        title: "Active quotes",
        value: "248",
        change: "+12%",
        desc: "HubSpot sample window vs prior period",
      },
      {
        title: "Urgent timeframe",
        value: "37",
        change: "0-1 mo",
        desc: "Quotes flagged for near-term purchase intent",
      },
      {
        title: "Consultation requests",
        value: "64",
        change: "26%",
        desc: "Share of quotes requesting dealer consultation",
      },
    ],
    recommendations: [
      "Prioritize dealer outreach on 0-1 month quotes with consultation=yes before end of week",
      "Pair HubSpot worklist with GA brand sessions to confirm browse support on hot SKUs",
      "Review LogRocket error clusters on quote and PDP paths tied to the same brands",
    ],
  },
  rfq_intent: {
    findings: [
      {
        title: "Potential buyers",
        value: "3",
        change: "15 days",
        desc: "High-intent sessions in illustrative intent model (not production GA4 funnel)",
      },
      {
        title: "Sessions scored",
        value: "40",
        change: "sample",
        desc: "Anonymous sessions with behavior-driven intent ranking",
      },
      {
        title: "Top score",
        value: "74",
        change: "Pitco",
        desc: "Highest intent session in illustrative sample (pages, dwell, repeat visits)",
      },
    ],
    recommendations: [
      "Route the 3 high-intent sessions to dealer outreach before medium-tier browse traffic",
      "Open the RFQ Intent insight session to see full session evidence and brand breakdown",
      "Pair intent scores with HubSpot quote history when the same buyer later submits an RFQ",
    ],
  },
  dealer_copilot: {
    findings: [
      {
        title: "Follow-up today",
        value: "18",
        change: "priority",
        desc: "Quotes with 0-1 month timeframe in the sample worklist",
      },
      {
        title: "Consultation=yes",
        value: "11",
        change: "urgent",
        desc: "High-intent quotes needing a dealer touch within 48 hours",
      },
      {
        title: "Stale open",
        value: "6",
        change: ">14d",
        desc: "Open quotes with no activity since last HubSpot update",
      },
    ],
    recommendations: [
      "Sort the worklist by timeframe, then consultation flag, and assign owners by dealer region",
      "Open the insight session chart to validate brand and product family concentration",
      "Escalate quotes with both urgent timeframe and consultation to sales leadership daily",
    ],
  },
  buyer_intent: {
    findings: [
      {
        title: "0-1 month",
        value: "37%",
        change: "largest",
        desc: "Share of quotes with near-term purchase timeframe",
      },
      {
        title: "Consultation",
        value: "26%",
        change: "of quotes",
        desc: "Buyers requesting dealer consultation on the form",
      },
      {
        title: "3+ months",
        value: "19%",
        change: "pipeline",
        desc: "Longer-horizon quotes suitable for nurture, not rush follow-up",
      },
    ],
    recommendations: [
      "Route near-term quotes to dealer copilot queues; keep long-horizon in nurture tracks",
      "Compare intent mix by brand to spot categories where consultation is under-offered",
      "Align marketing spend to brands with rising 0-1 month share in HubSpot",
    ],
  },
  repeat_brand: {
    findings: [
      {
        title: "Repeat brand",
        value: "142",
        change: "57%",
        desc: "Quotes from buyers returning to a known brand in HubSpot",
      },
      {
        title: "New to brand",
        value: "106",
        change: "43%",
        desc: "First-time brand quotes in the sample window",
      },
      {
        title: "Top repeat",
        value: "Pitco",
        change: "+8",
        desc: "Largest repeat-brand quote volume in the slice",
      },
    ],
    recommendations: [
      "Protect repeat-brand velocity with faster dealer response SLAs on consultation quotes",
      "For new-to-brand spikes, confirm PDP and RFQ content covers sizing and lead time",
      "Review cross-sell paths where repeat buyers quote adjacent brands but do not convert",
    ],
  },
  browse_quote: {
    findings: [
      {
        title: "Browse gap",
        value: "4 brands",
        change: "high GA",
        desc: "Brands with 500+ sessions but three or fewer quotes in sample",
      },
      {
        title: "Largest gap",
        value: "TurboChef",
        change: "612 sess",
        desc: "High GA traffic with minimal HubSpot quote capture",
      },
      {
        title: "Aligned",
        value: "2 brands",
        change: "on pace",
        desc: "Browse and quote volume move together in the window",
      },
    ],
    recommendations: [
      "Diagnose TurboChef-class gaps: RFQ friction, missing models, or dealer routing delays",
      "Pair GA landing pages with HubSpot quote sources to see where handoff breaks",
      "Test simplified quote paths on brands with high sessions and low form starts",
    ],
  },
  funnel_errors: {
    findings: [
      {
        title: "Funnel completion",
        value: "18%",
        change: "-4 pts",
        desc: "Illustrative RFQ funnel completion (production GA4 funnel events not live yet)",
      },
      {
        title: "Error sessions",
        value: "1.2k",
        change: "+9%",
        desc: "LogRocket sessions with JS errors on shop and quote paths",
      },
      {
        title: "Top drop step",
        value: "RFQ form",
        change: "42%",
        desc: "Largest step-to-step loss in the browse-to-quote funnel",
      },
    ],
    recommendations: [
      "Inspect LogRocket replays on RFQ form errors before changing media or promos",
      "Quantify revenue at risk from error sessions on high-intent product pages",
      "Ship a hotfix on the worst error signature, then re-run the funnel slice weekly",
    ],
  },
};

const CATALOG_TOPIC = {
  "middleby-uc-01": "rfq_intent",
  "middleby-uc-04": "dealer_copilot",
  "middleby-uc-09": "buyer_intent",
  "middleby-uc-11": "repeat_brand",
  "middleby-uc-18": "browse_quote",
};

const MIDDLEBY_EXPLORE_REPLIES = {
  "middleby-uc-01": {
    week: {
      topic: "rfq_intent",
      text: "This week on RFQ intent I am ranking anonymous sessions with an illustrative behavior score (pages, dwell, repeat visits). Demo read: 3 of 40 sessions in the sample are high-intent quote candidates (score ≥ 70), led by Pitco and Southbend; route those to dealer follow-up before medium-tier browse traffic. Production GA4 add-to-quote events are not live yet.",
    },
    quarter: {
      topic: "rfq_intent",
      text: "For the quarter I am trending how many anonymous sessions cross the high-intent threshold each week and which brands concentrate quote-ready behavior. Demo read: sustained wins come from acting on the top tier within 48 hours, not re-scoring the full funnel daily; review brand mix quarterly when browse rises but HubSpot quote capture stays flat.",
    },
  },
  "middleby-uc-04": {
    week: {
      topic: "dealer_copilot",
      text: "This week on dealer copilot I am sorting HubSpot quotes by purchase timeframe and consultation flag. Demo read: 18 quotes need follow-up in the 0-1 month window and 11 explicitly requested consultation; assign those to regional dealers before Friday and use the worklist chart to avoid duplicating outreach on the same brand family.",
    },
    quarter: {
      topic: "dealer_copilot",
      text: "For the quarter I am tracking quote aging, consultation conversion, and dealer response time by region. Demo read: sustained wins come from a daily priority queue and clear ownership rules, not ad hoc inbox sweeps; set a quarterly SLA for first touch on urgent timeframe quotes and review brands where consultation volume rises but close rate stalls.",
    },
  },
  "middleby-uc-09": {
    week: {
      topic: "buyer_intent",
      text: "This week on buyer intent I am reading HubSpot purchase timeframe and consultation fields on new quotes. Demo read: 37% of the sample wants delivery in 0-1 months while 26% asked for consultation; route near-term intent to dealer copilot today and keep 3+ month quotes in nurture so reps do not burn cycles on low-urgency pipeline.",
    },
    quarter: {
      topic: "buyer_intent",
      text: "Quarterly intent mix: compare timeframe and consultation share by brand and campaign source. Demo read: when consultation rises on a brand but quotes flatten, the fix is usually form clarity or dealer capacity, not more traffic; anchor quarterly reviews on intent distribution shifts, not quote count alone.",
    },
  },
  "middleby-uc-11": {
    week: {
      topic: "repeat_brand",
      text: "This week on repeat vs new brand quotes I am splitting HubSpot volume by buyer history. Demo read: repeat-brand quotes are 57% of the slice with Pitco leading; protect those buyers with faster dealer SLAs and check whether new-to-brand surges have matching PDP and RFQ content.",
    },
    quarter: {
      topic: "repeat_brand",
      text: "For the quarter I am trending loyalty mix and quote-to-close patterns by brand. Demo read: repeat buyers should see shorter paths to consultation; if new-to-brand share climbs without close-rate lift, audit onboarding content and dealer routing quarterly.",
    },
  },
  "middleby-uc-18": {
    week: {
      topic: "browse_quote",
      text: "This week on browse vs quote I am joining GA4 brand sessions to HubSpot quote counts. Demo read: TurboChef shows 612 sessions but only a handful of quotes; treat that as a handoff problem first (RFQ friction, model gaps, dealer delays) before increasing paid traffic.",
    },
    quarter: {
      topic: "browse_quote",
      text: "Quarterly browse-to-quote alignment by brand: quantify gaps where traffic scales but RFQ capture does not. Demo read: prioritize fixes on high-session brands with sub-scale quotes, then measure quarter-over-quarter lift in form starts and submitted quotes per thousand sessions.",
    },
  },
};

const MIDDLEBY_RULES = [
  {
    keywords: [
      "potential buyer",
      "potential buyers",
      "high intent",
      "high-intent",
      "intent score",
      "likely to submit",
      "likely to quote",
      "quote candidate",
      "rfq intent",
      "visitors most likely",
      "how many buyer",
      "how many potential",
    ],
    topic: "rfq_intent",
    catalogId: "middleby-uc-01",
    intro:
      "In the last 15 days on illustrative anonymous sessions (Middleby demo sample), 3 potential buyers crossed the high-intent threshold (score ≥ 70): Pitco (74), Southbend (73), and a repeat Southbend visitor (70). The sample has 40 scored sessions in the window; prioritize those three for dealer outreach before medium-tier browse traffic.",
  },
  {
    keywords: ["dealer copilot", "follow-up", "follow up", "urgent", "worklist", "consultation", "sales rep"],
    topic: "dealer_copilot",
    intro: "Sorting HubSpot quotes for dealer follow-up this week (Middleby demo data):",
  },
  {
    keywords: ["timeframe", "buyer intent", "purchase timeframe", "0-1", "consultation mix", "intent mix"],
    topic: "buyer_intent",
    intro: "Reading purchase timeframe and consultation mix from HubSpot quotes (Middleby demo data):",
  },
  {
    keywords: ["repeat", "new to brand", "new-to-brand", "loyalty", "repeat brand"],
    topic: "repeat_brand",
    intro: "Comparing repeat-brand vs new-to-brand quote volume in HubSpot (Middleby demo data):",
  },
  {
    keywords: ["browse vs quote", "browse and quote", "ga vs hubspot", "traffic vs quote", "session gap"],
    topic: "browse_quote",
    intro: "Joining GA4 brand sessions to HubSpot quote volume (Middleby demo data):",
  },
  {
    keywords: ["funnel", "drop-off", "drop off", "conversion", "rfq form", "quote form"],
    topic: "funnel_errors",
    intro: "Tracing illustrative RFQ funnel steps and LogRocket errors (Middleby demo data; production GA4 funnel not live yet):",
  },
  {
    keywords: ["logrocket", "javascript error", "js error", "error impact", "session replay", "broken"],
    topic: "funnel_errors",
    intro: "Correlating LogRocket errors with quote and PDP paths (Middleby demo data):",
  },
  {
    keywords: ["hubspot", "quote", "quotes", "rfq"],
    topic: "analytics",
    intro: "Pulling HubSpot quote signals for digital and RFQ analytics (Middleby demo data):",
  },
  {
    keywords: ["ga4", "google analytics", "sessions", "brand traffic", "site search"],
    topic: "analytics",
    intro: "Summarizing GA4 brand and path behavior for Middleby shop traffic (demo data):",
  },
];

function panelForTopic(topic) {
  return MIDDLEBY_TOPIC_PANELS[topic] || MIDDLEBY_TOPIC_PANELS.analytics;
}

function richMeta(topic, catalogId) {
  const resolvedTopic = catalogId && CATALOG_TOPIC[catalogId] ? CATALOG_TOPIC[catalogId] : topic;
  return {
    variant: "playground-rich",
    topic: resolvedTopic,
    panel: panelForTopic(resolvedTopic),
    ...(catalogId ? { catalogId } : {}),
  };
}

/**
 * @param {string} catalogId
 * @param {"week"|"quarter"} horizonKey
 * @returns {{ text: string, meta: object } | null}
 */
export function getMiddlebyExploreHubReply(catalogId, horizonKey) {
  if (!catalogId || (horizonKey !== "week" && horizonKey !== "quarter")) return null;
  const row = MIDDLEBY_EXPLORE_REPLIES[catalogId]?.[horizonKey];
  if (!row?.text || !row.topic) return null;
  return {
    text: row.text,
    meta: richMeta(row.topic, catalogId),
  };
}

export function resolveMiddlebyCatalogIdFromText(userText) {
  const routed = resolveMiddlebyCatalogWithConfidence(userText);
  return routed.catalogId ?? routed.suggestions[0]?.catalogId ?? legacyResolveMiddlebyCatalogIdFromText(userText);
}

function legacyResolveMiddlebyCatalogIdFromText(userText) {
  const matched = tryMatchMiddlebyPlaygroundReply(userText);
  if (matched?.meta?.catalogId) return matched.meta.catalogId;

  const lower = String(userText || "").toLowerCase();
  for (const row of MIDDLEBY_USE_CASE_CATALOG_SEED) {
    const problem = String(row.problemSignal || "").trim().toLowerCase();
    if (problem.length > 20 && lower.includes(problem.slice(0, 40))) return row.id;
    const title = String(row.title || "").trim().toLowerCase();
    if (title && lower.includes(title)) return row.id;
  }
  return null;
}

function matchCatalogIdFromText(lower) {
  if (
    /potential\s+buyers?|high[- ]intent|intent\s+score|likely to (submit|quote)|quote candidates?|how many.*buyers?/i.test(
      lower
    )
  ) {
    return "middleby-uc-01";
  }
  for (const row of MIDDLEBY_USE_CASE_CATALOG_SEED) {
    const title = String(row.title || "").toLowerCase();
    if (title && lower.includes(title)) return row.id;
    const problem = String(row.problemSignal || "").toLowerCase();
    if (problem.length > 24 && lower.includes(problem.slice(0, 40))) return row.id;
  }
  for (const [id, topic] of Object.entries(CATALOG_TOPIC)) {
    const panel = MIDDLEBY_TOPIC_PANELS[topic];
    if (panel?.findings?.some((f) => lower.includes(String(f.title).toLowerCase()))) return id;
  }
  return null;
}

/**
 * @param {string} userText
 * @param {{ catalogId?: string, horizon?: string, lens?: string } | undefined} [exploreHub]
 * @returns {{ text: string, meta?: object } | null}
 */
export function tryMatchMiddlebyPlaygroundReply(userText, exploreHub) {
  const lower = String(userText || "").toLowerCase();

  for (const rule of MIDDLEBY_RULES) {
    if (rule.keywords.some((k) => textMatchesKeyword(lower, k))) {
      const catalogId =
        rule.catalogId || Object.entries(CATALOG_TOPIC).find(([, t]) => t === rule.topic)?.[0];
      return {
        text: rule.intro,
        meta: richMeta(rule.topic, catalogId),
      };
    }
  }

  const inferredId = matchCatalogIdFromText(lower);
  if (inferredId) {
    const topic = CATALOG_TOPIC[inferredId] || "analytics";
    const entry = MIDDLEBY_USE_CASE_CATALOG_SEED.find((r) => r.id === inferredId);
    return {
      text: `${entry?.problemSignal || entry?.title || "Matched analytics use case"} (Middleby demo data):`,
      meta: richMeta(topic, inferredId),
    };
  }

  const catalogFromHub = exploreHub?.catalogId ? String(exploreHub.catalogId).trim() : "";
  if (catalogFromHub.startsWith("middleby-")) {
    const horizon =
      exploreHub?.horizon === "week" || exploreHub?.horizon === "quarter"
        ? exploreHub.horizon
        : null;
    if (horizon) {
      const seeded = getMiddlebyExploreHubReply(catalogFromHub, horizon);
      if (seeded) return seeded;
    }
    const topic = CATALOG_TOPIC[catalogFromHub] || "analytics";
    const entry = MIDDLEBY_USE_CASE_CATALOG_SEED.find((r) => r.id === catalogFromHub);
    const intro = entry?.problemSignal
      ? `${entry.problemSignal} (Middleby demo data):`
      : `Analytics view for ${entry?.title || "this use case"} (Middleby demo data):`;
    return { text: intro, meta: richMeta(topic, catalogFromHub) };
  }

  return null;
}
