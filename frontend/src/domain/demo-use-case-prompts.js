/**
 * Shared demo copy: explore prompts for Playground hubs, Use Case Templates, and chat handoff.
 * Keeps “problem statements” aligned with catalog seed rows via catalogIds (gallery-uc-* or gfi-uc-*).
 *
 * @see docs/DEMO_USE_CASE_PROMPTS.md
 */

import { GOURMET_FOODS_PROFILE_ID, MEDIA_PROFILE_ID, MIDDLEBY_PROFILE_ID, normalizeDemoProfileId } from "./demo-profiles";
import { GFI_USE_CASE_CATALOG_SEED } from "./gfi-demo-seed";
import { MEDIA_USE_CASE_CATALOG_SEED } from "./media-demo-seed";
import { MIDDLEBY_USE_CASE_CATALOG_SEED } from "./middleby-demo-seed";
import { getMiddlebyInsightFollowUpPlaceholder } from "./middleby-composer-hints";
import { USE_CASE_CATALOG_SEED } from "./use-case-catalog-seed";

/**
 * Hand-tuned “This week” / “This quarter” pairs. Keys must match `id` in use-case-catalog-seed.js.
 * If an id is missing here, prompts are auto-generated from that row’s `problemSignal` / `items` / `title`.
 */
export const EXPLORE_PROMPT_OVERRIDES = {
  "gallery-uc-01": [
    {
      lens: "This week",
      horizon: "week",
      text: "Stock levels are falling below safety threshold on multiple SKUs-what should I look at first in the North East?",
    },
    {
      lens: "This quarter",
      horizon: "quarter",
      text: "How are replenishment delays and DC service levels driving stockouts versus last quarter?",
    },
  ],
  "gallery-uc-04": [
    {
      lens: "This week",
      horizon: "week",
      text: "Staffing looks misaligned with foot traffic in several doors-where are the biggest coverage gaps?",
    },
    {
      lens: "This quarter",
      horizon: "quarter",
      text: "How is labor scheduling performance trending against plan, and what structural changes would help?",
    },
  ],
  "gallery-uc-05": [
    {
      lens: "This week",
      horizon: "week",
      text: "Cart abandonment is rising-where are shoppers dropping off in checkout and on mobile?",
    },
    {
      lens: "This quarter",
      horizon: "quarter",
      text: "What funnel and recovery changes would lift e-com conversion sustainably this quarter?",
    },
  ],
  "gallery-uc-06": [
    {
      lens: "This week",
      horizon: "week",
      text: "Transaction failure rates look elevated at some stores-which lanes or payment types are worst?",
    },
    {
      lens: "This quarter",
      horizon: "quarter",
      text: "How is POS stability trending, and what fixes reduce failures and queue time?",
    },
  ],
  "gallery-uc-07": [
    {
      lens: "This week",
      horizon: "week",
      text: "Return volumes are spiking-which products, regions, or reasons should we triage first?",
    },
    {
      lens: "This quarter",
      horizon: "quarter",
      text: "What’s driving return patterns over the quarter and which policy or assortment actions help?",
    },
  ],
};

/**
 * Optional hub icon level per explore prompt (Playground cards).
 * @typedef {'critical' | 'warning' | 'attention'} ExplorePromptSignal
 */

/** Short labels for tooltips (keep in sync with hub UI copy). */
export const EXPLORE_PROMPT_SIGNAL_LABELS = {
  critical: "Urgent near-term focus",
  warning: "Elevated risk or priority gap",
  attention: "Trends and quarterly planning",
};

/** Info tooltip next to “What do you need help with?” (includes icon legend). */
export const EXPLORE_PROMPT_HUB_HELP =
  "These are sample questions you can ask the Enterprise Assistant. They are not use cases or processes; just quick prompts to start a conversation. Icons: red shield, urgent near-term focus (usually This week); orange triangle, elevated risk or priority gap; orange warning circle, trends and quarterly planning (usually This quarter).";

