/**
 * Middleby client-KPI fixtures mirrored as JS constants so composer hints can
 * cite real numbers synchronously (placeholder text can't fetch).
 *
 * SOURCE OF TRUTH: /public/middleby/data/kpi_*.json (fetched by insight charts).
 * When those JSONs change, update the arrays below to match — the derivation
 * helpers rely on these numbers being current.
 */

const KPI_TRAFFIC_BY_DEVICE = [
  { channel: "Organic Search", device: "desktop", sessions: 9200, quotes: 210, conversion_rate: 2.3 },
  { channel: "Organic Search", device: "mobile", sessions: 6100, quotes: 88, conversion_rate: 1.4 },
  { channel: "Paid Search", device: "desktop", sessions: 3400, quotes: 96, conversion_rate: 2.8 },
  { channel: "Paid Search", device: "mobile", sessions: 2800, quotes: 41, conversion_rate: 1.5 },
  { channel: "Direct", device: "desktop", sessions: 4100, quotes: 120, conversion_rate: 2.9 },
  { channel: "Direct", device: "mobile", sessions: 1900, quotes: 32, conversion_rate: 1.7 },
  { channel: "Referral", device: "desktop", sessions: 1500, quotes: 45, conversion_rate: 3.0 },
  { channel: "Referral", device: "tablet", sessions: 420, quotes: 9, conversion_rate: 2.1 },
  { channel: "Email", device: "desktop", sessions: 980, quotes: 38, conversion_rate: 3.9 },
  { channel: "Email", device: "mobile", sessions: 740, quotes: 18, conversion_rate: 2.4 },
];

const KPI_CLOSED_WON_LOST = [
  { stage: "Closed Won", count: 118, share: 34, lost_reason: "" },
  { stage: "Closed Lost", count: 118, share: 34, lost_reason: "Price / budget" },
  { stage: "Closed Lost", count: 39, share: 11, lost_reason: "Chose competitor" },
  { stage: "Closed Lost", count: 20, share: 6, lost_reason: "Timing / project delayed" },
  { stage: "Closed Lost", count: 12, share: 3, lost_reason: "Spec mismatch" },
  { stage: "Closed Lost", count: 6, share: 2, lost_reason: "No response" },
  { stage: "Open pipeline", count: 34, share: 10, lost_reason: "" },
];

const KPI_LIVE_CHAT = [
  { chat_intent: "Can I buy / Buy Now Online", inquiries: 48, converted_to_quote: 19, conversion_rate: 40 },
  { chat_intent: "Pricing question", inquiries: 62, converted_to_quote: 14, conversion_rate: 23 },
  { chat_intent: "Dealer / service", inquiries: 31, converted_to_quote: 6, conversion_rate: 19 },
  { chat_intent: "Product availability", inquiries: 27, converted_to_quote: 9, conversion_rate: 33 },
];

const KPI_GUIDED_SHOPPING = [
  { tool: "Fryer selector", started: 420, completed_tool: 336, submitted_quote: 105, tool_completion_rate: 80, quote_conversion_rate: 25 },
  { tool: "Oven configurator", started: 310, completed_tool: 186, submitted_quote: 46, tool_completion_rate: 60, quote_conversion_rate: 15 },
  { tool: "Ice machine finder", started: 180, completed_tool: 95, submitted_quote: 20, tool_completion_rate: 53, quote_conversion_rate: 11 },
  { tool: "Brand comparison guide", started: 260, completed_tool: 104, submitted_quote: 18, tool_completion_rate: 40, quote_conversion_rate: 7 },
];

const KPI_BOUNCE = [
  { entry_page: "/", sessions: 8200, bounce_rate: 41, avg_pages: 2.1, avg_duration_sec: 98 },
  { entry_page: "/brands/turbochef", sessions: 2100, bounce_rate: 36, avg_pages: 2.8, avg_duration_sec: 142 },
  { entry_page: "/products/commercial-fryers", sessions: 1640, bounce_rate: 33, avg_pages: 3.2, avg_duration_sec: 168 },
  { entry_page: "/brands/blodgett", sessions: 1480, bounce_rate: 38, avg_pages: 2.6, avg_duration_sec: 131 },
  { entry_page: "/search", sessions: 920, bounce_rate: 52, avg_pages: 1.4, avg_duration_sec: 54 },
  { entry_page: "/quote", sessions: 610, bounce_rate: 28, avg_pages: 3.9, avg_duration_sec: 210 },
];

