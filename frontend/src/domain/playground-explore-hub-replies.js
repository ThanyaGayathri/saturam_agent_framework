/**
 * Scripted Playground chat replies for "What do you need help with?" links:
 * each catalog row × (This week | This quarter). Mirrors hub focus decks + prompts.
 */

import { GOURMET_FOODS_PROFILE_ID, MEDIA_PROFILE_ID, MIDDLEBY_PROFILE_ID, normalizeDemoProfileId } from "./demo-profiles";
import { getMiddlebyExploreHubReply } from "./middleby-playground-chat";
import { lensDisplayToHorizon } from "./playground-hub-handoff";

/** Prefer chip label (`lens`) over `horizon` when they disagree (matches serialize/parse). */
/** @param {{ horizon?: string, lens?: string } | undefined} o */
export function resolveExploreHorizonKey(o) {
 const fromLens = lensDisplayToHorizon(o?.lens);
 if (fromLens) return fromLens;
 if (o?.horizon === "week" || o?.horizon === "quarter") return o.horizon;
 return null;
}

/** Default profile: generic retail gallery focus decks. */
const GENERIC_EXPLORE_REPLIES = {
 "gallery-uc-05": {
 week: {
 topic: "orders",
 text: "For this week, I’m prioritizing live e-com funnel leaks: mobile vs desktop drop-off at payment, guest-checkout friction, and cart recovery eligibility. Demo read: checkout completion is softest on mobile Safari after 8pm ET; I’d pull the top 200 abandoned carts with inventory certainty and trigger a same-day save plus tightened PCI-retry rules before the weekend promo.",
 },
 quarter: {
 topic: "orders",
 text: "For the quarter view, I’m stacking funnel KPIs against your conversion and AOV plan: sessions → cart → checkout → paid, plus recovery rate and promo ROS. Demo read: you can lift sustainable conversion more from browse-to-cart clarity and delivery promise messaging than from deeper coupons; recommend a quarterly test grid on PDP shipping badges and one-click re-order for replenishment SKUs.",
 },
 },
 "gallery-uc-06": {
 week: {
 topic: "store_ops",
 text: "This week I’m isolating POS stability by lane, tender type, and store cluster. Demo read: failure spikes tie to a firmware batch and contactless timeout at four high-traffic doors; immediate play is rollback the reader profile on those lanes, add a second terminal on peak Saturday windows, and push a 10-minute cashier script for fallback.",
 },
 quarter: {
 topic: "store_ops",
 text: "Quarterly, I’m trending POS fault codes, median queue time, and tender mix shifts after your hardware refresh. Demo read: structural wins come from preventative maintenance cadence and standardized lane diagnostics, not one-off reboots; set a quarterly SLA for terminal swaps and train leads on exception codes so regional rollups stay honest.",
 },
 },
 "gallery-uc-04": {
 week: {
 topic: "store_ops",
 text: "This week’s store ops lens is coverage vs traffic: doors missing minimum cashier depth before noon, task bundles slipping before truck arrival, and late checklist closes. Demo read: shift one backup opener to the three worst AM gaps and freeze non-critical tasks until inbound scan completes so you protect instock and wait time together.",
 },
 quarter: {
 topic: "store_ops",
 text: "For the quarter, I’m comparing labor plan vs realized volume, task completion, and overtime drift by region. Demo read: chronic misalignments usually trace to stale forecasts at category level, not store discipline; recommend quarterly recalibration of task standards and a rolling 13-week labor model tied to traffic and promo intensity.",
 },
 },
 "gallery-uc-07": {
 week: {
 topic: "orders",
 text: "This week I’m triaging returns by reason code, high-value SKUs, and channels showing the spike. Demo read: consolidate top drivers (fit vs damage vs slow delivery), pause listing changes on the worst ASIN/SKU cluster until disposition rules are consistent, and route VIP cases to a dedicated queue to protect NPS.",
 },
 quarter: {
 topic: "orders",
 text: "Quarterly returns work is about policy, assortment, and vendor chargeback leverage. Demo read: segment return rate by supplier and category, align markdown and quality feedback loops, and set quarterly targets on salvage vs restock economics so CS isn’t absorbing margin leaks alone.",
 },
 },
};