/**
 * Maps each suggested question to a severity-style icon on the Playground hub.
 * Uses explicit `signal` on the prompt when set; otherwise infers from horizon/lens (This week vs This quarter).
 *
 * @param {{ lens?: string, horizon?: string, signal?: ExplorePromptSignal }} prompt
 * @param {number} [indexInCard]
 * @returns {ExplorePromptSignal}
 */
export function inferExplorePromptSignal(prompt, indexInCard = 0) {
  const explicit = prompt?.signal;
  if (explicit === "critical" || explicit === "warning" || explicit === "attention") return explicit;
  const h = String(prompt?.horizon ?? "")
    .trim()
    .toLowerCase();
  if (h === "week") return "critical";
  if (h === "quarter") return "attention";
  const lens = String(prompt?.lens ?? "").toLowerCase();
  if (/\bweek\b/.test(lens)) return "critical";
  if (/\bquarter\b/.test(lens)) return "attention";
  const mod = indexInCard % 3;
  if (mod === 0) return "critical";
  if (mod === 1) return "warning";
  return "attention";
}

/** Plain text for hub composer when prefilling from an explore prompt row. */
export function formatExplorePromptForComposer(prompt) {
  const text = String(prompt?.text ?? "").trim();
  if (!text) return "";
  const lens = String(prompt?.lens ?? "").trim();
  return lens ? `${lens}: ${text}` : text;
}

function autoPromptsFromSeedRow(row) {
  const problem = (row.problemSignal || row.items?.[0] || row.title || "").trim();
  const area = row.businessArea || "operations";
  if (!problem) {
    return [
      { lens: "This week", horizon: "week", text: `What should we prioritize this week for ${area}?` },
      { lens: "This quarter", horizon: "quarter", text: `What program-level improvements matter this quarter in ${area}?` },
    ];
  }
  return [
    {
      lens: "This week",
      horizon: "week",
      text: `${problem} What are the immediate actions and owners?`,
    },
    {
      lens: "This quarter",
      horizon: "quarter",
      text: `${problem} How does this compare to plan and what sustained fixes apply?`,
    },
  ];
}

const SEED_ID_TO_PROMPTS = Object.fromEntries(
  USE_CASE_CATALOG_SEED.map((row) => [
    row.id,
    EXPLORE_PROMPT_OVERRIDES[row.id] ?? autoPromptsFromSeedRow(row),
  ])
);

/** Hand-tuned GFI hub / templates prompts; keys match `id` in gfi-demo-seed.js `GFI_USE_CASE_CATALOG_SEED`. */
const GFI_EXPLORE_PROMPT_OVERRIDES = {
  "gfi-uc-01-perishable-forecast": [
    {
      lens: "This week",
      horizon: "week",
      text: "Cheese and charcuterie are selling through fast in the Northeast but stalling in other regions, which DCs should we re-forecast and reposition first?",
    },
    {
      lens: "This quarter",
      horizon: "quarter",
      text: "How is forecast error driving spoilage write-offs vs. stockouts on short-code SKUs across all 14 DCs compared to last quarter?",
    },
  ],
  "gfi-uc-02-inter-dc-rebalance": [
    {
      lens: "This week",
      horizon: "week",
      text: "Denver is long on Brie while Edison is at risk of stocking out; what temperature-lane transfer should we schedule on our own fleet this week?",
    },
    {
      lens: "This quarter",
      horizon: "quarter",
      text: "What’s the cost of stranded slow movers and emergency spot buys if we don’t automate inter-DC rebalancing across the network?",
    },
  ],
  "gfi-uc-06-multi-temp-routing": [
    {
      lens: "This week",
      horizon: "week",
      text: "Which mixed frozen/chilled/ambient routes are showing temperature excursions or late windows, where should we rebalance loads first?",
    },
    {
      lens: "This quarter",
      horizon: "quarter",
      text: "How much split-shipment and follow-up delivery cost could we remove with better multi-zone routing on GFI trucks?",
    },
  ],
  "gfi-uc-09-fsma-traceability": [
    {
      lens: "This week",
      horizon: "week",
      text: "Mock traceback: pick a high-risk lot; can we reconstruct supplier → DC storage → outbound shipment in under two hours with our current data?",
    },
    {
      lens: "This quarter",
      horizon: "quarter",
      text: "What gaps remain in lot capture at inbound, putaway, and pick paths before FSMA-style traceability expectations fully hit?",
    },
  ],
};