const KPI_PO_REFERENCE = [
  { brand: "TurboChef", closed_won: 22, po_reference_set: 18, po_coverage: 82 },
  { brand: "Blodgett", closed_won: 16, po_reference_set: 11, po_coverage: 69 },
  { brand: "Follett", closed_won: 19, po_reference_set: 17, po_coverage: 89 },
  { brand: "Southbend", closed_won: 14, po_reference_set: 9, po_coverage: 64 },
  { brand: "Pitco", closed_won: 11, po_reference_set: 7, po_coverage: 64 },
  { brand: "Globe", closed_won: 9, po_reference_set: 4, po_coverage: 44 },
];

const KPI_TERRITORY = [
  { territory: "Northeast", quotes: 96, closed_won_rate: 36 },
  { territory: "Southeast", quotes: 112, closed_won_rate: 31 },
  { territory: "Midwest", quotes: 88, closed_won_rate: 34 },
  { territory: "Southwest", quotes: 74, closed_won_rate: 29 },
  { territory: "West", quotes: 101, closed_won_rate: 38 },
  { territory: "Canada", quotes: 42, closed_won_rate: 33 },
];

const KPI_SEGMENT = [
  { customer_segment: "Restaurant / QSR", quotes: 186, share: 28, closed_won_rate: 32 },
  { customer_segment: "Hotel / Hospitality", quotes: 124, share: 19, closed_won_rate: 35 },
  { customer_segment: "Institutional / Education", quotes: 98, share: 15, closed_won_rate: 29 },
  { customer_segment: "Dealer / Distributor", quotes: 142, share: 21, closed_won_rate: 41 },
  { customer_segment: "Healthcare", quotes: 66, share: 10, closed_won_rate: 27 },
  { customer_segment: "Other / Unclassified", quotes: 48, share: 7, closed_won_rate: 22 },
];

const KPI_TOP_PDPS = [
  { product: "TurboChef Encore", brand: "TurboChef", page_views: 1840 },
  { product: "Blodgett Mark V", brand: "Blodgett", page_views: 1560 },
  { product: "Pitco Solstice SG14", brand: "Pitco", page_views: 1420 },
  { product: "Follett 7 Series Ice", brand: "Follett", page_views: 1280 },
  { product: "Southbend S36 Range", brand: "Southbend", page_views: 1110 },
  { product: "Globe C12 Slicer", brand: "Globe", page_views: 980 },
];

const KPI_QUOTES_BY_CATEGORY = [
  { l1_category: "Commercial Cooking Equipment", quotes: 94, share: 42 },
  { l1_category: "Commercial Refrigeration", quotes: 38, share: 17 },
  { l1_category: "Warewashing Equipment", quotes: 28, share: 13 },
  { l1_category: "Ventilation & Hoods", quotes: 22, share: 10 },
  { l1_category: "Commercial Beverage Equipment", quotes: 16, share: 7 },
  { l1_category: "Commercial Ice Equipment", quotes: 15, share: 7 },
  { l1_category: "Food Prep Equipment", quotes: 12, share: 5 },
  { l1_category: "Food Warming and Holding", quotes: 4, share: 2 },
];

const KPI_QUOTES_BY_STATE = [
  { state_name: "California", territory: "West", quotes: 62, closed_won_rate: 40 },
  { state_name: "Texas", territory: "Southwest", quotes: 52, closed_won_rate: 27 },
  { state_name: "Florida", territory: "Southeast", quotes: 38, closed_won_rate: 30 },
  { state_name: "New York", territory: "Northeast", quotes: 34, closed_won_rate: 36 },
  { state_name: "Pennsylvania", territory: "Northeast", quotes: 26, closed_won_rate: 34 },
  { state_name: "Illinois", territory: "Midwest", quotes: 24, closed_won_rate: 33 },
  { state_name: "Washington", territory: "West", quotes: 24, closed_won_rate: 36 },
  { state_name: "Arizona", territory: "Southwest", quotes: 22, closed_won_rate: 32 },
  { state_name: "Georgia", territory: "Southeast", quotes: 22, closed_won_rate: 33 },
  { state_name: "North Carolina", territory: "Southeast", quotes: 20, closed_won_rate: 31 },
  { state_name: "Ohio", territory: "Midwest", quotes: 20, closed_won_rate: 35 },
  { state_name: "Massachusetts", territory: "Northeast", quotes: 18, closed_won_rate: 38 },
  { state_name: "New Jersey", territory: "Northeast", quotes: 18, closed_won_rate: 32 },
  { state_name: "Virginia", territory: "Southeast", quotes: 18, closed_won_rate: 32 },
  { state_name: "Michigan", territory: "Midwest", quotes: 16, closed_won_rate: 32 },
  { state_name: "Colorado", territory: "West", quotes: 15, closed_won_rate: 38 },
  { state_name: "Tennessee", territory: "Southeast", quotes: 14, closed_won_rate: 29 },
  { state_name: "Indiana", territory: "Midwest", quotes: 12, closed_won_rate: 36 },
  { state_name: "Minnesota", territory: "Midwest", quotes: 8, closed_won_rate: 37 },
  { state_name: "Missouri", territory: "Midwest", quotes: 8, closed_won_rate: 30 },
];

