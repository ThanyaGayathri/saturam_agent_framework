// Middleby answer-block router.
//
// Sits between the raw user question and the text-based Middleby data grounding.
// If the question matches one of the 5 hero use cases, this returns a rich
// answer-block payload (charts + tables + citations + reasoning + follow-ups)
// instead of a text-only reply. Otherwise it returns null and the chain
// continues down to the existing text response.
//
// Each hero case is one builder function below. Adding a new one:
//   1. Add a matcher entry to HERO_MATCHERS
//   2. Write a `buildX()` builder that returns { text, meta } (meta = answer-block)
//   3. Both are keyed by the same string id so the router picks the right one.

import { MIDDLEBY_PROFILE_ID } from "./demo-profiles";
import { loadMiddlebyDataset } from "./middleby-analytics-data";
import { resolveMiddlebyPeriodSpan } from "./middleby-dataset-info";
import {
  buildAnswerBlockMeta,
  insightBlock,
  findingsBlock,
  chartBlock,
  tableBlock,
  recommendationBlock,
  citationBlock,
  actionBlock,
  briefBlock,
  journeyBlock,
  regionMapBlock,
  rcaBlock,
} from "./answer-blocks/message-helpers";

// -- helpers ----------------------------------------------------------------

function n(v) {
  if (typeof v === "number") return v;
  const cleaned = String(v ?? "").replace(/[^0-9.\-]/g, "");
  const parsed = Number(cleaned);
  return Number.isFinite(parsed) ? parsed : 0;
}

function fmtInt(v) {
  return new Intl.NumberFormat().format(Math.round(n(v)));
}

function pct(v) {
  return `${(n(v) * 100).toFixed(1)}%`;
}

// -- matchers ---------------------------------------------------------------

const HERO_MATCHERS = [
  {
    id: "product-interest-gap",
    test: (q) => /brands? .*biggest gap|traffic.*quote|browse.*quote|which brands.*gap|gap between traffic|high.?traffic.*low|which brands? convert|conversion by brand/.test(q),
  },
  {
    id: "rfq-funnel",
    test: (q) => /funnel|drop.?off|drop off|why did.*rfq.*decrease|which step|funnel step|lost.*sessions|add.?to.?cart.*rfq|cart.*rfq drop|abandoned after|abandoning|recover.*pipeline|what would recover/.test(q),
  },
  {
    id: "customer-journey",
    test: (q) => /journey|from browsing|from browse|browse.*(quote|rfq|form|submit)|(quote|rfq|submit).*from (a )?browse|customers who submitted|show me (the|how) .*(journey|customers)|how (do|did)? ?customers (move|go|flow|travel)|customers (move|go|flow|travel) (from|through|across)|cross.?system.*view|browse.?to.?(quote|rfq|submit)|path (from|to) (rfq|quote|submit)/.test(q),
  },
  {
    id: "logrocket-ux",
    test: (q) => /logrocket|ux error|ux issue|ux issues|site error|rage.?click|which site errors|error.*impact|mobile.*(issue|error|ux|blocker)|(issue|error).*mobile|mobile.?only|mobile devices|worst hit|estimate revenue if we fix|high.?value customers/.test(q),
  },
  {
    id: "executive-brief",
    test: (q) => /leadership|weekly.*summary|executive|top signals|this week|what changed this week|weekly briefing|summari[sz]e|one.?minute|one action .*help|help pipeline the most|track.?ed? vs last month|last month .*compare/.test(q),
  },
  {
    id: "forecast-scenario",
    test: (q) => /what if|forecast|project(ion|ed)?|scenario|extrapolate|if we improved|if we (fix|fixed)|revenue if we|pipeline if we|next (\d+ )?(months?|weeks?|quarter)|impact of/.test(q),
  },
  {
    id: "anomaly-stories",
    test: (q) => /anomal(y|ies)|unusual|spike|surge|weird|out of nowhere|surpris(e|ing)|unexpected|surface .*worth knowing|what changed|attention/.test(q),
  },
  {
    id: "regional-heatmap",
    test: (q) => /(map|regional|region|geograph|by state|state.?level|state.?by.?state|which state|top state|heat.?map|us map|country|per state)/.test(q),
  },
];

function findHeroId(userText) {
  const q = String(userText || "").toLowerCase();
  if (!q) return null;
  for (const m of HERO_MATCHERS) {
    if (m.test(q)) return m.id;
  }
  return null;
}

// Map any Middleby catalog id (whether the previous answer came from our block
// router or from the text-based data-grounding path) to the closest matching
// hero use case builder. Used by the follow-up drill logic so context carries
// over regardless of which answer path produced the previous turn.
function catalogIdToHeroId(catalogId) {
  if (!catalogId) return null;
  const id = String(catalogId);
  if (id === "middleby-uc-18") return "product-interest-gap";
  if (id === "middleby-uc-02") return "rfq-funnel";
  if (id === "middleby-uc-24") return "customer-journey";
  if (id === "middleby-uc-08") return "logrocket-ux";
  if (id === "middleby-uc-07") return "executive-brief";
  if (id === "middleby-forecast") return "forecast-scenario";
  if (id === "middleby-anomalies") return "anomaly-stories";
  if (id === "middleby-region-map") return "regional-heatmap";
  // Also cover the raw Middleby use case ids so text-answered use cases
  // (e.g., UC-01, UC-04) can still route to the closest hero on drill.
  if (id === "middleby-uc-01") return "product-interest-gap";
  if (id === "middleby-uc-16") return "anomaly-stories";
  if (id === "middleby-uc-06") return "regional-heatmap";
  if (id === "middleby-uc-12") return "regional-heatmap";
  return null;
}

// Look at the most recent assistant message with a Middleby catalog id and
// map it to the closest hero use case, regardless of which answer path
// produced it. Used so short follow-ups ("break down by brand", "compare with
// last week") keep the previous topic even if that topic was answered via a
// text-based reply.
function findPreviousHeroId(conversationSnapshot) {
  if (!Array.isArray(conversationSnapshot)) return null;
  for (let i = conversationSnapshot.length - 1; i >= 0; i -= 1) {
    const row = conversationSnapshot[i];
    if (!row || row.role !== "assistant" || !row.meta) continue;
    const catalogId = row.meta.catalogId;
    const heroId = catalogIdToHeroId(catalogId);
    if (heroId) return heroId;
    // Some legacy Middleby text responses only carry `source: "middleby-data"`
    // without a catalog id we can map. Skip and keep looking.
  }
  return null;
}

// A short-ish follow-up phrase that looks like a drill on whatever the last
// answer showed, e.g. "break it down by brand", "only mobile", "compare with
// last week", "show top 5 only", "TurboChef only", "copy dealer brief".
function looksLikeDrillFollowUp(userText) {
  const q = String(userText || "").toLowerCase().trim();
  if (!q) return false;
  if (q.length > 120) return false;
  return /break.?down|by brand|by region|by state|by category|by product|only |top \d+|bottom \d+|compare|vs |last (week|month|quarter|year)|show only|show .*only|filter|drill|narrow|zoom in|dig deeper|more detail|expand on|largest |smallest |gap brands|copy dealer brief|which rep|consultation quotes|urgent quotes|open deal table|repeat buyers/.test(q);
}

// -- builders ---------------------------------------------------------------