/** GFI profile: every catalog row × (This week | This quarter) for hub + builder handoffs. */
const GFI_EXPLORE_REPLIES = {
 "gfi-uc-01-perishable-forecast": {
 week: {
 topic: "inventory",
 text: "This week on perishable demand at GFI, I’m watching regional cheese and charcuterie bias after promos and events plus DC-level safety stock against supplier cutoffs. Demo read: Southeast blended models are flagging upside on soft-ripened SKUs while prepared salads run slightly long; prioritize re-forecast on the two temperature lanes feeding Atlanta and Charlotte before Tuesday’s PO batch so you don’t double-buy short-code.",
 },
 quarter: {
 topic: "inventory",
 text: "For the quarter, I’m tying WMAPE and bias on 11,340+ SKUs to spoilage write-offs, stockouts on hero programs, and import latency. Demo read: network error improved after vendor calendar enrichment but cold-start artisans still overweight safety; recommend a quarterly refresh of peer clustering by temperature lane and a spoilage $ at risk dashboard by DC so buyers see forecast error in margin terms, not just accuracy stats.",
 },
 },
 "gfi-uc-02-inter-dc-rebalance": {
 week: {
 topic: "inventory",
 text: "This week’s inter-DC lens is classic footprint mismatch on the Brie program: Front Range long cover while Mid-Atlantic chases allocation. Demo I’d run: stage a chilled consolidated move on tomorrow night’s milk run within verified reefer hours, attach trace IDs to the manifest, and compare net margin vs a spot PO so the trade desk signs one path, not three partials.",
 },
 quarter: {
 topic: "inventory",
 text: "For the quarter, I’m quantifying stranded slow movers, emergency fills, and split shipments avoided when rebalancing is automated across 14 DCs. Demo read: roughly several million in working capital can sit in eligible transfer lanes; rank moves by cash release, service risk, and temperature compliance, then lock a weekly governance cadence so manual planners aren’t redoing the same graph search every peak.",
 },
 },
 "gfi-uc-03-supplier-risk": {
 week: {
 topic: "inventory",
 text: "This week’s supplier-risk cut: I’m surfacing lanes where NA tariff deltas, weather, or port slack would hit fill schedules for import-heavy families before allocations tighten. Demo read: tier your 2,000+ vendors by exposure and alternate eligibility; for the top ten at-risk SKUs, pre-stage approved alternates with landed-cost gates and push alerts to buyers before cutoffs roll to emergency air.",
 },
 quarter: {
 topic: "inventory",
 text: "Quarterly, I’m benchmarking disruption spend, alternate usage, and forecast bias tied to artisan fragility vs stable broadline suppliers. Demo read: consolidate a single risk scorecard-tariff, climate, lead-time variance, single-source flags-then review with merchandising so NPI gates and PO cadence aren’t chasing spreadsheets when the next shock hits.",
 },
 },
 "gfi-uc-04-markdown-optimization": {
 week: {
 topic: "inventory",
 text: "This week on short-code perishables, I’m isolating SKUs inside the markdown window where a second wave recovers margin without trashing the specialty ladder. Demo read: prepared deli and soft-ripened sets in two DCs show price elasticity; sequence a shallow first cut with targeted account packs before a deeper outlet path, and freeze list changes once sell-through crosses the guardrail.",
 },
 quarter: {
 topic: "inventory",
 text: "For the quarter, I’m stacking markdown recovery vs write-off by temperature lane and region. Demo read: multi-wave policy beats flat rules on artisan mix; set quarterly targets on % recovered pre-day-zero and train buyers on ladder discipline so hero SKUs don’t train customers to wait for fire sales.",
 },
 },
 "gfi-uc-05-labor-optimization": {
 week: {
 topic: "store_ops",
 text: "This week at the 14 DCs I’m aligning waves to inbound variance and outbound SLAs-where pick teams are short before noon, overtime is creeping, or dock turns slip. Demo read: shift a backup pod on peak outbound days at the two worst burnout pockets and rebalance batch picks so frozen waves don’t starve chilled cut lines.",
 },
 quarter: {
 topic: "store_ops",
 text: "Quarterly labor view: plan vs realized volume, overtime drift, and productivity by lane after holiday and show-season peaks. Demo read: chronic gaps usually trace to stale category forecasts, not floor discipline; refresh standards quarterly and tie staffing envelopes to promo intensity plus temperature complexity.",
 },
 },
 "gfi-uc-06-multi-temp-routing": {
 week: {
 topic: "orders",
 text: "This week on mixed-temp routing, I’m flagging routes with excursion risk, late customer windows, and split follow-ups from naive sequencing. Demo read: split frozen and chilled picks unless the rig is dual-compartment approved; tonight’s plan should only commit hours your driver-tenure model validates so broker backup stays explicit with surcharge flags.",
 },
 quarter: {
 topic: "orders",
 text: "Quarterly, I’m stacking route cost, on-time-in-full, and split-shipment rate for GFI’s owned fleet vs broadline benchmarks. Demo read: the sustainable win is fewer second legs and fewer temperature breaks, not just miles; target lane-level templates that respect zone, backhaul, and lunch rush cutoffs at Edison-class customers, then review quarter over quarter by spine (Decatur hub outward) and DC swing pairs like Kansas City.",
 },
 },
 "gfi-uc-07-vendor-scorecards": {
 week: {
 topic: "inventory",
 text: "This week’s vendor lens highlights fill-rate slippage and case-accuracy noise before negotiation windows. Demo read: two import-heavy suppliers are missing ASN timeliness-pull incident trails, not just averages-and give buyers a single tiered view (A/B watch) so talks focus on service recovery, not stale spreadsheets.",
 },
 quarter: {
 topic: "inventory",
 text: "Quarterly scorecards across 2,000+ suppliers: consolidate fill rate, quality incidents, lead-time variance, and accuracy by tier and region. Demo read: rationalize rebates and assortment locks with data buyers trust; set quarterly business reviews on watch-list vendors before you renew minimums.",
 },
 },
 "gfi-uc-08-npi-tracking": {
 week: {
 topic: "inventory",
 text: "This week on NPI I’m watching first-8-week sell-through vs launch forecast for new artisan and import SKUs. Demo read: flag two clusters trailing plan with rising cover-cut reorder authorizations early and swing display budget to winners before dead stock solidifies.",
 },
 quarter: {
 topic: "inventory",
 text: "Quarterly NPI governance: thousands of launches annually need a kill/scale playbook. Demo read: rank introductions by margin-at-risk and velocity index; institutionalize week-4 and week-8 gates with merchandising and supply so capital doesn’t bury under slow feedback loops.",
 },
 },
 "gfi-uc-09-fsma-traceability": {
 week: {
 topic: "inventory",
 text: "This week’s FSMA drill: pick a regulated high-risk lot and time-box traceback from supplier ASN to DC putaway to outbound shipment. Demo read: if genealogy breaks at receiving scan or mixed-pallet storage, that’s your fix list before audit season; attach compliance release notes to any transfer lots you execute so custody stays continuous, not a Friday spreadsheet scramble.",
 },
 quarter: {
 topic: "inventory",
 text: "For the quarter, I’m mapping lot-capture coverage by SKU tier, supplier onboarding state, and DC workflow (receive, move, pick, ship). Demo read: unify keys across WMS and OMS, eliminate shadow spreadsheets for alternates, and instrument mock recalls monthly; the goal is 100% regulatory target coverage with measurable drill time reduction, not checkbox forms without data lineage.",
 },
 },
 "gfi-uc-10-order-routing": {
 week: {
 topic: "orders",
 text: "This week on order routing I’m prioritizing B2B lines where sourcing-DC choice drives split shipments or emergency transfers. Demo read: re-score top accounts by temperature mix and proximity; for at-risk orders, simulate one-DC vs dual-ship margin and OTIF before committing so you don’t honor a bad default path.",
 },
 quarter: {
 topic: "orders",
 text: "Quarterly routing review: on-hand, proximity, temperature rules, and cadence vs split-shipment baseline (~30% reduction opportunity in the brief). Demo read: lock decision rules by customer class and lane, then measure quarter-over-quarter reduction in second drops and broker premiums.",
 },
 },
 "gfi-uc-11-infra-curated-programs": {
 week: {
 topic: "inventory",
 text: "This week for INFRA retailers I’m surfacing where curated programs need basket suggestions and education hooks-not more SKUs on the page. Demo read: two regions show wallet-share softness on specialty cheese sets; push data-driven bundles plus Institut-style notes without drowning independents in 11k-item search.",
 },
 quarter: {
 topic: "inventory",
 text: "Quarterly INFRA scale: measure attach rate on curated lists vs open catalog orders. Demo read: pair AI-driven assortment cards with training touchpoints so curation stays credible; target quarterly lift on member doors without ballooning manual merchandising hours.",
 },
 },
 "gfi-uc-12-customer-demand-intelligence": {
 week: {
 topic: "orders",
 text: "This week in the B2B portal I’m highlighting trending specialty items and regional benchmarks accounts actually open. Demo read: flag under-read categories where sell-through outpaces order cadence-those are sticky upsell moments vs broadline catalog plays.",
 },
 quarter: {
 topic: "orders",
 text: "Quarterly demand intelligence: session depth, reorder rate, and insight engagement by account tier. Demo read: specialty differentiation sticks when buyers trust your benchmarks; publish seasonal plays quarterly and tie recommendations to their order history, not generic lists.",
 },
 },
 "gfi-uc-13-shrinkage-detection": {
 week: {
 topic: "inventory",
 text: "This week’s shrink cut correlates spoilage, damage, and miscount signals across shifts and lanes at the worst two DCs. Demo read: temperature excursions line up with specific dock windows-tighten receiving discipline there before you assume theft or vendor fault.",
 },
 quarter: {
 topic: "inventory",
 text: "Quarterly shrink discipline vs your sub-1.5% COGS goal: roll up loss by lane, supplier, and shift pattern across 14 DCs. Demo read: finance and ops share one model so margin audits don’t rediscover the same systemic gaps each quarter.",
 },
 },
 "gfi-uc-14-dynamic-specialty-pricing": {
 week: {
 topic: "inventory",
 text: "This week on import specialty pricing I’m watching tariff and FX shocks against Chef’s Warehouse-class moves on overlapping SKUs. Demo read: scenario guardrails on anchor items protect premium positioning-publish min/max corridors buyers can defend in the field without nightly fire drills.",
 },
 quarter: {
 topic: "inventory",
 text: "Quarterly dynamic pricing: tie list moves to landed-cost scenarios, competitor indices, and ladder integrity on hero SKUs. Demo read: import-heavy books need policy, not one-off overrides; review margin leakage when tariffs swing and assortments overlap broadline adjacencies.",
 },
 },
 "gfi-uc-15-competitive-positioning": {
 week: {
 topic: "inventory",
 text: "This week’s competitive lens maps where broadline giants push into specialty adjacencies GFI cares about. Demo read: quantify share-of-wallet risk on affinage and education-led categories, then counter with curation plays your systems can scale-Institut narratives tied to in-stock hero lists, not generic markdowns.",
 },
 quarter: {
 topic: "inventory",
 text: "Quarterly moat review: Sysco-scale AI investment vs GFI’s expertise edge. Demo read: track encroachment SKUs, win-backs, and education-attached accounts; systems should make expertise repeatable-suggested assortments, risk alerts, and storytelling data-not spreadsheets only your veterans can read.",
 },
 },
};