const KPI_BRAND_BY_TERRITORY = [
  { brand: "TurboChef", territory: "Northeast", quotes: 24, share_of_territory: 25, closed_won_rate: 41 },
  { brand: "TurboChef", territory: "Southeast", quotes: 18, share_of_territory: 16, closed_won_rate: 33 },
  { brand: "TurboChef", territory: "West", quotes: 26, share_of_territory: 26, closed_won_rate: 44 },
  { brand: "TurboChef", territory: "Midwest", quotes: 14, share_of_territory: 16, closed_won_rate: 37 },
  { brand: "TurboChef", territory: "Southwest", quotes: 11, share_of_territory: 15, closed_won_rate: 31 },
  { brand: "Blodgett", territory: "Northeast", quotes: 22, share_of_territory: 23, closed_won_rate: 38 },
  { brand: "Blodgett", territory: "Southeast", quotes: 28, share_of_territory: 25, closed_won_rate: 32 },
  { brand: "Blodgett", territory: "Midwest", quotes: 20, share_of_territory: 23, closed_won_rate: 35 },
  { brand: "Blodgett", territory: "West", quotes: 14, share_of_territory: 14, closed_won_rate: 36 },
  { brand: "Follett", territory: "Southeast", quotes: 24, share_of_territory: 21, closed_won_rate: 42 },
  { brand: "Follett", territory: "West", quotes: 18, share_of_territory: 18, closed_won_rate: 44 },
  { brand: "Follett", territory: "Northeast", quotes: 14, share_of_territory: 15, closed_won_rate: 39 },
  { brand: "Southbend", territory: "Southeast", quotes: 20, share_of_territory: 18, closed_won_rate: 30 },
  { brand: "Southbend", territory: "Midwest", quotes: 18, share_of_territory: 20, closed_won_rate: 33 },
  { brand: "Southbend", territory: "Southwest", quotes: 14, share_of_territory: 19, closed_won_rate: 27 },
  { brand: "Pitco", territory: "Southwest", quotes: 18, share_of_territory: 24, closed_won_rate: 31 },
  { brand: "Pitco", territory: "West", quotes: 14, share_of_territory: 14, closed_won_rate: 36 },
  { brand: "Pitco", territory: "Midwest", quotes: 12, share_of_territory: 14, closed_won_rate: 32 },
  { brand: "Globe", territory: "Midwest", quotes: 8, share_of_territory: 9, closed_won_rate: 24 },
  { brand: "Globe", territory: "Northeast", quotes: 6, share_of_territory: 6, closed_won_rate: 29 },
];