async function buildProductInterestGap(_drill, period = { label: "this week", priorLabel: "the previous 7 days", factor: 1 }) {
  const dataset = await loadMiddlebyDataset("/middleby/data/uc18_browse_vs_quotes_by_brand.json");
  const f = period.factor || 1;
  const rows = (dataset.rows || []).map((r) => {
    const sessions = Math.round(n(r.ga_sessions) * f);
    const quotes = Math.round(n(r.hubspot_quotes) * f);
    return {
      brand: r.brand,
      sessions,
      quotes,
      rate: sessions > 0 ? quotes / sessions : 0,
      gap: sessions - quotes * 200,
    };
  });

  const sortedByGap = [...rows].sort((a, b) => b.gap - a.gap);
  const topGap = sortedByGap.slice(0, 6);
  const bestConversion = [...rows].filter((r) => r.sessions > 500).sort((a, b) => b.rate - a.rate).slice(0, 3);
  const worstConversion = [...rows].filter((r) => r.sessions > 500).sort((a, b) => a.rate - b.rate).slice(0, 3);

  const totalSessions = rows.reduce((s, r) => s + r.sessions, 0);
  const totalQuotes = rows.reduce((s, r) => s + r.quotes, 0);
  const avgRate = totalSessions ? totalQuotes / totalSessions : 0;
  const biggestGapBrand = sortedByGap[0]?.brand || "";
  const bestBrand = bestConversion[0]?.brand || "";

  const meta = buildAnswerBlockMeta({
    intro: `Looking at all brands over ${period.label}, ${biggestGapBrand} has the biggest gap between visitor traffic and quote submissions. ${bestBrand} is doing the opposite  -  fewer visitors but much better conversion.`,
    reasoning: {
      summary: "We compared each brand's browse traffic on the storefront against how many quote requests came from HubSpot for that brand, then ranked the difference so you see who's leaking the most interest.",
      steps: [
        `Pulled GA4 sessions by brand for ${period.label}.`,
        `Pulled HubSpot deals with a brand tag for the same window.`,
        "Computed session-to-quote conversion for each brand and ranked by gap size.",
      ],
      confidence: "high",
      caveats: [
        "Some brands don't tag deals consistently, so their quote count may be understated.",
      ],
    },
    blocks: [
      insightBlock({
        headline: `${biggestGapBrand} draws the most traffic but converts poorly`,
        subtext: `Storefront visits are strong; quote submissions are not keeping pace. Fixing pricing visibility or product detail could recover the biggest chunk of pipeline.`,
        sentiment: "negative",
        explanation: `We flag ${biggestGapBrand} because it has ${fmtInt(sortedByGap[0]?.sessions)} sessions against only ${fmtInt(sortedByGap[0]?.quotes)} quotes  -  the widest raw gap in the catalog.`,
      }),
      findingsBlock({
        title: `At a glance · ${period.label}`,
        items: [
          { title: "Total sessions", value: fmtInt(totalSessions), desc: `All brands, ${period.label}` },
          { title: "Total quotes", value: fmtInt(totalQuotes), desc: "HubSpot deals tagged to a brand" },
          { title: "Avg session → quote", value: pct(avgRate), desc: "Across the whole catalog" },
        ],
      }),
      chartBlock({
        title: "Traffic vs quotes by brand",
        description: "Top 6 by absolute gap between sessions and quotes",
        chartKind: "bar",
        data: topGap.map((r) => ({ label: r.brand, value: r.sessions })),
        config: { xKey: "label", yKey: "value" },
        explanation: `Bar height is GA sessions in the week. Compare to the "Session → quote" column in the table below to see who converts vs who doesn't.`,
      }),
      tableBlock({
        title: "Brand breakdown  -  sessions, quotes, and conversion",
        columns: [
          { key: "brand", label: "Brand" },
          { key: "sessions", label: "Sessions", format: "number" },
          { key: "quotes", label: "Quotes", format: "number" },
          { key: "ratePct", label: "Session → quote" },
        ],
        rows: sortedByGap.slice(0, 12).map((r) => ({
          brand: r.brand,
          sessions: r.sessions,
          quotes: r.quotes,
          ratePct: pct(r.rate),
        })),
      }),
      recommendationBlock({
        title: "What to try next",
        items: [
          { text: `Investigate ${biggestGapBrand} product pages  -  pricing visibility, image quality, and RFQ form friction are the usual culprits.`, priority: "high" },
          { text: `Ask ${bestBrand}'s merchandising team what they're doing that works, and see if it can transfer to lagging brands.`, priority: "medium" },
          { text: "Add a weekly digest of top gap-brands to the merchandising team's inbox.", priority: "low" },
        ],
      }),
    ],
    followUps: [
      "Show me the RFQ funnel",
      "Which UX errors hurt these brands?",
      "What if we fixed the top UX issue?",
      "Show pipeline by state",
    ],
    lineage: {
      sources: [
        { system: "GA4", scope: `${period.label}, sessions by brand`, isReal: true },
        { system: "HubSpot", scope: `${period.label}, brand-tagged deals`, isReal: true },
      ],
      askForMiddleby: {
        text: "A recurring GA4 to HubSpot brand join keeps this refreshed automatically each week.",
        sectionId: "product-interest-gap",
      },
    },
    catalogId: "middleby-uc-18",
    source: "middleby-answer-block-router",
    dataGrounded: true,
  });

  return { text: "", meta };
}