const GFI_SEED_ID_TO_PROMPTS = Object.fromEntries(
  GFI_USE_CASE_CATALOG_SEED.map((row) => [
    row.id,
    GFI_EXPLORE_PROMPT_OVERRIDES[row.id] ?? autoPromptsFromSeedRow(row),
  ])
);

const MIDDLEBY_SEED_ID_TO_PROMPTS = Object.fromEntries(
  MIDDLEBY_USE_CASE_CATALOG_SEED.map((row) => [row.id, autoPromptsFromSeedRow(row)])
);

/** Hand-tuned media (OTT + broadcast) prompts; keys match `id` in media-demo-seed.js. */
const MEDIA_EXPLORE_PROMPT_OVERRIDES = {
  "media-uc-01-content-conversation-trends": [
    {
      lens: "This week",
      horizon: "week",
      text: "What content themes are trending among audiences this week, and which are sustained vs. spikes?",
    },
    {
      lens: "This quarter",
      horizon: "quarter",
      text: "Which conversation themes have kept 4-week growth momentum this quarter, and who is driving them?",
    },
  ],
  "media-uc-02-future-content-opportunity": [
    {
      lens: "This week",
      horizon: "week",
      text: "Based on current audience trends, what content themes should we be exploring for commissioning research?",
    },
    {
      lens: "This quarter",
      horizon: "quarter",
      text: "Which emerging themes are under-represented in our slate and resemble historically successful shows?",
    },
  ],
  "media-uc-03-competitor-content-benchmark": [
    {
      lens: "This week",
      horizon: "week",
      text: "How did competing Indian OTT platforms refresh their libraries this week vs. our release cadence?",
    },
    {
      lens: "This quarter",
      horizon: "quarter",
      text: "Where are competitor libraries out-indexing ours on genre depth and star-cast pull this quarter?",
    },
  ],
  "media-uc-04-digital-market-landscape": [
    {
      lens: "This week",
      horizon: "week",
      text: "What pricing, bundle, or licensing moves did competitors make in the last 7 days that we need to respond to?",
    },
    {
      lens: "This quarter",
      horizon: "quarter",
      text: "How is our platform-level MAU and watch-hour share tracking vs. the top 5 Indian OTT competitors this quarter?",
    },
  ],
  "media-uc-05-content-gap-analysis": [
    {
      lens: "This week",
      horizon: "week",
      text: "Which regional-language genres are we thin on this week where audience watch hours are climbing on other platforms?",
    },
    {
      lens: "This quarter",
      horizon: "quarter",
      text: "What content gap by genre and language should acquisitions and originals close this quarter?",
    },
  ],
  "media-uc-10-aeo-geo-visibility": [
    {
      lens: "This week",
      horizon: "week",
      text: "Where does our brand or titles surface in ChatGPT, Perplexity, and Google AI Overviews this week vs. competitors?",
    },
    {
      lens: "This quarter",
      horizon: "quarter",
      text: "How has our AI-answer presence score trended this quarter, and where is the gap to the category leader widening?",
    },
  ],
  "media-uc-11-subscription-churn-predictor": [
    {
      lens: "This week",
      horizon: "week",
      text: "Which renewal cohorts show the highest churn risk this week, and which save offers should we stage first?",
    },
    {
      lens: "This quarter",
      horizon: "quarter",
      text: "How is post-marquee-title churn trending this quarter, and what content or plan interventions cut voluntary churn?",
    },
  ],
  "media-uc-13-ad-yield-optimization": [
    {
      lens: "This week",
      horizon: "week",
      text: "Where is ad fill rate or eCPM slipping by device or daypart this week, and which demand partners are underperforming?",
    },
    {
      lens: "This quarter",
      horizon: "quarter",
      text: "How is AVOD blended yield tracking against plan this quarter, and which auction-health changes recover the gap?",
    },
  ],
};