/** Media profile: structured explainability replies for the top hero UCs. */
const MEDIA_EXPLORE_REPLIES = {
 "media-uc-01-content-conversation-trends": {
 week: {
 topic: "content_trend_intelligence",
 text: "Your Content Intelligence Analyst has run the full Listen → Understand → Detect → Explain loop on this week's audience conversation. Conversation around mythological drama reboots has grown for four straight weeks, driven mainly by nostalgia-led social clips and a rival network's successful relaunch, a Consumer + Culture signal, not a competitor-only move. Strongest signals come from Hindi-speaking audiences in Tier-2 markets. Similar themes have historically performed well in our mythology and family-drama slate. Every claim is source-traceable.",
 },
 quarter: {
 topic: "content_trend_intelligence_quarter",
 text: "For the quarter, the Content Intelligence Analyst has stacked sustained 4-week themes across the Consumer / Culture / Competition dimensions: mythology reboots, competitive cooking formats, and regional-language thrillers cluster above the sustained-momentum threshold. Viral dance cameos are visible but the explainability layer flags them as spikes, not durable interest. Treat this as an evidence-backed watchlist for Content Strategy, Programming, and Marketing, not a forecast.",
 },
 },
 "media-uc-02-future-content-opportunity": {
 week: {
 topic: "content_scoping",
 text: "Your Content Strategy Advisor has extended the trend intelligence into the Anticipate → Advise stages. Interest in competitive cooking formats has risen steadily for six months. Similar formats have historically performed strongly with 25-40 urban female audiences. On the current trajectory this is a potential content opportunity worth deeper research. The brief clearly separates fact vs. inferred trend vs. recommendation, and flags this theme as under-represented in our current slate.",
 },
 quarter: {
 topic: "content_scoping_quarter",
 text: "For the quarter, the Content Strategy Advisor has extrapolated sustained trends against our own historical hits and competitor slate: regional mythology, women-led mythology, and youth-led courtroom drama are all under-represented in our lineup and match historically successful audience patterns. This is decision support for commissioning, not a prediction of the next hit. Recommend two scoping-research shortlists before the next commissioning cycle.",
 },
 },
 "media-uc-03-competitor-content-benchmark": {
 week: {
 topic: "competitor_landscape",
 text: "This week, competing OTT platforms shipped 12 marquee releases vs. our 8; thriller catalog depth is 3 titles behind the top two rivals on Hindi. One competitor cut their mobile-only plan by 19% mid-week; ARPU compression risk on affected cohorts. Recommend a defensive read on plan pricing plus a Hindi thriller acquisition sweep.",
 },
 quarter: {
 topic: "competitor_landscape_quarter",
 text: "Quarterly, our category watch-hour share vs. the top 5 Indian OTT platforms is stable at #2 with a widening gap on regional-language originals. Sustained investment in Marathi and Tamil catalog closes the largest quarter-on-quarter share loss window.",
 },
 },
 "media-uc-04-digital-market-landscape": {
 week: {
 topic: "market_landscape_week",
 text: "This week's landscape scan flags a rival mobile-only price cut, two telco-bundle expansions in Tier-2 markets, and three upcoming premieres from category leaders overlapping our marquee launch window. Recommend a same-day marketing counter and scenario in the plan & pricing optimization use case.",
 },
 quarter: {
 topic: "market_landscape_quarter",
 text: "Quarterly landscape shows sustained bundling pressure from telco partnerships and a widening gap on regional-language depth. Category share loss concentrates on 2 of 5 named competitors; the response horizon is 6-8 weeks before ARPU compression cascades.",
 },
 },
 "media-uc-05-content-gap-analysis": {
 week: {
 topic: "content_gap_week",
 text: "This week, regional-language demand outpaces our acquisition cadence on Marathi thrillers and Tamil family drama. Competitor libraries out-index ours by 3 and 5 titles respectively. Under-represented themes are flagged for content-development scoping this cycle.",
 },
 quarter: {
 topic: "content_gap_quarter",
 text: "This quarter's content-gap read: Hindi thriller depth is 3 titles behind rivals; women-led mythology is thin; regional-language cricket adjacencies are absent. Commissioning shortlist prioritizes the three highest-momentum under-represented themes.",
 },
 },
 "media-uc-10-aeo-geo-visibility": {
 week: {
 topic: "aeo_geo_visibility",
 text: "This week, our brand or titles surface in 34% of tracked prompts across ChatGPT, Perplexity, Google AI Overviews, and Gemini. Generative-search referrals climbed 11% week-over-week. We rank #2 among named India OTT competitors; the category leader is 9 points ahead.",
 },
 quarter: {
 topic: "aeo_geo_visibility_quarter",
 text: "Quarterly AEO / GEO read: presence score is trending +6 pts, but the gap to the category leader has widened from 5 to 9 pts. Sustained editorial + title-metadata investment on the top 5 under-cited titles closes the gap fastest.",
 },
 },
 "media-uc-11-subscription-churn-predictor": {
 week: {
 topic: "churn_predictor_week",
 text: "This week's churn read: the monthly cohort renewing after the marquee title finished is 3.4 pts hotter than the trailing average. Save-offer eligibility should stage immediately for the 42K at-risk subscribers.",
 },
 quarter: {
 topic: "churn_predictor_quarter",
 text: "Quarterly, voluntary churn traces to two structural drivers: marquee-title finish gaps and mobile-plan price competition. Content pacing + save-offer eligibility windows are the two levers with quantified impact this quarter.",
 },
 },
 "media-uc-13-ad-yield-optimization": {
 week: {
 topic: "ad_yield_week",
 text: "This week's ad-yield read: AVOD fill on connected TV is 72% vs. 80% target; eCPM slipped 5% on mobile mid-week. Header-bidding waterfall audit + demand-partner rebalance recovers most of the gap without floor changes.",
 },
 quarter: {
 topic: "ad_yield_quarter",
 text: "Quarterly ad-yield trend: fill and eCPM are stable pan-network but connected TV keeps drifting. Structural fix is a quarterly demand-partner review + creative-fatigue rules; house-sold blend keeps yield defensible when programmatic dips.",
 },
 },
};

/**
 * @param {string} catalogId
 * @param {"week"|"quarter"} horizonKey
 * @param {string} profileId
 * @returns {{ text: string, meta: { variant: string, topic: string } } | null}
 */
export function getPlaygroundExploreHubReply(catalogId, horizonKey, profileId) {
 if (!catalogId || (horizonKey !== "week" && horizonKey !== "quarter")) return null;
 const id = normalizeDemoProfileId(profileId);
 if (id === MIDDLEBY_PROFILE_ID) {
 return getMiddlebyExploreHubReply(catalogId, horizonKey);
 }
 let table;
 if (id === GOURMET_FOODS_PROFILE_ID) table = GFI_EXPLORE_REPLIES;
 else if (id === MEDIA_PROFILE_ID) table = MEDIA_EXPLORE_REPLIES;
 else table = GENERIC_EXPLORE_REPLIES;
 const row = table[catalogId]?.[horizonKey];
 if (!row?.text || !row.topic) return null;
 return {
 text: row.text,
 meta: { variant: "playground-rich", topic: row.topic },
 };
}