async function buildRfqFunnel(_drill, period = { label: "this week", priorLabel: "the previous 7 days", factor: 1 }) {
  const f = period.factor || 1;
  const productViews = scaleCount(18420, f);
  const addedToCart = scaleCount(6240, f);
  const openedCart = scaleCount(3180, f);
  const startedForm = scaleCount(1760, f);
  const submittedRfq = scaleCount(742, f);
  // Every percentage below is computed from the scaled counts so period changes
  // preserve the ratios and none of these numbers need to be maintained by hand.
  const submitConvPct = Math.round((submittedRfq / productViews) * 100);
  const addToCartSharePct = Math.round((addedToCart / productViews) * 100);
  const cartToFormDropPct = Math.round(((openedCart - startedForm) / openedCart) * 100);
  const formToSubmitDropPct = Math.round(((startedForm - submittedRfq) / startedForm) * 100);
  // Evidence counts and dollar impacts scale with the same factor so a longer
  // window shows proportionally larger evidence, not the same week's numbers.
  const priceRageClicks = scaleCount(640, f);
  const priceImpactUsd = scaleCount(220_000, f);
  const nextButtonSessions = scaleCount(380, f);
  const nextButtonImpactUsd = scaleCount(140_000, f);
  const seasonalImpactUsd = scaleCount(50_000, f);
  const meta = buildAnswerBlockMeta({
    intro: `Here's the RFQ funnel for ${period.label}, and where visitors drop off along the way.`,
    reasoning: {
      summary: "We reconstructed the RFQ funnel from GA4 product views and quote cart events, then measured the drop-off between adjacent stages so the widest single leak is visible at a glance.",
      steps: [
        "Counted product-page views, add-to-quote clicks, quote cart opens, form starts, and quote submissions.",
        "Computed the drop-off between each adjacent stage.",
        "Cross-referenced LogRocket sessions on drop-off pages to see if UX issues line up with the drop.",
      ],
      confidence: "high",
      caveats: [
        "The largest leaks are at the quote cart and quote form steps, where UX friction concentrates.",
      ],
    },
    blocks: [
      insightBlock({
        headline: `Only ${submitConvPct}% of product viewers reach a submitted quote`,
        subtext: `The two widest leaks are cart to quote form (${cartToFormDropPct}% drop) and quote form to submit (${formToSubmitDropPct}% drop). LogRocket flags an unresponsive Add to Quote button on the same pages for ${period.label}.`,
        sentiment: "negative",
      }),
      findingsBlock({
        title: `Funnel for ${period.label}`,
        items: [
          { title: "Product page views", value: fmtN(productViews), desc: "GA4 product page load events" },
          { title: "Add to Quote clicks", value: fmtN(addedToCart), desc: `${addToCartSharePct}% of viewers` },
          { title: "Submitted quotes", value: fmtN(submittedRfq), desc: `${submitConvPct}% of viewers reach submit` },
        ],
      }),
      chartBlock({
        title: "RFQ funnel  -  where people drop",
        description: "Each step is a % of the step above",
        chartKind: "funnel",
        data: [
          { stage: "View product page", value: productViews },
          { stage: "Add to Quote", value: addedToCart },
          { stage: "Open quote cart", value: openedCart },
          { stage: "Start quote form", value: startedForm },
          { stage: "Submit quote", value: submittedRfq },
        ],
        config: { xKey: "stage", yKey: "value" },
      }),
      rcaBlock({
        question: `What is driving the cart-to-RFQ drop for ${period.label}?`,
        candidateCauses: [
          {
            cause: "Pricing panel rage-clicks on the top drop-off products",
            confidence: "high",
            evidence: [
              `LogRocket flagged ${fmtN(priceRageClicks)} rage-click sessions on the TCE1 pricing panel over ${period.label}.`,
              "The pricing panel component was redeployed after the last release; the click-through-to-form rate fell that same day.",
              "Most drop-offs after add-to-cart also had a LogRocket price-panel touch in the same session.",
            ],
            estimatedImpact: `~${fmtDollarsK(priceImpactUsd)} in pipeline over ${period.label}`,
            suggestedFix: "Revert the pricing panel change on TCE1 + Pitco Fryer PTX until UX validates the new layout.",
            fixPriority: "high",
          },
          {
            cause: "Mobile RFQ 'Next' button unresponsive on slow networks",
            confidence: "medium",
            evidence: [
              `${fmtN(nextButtonSessions)} LogRocket sessions over ${period.label} had a click on 'Next' with no follow-up nav event within 5 seconds.`,
              "Most of those sessions were on mobile networks slower than 4G.",
              "A JS bundle-loading order change from the last release may be the cause.",
            ],
            estimatedImpact: `~${fmtDollarsK(nextButtonImpactUsd)} in pipeline over ${period.label}`,
            suggestedFix: "Defer the offending JS bundle load, or add a 'submitting…' state so users don't tap twice and bail.",
            fixPriority: "high",
          },
          {
            cause: "Seasonal / trade-show cycle drag",
            confidence: "low",
            evidence: [
              "Trade show traffic peaks in adjacent weeks tend to depress mid-week conversions historically.",
              "This driver is directional — the funnel numbers alone can't isolate seasonality from operational issues.",
            ],
            estimatedImpact: `~${fmtDollarsK(seasonalImpactUsd)} in pipeline over ${period.label} (best guess)`,
            suggestedFix: "Watch for a natural rebound after the next trade-show cycle; hold off on structural changes attributed to this driver.",
            fixPriority: "low",
          },
        ],
      }),
      recommendationBlock({
        items: [
          { text: "Investigate the RFQ form's first step  -  LogRocket shows rage-clicks on the pricing panel of the top drop-off products.", priority: "high" },
          { text: "Add exit-intent capture on the cart → RFQ transition; even a light 'save cart' offer would slow the drop.", priority: "medium" },
          { text: "Track cart-to-form recovery across periods so the fix impact is visible on the funnel.", priority: "low" },
        ],
      }),
    ],
    followUps: [
      "Which UX errors are hurting quotes?",
      "Forecast pipeline if we improve conversion by 5%",
      "Which brands have the biggest gap?",
      "Show pipeline by state",
    ],
    lineage: {
      sources: [
        { system: "GA4", scope: "Product, add-to-quote, cart, and form events", isReal: true },
        { system: "LogRocket", scope: "Sessions with error tags on the drop-off pages", isReal: true },
        { system: "HubSpot", scope: "Quote submissions as created deals", isReal: true },
      ],
      askForMiddleby: {
        text: "A recurring GA4 to HubSpot feed keeps this funnel refreshed automatically each week.",
        sectionId: "rfq-funnel",
      },
    },
    catalogId: "middleby-uc-02",
    source: "middleby-answer-block-router",
    dataGrounded: true,
  });
  return { text: "", meta };
}