const KPI_SEGMENT_BY_CATEGORY = [
  { customer_segment: "Restaurant / QSR", l1_category: "Commercial Cooking Equipment", quotes: 96, share_of_segment: 52 },
  { customer_segment: "Restaurant / QSR", l1_category: "Commercial Refrigeration", quotes: 32, share_of_segment: 17 },
  { customer_segment: "Restaurant / QSR", l1_category: "Warewashing Equipment", quotes: 22, share_of_segment: 12 },
  { customer_segment: "Restaurant / QSR", l1_category: "Ventilation & Hoods", quotes: 18, share_of_segment: 10 },
  { customer_segment: "Restaurant / QSR", l1_category: "Commercial Ice Equipment", quotes: 10, share_of_segment: 5 },
  { customer_segment: "Hotel / Hospitality", l1_category: "Commercial Cooking Equipment", quotes: 48, share_of_segment: 39 },
  { customer_segment: "Hotel / Hospitality", l1_category: "Commercial Refrigeration", quotes: 28, share_of_segment: 23 },
  { customer_segment: "Hotel / Hospitality", l1_category: "Commercial Beverage Equipment", quotes: 18, share_of_segment: 15 },
  { customer_segment: "Hotel / Hospitality", l1_category: "Food Warming and Holding", quotes: 14, share_of_segment: 11 },
  { customer_segment: "Institutional / Education", l1_category: "Commercial Cooking Equipment", quotes: 32, share_of_segment: 33 },
  { customer_segment: "Institutional / Education", l1_category: "Warewashing Equipment", quotes: 24, share_of_segment: 24 },
  { customer_segment: "Institutional / Education", l1_category: "Commercial Refrigeration", quotes: 20, share_of_segment: 20 },
  { customer_segment: "Institutional / Education", l1_category: "Food Warming and Holding", quotes: 14, share_of_segment: 14 },
  { customer_segment: "Dealer / Distributor", l1_category: "Commercial Cooking Equipment", quotes: 58, share_of_segment: 41 },
  { customer_segment: "Dealer / Distributor", l1_category: "Commercial Refrigeration", quotes: 34, share_of_segment: 24 },
  { customer_segment: "Dealer / Distributor", l1_category: "Commercial Ice Equipment", quotes: 22, share_of_segment: 15 },
  { customer_segment: "Dealer / Distributor", l1_category: "Warewashing Equipment", quotes: 18, share_of_segment: 13 },
  { customer_segment: "Healthcare", l1_category: "Commercial Refrigeration", quotes: 24, share_of_segment: 36 },
  { customer_segment: "Healthcare", l1_category: "Commercial Cooking Equipment", quotes: 18, share_of_segment: 27 },
  { customer_segment: "Healthcare", l1_category: "Warewashing Equipment", quotes: 14, share_of_segment: 21 },
];

const EX04_SITE_SEARCH = [
  { search_term: "combi oven", search_volume: 1240, zero_result_rate: 4 },
  { search_term: "countertop fryer", search_volume: 980, zero_result_rate: 6 },
  { search_term: "blast chiller", search_volume: 760, zero_result_rate: 12 },
  { search_term: "ice machine parts", search_volume: 610, zero_result_rate: 73 },
  { search_term: "conveyor pizza oven", search_volume: 540, zero_result_rate: 8 },
  { search_term: "used equipment", search_volume: 380, zero_result_rate: 88 },
  { search_term: "warranty registration", search_volume: 260, zero_result_rate: 67 },
];

function maxBy(rows, key) {
  return rows.reduce((best, row) => (row[key] > (best?.[key] ?? -Infinity) ? row : best), null);
}
function minBy(rows, key) {
  return rows.reduce((best, row) => (row[key] < (best?.[key] ?? Infinity) ? row : best), null);
}

/**
 * Facts derived from KPI fixtures. Each fact re-computes on module load so
 * updating the source arrays above automatically updates every hint below.
 */