const MEDIA_SEED_ID_TO_PROMPTS = Object.fromEntries(
  MEDIA_USE_CASE_CATALOG_SEED.map((row) => [
    row.id,
    MEDIA_EXPLORE_PROMPT_OVERRIDES[row.id] ?? autoPromptsFromSeedRow(row),
  ])
);

function promptsMapForProfile(profileId) {
  const id = normalizeDemoProfileId(profileId);
  if (id === GOURMET_FOODS_PROFILE_ID) return GFI_SEED_ID_TO_PROMPTS;
  if (id === MIDDLEBY_PROFILE_ID) return MIDDLEBY_SEED_ID_TO_PROMPTS;
  if (id === MEDIA_PROFILE_ID) return MEDIA_SEED_ID_TO_PROMPTS;
  return SEED_ID_TO_PROMPTS;
}

/** @returns {{ lens: string, horizon?: string, text: string, signal?: 'critical' | 'warning' | 'attention' }[]} */
export function getExplorePromptsForCatalogId(catalogId, profileId) {
  if (!catalogId) return [];
  const map = promptsMapForProfile(profileId);
  const row = map[catalogId];
  if (!row?.length) return [];
  return row.map((p) => ({ ...p }));
}

/** @returns {{ lens: string, horizon?: string, text: string, signal?: 'critical' | 'warning' | 'attention' }[]} */
export function getExplorePromptsForCatalogEntry(entry) {
  if (!entry) return [];
  if (entry.id) {
    if (SEED_ID_TO_PROMPTS[entry.id]) return SEED_ID_TO_PROMPTS[entry.id];
    if (GFI_SEED_ID_TO_PROMPTS[entry.id]) return GFI_SEED_ID_TO_PROMPTS[entry.id];
    if (MIDDLEBY_SEED_ID_TO_PROMPTS[entry.id]) return MIDDLEBY_SEED_ID_TO_PROMPTS[entry.id];
    if (MEDIA_SEED_ID_TO_PROMPTS[entry.id]) return MEDIA_SEED_ID_TO_PROMPTS[entry.id];
  }
  const line = entry.items?.[0] || entry.title;
  if (!line) return [];
  const t = String(line);
  // Text stays clean; `formatExplorePromptForComposer` prepends the lens once.
  return [
    { lens: "This week", horizon: "week", text: t },
    { lens: "This quarter", horizon: "quarter", text: t },
  ];
}

/**
 * Default hub: four retail cards. Each `catalogId` ties to gallery seed rows.
 */
export const PLAYGROUND_FOCUS_DECKS = [
  {
    id: "pg-ecom",
    catalogId: "gallery-uc-05",
    title: "E-Com",
    actionLabel: "Convert",
    subtitle: "Funnel, cart, and conversion",
  },
  {
    id: "pg-pos",
    catalogId: "gallery-uc-06",
    title: "POS",
    actionLabel: "Diagnose",
    subtitle: "Checkout health and tender stability",
  },
  {
    id: "pg-store-ops",
    catalogId: "gallery-uc-04",
    title: "Store Ops",
    actionLabel: "Improve",
    subtitle: "Labor, traffic, and in-store execution",
  },
  {
    id: "pg-support",
    catalogId: "gallery-uc-07",
    title: "Support",
    actionLabel: "Diagnose",
    subtitle: "Returns and care volume",
  },
];

/**
 * Gourmet Foods International hub: Tier-1 inventory problems + fleet + compliance (catalog_ids match GFI seeds).
 */