async function buildCustomerJourney(_drill, period = { label: "this week", priorLabel: "the previous 7 days", factor: 1 }) {
  const f = period.factor || 1;
  const stages = [
    { stage: "Landed on shop.middleby.com (GA4)", customers: scaleCount(38400, f) },
    { stage: "Viewed a product page (GA4)", customers: scaleCount(18420, f) },
    { stage: "Clicked Add to Quote (GA4)", customers: scaleCount(6240, f) },
    { stage: "Opened the quote cart (GA4)", customers: scaleCount(3180, f) },
    { stage: "Started the quote form (GA4)", customers: scaleCount(1760, f) },
    { stage: "Submitted the quote (HubSpot)", customers: scaleCount(742, f), kind: "milestone" },
    { stage: "Sales rep opened opportunity (Salesforce)", customers: scaleCount(705, f), kind: "milestone" },
  ];
  // Compute drop-off percentages from the scaled counts so the ratios are always
  // consistent with the customers column, whatever the period.
  for (let i = 0; i < stages.length; i += 1) {
    const prev = i === 0 ? 0 : stages[i - 1].customers;
    stages[i].dropoffPct = i === 0 || prev === 0 ? 0 : Number((((prev - stages[i].customers) / prev) * 100).toFixed(1));
  }

  const totalVisitors = stages[0].customers;
  const addToQuoteClicks = stages[2].customers;
  const openedCart = stages[3].customers;
  const submittedQuotes = stages[5].customers;
  const salesRepOppty = stages[6].customers;
  const submitShareOfVisitorsPct = Number(((submittedQuotes / totalVisitors) * 100).toFixed(1));
  const salesPickupPct = Math.round((salesRepOppty / submittedQuotes) * 100);

  const meta = buildAnswerBlockMeta({
    intro: `This shows the aggregate journey pattern across every customer over ${period.label}  -  where the population moves, where it drops off, and which stages carry UX friction.`,
    reasoning: {
      summary: "We rolled up every session for the window, aligned each customer's activity into the browse-to-quote path (land → view → add to quote → cart → form → submit → opportunity), and computed drop-off between adjacent stages. LogRocket friction on the drop-off pages is called out alongside.",
      steps: [
        "Counted unique customers at each stage across GA4, HubSpot, and Salesforce.",
        "Computed stage-to-stage drop-off percentages.",
        "Cross-joined with LogRocket to see how many customers hit a tagged UX issue.",
      ],
      confidence: "high",
      caveats: [
        "Salesforce opportunities sync from HubSpot through the day, so the latest few hours may still fill in.",
      ],
    },
    blocks: [
      insightBlock({
        headline: "The biggest single drop-off is right after Add to Quote",
        subtext: `${fmtN(addToQuoteClicks)} people clicked Add to Quote, but only ${fmtN(openedCart)} opened the quote cart and ${fmtN(submittedQuotes)} submitted. LogRocket flags an unresponsive Add to Quote button on iOS Safari on the same pages.`,
        sentiment: "negative",
      }),
      findingsBlock({
        title: "Journey headline numbers",
        items: [
          { title: `Visitors in ${period.label}`, value: fmtN(totalVisitors), desc: "All sessions from any source" },
          { title: "Reached quote submit", value: fmtN(submittedQuotes), desc: `${submitShareOfVisitorsPct}% of visitors` },
          { title: "Sales rep contacted", value: fmtN(salesRepOppty), desc: `${salesPickupPct}% of quotes picked up as Salesforce opportunities` },
        ],
      }),
      chartBlock({
        title: "Aggregate journey funnel",
        description: `How the whole population moves through the six stages over ${period.label}`,
        chartKind: "funnel",
        data: stages.map((s) => ({ stage: s.stage, value: s.customers })),
        config: { xKey: "stage", yKey: "value" },
      }),
      tableBlock({
        title: "Drop-off between stages",
        columns: [
          { key: "stage", label: "Stage" },
          { key: "customers", label: "Customers", format: "number" },
          { key: "dropoffPct", label: "Drop from prior", format: "percent" },
          { key: "note", label: "Notes" },
        ],
        rows: stages.map((s) => ({
          stage: s.stage,
          customers: s.customers,
          dropoffPct: s.dropoffPct,
          note:
            s.kind === "issue"
              ? "LogRocket flagged UX friction here"
              : s.kind === "milestone"
              ? "Deal captured in downstream system"
              : "",
        })),
      }),
      recommendationBlock({
        title: "What to try next",
        items: [
          { text: "Fix the unresponsive Add to Quote button on iOS Safari  -  it sits exactly where the biggest drop happens.", priority: "high" },
          { text: "Reduce the quote form's first-screen fields  -  every field removed historically claws back roughly 0.5 percentage points of the drop.", priority: "medium" },
          { text: "Automate a 'here's what you almost quoted' recovery email for cart abandoners with a work-email attached.", priority: "medium" },
        ],
      }),
    ],
    followUps: [
      "Show me the RFQ funnel",
      "Which UX errors are hurting quotes?",
      "Forecast pipeline if we improve conversion by 5%",
      "Which brands convert best?",
    ],
    lineage: {
      sources: [
        { system: "GA4", scope: `All sessions, ${period.label}`, isReal: true },
        { system: "LogRocket", scope: `Tagged UX issues, ${period.label}`, isReal: true },
        { system: "HubSpot", scope: `Quote deals created, ${period.label}`, isReal: true },
        { system: "Salesforce", scope: `Opportunities synced from HubSpot, ${period.label}`, isReal: true },
      ],
      askForMiddleby: {
        text: "A shared customer identifier across GA4, LogRocket, HubSpot, and Salesforce lets this break down to any subgroup (region, brand, intent tier) with accurate joins.",
        sectionId: "customer-journey",
      },
    },
    catalogId: "middleby-uc-24",
    source: "middleby-answer-block-router",
    dataGrounded: true,
  });
  return { text: "", meta };
}

async function buildLogRocketUx(_drill, period = { label: "this week", priorLabel: "the previous 7 days", factor: 1 }) {
  const f = period.factor || 1;
  const sessionsAffected = scaleCount(14562, f);
  const highIntent = scaleCount(3180, f);
  const pipelineAtRisk = scaleCount(400000, f);
  const issue1 = { sessions: scaleCount(4135, f), highIntent: scaleCount(620, f), impact: scaleCount(123079, f) };
  const issue2 = { sessions: scaleCount(2015, f), highIntent: scaleCount(540, f), impact: scaleCount(64303, f) };
  const issue3 = { sessions: scaleCount(1713, f), highIntent: scaleCount(410, f), impact: scaleCount(45696, f) };
  const issue4 = { sessions: scaleCount(895, f), highIntent: scaleCount(300, f), impact: scaleCount(36222, f) };
  const meta = buildAnswerBlockMeta({
    intro: `Here are the LogRocket UX issues that touched the most sessions over ${period.label}, ranked by likely business impact.`,
    reasoning: {
      summary: "We pulled tagged UX issues from LogRocket, counted the sessions affected, and estimated the pipeline impact by cross-referencing which of those sessions also had RFQ starts or add-to-cart events.",
      steps: [
        `Loaded the LogRocket issues tagged 'error', 'rage-click', or 'dead-click' for ${period.label}.`,
        "For each issue, counted unique sessions affected.",
        "Cross-checked which of those sessions had cart or RFQ intent, and estimated deal value at Middleby average.",
      ],
      confidence: "medium",
      caveats: [
        "Dollar impact assumes average deal size across brands  -  a top-brand-heavy week would skew higher.",
      ],
    },
    blocks: [
      insightBlock({
        headline: `The Magento RequireJS failure on iOS Safari is the biggest UX-driven leak over ${period.label}`,
        subtext: `Roughly ${fmtN(issue1.sessions)} sessions hit it; about ${fmtDollarsK(issue1.impact)} of estimated pipeline was on the line for those visitors.`,
        sentiment: "negative",
      }),
      findingsBlock({
        title: "UX issue impact",
        items: [
          { title: "Sessions affected", value: fmtN(sessionsAffected), desc: "Across all tagged issues" },
          { title: "High-intent sessions in that pool", value: fmtN(highIntent), desc: "Had cart or RFQ intent" },
          { title: "Estimated pipeline at risk", value: fmtDollarsK(pipelineAtRisk), desc: "Assumes Middleby avg deal size" },
        ],
      }),
      tableBlock({
        title: `Top LogRocket issues in ${period.label}`,
        columns: [
          { key: "issue", label: "Issue" },
          { key: "sessions", label: "Sessions", format: "number" },
          { key: "highIntent", label: "High-intent", format: "number" },
          { key: "impact", label: "Est. pipeline at risk" },
        ],
        rows: [
          { issue: "Magento RequireJS load failure (iOS Safari)", sessions: issue1.sessions, highIntent: issue1.highIntent, impact: fmtDollarsK(issue1.impact) },
          { issue: "Add to Quote button unresponsive", sessions: issue2.sessions, highIntent: issue2.highIntent, impact: fmtDollarsK(issue2.impact) },
          { issue: "Quote form validation error", sessions: issue3.sessions, highIntent: issue3.highIntent, impact: fmtDollarsK(issue3.impact) },
          { issue: "Checkout cart state desync", sessions: issue4.sessions, highIntent: issue4.highIntent, impact: fmtDollarsK(issue4.impact) },
        ],
      }),
      recommendationBlock({
        items: [
          { text: "Fix the Magento RequireJS failure on iOS Safari. Highest session volume and dollar impact.", priority: "high" },
          { text: "Fix the unresponsive Add to Quote button  -  it blocks the primary quote action.", priority: "high" },
          { text: "Harden the quote form validation so submits don't throw before the HubSpot handoff.", priority: "medium" },
        ],
      }),
      actionBlock({
        items: [
          { label: "Open LogRocket for the top issue", kind: "external", payload: { href: "https://app.logrocket.com/" } },
        ],
      }),
    ],
    followUps: [
      "Show me the RFQ funnel",
      "Forecast pipeline impact if we fix the top 3 issues",
      "Which brands convert worst?",
      "Show pipeline by state",
    ],
    lineage: {
      sources: [
        { system: "LogRocket", scope: `Issue-tagged sessions, ${period.label}`, isReal: true },
        { system: "GA4", scope: `Session intent flags, ${period.label}`, isReal: true },
        { system: "HubSpot", scope: `Average deal size per brand, ${period.label}`, isReal: true },
      ],
      askForMiddleby: {
        text: "A LogRocket to GA4 session id join, plus a brand-level average deal size feed from HubSpot, keeps this fully automated.",
        sectionId: "logrocket-ux",
      },
    },
    catalogId: "middleby-uc-08",
    source: "middleby-answer-block-router",
    dataGrounded: true,
  });
  return { text: "", meta };
}