export const MIDDLEBY_KPI_FACTS = {
  // kpi-01: best-converting channel/device pair
  topChannelDevice: (() => {
    const top = maxBy(KPI_TRAFFIC_BY_DEVICE, "conversion_rate");
    return {
      channel: top.channel,
      device: top.device,
      rate: top.conversion_rate,
      sessions: top.sessions,
    };
  })(),
  // kpi-02: dominant lost reason
  topLostReason: (() => {
    const lost = KPI_CLOSED_WON_LOST.filter((r) => r.stage === "Closed Lost");
    const totalLost = lost.reduce((sum, r) => sum + r.count, 0);
    const top = maxBy(lost, "count");
    return {
      reason: top.lost_reason,
      count: top.count,
      shareOfLosses: Math.round((top.count / totalLost) * 100),
      shareOfTotal: top.share,
    };
  })(),
  closedWonRate: (() => {
    const won = KPI_CLOSED_WON_LOST.find((r) => r.stage === "Closed Won");
    return won?.share ?? null;
  })(),
  // kpi-03: chat intent with best quote conversion
  topChatIntent: (() => {
    const top = maxBy(KPI_LIVE_CHAT, "conversion_rate");
    return {
      intent: top.chat_intent,
      inquiries: top.inquiries,
      converted: top.converted_to_quote,
      rate: top.conversion_rate,
    };
  })(),
  // kpi-04: best + worst guided tool
  bestGuidedTool: (() => {
    const top = maxBy(KPI_GUIDED_SHOPPING, "quote_conversion_rate");
    return { tool: top.tool, rate: top.quote_conversion_rate, started: top.started };
  })(),
  worstGuidedTool: (() => {
    const bottom = minBy(KPI_GUIDED_SHOPPING, "quote_conversion_rate");
    return { tool: bottom.tool, rate: bottom.quote_conversion_rate, started: bottom.started };
  })(),
  // kpi-05: worst-bouncing entry page
  worstBounceEntry: (() => {
    const top = maxBy(KPI_BOUNCE, "bounce_rate");
    return { entry: top.entry_page, rate: top.bounce_rate, sessions: top.sessions };
  })(),
  // kpi-06: brand with weakest PO coverage
  worstPoCoverage: (() => {
    const bottom = minBy(KPI_PO_REFERENCE, "po_coverage");
    return {
      brand: bottom.brand,
      coverage: bottom.po_coverage,
      missing: bottom.closed_won - bottom.po_reference_set,
      closed_won: bottom.closed_won,
    };
  })(),
  // kpi-07: top territory by volume and by win rate
  topTerritoryByQuotes: (() => {
    const top = maxBy(KPI_TERRITORY, "quotes");
    return { territory: top.territory, quotes: top.quotes, winRate: top.closed_won_rate };
  })(),
  bestTerritoryByWinRate: (() => {
    const top = maxBy(KPI_TERRITORY, "closed_won_rate");
    return { territory: top.territory, quotes: top.quotes, winRate: top.closed_won_rate };
  })(),
  // kpi-08: best-winning segment
  bestSegmentByWinRate: (() => {
    const top = maxBy(KPI_SEGMENT, "closed_won_rate");
    return { segment: top.customer_segment, quotes: top.quotes, winRate: top.closed_won_rate };
  })(),
  topSegmentByQuotes: (() => {
    const top = maxBy(KPI_SEGMENT, "quotes");
    return { segment: top.customer_segment, quotes: top.quotes, share: top.share };
  })(),
  // kpi-09: top PDP
  topPdp: (() => {
    const top = maxBy(KPI_TOP_PDPS, "page_views");
    return { product: top.product, brand: top.brand, views: top.page_views };
  })(),
  // kpi-10: top category by quotes
  topCategoryByQuotes: (() => {
    const top = maxBy(KPI_QUOTES_BY_CATEGORY, "quotes");
    return { category: top.l1_category, quotes: top.quotes, share: top.share };
  })(),
  // kpi-11: top state and best win rate state
  topStateByQuotes: (() => {
    const top = maxBy(KPI_QUOTES_BY_STATE, "quotes");
    return { state: top.state_name, quotes: top.quotes, winRate: top.closed_won_rate };
  })(),
  bestStateByWinRate: (() => {
    // Only consider states with 15+ quotes to avoid noise-driven leaders.
    const eligible = KPI_QUOTES_BY_STATE.filter((r) => r.quotes >= 15);
    const top = maxBy(eligible, "closed_won_rate");
    return { state: top.state_name, quotes: top.quotes, winRate: top.closed_won_rate };
  })(),
  // kpi-12: strongest and weakest brand-territory pairs
  bestBrandTerritoryPair: (() => {
    const eligible = KPI_BRAND_BY_TERRITORY.filter((r) => r.quotes >= 10);
    const top = maxBy(eligible, "closed_won_rate");
    return { brand: top.brand, territory: top.territory, winRate: top.closed_won_rate, quotes: top.quotes };
  })(),
  worstBrandTerritoryPair: (() => {
    const eligible = KPI_BRAND_BY_TERRITORY.filter((r) => r.quotes >= 6);
    const bottom = minBy(eligible, "closed_won_rate");
    return { brand: bottom.brand, territory: bottom.territory, winRate: bottom.closed_won_rate, quotes: bottom.quotes };
  })(),
  // kpi-13: most concentrated segment × category cell
  topSegmentCategoryPair: (() => {
    const top = maxBy(KPI_SEGMENT_BY_CATEGORY, "share_of_segment");
    return {
      segment: top.customer_segment,
      category: top.l1_category,
      share: top.share_of_segment,
      quotes: top.quotes,
    };
  })(),
  // ex-04: worst zero-result search term (by volume × zero-result rate)
  topZeroResultSearch: (() => {
    const scored = EX04_SITE_SEARCH.map((r) => ({
      ...r,
      lost_searches: Math.round((r.search_volume * r.zero_result_rate) / 100),
    }));
    const top = maxBy(scored, "lost_searches");
    return {
      term: top.search_term,
      volume: top.search_volume,
      zeroResultRate: top.zero_result_rate,
      lostSearches: top.lost_searches,
    };
  })(),
};