export const GFI_PLAYGROUND_FOCUS_DECKS = [
  {
    id: "gfi-deck-forecast",
    catalogId: "gfi-uc-01-perishable-forecast",
    title: "Perishable forecasting",
    actionLabel: "Forecast",
    subtitle: "Short shelf life SKUs across 14 DCs",
  },
  {
    id: "gfi-deck-rebalance",
    catalogId: "gfi-uc-02-inter-dc-rebalance",
    title: "Inter-DC rebalancing",
    actionLabel: "Rebalance",
    subtitle: "Regional stockouts vs. stranded inventory",
  },
  {
    id: "gfi-deck-routing",
    catalogId: "gfi-uc-06-multi-temp-routing",
    title: "Multi-temp routing",
    actionLabel: "Optimize",
    subtitle: "Frozen, chilled, and ambient on one fleet",
  },
  {
    id: "gfi-deck-fsma",
    catalogId: "gfi-uc-09-fsma-traceability",
    title: "FSMA traceability",
    actionLabel: "Trace",
    subtitle: "Lot-level compliance across the network",
  },
];

export const MIDDLEBY_PLAYGROUND_FOCUS_DECKS = [
  {
    id: "mb-deck-copilot",
    catalogId: "middleby-uc-04",
    title: "Dealer copilot",
    actionLabel: "Analyze",
    subtitle: "Quotes that need follow-up today",
  },
  {
    id: "mb-deck-intent",
    catalogId: "middleby-uc-09",
    title: "Buyer intent mix",
    actionLabel: "Analyze",
    subtitle: "Timeframe and consultation split",
  },
  {
    id: "mb-deck-repeat",
    catalogId: "middleby-uc-11",
    title: "Repeat vs new",
    actionLabel: "Analyze",
    subtitle: "Brand loyalty from HubSpot quotes",
  },
  {
    id: "mb-deck-browse",
    catalogId: "middleby-uc-18",
    title: "Browse vs quote",
    actionLabel: "Analyze",
    subtitle: "GA traffic vs HubSpot quotes by brand",
  },
];

export function getPlaygroundFocusDecks(profileId) {
  const id = normalizeDemoProfileId(profileId);
  if (id === GOURMET_FOODS_PROFILE_ID) return GFI_PLAYGROUND_FOCUS_DECKS;
  if (id === MIDDLEBY_PROFILE_ID) return MIDDLEBY_PLAYGROUND_FOCUS_DECKS;
  return PLAYGROUND_FOCUS_DECKS;
}

export function getPlaygroundDeckWithPrompts(deck, profileId) {
  return {
    ...deck,
    prompts: getExplorePromptsForCatalogId(deck.catalogId, profileId),
  };
}

function catalogRowForProfile(catalogId, profileId) {
  const id = normalizeDemoProfileId(profileId);
  if (id === MIDDLEBY_PROFILE_ID) {
    return MIDDLEBY_USE_CASE_CATALOG_SEED.find((r) => r.id === catalogId) ?? null;
  }
  if (id === GOURMET_FOODS_PROFILE_ID) {
    return GFI_USE_CASE_CATALOG_SEED.find((r) => r.id === catalogId) ?? null;
  }
  return USE_CASE_CATALOG_SEED.find((r) => r.id === catalogId) ?? null;
}

/** Grey composer hint on analytics insight follow-up (one line, per use case). */
export function getInsightFollowUpPlaceholder(catalogId, profileId = null, role = null) {
  if (!catalogId) return "Ask a follow-up about this chart…";

  const id = normalizeDemoProfileId(profileId);
  if (id === MIDDLEBY_PROFILE_ID) {
    return getMiddlebyInsightFollowUpPlaceholder(catalogId, role);
  }

  const prompts = getExplorePromptsForCatalogId(catalogId, profileId);
  const quarter = prompts.find((p) => p.horizon === "quarter")?.text?.trim();
  const week = prompts.find((p) => p.horizon === "week")?.text?.trim();
  const fromPrompts = quarter || week;
  if (fromPrompts) {
    return fromPrompts.length > 150 ? `${fromPrompts.slice(0, 147)}…` : fromPrompts;
  }

  const row = catalogRowForProfile(catalogId, profileId);
  const fromRow = String(row?.problemSignal || row?.sampleQuestion || "").trim();
  if (fromRow) return fromRow.length > 150 ? `${fromRow.slice(0, 147)}…` : fromRow;

  return "Ask a follow-up about this chart…";
}