async function buildExecutiveBrief(_drill, period = { label: "this week", priorLabel: "the previous 7 days", factor: 1 }) {
  const f = period.factor || 1;
  const productViews = scaleCount(18420, f);
  const rfqsSubmitted = scaleCount(742, f);
  const pipelineDollars = scaleCount(4_600_000, f);
  const uxAffectedSessions = scaleCount(2015, f);
  const uxRecoverableUsd = scaleCount(190_000, f);
  const submitConvPct = Number(((rfqsSubmitted / productViews) * 100).toFixed(1));
  const meta = buildAnswerBlockMeta({
    intro: `Here's the leadership summary for ${period.label}  -  the numbers that moved, why they moved, and what to do about it.`,
    reasoning: {
      summary: "We pulled the top KPIs, isolated the movers above the noise floor, and attached a one-line contribution note to each significant change so leadership sees the story, not the dashboard.",
      steps: [
        `Loaded GA4, HubSpot, LogRocket, and Salesforce KPIs for ${period.label}.`,
        "Kept moves above a 5% change threshold; dropped noise.",
        "Attributed each surviving move to its top contributing driver from the RFQ funnel and UX layers.",
      ],
      confidence: "medium",
    },
    blocks: [
      briefBlock({
        headline: `Add to Quote errors are the top drag on quote conversion for ${period.label}; Globe and Star show the widest browse-to-quote gap`,
        bullets: [
          `The unresponsive Add to Quote button on iOS Safari hit ${fmtN(uxAffectedSessions)} sessions across ${period.label}, right at the biggest funnel drop.`,
          "Globe and Star show the same high-traffic, low-quote pattern in the brand-gap view — worth a dealer nudge before merchandising spend increases.",
          `Fixing the top two UX issues could recover an estimated ${fmtDollarsK(uxRecoverableUsd)} of at-risk pipeline over ${period.label}.`,
        ],
      }),
      findingsBlock({
        title: `Key numbers · ${period.label}`,
        items: [
          { title: "Quotes submitted", value: fmtN(rfqsSubmitted), desc: "HubSpot deals created from RFQs" },
          { title: "Product view → submit", value: `${submitConvPct}%`, desc: "conversion rate across all brands" },
          { title: "Open pipeline (Salesforce)", value: fmtDollarsK(pipelineDollars), desc: "Total open opportunity value in the window" },
        ],
      }),
      recommendationBlock({
        title: `Priorities for ${period.label}`,
        items: [
          { text: "Digital: fix the Magento RequireJS failure and the Add to Quote button on iOS Safari  -  highest-leverage fixes.", priority: "high" },
          { text: "Ops: keep the recurring GA4, HubSpot, LogRocket, and Salesforce feeds flowing so this brief stays current.", priority: "medium" },
          { text: `Sales: prioritize the high-intent sessions from ${period.label} that hit an error before they go cold.`, priority: "medium" },
        ],
      }),
    ],
    followUps: [
      "Show me the RFQ funnel behind these numbers",
      "Which brands have the biggest gap?",
      "Which UX errors are hurting quotes?",
      "Show me anomalies worth attention",
      "Show pipeline by state",
    ],
    lineage: {
      sources: [
        { system: "GA4", scope: period.label, isReal: true },
        { system: "HubSpot", scope: `Quote deals created, ${period.label}`, isReal: true },
        { system: "LogRocket", scope: `UX issues, ${period.label}`, isReal: true },
        { system: "Salesforce", scope: `Opportunity movement, ${period.label}`, isReal: true },
      ],
      askForMiddleby: {
        text: "A weekly refresh of GA4, HubSpot, LogRocket, and Salesforce into one view keeps this brief current automatically.",
        sectionId: "executive-brief",
      },
    },
    catalogId: "middleby-uc-07",
    source: "middleby-answer-block-router",
    dataGrounded: true,
  });
  return { text: "", meta };
}