/**
 * KPI-specific composer hints, string-composed from live facts.
 * Numbers here always match the source arrays above.
 */
export function getMiddlebyKpiDerivedHint(catalogId) {
  const F = MIDDLEBY_KPI_FACTS;
  switch (catalogId) {
    case "middleby-kpi-01":
      return `Why does ${F.topChannelDevice.channel} on ${F.topChannelDevice.device} lead at ${F.topChannelDevice.rate}% quote conversion?`;
    case "middleby-kpi-02":
      return `Closed-won rate is ${F.closedWonRate}%. Why does "${F.topLostReason.reason}" cause ${F.topLostReason.shareOfLosses}% of losses?`;
    case "middleby-kpi-03":
      return `${F.topChatIntent.intent} converts at ${F.topChatIntent.rate}% (${F.topChatIntent.converted} of ${F.topChatIntent.inquiries}). Which brands do these chats mention most?`;
    case "middleby-kpi-04":
      return `${F.bestGuidedTool.tool} converts at ${F.bestGuidedTool.rate}% but ${F.worstGuidedTool.tool} only ${F.worstGuidedTool.rate}%. What's dropping in the ${F.worstGuidedTool.tool}?`;
    case "middleby-kpi-05":
      return `${F.worstBounceEntry.entry} bounces at ${F.worstBounceEntry.rate}% across ${F.worstBounceEntry.sessions.toLocaleString()} sessions. Which brands are the entry sources?`;
    case "middleby-kpi-06":
      return `${F.worstPoCoverage.brand} PO coverage is only ${F.worstPoCoverage.coverage}% (${F.worstPoCoverage.missing} of ${F.worstPoCoverage.closed_won} closed-won missing). Which reps and which deals?`;
    case "middleby-kpi-07":
      return `${F.topTerritoryByQuotes.territory} led on volume (${F.topTerritoryByQuotes.quotes} quotes) but ${F.bestTerritoryByWinRate.territory} led on win rate (${F.bestTerritoryByWinRate.winRate}%). Why the gap?`;
    case "middleby-kpi-08":
      return `${F.bestSegmentByWinRate.segment} wins at ${F.bestSegmentByWinRate.winRate}% versus a ${F.closedWonRate}% average. What makes that segment convert?`;
    case "middleby-kpi-09":
      return `${F.topPdp.product} led PDP views at ${F.topPdp.views.toLocaleString()}. How many turned into quotes?`;
    case "middleby-kpi-10":
      return `${F.topCategoryByQuotes.category} generated ${F.topCategoryByQuotes.share}% of quotes (${F.topCategoryByQuotes.quotes}). Which brands drove that?`;
    case "middleby-kpi-11":
      return `${F.topStateByQuotes.state} led on quote volume (${F.topStateByQuotes.quotes}) but ${F.bestStateByWinRate.state} led on win rate (${F.bestStateByWinRate.winRate}%). Where should we lean in?`;
    case "middleby-kpi-12":
      return `${F.bestBrandTerritoryPair.brand} in ${F.bestBrandTerritoryPair.territory} wins at ${F.bestBrandTerritoryPair.winRate}%, while ${F.worstBrandTerritoryPair.brand} in ${F.worstBrandTerritoryPair.territory} sits at ${F.worstBrandTerritoryPair.winRate}%. What's driving the gap?`;
    case "middleby-kpi-13":
      return `${F.topSegmentCategoryPair.segment} concentrates ${F.topSegmentCategoryPair.share}% of its quotes in ${F.topSegmentCategoryPair.category}. Where's the biggest cross-sell opening?`;
    case "middleby-ex-04":
      return `"${F.topZeroResultSearch.term}" is the biggest zero-result gap — ${F.topZeroResultSearch.lostSearches.toLocaleString()} lost searches (${F.topZeroResultSearch.zeroResultRate}% zero-result of ${F.topZeroResultSearch.volume.toLocaleString()}). What should we add to the catalog?`;
    default:
      return null;
  }
}