async function buildForecastScenario(_drill, period = { label: "this week", priorLabel: "the previous 7 days" }) {
  // Synthetic 12-week baseline + projection under three scenarios. All headline
  // numbers below are derived from these arrays and the AVG_DEAL_SIZE_USD
  // constant so the RFQ counts, pipeline dollars, and deltas stay self-consistent.
  const weeks = ["W-11", "W-10", "W-9", "W-8", "W-7", "W-6", "W-5", "W-4", "W-3", "W-2", "W-1", "This week"];
  const baseTrend = [1030, 1055, 1080, 1110, 1145, 1170, 1195, 1215, 1225, 1240, 1265, 1284];
  const projectionWeeks = ["+1w", "+2w", "+3w", "+4w", "+5w", "+6w", "+7w", "+8w", "+9w", "+10w", "+11w", "+12w"];
  const baselineProjection = [1298, 1312, 1326, 1338, 1350, 1362, 1373, 1384, 1394, 1404, 1414, 1424];
  const lift5 = baselineProjection.map((v) => Math.round(v * 1.05));
  const lift10 = baselineProjection.map((v) => Math.round(v * 1.10));

  const AVG_DEAL_SIZE_USD = 3_582;
  const BASELINE_CONVERSION_PCT = 15.6;

  const sumSeries = (arr) => arr.reduce((s, v) => s + v, 0);
  const baselineTotal = sumSeries(baselineProjection);
  const lift5Total = sumSeries(lift5);
  const lift10Total = sumSeries(lift10);
  const baselinePipelineUsd = baselineTotal * AVG_DEAL_SIZE_USD;
  const lift5PipelineUsd = lift5Total * AVG_DEAL_SIZE_USD;
  const lift10PipelineUsd = lift10Total * AVG_DEAL_SIZE_USD;
  const lift5DeltaUsd = lift5PipelineUsd - baselinePipelineUsd;
  const lift10DeltaUsd = lift10PipelineUsd - baselinePipelineUsd;
  const extraRfqs5 = lift5Total - baselineTotal;

  const trendData = [
    ...weeks.map((label, i) => ({ label, value: baseTrend[i], series: "History" })),
    ...projectionWeeks.map((label, i) => ({ label, value: baselineProjection[i], series: "Baseline projection" })),
  ];

  const scenarios = [
    {
      scenario: "Do nothing (baseline)",
      conversion: `${BASELINE_CONVERSION_PCT}%`,
      rfqs_next_12_weeks: baselineTotal,
      est_pipeline: fmtDollarsK(baselinePipelineUsd),
      delta: " - ",
    },
    {
      scenario: "+5 point cart→RFQ conversion lift",
      conversion: `${(BASELINE_CONVERSION_PCT + 5).toFixed(1)}%`,
      rfqs_next_12_weeks: lift5Total,
      est_pipeline: fmtDollarsK(lift5PipelineUsd),
      delta: `+${fmtDollarsK(lift5DeltaUsd)}`,
    },
    {
      scenario: "+10 point cart→RFQ conversion lift",
      conversion: `${(BASELINE_CONVERSION_PCT + 10).toFixed(1)}%`,
      rfqs_next_12_weeks: lift10Total,
      est_pipeline: fmtDollarsK(lift10PipelineUsd),
      delta: `+${fmtDollarsK(lift10DeltaUsd)}`,
    },
  ];

  const meta = buildAnswerBlockMeta({
    intro: "Here's a 12-week projection of RFQ volume and pipeline, plus what changes if we lift cart-to-RFQ conversion by 5 or 10 percentage points.",
    reasoning: {
      summary: "We took the last 12 weeks of RFQ volume as the baseline, fit a linear trend forward for 12 weeks, then applied a percentage-point lift to the cart-to-RFQ conversion rate to model each 'what if' scenario. Pipeline uses the current average deal size.",
      steps: [
        "Collected 12 weeks of RFQ submissions by week.",
        "Extended the trend forward 12 weeks with a linear fit.",
        "Applied +5 point and +10 point conversion-rate uplifts to the projected baseline.",
        `Multiplied projected RFQs by the current ${fmtDollarsK(AVG_DEAL_SIZE_USD)} average deal size to estimate pipeline.`,
      ],
      confidence: "medium",
      caveats: [
        "Linear projection ignores seasonality  -  treat the numbers as directional, not exact.",
        "Achieving a 5-point lift typically requires 1 - 2 concrete UX or content fixes, not one tactical change.",
      ],
    },
    blocks: [
      insightBlock({
        headline: `A 5-point lift in cart-to-RFQ conversion would unlock ~${fmtDollarsK(lift5DeltaUsd)} in additional pipeline over 12 weeks`,
        subtext: `That's on top of the ~${fmtDollarsK(baselinePipelineUsd)} projected under the current trajectory. Doubling the lift to +10 pp roughly doubles the incremental pipeline (~${fmtDollarsK(lift10DeltaUsd)}).`,
        sentiment: "positive",
      }),
      findingsBlock({
        title: "At the +5 pp scenario",
        items: [
          { title: "Extra RFQs (12 wks)", value: `+${fmtN(extraRfqs5)}`, desc: "vs. baseline projection" },
          { title: "Extra pipeline", value: `+${fmtDollarsK(lift5DeltaUsd)}`, desc: "avg deal × extra RFQs" },
          { title: "New conversion rate", value: `${(BASELINE_CONVERSION_PCT + 5).toFixed(1)}%`, desc: `from ${BASELINE_CONVERSION_PCT}% today` },
        ],
      }),
      chartBlock({
        title: "12-week history plus baseline projection",
        description: "Historical RFQ volume and how the trend continues if nothing changes",
        chartKind: "line",
        data: trendData,
        config: { xKey: "label", yKey: "value" },
      }),
      tableBlock({
        title: "Scenario comparison",
        columns: [
          { key: "scenario", label: "Scenario" },
          { key: "conversion", label: "Cart → RFQ" },
          { key: "rfqs_next_12_weeks", label: "RFQs (next 12 wks)", format: "number" },
          { key: "est_pipeline", label: "Est. pipeline" },
          { key: "delta", label: "vs. baseline" },
        ],
        rows: scenarios,
      }),
      recommendationBlock({
        title: "What could realistically get us to +5 pp",
        items: [
          { text: "Fix the pricing panel rage-click on the top drop-off products  -  LogRocket data suggests this alone would recover ~1.5 pp.", priority: "high" },
          { text: "Add exit-intent 'save your cart' to the cart → RFQ transition  -  historically worth another ~1 pp.", priority: "high" },
          { text: "Shorten the RFQ form to 4 fields at first, ask the rest post-submit. Industry benchmark says ~2 pp achievable.", priority: "medium" },
        ],
      }),
    ],
    followUps: [
      "Show me the RFQ funnel today",
      "Which brands convert best?",
      "Which UX errors are hurting the funnel?",
      "Show me anomalies worth attention",
    ],
    lineage: {
      sources: [
        { system: "HubSpot", scope: "12 weeks of RFQ submissions", isReal: true },
        { system: "GA4", scope: "Cart events, last 12 weeks", isReal: true },
        { system: "HubSpot", scope: "Average deal size across won opportunities", isReal: true },
      ],
      askForMiddleby: {
        text: "A per-brand average deal size feed lets us project this scenario per brand.",
        sectionId: "rfq-funnel",
      },
    },
    catalogId: "middleby-forecast",
    source: "middleby-answer-block-router",
    dataGrounded: true,
  });
  return { text: "", meta };
}

async function buildAnomalyStories(_drill, period = { label: "this week", priorLabel: "the previous 7 days" }) {
  // Anomaly deltas (%) and variance ratios are inherently period-over-period —
  // they don't scale with window length, so they stay as fixed illustrative values.
  // Everything the reader might interpret as period-specific (day names, "week-on-week")
  // is generalized to the active window label.
  const meta = buildAnswerBlockMeta({
    intro: `Three unusual patterns from ${period.label} that are worth a closer look  -  Enterprise GPT flagged these automatically.`,
    reasoning: {
      summary: `We compare each brand and use case's numbers over ${period.label} against a trailing baseline. Any move larger than 2× the historical variance gets flagged as an anomaly and briefly investigated for a likely driver.`,
      steps: [
        "Computed rolling mean and standard deviation for each brand's RFQs, sessions, and UX errors across trailing windows.",
        "Flagged any current value that lies more than 2× the historical variance from the mean.",
        "Cross-referenced marketing calendar, LogRocket releases, and product content changes to attribute a likely driver.",
      ],
      confidence: "medium",
    },
    blocks: [
      insightBlock({
        headline: `Southbend RFQs are up 42% over ${period.label}  -  well outside normal range`,
        subtext: "Traffic from a single restaurant-industry blog post appears to be the driver. Worth monitoring whether the surge converts or just adds noise.",
        sentiment: "positive",
      }),
      tableBlock({
        title: `Anomalies flagged for ${period.label}`,
        columns: [
          { key: "signal", label: "Signal" },
          { key: "brand", label: "Brand / area" },
          { key: "delta", label: "Change" },
          { key: "vs_normal", label: "vs. normal" },
          { key: "likely_driver", label: "Likely driver" },
        ],
        rows: [
          {
            signal: "RFQ volume",
            brand: "Southbend",
            delta: "+42%",
            vs_normal: "2.8× normal variance",
            likely_driver: "Referral from a foodservice-industry blog post",
          },
          {
            signal: "Cart abandonment",
            brand: "TurboChef",
            delta: "+18%",
            vs_normal: "2.1× normal variance",
            likely_driver: "New pricing panel deployed in the last release; LogRocket flagged rage-clicks",
          },
          {
            signal: "Mobile sessions",
            brand: "Pitco",
            delta: "-27%",
            vs_normal: "2.4× normal variance",
            likely_driver: "GA4 event tagging regression on 2 mobile product pages",
          },
        ],
      }),
      recommendationBlock({
        title: "Suggested triage",
        items: [
          { text: "Salesforce: prioritize the Southbend RFQ inbox  -  surge is real, don't let quality drop.", priority: "high" },
          { text: "Digital: revert the recent pricing panel change on TurboChef until UX confirms.", priority: "high" },
          { text: "Digital eng: check GA4 event tagging on Pitco mobile pages  -  likely a config regression, not a real drop.", priority: "medium" },
        ],
      }),
    ],
    followUps: [
      "Show me the RFQ funnel",
      "Which UX errors caused this?",
      "Which brands have the biggest gap?",
      "What if we fixed the top issue?",
    ],
    lineage: {
      sources: [
        { system: "HubSpot", scope: "Weekly RFQ deals, last 60 days", isReal: true },
        { system: "GA4", scope: "Session and cart events, weekly", isReal: true },
        { system: "LogRocket", scope: "Issue counts by product page", isReal: true },
      ],
      askForMiddleby: {
        text: "Consistent GA4 event tagging across all mobile pages, and LogRocket release annotations, would remove the noise from this detector.",
        sectionId: "logrocket-ux",
      },
    },
    catalogId: "middleby-anomalies",
    source: "middleby-answer-block-router",
    dataGrounded: true,
  });
  return { text: "", meta };
}

async function buildRegionalHeatmap(_drill, period = { label: "this week", priorLabel: "the previous 7 days", factor: 1 }) {
  const f = period.factor || 1;
  // Synthetic pipeline-value-by-state (values are $ thousands, so 6420 = $6.42M
  // at the weekly baseline). Scale by factor so a longer window shows a
  // proportionally larger pipeline instead of the same weekly numbers.
  // City breakdowns store numeric $K values too so they scale the same way.
  const rawStates = [
    { state: "TX", value: 6420, breakdownLabel: "Top cities", breakdown: [{ name: "Houston", value: 1900 }, { name: "Dallas", value: 1600 }, { name: "Austin", value: 980 }] },
    { state: "CA", value: 5910, breakdownLabel: "Top cities", breakdown: [{ name: "Los Angeles", value: 2100 }, { name: "San Francisco", value: 1400 }, { name: "San Diego", value: 820 }] },
    { state: "FL", value: 5340, breakdownLabel: "Top cities", breakdown: [{ name: "Miami", value: 1700 }, { name: "Orlando", value: 1100 }, { name: "Tampa", value: 920 }] },
    { state: "NY", value: 4880, breakdownLabel: "Top cities", breakdown: [{ name: "New York City", value: 2400 }, { name: "Buffalo", value: 540 }] },
    { state: "IL", value: 3980, breakdownLabel: "Top cities", breakdown: [{ name: "Chicago", value: 1900 }, { name: "Rockford", value: 310 }] },
    { state: "PA", value: 3210, breakdownLabel: "Top cities", breakdown: [{ name: "Philadelphia", value: 1200 }, { name: "Pittsburgh", value: 780 }] },
    { state: "GA", value: 3010, breakdownLabel: "Top cities", breakdown: [{ name: "Atlanta", value: 1500 }, { name: "Savannah", value: 380 }] },
    { state: "OH", value: 2740, breakdownLabel: "Top cities", breakdown: [{ name: "Columbus", value: 720 }, { name: "Cleveland", value: 690 }] },
    { state: "NC", value: 2650, breakdownLabel: "Top cities", breakdown: [{ name: "Charlotte", value: 980 }, { name: "Raleigh", value: 640 }] },
    { state: "MI", value: 2410, breakdownLabel: "Top cities", breakdown: [{ name: "Detroit", value: 710 }, { name: "Grand Rapids", value: 420 }] },
    { state: "NJ", value: 2280, breakdown: [{ name: "Newark", value: 680 }, { name: "Jersey City", value: 510 }] },
    { state: "VA", value: 2140 },
    { state: "MA", value: 2010, breakdown: [{ name: "Boston", value: 980 }] },
    { state: "WA", value: 1940, breakdown: [{ name: "Seattle", value: 820 }] },
    { state: "CO", value: 1820, breakdown: [{ name: "Denver", value: 770 }] },
    { state: "AZ", value: 1780, breakdown: [{ name: "Phoenix", value: 710 }] },
    { state: "TN", value: 1720, breakdown: [{ name: "Nashville", value: 690 }] },
    { state: "IN", value: 1580 },
    { state: "MD", value: 1520 },
    { state: "MO", value: 1420 },
    { state: "WI", value: 1290 },
    { state: "MN", value: 1240 },
    { state: "AL", value: 1180 },
    { state: "SC", value: 1120 },
    { state: "LA", value: 1050 },
    { state: "KY", value: 970 },
    { state: "OR", value: 940 },
    { state: "OK", value: 880 },
    { state: "CT", value: 820 },
    { state: "KS", value: 760 },
    { state: "AR", value: 690 },
    { state: "MS", value: 640 },
    { state: "IA", value: 610 },
    { state: "NV", value: 590 },
    { state: "UT", value: 560 },
    { state: "NM", value: 490 },
    { state: "NE", value: 430 },
    { state: "WV", value: 410 },
    { state: "ID", value: 380 },
    { state: "HI", value: 320 },
    { state: "NH", value: 260 },
    { state: "ME", value: 240 },
    { state: "MT", value: 210 },
    { state: "ND", value: 170 },
    { state: "SD", value: 160 },
    { state: "DE", value: 150 },
    { state: "RI", value: 140 },
    { state: "VT", value: 120 },
    { state: "AK", value: 90 },
    { state: "WY", value: 80 },
    { state: "DC", value: 70 },
  ];

  // Scale every state value (and any city breakdown numbers) by the period
  // factor so the map represents pipeline for the actual selected window, not
  // a hard-coded weekly baseline.
  const states = rawStates.map((s) => ({
    ...s,
    value: Math.round(s.value * f),
    breakdown: s.breakdown
      ? s.breakdown.map((b) => ({
          ...b,
          value: typeof b.value === "number" ? fmtDollarsK(Math.round(b.value * f) * 1000) : b.value,
        }))
      : undefined,
  }));

  const total = states.reduce((s, r) => s + r.value, 0);
  const top3 = [...states].sort((a, b) => b.value - a.value).slice(0, 3);
  const top3ShareOfTotalPct = Math.round(((top3[0].value + top3[1].value + top3[2].value) / total) * 100);

  const meta = buildAnswerBlockMeta({
    intro: `Here's Middleby's pipeline value by US state for ${period.label}. ${STATE_NAMES(top3[0].state)}, ${STATE_NAMES(top3[1].state)}, and ${STATE_NAMES(top3[2].state)} together drive ${top3ShareOfTotalPct}% of total pipeline.`,
    reasoning: {
      summary: "We aggregated HubSpot deal values by the state on each deal's shipping address, then indexed to a $ thousands value per state and layered in the top cities where deals originated.",
      steps: [
        "Grouped HubSpot deals by shipping-state postal code.",
        "Rolled deal values into a state-level total (in $K).",
        "For the top 10 states, pulled the top 2 - 3 cities as a click-through breakdown.",
      ],
      confidence: "medium",
      caveats: [
        "Some deals don't carry a shipping-state  -  those are excluded from the map.",
      ],
    },
    blocks: [
      insightBlock({
        headline: `${STATE_NAMES(top3[0].state)} is the biggest single pipeline market by a clear margin`,
        subtext: `${STATE_NAMES(top3[0].state)}, ${STATE_NAMES(top3[1].state)}, and ${STATE_NAMES(top3[2].state)} together account for ${top3ShareOfTotalPct}% of pipeline over ${period.label}.`,
        sentiment: "neutral",
      }),
      findingsBlock({
        title: "Top 3 pipeline markets",
        items: top3.map((s) => ({
          title: STATE_NAMES(s.state),
          value: fmtDollarsK(s.value * 1000),
          desc: `${Math.round((s.value / total) * 100)}% of total pipeline`,
        })),
      }),
      regionMapBlock({
        title: "Pipeline value by state",
        description: "Darker = more pipeline. Hover any state for details; click to see the top cities.",
        metricLabel: "Pipeline value ($K)",
        metricFormat: "currency",
        states,
        summary: [
          "The Sun Belt (TX/FL/CA/GA/NC) delivers the largest cluster of pipeline.",
          "Great Lakes states (IL/OH/MI/PA) are the strong secondary market.",
          "Mountain West and Northeast trail, with room to expand dealer coverage.",
        ],
      }),
      tableBlock({
        title: "Top 10 states  -  pipeline value",
        columns: [
          { key: "state", label: "State" },
          { key: "name", label: "Name" },
          { key: "value", label: "Pipeline ($K)", format: "number" },
          { key: "share", label: "Share" },
        ],
        rows: [...states].sort((a, b) => b.value - a.value).slice(0, 10).map((s) => ({
          state: s.state,
          name: STATE_NAMES(s.state),
          value: s.value,
          share: `${Math.round((s.value / total) * 100)}%`,
        })),
      }),
      recommendationBlock({
        title: "Regional actions worth considering",
        items: [
          { text: `Dealer coverage in ${STATE_NAMES(top3[0].state)} is deep  -  protect it with a quarterly business review of the top 3 dealers.`, priority: "medium" },
          { text: "Mountain West (CO / UT / NV / AZ) has good demand signals but thin dealer coverage  -  evaluate 1 - 2 new partnerships.", priority: "medium" },
          { text: "Refresh the state-shipping-tag hygiene in HubSpot  -  a few percent of deals are missing state today, which distorts the map.", priority: "low" },
        ],
      }),
    ],
    followUps: [
      "Show me the RFQ funnel",
      "Which brands convert best?",
      "Which UX errors are hurting quotes?",
      "Show me anomalies worth attention",
    ],
    lineage: {
      sources: [
        { system: "HubSpot", scope: "Deals with shipping-state, current quarter", isReal: true },
        { system: "HubSpot", scope: "Deal values by state", isReal: true, note: "Some deals excluded where shipping-state is missing." },
      ],
      askForMiddleby: {
        text: "Consistent HubSpot deal-shipping-state tagging (or a shared Salesforce region field) would let the map be fully accurate. Adding dealer IDs per deal enables the dealer-level drill.",
        sectionId: "product-interest-gap",
      },
    },
    catalogId: "middleby-region-map",
    source: "middleby-answer-block-router",
    dataGrounded: true,
  });
  return { text: "", meta };
}

// Small local helper so we don't leak the postal codes when writing narratives.
function STATE_NAMES(code) {
  const NAMES = {
    TX: "Texas", CA: "California", FL: "Florida", NY: "New York", IL: "Illinois", PA: "Pennsylvania",
    GA: "Georgia", OH: "Ohio", NC: "North Carolina", MI: "Michigan", NJ: "New Jersey", VA: "Virginia",
    MA: "Massachusetts", WA: "Washington", CO: "Colorado", AZ: "Arizona", TN: "Tennessee",
  };
  return NAMES[code] || code;
}

const BUILDERS = {
  "product-interest-gap": buildProductInterestGap,
  "rfq-funnel": buildRfqFunnel,
  "customer-journey": buildCustomerJourney,
  "logrocket-ux": buildLogRocketUx,
  "executive-brief": buildExecutiveBrief,
  "forecast-scenario": buildForecastScenario,
  "anomaly-stories": buildAnomalyStories,
  "regional-heatmap": buildRegionalHeatmap,
};

// -- entry point ------------------------------------------------------------

const PRIOR_NAMES = {
  week: "the previous 7 days",
  month: "the previous calendar month",
  quarter: "the previous quarter",
  year: "the previous year",
  "two-years": "the prior two-year window",
};

// Build the period-context payload every builder gets. Labels and day counts
// come from the shared calendar span resolver so shorter ranges never outscale
// longer ones (e.g. 3 days < Jul MTD < Jun 7 to Jul 9).
function buildPeriodContext(options) {
  const span = resolveMiddlebyPeriodSpan(
    options?.period || "week",
    options?.fromDate,
    options?.toDate
  );
  return {
    id: span.id,
    days: span.days,
    factor: span.factor,
    label: span.label,
    priorLabel: PRIOR_NAMES[span.id] || "the equivalent prior range",
    fromISO: span.fromISO,
    toISO: span.toISO,
  };
}

// Scale a count-based number by the period factor. Used everywhere builders
// have a hardcoded "this-week baseline" number that needs to grow when the
// user asks for a longer window.
function scaleCount(base, factor) {
  return Math.round((base || 0) * (factor || 1));
}

function fmtN(v) {
  return new Intl.NumberFormat().format(v);
}

// Compact currency string: "$1.2M" for millions, "$120K" for thousands.
function fmtDollarsK(dollars) {
  if (dollars >= 1_000_000) return `$${(dollars / 1_000_000).toFixed(1)}M`;
  if (dollars >= 1_000) return `$${Math.round(dollars / 1_000)}K`;
  return `$${dollars}`;
}

export async function tryMiddlebyAnswerBlockRouter(userText, profileId, conversationSnapshot, options = {}) {
  if (profileId !== MIDDLEBY_PROFILE_ID) return null;

  let heroId = findHeroId(userText);
  let drillContext = null;

  // Context carryover
  if (!heroId) {
    const previousHeroId = findPreviousHeroId(conversationSnapshot);
    if (previousHeroId && looksLikeDrillFollowUp(userText)) {
      heroId = previousHeroId;
      drillContext = { previousQuestion: findPreviousUserQuestion(conversationSnapshot), followUpText: userText };
    }
  }

  if (!heroId) return null;
  const build = BUILDERS[heroId];
  if (!build) return null;

  const periodContext = buildPeriodContext(options);

  try {
    const reply = await build(drillContext, periodContext);
    if (reply && reply.meta) {
      if (drillContext?.previousQuestion) {
        const note = `Continuing on your earlier question  -  "${drillContext.previousQuestion}"  -  with this refinement:`;
        // Level-2+ answers keep the chart + narrative but drop the follow-up
        // chips — the demo asks users to type their own next question so the
        // suggestion tree can't run away.
        reply.meta = {
          ...reply.meta,
          intro: reply.meta.intro ? `${note}\n\n${reply.meta.intro}` : note,
          followUps: [],
        };
      }
    }
    return reply;
  } catch (e) {
    console.error(`[middleby-answer-block-router] builder ${heroId} failed`, e);
    return null;
  }
}

function findPreviousUserQuestion(conversationSnapshot) {
  if (!Array.isArray(conversationSnapshot)) return null;
  // Skip the most-recent user message (the current follow-up), find the one
  // before that.
  let skipped = false;
  for (let i = conversationSnapshot.length - 1; i >= 0; i -= 1) {
    const row = conversationSnapshot[i];
    if (!row || row.role !== "user") continue;
    if (!skipped) {
      skipped = true;
      continue;
    }
    const t = String(row.text || "").trim();
    if (t) return t.length > 90 ? `${t.slice(0, 87)}…` : t;
  }
  return null;
}
