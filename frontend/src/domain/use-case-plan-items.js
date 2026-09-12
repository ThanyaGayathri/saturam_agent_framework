/**
 * Per-catalog plan-item resolution.
 *
 * Every seeded use case (138 across default retail / GFI / Middleby profiles)
 * needs Step 4 (AI Plan) / Step 5 (Plan Breakdown) / Step 6 (Orchestration)
 * items that reflect the specific work of that use case - not a generic
 * category template that renders identically for every use case in the same
 * business area (previously "NPI Tracking" and "Vendor Scorecards" both
 * showed the merchandise-category template, which was clearly wrong).
 *
 * Resolution priority for a given catalog entry:
 *
 *   1. Hand-authored override in PLAN_ITEMS_BY_CATALOG_ID (highest-visibility
 *      demo use cases; ~15 entries).
 *   2. Entry-derived generator (uses title + problemSignal + items[0] +
 *      sampleQuestion to produce 5 bespoke steps for any seeded entry).
 *   3. Title-derived fallback (Builder wizard for a brand-new draft that
 *      has a title but no catalog metadata yet).
 *   4. Category template (last resort; used when the Builder wizard is
 *      mid-configuration and only a category slug is available).
 *
 * Placeholders `{source1}`, `{source2}`, `{knowledge1}` get filled in from
 * confirmed sources / knowledge at resolve time.
 */

// ── Layer 1: hand-authored overrides for high-visibility demos ──────────────
export const PLAN_ITEMS_BY_CATALOG_ID = {
  // ── GFI (all 15 flagship use cases) ─────────────────────────────────────
  "gfi-uc-01-perishable-forecast": [
    { title: "Fetch perishable sales history and shelf-life data from {source1}", description: "Query 90-day sell-through, receipts, and waste for cheeses, charcuterie, and prepared foods across the 14 DCs." },
    { title: "Blend promo, seasonality, and event signals from {source2}", description: "Layer promotions, holidays, and trade-show demand onto the base forecast for short-shelf-life SKUs." },
    { title: "Apply spoilage tolerance and safety-stock rules from {knowledge1}", description: "Constrain the forecast with per-category shelf-life limits and DC-level safety-stock policy." },
    { title: "Rank SKUs by over/understock risk vs waste cost", description: "Score each perishable SKU by projected waste dollars vs projected lost sales; highlight top rebalance candidates." },
    { title: "Generate perishable buy and pull-forward recommendations", description: "Produce ranked adjustments (buy up, cut order, transfer) with waste-vs-fill-rate trade-off for buyer approval." },
  ],
  "gfi-uc-02-inter-dc-rebalance": [
    { title: "Fetch cross-DC on-hand and days-of-cover from {source1}", description: "Pull SKU-level inventory across all 14 DCs; compute days-of-cover using current sell-rate per DC." },
    { title: "Detect stranded slow-movers and regional stockouts from {source2}", description: "Flag DCs where a SKU is overstocked vs peer DCs where the same SKU is stocking out in the next 7-14 days." },
    { title: "Apply temperature-lane and transit-window rules from {knowledge1}", description: "Only propose transfers respecting temperature zones, minimum shipment sizes, and fleet route windows." },
    { title: "Rank rebalance moves by margin recovery vs transfer cost", description: "Score each proposed transfer by revenue-at-risk avoided minus internal transfer cost; suppress low-value moves." },
    { title: "Generate temperature-aware transfer recommendations", description: "Produce a routed transfer list (source DC → destination DC, SKU, qty, lane, ETA) for ops approval." },
  ],
  "gfi-uc-03-supplier-risk": [
    { title: "Fetch supplier, port, and disruption signals from {source1}", description: "Aggregate tariff notices, weather advisories, port dwell times, and small-producer financial flags for 2,000+ suppliers." },
    { title: "Score supplier fragility from {source2}", description: "Combine order history, single-source dependency, and geographic concentration to build a per-supplier fragility index." },
    { title: "Apply escalation thresholds from {knowledge1}", description: "Only surface suppliers breaching materiality thresholds (annual spend, category coverage, lead-time exposure)." },
    { title: "Rank suppliers by allocation-at-risk", description: "Score each at-risk supplier by dollars of committed allocations exposed in the next 30 days; highlight alternate-source availability." },
    { title: "Generate proactive alternate-source recommendations", description: "Produce a supplier-by-supplier action list: switch source, dual-source, or accept-and-hedge, with buyer approval." },
  ],
  "gfi-uc-04-markdown-optimization": [
    { title: "Fetch short-code inventory and price ladder from {source1}", description: "Query approaching-expiry SKUs across DCs with current price, remaining shelf life, and receipts pipeline." },
    { title: "Model expected sell-through per markdown wave from {source2}", description: "Apply elasticity model per category and shelf-life bucket to project sell-through at candidate discount depths." },
    { title: "Apply premium-brand price-ladder rules from {knowledge1}", description: "Guardrail: never breach anchor-SKU price floors that would compress premium positioning post-markdown." },
    { title: "Rank markdown waves by recovery vs write-off cost", description: "Score wave options (e.g. 15%→25%→40%) by expected net recovery vs risk of write-off; highlight the best wave per SKU." },
    { title: "Generate wave-by-wave markdown recommendations", description: "Produce per-SKU markdown schedule with recommended depth, timing, and channel targeting for pricing lead approval." },
  ],
  "gfi-uc-05-labor-optimization": [
    { title: "Fetch shift plans and forecast volume per DC from {source1}", description: "Query current pick-team rosters, waves, and forecast case volume by hour for each of the 14 DCs." },
    { title: "Model coverage vs demand and detect burnout signals from {source2}", description: "Compute pick-rate per shift, overtime creep, and consecutive-shift-load per associate; flag stress pockets." },
    { title: "Apply overtime and break-compliance rules from {knowledge1}", description: "Only propose schedules within OT budget, meal-break policy, and cross-training authorization." },
    { title: "Rank DCs by coverage risk and OT drift", description: "Score each DC-shift combination by staffing gap vs forecast volume and by projected OT dollars." },
    { title: "Generate DC-by-DC schedule and reallocation recommendations", description: "Produce recommended adds, wave rebalances, and cross-DC support suggestions for warehouse manager approval." },
  ],
  "gfi-uc-06-multi-temp-routing": [
    { title: "Fetch mixed-temperature order backlog and truck capacity from {source1}", description: "Query pending B2B orders with frozen / chilled / ambient item mix and available fleet capacity by route." },
    { title: "Solve routes respecting temperature zones from {source2}", description: "Optimize truck loading and route sequencing so each stop's temperature-zone segmentation stays valid end-to-end." },
    { title: "Apply time-window and backhaul rules from {knowledge1}", description: "Enforce customer receiving windows, driver-hour limits, and preferred backhaul opportunities." },
    { title: "Rank routes by carbon and cost vs SLA compliance", description: "Score each candidate route plan by cost per mile, CO2 emissions, and on-time reliability." },
    { title: "Generate optimized daily route plan", description: "Produce route sheets ready for dispatch: truck, stops, load plan, temperature checks, and driver instructions." },
  ],
  "gfi-uc-07-vendor-scorecards": [
    { title: "Fetch vendor performance signals from {source1}", description: "Aggregate fill rate, on-time delivery, quality incidents, lead-time variance, and case-accuracy per supplier over the last 12 months." },
    { title: "Roll up scores by tier, category, and region from {source2}", description: "Compute weighted scorecards; segment by strategic vs tactical vendor and by category exposure." },
    { title: "Apply category-level performance thresholds from {knowledge1}", description: "Compare each vendor to category-specific SLA thresholds and improvement plan commitments." },
    { title: "Rank vendors for negotiation and remediation", description: "Score suppliers by dollar impact of underperformance; highlight top negotiation and remediation candidates." },
    { title: "Generate scorecard packet for buyer QBRs", description: "Produce a per-vendor scorecard bundle with trend, root-cause narrative, and recommended discussion topics." },
  ],
  "gfi-uc-08-npi-tracking": [
    { title: "Fetch new-launch SKU sell-through from {source1}", description: "Query weekly sales, DC-fill, and returns for every SKU in its first 8 weeks post-launch across all channels." },
    { title: "Compare vs launch forecast and category baseline from {source2}", description: "Compute variance to plan and vs benchmark launches in the same category and price tier for peer comparison." },
    { title: "Apply launch criteria and gate rules from {knowledge1}", description: "Cross-check each SKU against launch-gate thresholds (sell-through %, distribution reach, gross margin, returns rate)." },
    { title: "Rank new items as Scale, Watch, or Cut", description: "Score every launch SKU on the composite of performance vs plan, momentum, and margin - producing a Scale/Watch/Cut decision." },
    { title: "Generate NPI portfolio recommendations", description: "Produce a launch-by-launch action list (increase buy, hold, discontinue) with justification for merch and finance approval." },
  ],
  "gfi-uc-09-fsma-traceability": [
    { title: "Fetch lot-level receipts, storage, and shipment records from {source1}", description: "Aggregate lot genealogy across inbound receiving, DC storage, and outbound orders for all 11,340+ SKUs." },
    { title: "Reconstruct end-to-end lot flow per SKU from {source2}", description: "Build a supplier → DC → customer chain per lot, joining across systems to prove one-up / one-down traceability." },
    { title: "Apply FSMA 204 record-keeping rules from {knowledge1}", description: "Validate every lot chain against FSMA Key Data Element requirements; flag gaps or delayed records." },
    { title: "Rank lots by traceback exposure and recall risk", description: "Score lots by supplier disruption exposure, customer breadth, and product risk category; prep priority for mock recall." },
    { title: "Generate recall-ready traceability packet", description: "Produce a per-lot traceability report ready for regulator or customer submission with 24-hour compliance ready state." },
  ],
  "gfi-uc-10-order-routing": [
    { title: "Fetch open B2B orders and DC availability from {source1}", description: "Pull all in-flight B2B orders with SKU-level requirements, delivery date, and current sourcing DC assignment." },
    { title: "Score sourcing options per order line from {source2}", description: "For each order line, score every DC candidate by on-hand, proximity, temperature compatibility, and split-shipment risk." },
    { title: "Apply routing and consolidation rules from {knowledge1}", description: "Enforce customer-specific delivery cadence, minimum-order thresholds, and preferred DC assignments." },
    { title: "Rank re-routing moves by split-shipment reduction", description: "Score routing changes by dollar savings from split-shipment reduction and emergency-transfer avoidance." },
    { title: "Generate sourcing changes for pending orders", description: "Produce a per-order re-routing list ready for fulfillment ops approval; target the ~30% split-shipment reduction opportunity." },
  ],
  "gfi-uc-11-infra-curated-programs": [
    { title: "Fetch INFRA member basket and category mix from {source1}", description: "Aggregate order history, category penetration, and repeat-purchase patterns for INFRA-affiliated independent retailers." },
    { title: "Identify curated program opportunities from {source2}", description: "Detect basket gaps vs successful peer members; find under-represented categories and specialty items with proven pull." },
    { title: "Apply member education and program rules from {knowledge1}", description: "Filter recommendations against active INFRA programs, education modules, and preferred-brand agreements." },
    { title: "Rank programs by member growth potential", description: "Score curated program suggestions by projected wallet-share lift, adoption likelihood, and merchandising fit per member." },
    { title: "Generate curated program packets per member", description: "Produce a member-by-member playbook: recommended assortments, education modules, and outreach cadence for INFRA lead approval." },
  ],
  "gfi-uc-12-customer-demand-intelligence": [
    { title: "Fetch trending specialty items and regional sell-through from {source1}", description: "Aggregate order signals across key accounts, regions, and seasons to surface rising specialty items." },
    { title: "Benchmark customer baskets vs regional peers from {source2}", description: "Compute per-customer basket vs peer accounts of similar segment / geography to identify demand-intelligence gaps." },
    { title: "Apply insight surfacing rules from {knowledge1}", description: "Filter to insights the customer can act on this cycle; suppress signals below the materiality or seasonality threshold." },
    { title: "Rank insights by revenue opportunity per account", description: "Score each customer-specific insight by revenue opportunity and confidence; prioritize what to surface in the portal." },
    { title: "Generate portal insight cards per account", description: "Produce per-account insight cards for the B2B portal: trending items, seasonal plays, and benchmark comparisons." },
  ],
  "gfi-uc-13-shrinkage-detection": [
    { title: "Fetch spoilage, damage, and count-variance events from {source1}", description: "Query loss events across DCs, lanes, and shifts; join with temperature-excursion logs and receiving discrepancies." },
    { title: "Correlate loss to lane, shift, and supplier from {source2}", description: "Build multi-dimensional loss attribution: cold-chain lane, associate shift, supplier lot, product family." },
    { title: "Apply materiality and shrink-target thresholds from {knowledge1}", description: "Compare loss rates against sub-1.5% COGS shrink target; escalate patterns exceeding acceptable variance." },
    { title: "Rank shrink hotspots by dollar impact and root cause", description: "Score each hotspot by annualized dollar exposure and confidence of root cause (temperature, handling, or supplier)." },
    { title: "Generate shrink remediation action list", description: "Produce a ranked action list (equipment fix, process change, supplier intervention) with expected dollar recovery." },
  ],
  "gfi-uc-14-dynamic-specialty-pricing": [
    { title: "Fetch tariff, FX, and competitor index moves from {source1}", description: "Query tariff bulletins, FX rates, and competitor specialty-index changes affecting imported specialty SKUs." },
    { title: "Model landed-cost and margin scenarios from {source2}", description: "Recompute landed cost and gross margin per SKU under the new tariff / FX regime; flag margin compression risk." },
    { title: "Apply premium price-ladder guardrails from {knowledge1}", description: "Only propose price actions respecting anchor-SKU price ladders and premium-positioning rules." },
    { title: "Rank SKUs by margin exposure and price-action confidence", description: "Score each SKU by dollars-at-risk and confidence in the price-move recommendation given competitor context." },
    { title: "Generate specialty pricing recommendations", description: "Produce per-SKU price actions (hold, raise, hedge) with scenario-aware justification for pricing lead approval." },
  ],
  "gfi-uc-15-competitive-positioning": [
    { title: "Fetch broadline competitor specialty moves from {source1}", description: "Monitor broadline distributor expansions into specialty adjacencies (assortment, pricing, curation, education)." },
    { title: "Quantify share-of-wallet risk per account from {source2}", description: "Model per-account wallet exposure to broadline encroachment; identify accounts most at risk of split-loyalty." },
    { title: "Apply specialty moat differentiators from {knowledge1}", description: "Match at-risk accounts to Institut du Fromage-style education, curation, and expert-led programs GFI can deploy." },
    { title: "Rank accounts by defense priority", description: "Score at-risk accounts by revenue exposure, competitor pressure level, and program-fit readiness." },
    { title: "Generate account defense playbooks", description: "Produce per-account counter-plans (curation packet, education program, dealer visit) for sales-lead approval." },
  ],

  // ── Retail (highest-visibility flagship use cases) ───────────────────────
  "gallery-uc-01": [
    { title: "Fetch current SKU-level on-hand and sales velocity from {source1}", description: "Query inventory, receipts pipeline, and 30-day sell rate for every active SKU across DCs and stores." },
    { title: "Project days-of-cover and 14-day stockout risk from {source2}", description: "Apply demand forecast and lead-time variance to compute days-of-cover; flag SKUs at risk of stockout within 14 days." },
    { title: "Apply reorder thresholds and category priority from {knowledge1}", description: "Cross-check at-risk SKUs against per-category safety-stock rules and supplier lead-time tolerance." },
    { title: "Rank stockout risks by revenue exposure", description: "Score each at-risk SKU by revenue at risk, brand priority, and substitutability." },
    { title: "Generate reorder recommendations for approval", description: "Produce a ranked reorder list with quantities and preferred suppliers; route as Decision Cards or auto-raise POs within guardrails." },
  ],
  "gallery-uc-02": [
    { title: "Fetch open orders and node-level inventory from {source1}", description: "Pull all pending orders with SKU lines and available inventory across fulfillment nodes (DCs and stores)." },
    { title: "Score sourcing options by cost and SLA from {source2}", description: "For each order line, score every candidate node by fulfillment cost, carrier capacity, and SLA reliability." },
    { title: "Apply routing rules and constraints from {knowledge1}", description: "Enforce customer preferences, carrier capacity limits, and node-level fulfillment authorities." },
    { title: "Rank routing changes by savings and split-shipment reduction", description: "Score each proposed re-route by dollar savings, split-shipment avoidance, and SLA reliability lift." },
    { title: "Generate optimized sourcing plan for pending orders", description: "Produce a per-order routing recommendation for fulfillment ops approval or auto-execute within guardrails." },
  ],
  "gallery-uc-34": [
    { title: "Fetch promo events and sales lift signals from {source1}", description: "Query all active and recent promos with baseline sell-through, halo/cannibalization data, and channel targeting." },
    { title: "Attribute incremental revenue by promo from {source2}", description: "Apply attribution model to isolate promo-driven lift from overlapping campaigns, halo effects, and cannibalization." },
    { title: "Apply attribution and materiality rules from {knowledge1}", description: "Filter to promos with statistically significant lift; suppress noise from below-threshold overlap." },
    { title: "Rank promos by ROI and margin contribution", description: "Score each promo by net incremental revenue, margin impact, and cost-per-incremental-unit." },
    { title: "Generate ROI report and next-cycle recommendations", description: "Produce a promo-by-promo ROI card with recommended actions (scale, discontinue, restructure) for pricing lead approval." },
  ],
  "gallery-uc-35": [
    { title: "Fetch inventory shrink and count-variance signals from {source1}", description: "Aggregate cycle-count variances, adjustments, and known-loss events by region, store format, and category." },
    { title: "Correlate shrink to store, staff, and lane from {source2}", description: "Attribute loss patterns to store-level, staff-shift, and lane-level factors; highlight concentration hotspots." },
    { title: "Apply loss-tolerance and investigation rules from {knowledge1}", description: "Compare shrink rates against category and store-format tolerances; escalate patterns exceeding thresholds." },
    { title: "Rank hotspots by dollar impact and root-cause confidence", description: "Score each concentration by annualized dollar exposure and confidence in operational vs external root cause." },
    { title: "Generate loss-prevention action list", description: "Produce a ranked remediation list (equipment, staffing, process) with expected dollar recovery for LP-lead approval." },
  ],

  // ── Middleby (highest-visibility analytics use cases) ────────────────────
  "middleby-uc-01": [
    { title: "Fetch anonymous session behavior from {source1}", description: "Query sessions with dwell time, page depth, repeat visits, and multi-brand browsing signals for the last 30 days." },
    { title: "Score sessions on illustrative intent signals from {source2}", description: "Compute a behavior-driven intent score per session; production add-to-quote events will strengthen this later." },
    { title: "Apply intent thresholds from {knowledge1}", description: "Filter to sessions above the high-intent threshold; segment by brand, region, and time-to-quote horizon." },
    { title: "Rank sessions by 7-day quote likelihood", description: "Score every high-intent session by likelihood of RFQ submission in the next 7 days; highlight the top decile." },
    { title: "Generate high-intent session worklist for sales", description: "Produce a session-by-session worklist with intent signals and recommended outreach for sales-rep review." },
  ],
  "middleby-uc-18": [
    { title: "Fetch per-brand session traffic from {source1}", description: "Query GA brand-level sessions and dwell for the last 30 days across the Middleby brand portfolio." },
    { title: "Fetch per-brand HubSpot quote counts from {source2}", description: "Query real quote counts per brand from HubSpot, normalized to the same 30-day window as the session data." },
    { title: "Apply materiality thresholds from {knowledge1}", description: "Filter to brands with meaningful traffic AND quote volume; suppress low-traffic outliers from the gap analysis." },
    { title: "Rank brands by browse-to-quote gap", description: "Score every qualifying brand by absolute browse-to-quote gap and by percentage under-conversion vs portfolio average." },
    { title: "Generate biggest browse-to-quote gap report", description: "Produce a brand-ranked report highlighting the top three gap brands with hypothesized causes for merch review." },
  ],

  // ── Retail (default gallery) - Inventory ─────────────────────────────────
  "gallery-uc-11": [
    { title: "Fetch seasonal SKU sales and forecast history from {source1}", description: "Query 24 months of sell-through, receipts, and forecast records for SKUs flagged as seasonal in the merchandising taxonomy." },
    { title: "Compute forecast error and excess-stock exposure from {source2}", description: "Compare forecast vs actuals per SKU and season; project weeks-of-cover overhang at current sell-rate." },
    { title: "Apply seasonal forecasting policy from {knowledge1}", description: "Cross-reference each SKU against seasonal-buy tolerances, in-season override rules, and end-of-season exit plans." },
    { title: "Rank SKUs by forecast-error dollar exposure", description: "Score each SKU by projected excess-stock dollars and by markdown risk if error persists to end of season." },
    { title: "Generate forecast-tuning and buy-adjustment recommendations", description: "Produce ranked actions (cut open buys, pull markdown forward, retune model inputs) for planner approval." },
  ],
  "gallery-uc-12": [
    { title: "Fetch on-hand and sales velocity by SKU and region from {source1}", description: "Query inventory positions and recent sell-rate across regions and channels; identify SKUs with declining velocity." },
    { title: "Compute weeks-of-cover and aging buckets from {source2}", description: "Bucket SKUs into fresh, slow, stagnant, and dead based on days-since-last-sale and weeks-of-cover per node." },
    { title: "Apply slow-mover policy thresholds from {knowledge1}", description: "Cross-reference aging SKUs against category-specific tolerance windows and required liquidation timing." },
    { title: "Rank slow movers by tied-up capital and salvage risk", description: "Score each SKU by carrying cost, obsolescence risk, and expected salvage value if not moved in the next cycle." },
    { title: "Generate slow-mover liquidation recommendations", description: "Produce ranked options (transfer, markdown, clearance channel, write-off) with expected recovery for planner approval." },
  ],
  "gallery-uc-13": [
    { title: "Fetch SKU-level on-hand across DCs from {source1}", description: "Query current inventory positions and days-of-cover per DC for every SKU flagged as active in the assortment." },
    { title: "Detect over/under imbalance across nodes from {source2}", description: "Identify SKUs where one DC is overstocked while a peer DC is projected to stock out in the next 14 days." },
    { title: "Apply cross-DC transfer rules from {knowledge1}", description: "Enforce minimum shipment sizes, lane availability, and cost-per-transfer thresholds from the logistics playbook." },
    { title: "Rank rebalance moves by fulfilment lift vs transfer cost", description: "Score each proposed transfer by fill-rate lift, revenue-at-risk avoided, and net-of-transfer-cost benefit." },
    { title: "Generate DC-to-DC transfer plan", description: "Produce a routed transfer list (source, destination, SKU, qty, lane) ready for logistics approval." },
  ],
  "gallery-uc-41": [
    { title: "Fetch actual lead-time distributions per SKU from {source1}", description: "Query 12 months of PO-to-receipt intervals per SKU and supplier; compute mean, variance, and 90th-percentile lead time." },
    { title: "Model service-level vs safety-stock trade-off from {source2}", description: "Simulate service-level outcomes at candidate safety-stock levels using the observed lead-time variability." },
    { title: "Apply service-level and inventory policy from {knowledge1}", description: "Cross-check current safety-stock parameters against the finance-approved service level and inventory cost budget." },
    { title: "Rank SKUs where parameters diverge from optimal", description: "Score each SKU by dollar impact of the current-vs-optimal gap in either understock risk or overstock cost." },
    { title: "Generate safety-stock parameter update list", description: "Produce a ranked list of parameter changes with projected service-level lift and working-capital impact for planner approval." },
  ],
  "gallery-uc-51": [
    { title: "Fetch inter-store transfer records and receive dates from {source1}", description: "Query all transfers initiated in the last 90 days with source store, destination store, SKU, and promised receive date." },
    { title: "Compute on-time-receive rate by lane and store from {source2}", description: "Attribute delays to originating store, receiving store, and transport lane; identify chronic underperformers." },
    { title: "Apply transfer-SLA policy from {knowledge1}", description: "Cross-reference actual vs promised receive dates against the internal transfer SLA and escalation rules." },
    { title: "Rank chronic delay hotspots by revenue exposure", description: "Score delayed transfers by tied-up inventory dollars and by fulfilment impact at the receiving store." },
    { title: "Generate transfer-SLA remediation actions", description: "Produce a ranked list of interventions (lane change, batching, store-level coaching) for logistics-lead approval." },
  ],
  "gallery-uc-61": [
    { title: "Fetch supplier confirmed vs actual PO dates from {source1}", description: "Query 12 months of PO history with confirmed dates, actual receipt dates, and any expedite events per supplier." },
    { title: "Detect lead-time drift patterns from {source2}", description: "Identify suppliers whose recent lead times are drifting materially above their historical mean or SLA commitment." },
    { title: "Apply supplier-SLA and escalation rules from {knowledge1}", description: "Cross-reference drift against supplier scorecards, contractual SLAs, and escalation thresholds." },
    { title: "Rank suppliers by drift severity and category exposure", description: "Score each drifting supplier by dollar exposure to slipping lead times and by SKU criticality in the category." },
    { title: "Generate supplier drift escalation packet", description: "Produce a per-supplier packet with drift narrative, exposure numbers, and recommended remediation for buyer approval." },
  ],
  "gallery-uc-66": [
    { title: "Fetch demand forecast and current inventory position from {source1}", description: "Query projected demand for the next 30 days and current on-hand plus in-transit inventory across priority categories." },
    { title: "Quantify revenue at risk from inventory gaps from {source2}", description: "Compute dollars-at-risk per SKU where projected demand exceeds available supply within the analysis window." },
    { title: "Apply priority-category rules from {knowledge1}", description: "Filter to categories flagged as revenue-critical; suppress non-priority items to keep the action list focused." },
    { title: "Rank SKUs by dollars-at-risk and margin exposure", description: "Score every at-risk SKU by revenue exposure, margin contribution, and substitutability of the missing product." },
    { title: "Generate revenue-at-risk mitigation plan", description: "Produce a ranked list (expedite receipts, transfer, substitute promo) with expected recovery for planner approval." },
  ],
  "gallery-uc-67": [
    { title: "Fetch vendor OTIF signals from {source1}", description: "Aggregate fill rate, on-time-delivery, and dock-scheduling metrics per leading vendor across the last 12 months." },
    { title: "Detect vendors slipping on OTIF from {source2}", description: "Identify vendors whose recent fill rate or on-time performance is trending materially below their 12-month baseline." },
    { title: "Apply OTIF thresholds and remediation rules from {knowledge1}", description: "Cross-reference declining vendors against SLA thresholds and defined remediation triggers from the vendor playbook." },
    { title: "Rank vendors by OTIF-driven revenue exposure", description: "Score each slipping vendor by dollar exposure of missed shipments and by category dependency risk." },
    { title: "Generate vendor OTIF remediation actions", description: "Produce a per-vendor packet with OTIF trend, root-cause hypothesis, and recommended intervention for buyer approval." },
  ],

  // ── Retail (default gallery) - Orders ────────────────────────────────────
  "gallery-uc-14": [
    { title: "Fetch multi-node order records and shipment splits from {source1}", description: "Query orders in the last 90 days that shipped from more than one node; capture line items, nodes, and carrier events." },
    { title: "Attribute split root cause from {source2}", description: "Attribute each split to inventory position, sourcing logic, or node capacity constraint using fulfilment event data." },
    { title: "Apply sourcing and consolidation rules from {knowledge1}", description: "Cross-check current sourcing logic against the consolidation policy and preferred-node rules from the ops playbook." },
    { title: "Rank split patterns by cost and SLA impact", description: "Score each split pattern by extra shipping cost, delay to customer, and frequency across the order pool." },
    { title: "Generate sourcing-logic change recommendations", description: "Produce a ranked list of rule adjustments (stock policy, node priority, threshold) for fulfilment-lead approval." },
  ],
  "gallery-uc-15": [
    { title: "Fetch SLA-missed orders and priority segments from {source1}", description: "Query orders that missed SLA in the last 30 days for priority customer segments (VIP, contract, subscription)." },
    { title: "Attribute breach cause to node, carrier, or exception from {source2}", description: "Attribute each breach to a root cause using fulfilment events, carrier scans, and exception logs." },
    { title: "Apply priority-segment SLA policy from {knowledge1}", description: "Cross-check breach severity against tier-specific SLA commitments and escalation triggers." },
    { title: "Rank breach patterns by segment revenue impact", description: "Score each recurring breach pattern by dollar exposure to the priority segment and by repeat-customer risk." },
    { title: "Generate SLA-breach remediation action list", description: "Produce ranked interventions (node capacity, carrier swap, exception routing) for fulfilment-lead approval." },
  ],
  "gallery-uc-16": [
    { title: "Fetch BOPIS orders and pickup-ready timestamps from {source1}", description: "Query BOPIS orders at high-volume stores with placed, ready, and picked-up timestamps for the last 30 days." },
    { title: "Compute pickup-ready-rate and slippage causes from {source2}", description: "Compute ready-within-SLA rate per store and attribute misses to inventory, staffing, or handoff friction." },
    { title: "Apply BOPIS reliability targets from {knowledge1}", description: "Cross-reference store performance against BOPIS SLA targets and store-format expectations from the ops playbook." },
    { title: "Rank stores by reliability gap and customer impact", description: "Score each underperforming store by pickup-slippage rate and by cancellation / satisfaction impact." },
    { title: "Generate BOPIS reliability action list", description: "Produce a per-store list of fixes (inventory accuracy, handoff redesign, staffing) for store-ops-lead approval." },
  ],
  "gallery-uc-42": [
    { title: "Fetch backordered lines and channel allocation from {source1}", description: "Query current backorders with SKU, channel, customer tier, and allocation timestamps under constrained supply." },
    { title: "Model allocation impact by channel from {source2}", description: "Simulate customer-value outcomes under alternative allocation rules across store, web, wholesale, and B2B channels." },
    { title: "Apply allocation policy from {knowledge1}", description: "Cross-reference proposed allocation against the finance-approved channel-priority rules and margin targets." },
    { title: "Rank allocation changes by margin and retention impact", description: "Score each allocation swap by margin delta and by expected retention impact on high-value customers." },
    { title: "Generate revised allocation plan", description: "Produce a ranked reallocation list with expected margin lift and customer-experience impact for ops-lead approval." },
  ],
  "gallery-uc-52": [
    { title: "Fetch same-day-ship performance on promo days from {source1}", description: "Query order-cut-off adherence and ship-same-day rate for the last 5 promotional peak days across warehouses." },
    { title: "Model cut-off-time sensitivity from {source2}", description: "Simulate impact of shifting the cut-off by 30 / 60 / 90 minutes on ship-same-day rate and warehouse workload." },
    { title: "Apply cut-off and warehouse-capacity rules from {knowledge1}", description: "Cross-reference proposed cut-offs against warehouse-shift plans and carrier pickup windows from the ops playbook." },
    { title: "Rank cut-off changes by promise-kept lift", description: "Score each candidate cut-off shift by same-day promise-kept lift and by cost of the required warehouse capacity change." },
    { title: "Generate cut-off-time optimisation recommendation", description: "Produce a warehouse-by-warehouse recommendation for peak-day cut-off adjustments for ops-lead approval." },
  ],
  "gallery-uc-62": [
    { title: "Fetch DSV order events and milestone timestamps from {source1}", description: "Query drop-ship-vendor orders with carrier-scan and delivery-commit milestones for the last 60 days." },
    { title: "Detect vendors missing carrier or delivery milestones from {source2}", description: "Identify DSVs whose scan-on-time or deliver-on-time rate is materially below the fleet baseline." },
    { title: "Apply DSV performance thresholds from {knowledge1}", description: "Cross-reference vendor performance against contractual delivery SLAs and remediation triggers." },
    { title: "Rank DSVs by customer-experience exposure", description: "Score each vendor by orders affected, revenue exposure, and repeat-purchase risk from delivery misses." },
    { title: "Generate DSV performance escalation packet", description: "Produce a per-vendor packet with milestone-miss narrative and recommended remediation for ops-lead approval." },
  ],
  "gallery-uc-68": [
    { title: "Fetch chargeback and fraud events from {source1}", description: "Query chargebacks and fraud flags in the last 60 days segmented by tender type, basket value, and channel." },
    { title: "Detect chargeback concentration patterns from {source2}", description: "Attribute chargeback spikes to specific tender, basket-value, or velocity signals using pattern detection." },
    { title: "Apply fraud rules and threshold policy from {knowledge1}", description: "Cross-reference detected patterns against the fraud playbook thresholds and issuer-agreement rules." },
    { title: "Rank chargeback patterns by dollar exposure and repeat risk", description: "Score each pattern by chargeback dollar exposure, incremental cost, and repeat-fraud probability." },
    { title: "Generate fraud-rule tuning recommendations", description: "Produce a ranked list of threshold or rule updates with expected chargeback-reduction impact for risk-lead approval." },
  ],

  // ── Retail (default gallery) - Merchandise ───────────────────────────────
  "gallery-uc-03": [
    { title: "Fetch active assortment and competitor SKU signals from {source1}", description: "Query current SKUs by category alongside monitored competitor assortments and customer-demand indicators." },
    { title: "Detect assortment gaps and adjacencies from {source2}", description: "Identify categories with weak SKU coverage vs peer competitors or unmet customer demand signals." },
    { title: "Apply assortment strategy rules from {knowledge1}", description: "Cross-reference detected gaps against category strategy, brand mandates, and financial gate criteria." },
    { title: "Rank gaps by revenue opportunity and confidence", description: "Score each gap by projected revenue lift, brand-fit confidence, and time-to-execute for a range extension." },
    { title: "Generate assortment expansion recommendations", description: "Produce a ranked list of new-SKU or range-extension proposals for merchandising-lead approval." },
  ],
  "gallery-uc-17": [
    { title: "Fetch shelf-execution audits and store cluster metadata from {source1}", description: "Query recent planogram compliance audits, shelf-execution photos, and store-cluster attributes for the last cycle." },
    { title: "Detect compliance gaps vs headquarters plan from {source2}", description: "Attribute gaps to store cluster, category, and specific planogram sections; highlight repeat-offender combinations." },
    { title: "Apply planogram compliance rules from {knowledge1}", description: "Cross-reference audit findings against critical-item lists and category-level compliance thresholds." },
    { title: "Rank stores and categories by compliance risk", description: "Score each cluster-category combo by dollar impact of shelf-execution gaps and repeat-audit failure risk." },
    { title: "Generate compliance remediation action list", description: "Produce a store-by-store fix list (reset, coaching, layout change) for store-ops-lead approval." },
  ],
  "gallery-uc-18": [
    { title: "Fetch launch-SKU performance for weeks 1-8 from {source1}", description: "Query weekly sell-through, distribution reach, and returns for all launches in their first 8 weeks." },
    { title: "Compare each launch against category benchmarks from {source2}", description: "Compute variance vs peer launches in the same category and price tier; highlight over- and under-performers." },
    { title: "Apply launch-gate rules from {knowledge1}", description: "Cross-check each launch against sell-through gates, margin gates, and returns-rate thresholds from the NPI playbook." },
    { title: "Rank launches by scale, watch, or cut decision", description: "Score every launch on the composite of performance vs plan, momentum, and margin for a portfolio decision." },
    { title: "Generate NPI performance action list", description: "Produce a launch-by-launch action list (scale, watch, cut) with justification for merchandising-lead approval." },
  ],
  "gallery-uc-19": [
    { title: "Fetch category sell-through curves and markdown history from {source1}", description: "Query weekly sell-through and past markdown timing per seasonal category over the last 4 seasons." },
    { title: "Model markdown-timing sensitivity from {source2}", description: "Simulate expected end-of-season inventory position under alternative markdown-start weeks and depth ladders." },
    { title: "Apply markdown cadence policy from {knowledge1}", description: "Cross-reference proposed timing against category-specific markdown-cadence rules and price-ladder floors." },
    { title: "Rank timing changes by exit-inventory and margin impact", description: "Score each candidate timing shift by end-of-season inventory reduction and net margin impact." },
    { title: "Generate season markdown cadence recommendation", description: "Produce a category-by-category markdown-timing plan with expected sell-through lift for pricing-lead approval." },
  ],
  "gallery-uc-43": [
    { title: "Fetch vendor fill-rate history for top revenue SKUs from {source1}", description: "Query 12 months of vendor shipment records for SKUs flagged in the top-revenue tier of each category." },
    { title: "Detect fill-rate degradation vs baseline from {source2}", description: "Identify vendors whose recent fill rate on top-revenue SKUs is materially below their historical baseline." },
    { title: "Apply vendor performance rules from {knowledge1}", description: "Cross-reference declining fill rate against the vendor scorecard thresholds and remediation triggers." },
    { title: "Rank vendors by top-SKU exposure", description: "Score each degrading vendor by revenue exposure of their affected top SKUs and by category dependency." },
    { title: "Generate vendor fill-rate escalation packet", description: "Produce a per-vendor packet with fill-rate trend, exposure, and recommended remediation for buyer approval." },
  ],
  "gallery-uc-53": [
    { title: "Fetch private-label and national-brand share by category from {source1}", description: "Query own-brand vs national-brand share within priority categories for the last 12 rolling months." },
    { title: "Detect penetration gap vs plan from {source2}", description: "Compare current private-label share against the annual penetration plan and category-specific target share." },
    { title: "Apply own-brand growth policy from {knowledge1}", description: "Cross-reference gap size against category-strategy targets, brand-mandate rules, and margin objectives." },
    { title: "Rank categories by own-brand share opportunity", description: "Score each priority category by share-gap dollars and by ease of shifting mix through range and merchandising." },
    { title: "Generate own-brand penetration action list", description: "Produce ranked interventions (range change, planogram, promo) with expected share lift for merchandising-lead approval." },
  ],
  "gallery-uc-63": [
    { title: "Fetch post-markdown inventory positions from {source1}", description: "Query weeks-on-hand and residual inventory dollars for SKUs whose markdown window has closed in the last 90 days." },
    { title: "Detect markdown-exit tail risk from {source2}", description: "Identify SKUs where post-markdown inventory still exceeds target weeks-on-hand and residual carrying cost is climbing." },
    { title: "Apply markdown-exit policy from {knowledge1}", description: "Cross-reference tail-inventory positions against clearance channel options and write-off thresholds." },
    { title: "Rank SKUs by residual dollar and clearance urgency", description: "Score each tail SKU by residual inventory dollars, obsolescence risk, and expected clearance recovery." },
    { title: "Generate markdown-exit clearance action list", description: "Produce ranked options (deep clearance, jobber, write-off) with expected recovery for pricing-lead approval." },
  ],

  // ── Retail (default gallery) - Store Ops ─────────────────────────────────
  "gallery-uc-04": [
    { title: "Fetch confirmed rosters and foot-traffic forecast from {source1}", description: "Query staff schedules and hourly foot-traffic forecast per store for the next 14 days." },
    { title: "Model coverage vs demand by store-hour from {source2}", description: "Compute required headcount using the staffing model and detect stores or hours falling below coverage minimum." },
    { title: "Apply staffing ratio rules from {knowledge1}", description: "Cross-reference required-vs-confirmed coverage against staffing ratios for each store format and traffic band." },
    { title: "Rank stores by coverage gap and revenue exposure", description: "Score each understaffed store-hour by projected lost transactions and by customer-wait-time impact." },
    { title: "Generate schedule adjustment recommendations", description: "Produce a store-by-store list of adds, transfers, or shift changes for store-ops-lead approval." },
  ],
  "gallery-uc-20": [
    { title: "Fetch task-completion records across districts from {source1}", description: "Query required-vs-completed task logs by store, role, and task category for the last 30 days." },
    { title: "Compute completion rates and overdue trends from {source2}", description: "Compute completion rate, overdue rate, and repeat-miss rate per district; segment by task criticality." },
    { title: "Apply task-compliance rules from {knowledge1}", description: "Cross-reference district performance against required task list and compliance thresholds from the ops playbook." },
    { title: "Rank districts by compliance risk", description: "Score each district by overall completion, trend direction, and severity of missed tasks." },
    { title: "Generate district task-completion action list", description: "Produce a district-by-district remediation list with prioritized task categories for ops-lead approval." },
  ],
  "gallery-uc-21": [
    { title: "Fetch cashier tender-variance records from {source1}", description: "Query cash and tender variances by store, register, and shift for the last 90 days." },
    { title: "Detect trend and concentration patterns from {source2}", description: "Identify stores where variance is trending up and attribute to specific registers, shifts, or cashier cohorts." },
    { title: "Apply cash-handling policy from {knowledge1}", description: "Cross-reference detected variance against acceptable tolerance thresholds and required investigation triggers." },
    { title: "Rank stores by variance dollar exposure and repeat risk", description: "Score each store by dollar variance, growth rate, and shift-level concentration for prioritised review." },
    { title: "Generate variance-investigation action list", description: "Produce a per-store list of interventions (audit, coaching, process change) for LP-lead approval." },
  ],
  "gallery-uc-22": [
    { title: "Fetch opening-checklist completion timestamps from {source1}", description: "Query per-store opening-checklist logs with expected vs actual completion times for the last 30 days." },
    { title: "Detect first-hour readiness impact from {source2}", description: "Correlate opening delays with first-hour sales performance and customer-experience signals per store." },
    { title: "Apply opening-checklist policy from {knowledge1}", description: "Cross-reference miss patterns against the required opening tasks and store-format expectations." },
    { title: "Rank stores by first-hour revenue exposure", description: "Score each store by projected first-hour sales impact from readiness gaps and by repeat-miss frequency." },
    { title: "Generate opening-readiness action list", description: "Produce a per-store remediation list (task retiming, coaching, staffing) for store-ops-lead approval." },
  ],
  "gallery-uc-44": [
    { title: "Fetch site-level utility spend and operating hours from {source1}", description: "Query utility bills, foot-traffic, and operating-hour records per site for the last 12 months." },
    { title: "Detect utility-spend anomalies from {source2}", description: "Identify sites where utility spend is spiking without matching traffic, hours, or seasonal pattern." },
    { title: "Apply energy-management rules from {knowledge1}", description: "Cross-reference anomalies against maintenance-schedule policy and expected consumption baselines." },
    { title: "Rank sites by anomaly-driven dollar exposure", description: "Score each anomalous site by annualised utility overspend and by likely fixable root cause." },
    { title: "Generate energy anomaly action list", description: "Produce a per-site remediation list (audit, equipment check, schedule change) for facilities-lead approval." },
  ],
  "gallery-uc-54": [
    { title: "Fetch open maintenance work orders and SLAs from {source1}", description: "Query open work orders by site with priority, SLA target, and current age across all critical repair categories." },
    { title: "Detect SLA-breach clusters at high-traffic sites from {source2}", description: "Identify sites where multiple critical work orders are aging past SLA and where customer-experience risk is highest." },
    { title: "Apply maintenance priority rules from {knowledge1}", description: "Cross-reference each backlog against priority rules for critical repairs and high-traffic-site escalation triggers." },
    { title: "Rank work orders by traffic impact and SLA breach", description: "Score each aged work order by store traffic, expected disruption, and category criticality for prioritised dispatch." },
    { title: "Generate work-order dispatch plan", description: "Produce a ranked dispatch list with vendor assignment and target complete-by date for facilities-lead approval." },
  ],
  "gallery-uc-64": [
    { title: "Fetch temperature-monitoring logs from {source1}", description: "Query temperature-sensor readings across cold-chain equipment for the last 30 days with excursion timestamps." },
    { title: "Detect excursions vs QC-hold triggers from {source2}", description: "Identify temperature excursions that exceed threshold duration and where the risk of a QC hold is highest." },
    { title: "Apply cold-chain compliance rules from {knowledge1}", description: "Cross-reference excursions against product-category tolerance windows and mandatory hold triggers." },
    { title: "Rank excursions by product-loss exposure", description: "Score each excursion by projected product-loss dollars and by regulatory-reporting exposure." },
    { title: "Generate cold-chain intervention list", description: "Produce a ranked action list (equipment check, product hold, reset) for QA-lead approval." },
  ],
  "gallery-uc-69": [
    { title: "Fetch door counts and basket conversion by district from {source1}", description: "Query door-counter feeds and basket-conversion per district over the last 90 days at daily granularity." },
    { title: "Detect traffic-up / conversion-flat divergence from {source2}", description: "Identify districts where door counts are rising but conversion is flat or declining; attribute to root cause." },
    { title: "Apply conversion-improvement rules from {knowledge1}", description: "Cross-reference divergence patterns against staffing model, planogram compliance, and merchandising rules." },
    { title: "Rank districts by conversion opportunity", description: "Score each divergent district by lost-revenue exposure from unconverted traffic and by root-cause confidence." },
    { title: "Generate conversion-lift action list", description: "Produce a per-district recommendation (staffing, layout, greeter) for retail-ops-lead approval." },
  ],

  // ── Retail (default gallery) - E-Com ─────────────────────────────────────
  "gallery-uc-05": [
    { title: "Fetch cart-abandonment events from {source1}", description: "Query sessions with add-to-cart but no checkout completion over the last 30 days; capture funnel step of abandonment." },
    { title: "Attribute abandonment to root cause from {source2}", description: "Attribute drop-off to shipping, payment friction, price sensitivity, or account creation using session-level signals." },
    { title: "Apply re-engagement policy from {knowledge1}", description: "Cross-reference eligible sessions against re-engagement rules (opt-in, incentive-depth caps, channel cadence)." },
    { title: "Rank abandonment segments by recoverable revenue", description: "Score each root-cause segment by recoverable revenue at expected re-engagement conversion rate." },
    { title: "Generate re-engagement campaign recommendations", description: "Produce ranked campaign options (email nudge, incentive, retarget) with expected recovery for ecom-lead approval." },
  ],
  "gallery-uc-23": [
    { title: "Fetch site-search queries and result metrics from {source1}", description: "Query top-volume search terms with zero-result rate, click-through rate, and downstream conversion for the last 30 days." },
    { title: "Detect zero-result and weak-CTR outliers from {source2}", description: "Identify high-volume search terms with disproportionate zero-result rate or weak click-through vs peer terms." },
    { title: "Apply search-relevance rules from {knowledge1}", description: "Cross-reference weak terms against synonym mappings, redirect policy, and merchandising boosts from the catalog playbook." },
    { title: "Rank search terms by revenue-recovery opportunity", description: "Score each weak term by lost-conversion revenue and by ease of relevance fix (synonym, redirect, merch)." },
    { title: "Generate search-relevance action list", description: "Produce ranked fixes (add synonyms, tune ranking, merchandising boost) for ecom-lead approval." },
  ],
  "gallery-uc-24": [
    { title: "Fetch payment-authorisation events from {source1}", description: "Query auth attempts, declines, and success events across issuers and regions for the last 30 days." },
    { title: "Detect decline concentration by issuer and region from {source2}", description: "Identify issuer-region combinations with abnormally high decline rate vs the fleet baseline." },
    { title: "Apply payment routing and retry rules from {knowledge1}", description: "Cross-reference decline patterns against retry-window rules, issuer-agreement configs, and 3DS-step-up policy." },
    { title: "Rank patterns by revenue exposure", description: "Score each issuer-region decline cluster by revenue exposure and by fix-attribution confidence." },
    { title: "Generate payment-decline mitigation actions", description: "Produce ranked interventions (routing change, retry logic, 3DS tuning) for payments-lead approval." },
  ],
  "gallery-uc-25": [
    { title: "Fetch mobile checkout funnel events from {source1}", description: "Query mobile session events from cart to order-confirmation with step-level timestamps for the last 30 days." },
    { title: "Detect mobile-specific drop-off patterns from {source2}", description: "Attribute mobile abandonment to specific checkout steps (address, tender, review) using device-segmented data." },
    { title: "Apply mobile UX policy from {knowledge1}", description: "Cross-reference weak steps against mobile UX guidelines and pass/fail heuristics from the ecom playbook." },
    { title: "Rank steps by revenue impact of a fix", description: "Score each mobile weak step by revenue lift of a 10-point conversion improvement at that step." },
    { title: "Generate mobile checkout optimisation list", description: "Produce ranked fixes (form redesign, tender flow, review UX) for ecom-lead approval." },
  ],
  "gallery-uc-45": [
    { title: "Fetch organic-traffic and ranking data for hero URLs from {source1}", description: "Query organic sessions and SERP rankings for the top category-landing URLs over the last 90 days." },
    { title: "Detect ranking-decline patterns from {source2}", description: "Identify hero URLs where rank has slipped materially and organic traffic is trending down vs baseline." },
    { title: "Apply SEO health rules from {knowledge1}", description: "Cross-reference decline patterns against content-freshness rules, link-health checks, and technical-SEO thresholds." },
    { title: "Rank URLs by traffic-loss dollar exposure", description: "Score each declining hero URL by lost-organic-revenue exposure and by ease of recovery through content or technical fix." },
    { title: "Generate SEO recovery action list", description: "Produce ranked fixes (content refresh, links, technical) with expected recovery for ecom-lead approval." },
  ],
  "gallery-uc-55": [
    { title: "Fetch fraud-score outcomes and false-decline events from {source1}", description: "Query fraud-score outputs, actual chargeback outcomes, and false-decline flags for the last 90 days." },
    { title: "Detect false-decline concentration on good customers from {source2}", description: "Attribute false declines to specific score bands, basket profiles, and known-good-customer segments." },
    { title: "Apply fraud-threshold policy from {knowledge1}", description: "Cross-reference proposed threshold changes against the fraud playbook, chargeback tolerance, and issuer agreements." },
    { title: "Rank threshold changes by revenue-recovery vs risk", description: "Score each candidate threshold change by false-decline recovery and by incremental fraud exposure." },
    { title: "Generate fraud-threshold tuning recommendations", description: "Produce ranked threshold updates with expected net revenue impact for risk-lead approval." },
  ],
  "gallery-uc-65": [
    { title: "Fetch international checkouts and landed-cost estimates from {source1}", description: "Query cross-border orders and their duty and landed-cost estimates vs actuals for the last 90 days." },
    { title: "Detect estimate-vs-actual gaps causing cancellations from {source2}", description: "Attribute post-checkout cancellations to landed-cost mis-estimates by destination country and product category." },
    { title: "Apply international-pricing rules from {knowledge1}", description: "Cross-reference gaps against the duty-and-tax rules engine, HS-code mappings, and carrier surcharge policy." },
    { title: "Rank countries and categories by cancellation exposure", description: "Score each destination-category combination by cancelled revenue exposure and by fixable estimate accuracy." },
    { title: "Generate landed-cost accuracy action list", description: "Produce ranked fixes (HS code, duty table, carrier config) for ecom-lead approval." },
  ],
  "gallery-uc-70": [
    { title: "Fetch active experiments and personalisation rules from {source1}", description: "Query the experimentation platform for active tests, targeting rules, and personalisation overlaps." },
    { title: "Detect experiment overlap and funnel muddying from {source2}", description: "Identify experiments overlapping on the same audience segments and generating conflicting funnel signals." },
    { title: "Apply experiment governance rules from {knowledge1}", description: "Cross-reference overlaps against the experimentation playbook rules on segment isolation and mutual-exclusion." },
    { title: "Rank experiments by clarity gain from consolidation", description: "Score each cleanup option by expected clarity gain, statistical power lift, and time saved reading the funnel." },
    { title: "Generate experiment backlog cleanup plan", description: "Produce ranked actions (pause, merge, mutually exclude) for experimentation-lead approval." },
  ],
  "gallery-uc-71": [
    { title: "Fetch paid-media performance for hero campaigns from {source1}", description: "Query CAC, ROAS, and blended-cost data for hero paid campaigns over the last 90 days." },
    { title: "Detect ROAS-drift patterns from {source2}", description: "Identify campaigns where blended ROAS is weakening and attribute to bid, creative fatigue, or audience saturation." },
    { title: "Apply paid-media policy from {knowledge1}", description: "Cross-reference declining campaigns against bid rules, creative-refresh cadence, and budget-guardrail thresholds." },
    { title: "Rank campaigns by ROAS-recovery opportunity", description: "Score each declining campaign by expected ROAS recovery from a bid, creative, or audience action." },
    { title: "Generate paid-media efficiency action list", description: "Produce ranked adjustments (bid, creative, audience) with expected ROAS lift for marketing-lead approval." },
  ],
  "gallery-uc-72": [
    { title: "Fetch mobile checkout latency traces from {source1}", description: "Query API latency, front-end render time, and abandonment events on mobile checkout for the last 30 days." },
    { title: "Detect latency-abandonment correlations from {source2}", description: "Attribute abandonment spikes to specific latency thresholds, endpoints, or device-and-network cohorts." },
    { title: "Apply performance-budget rules from {knowledge1}", description: "Cross-reference latency patterns against the front-end performance budget and API-SLA thresholds." },
    { title: "Rank endpoints by abandonment dollar exposure", description: "Score each slow endpoint or asset by revenue exposure from checkout abandonment attributable to latency." },
    { title: "Generate checkout latency remediation list", description: "Produce ranked engineering actions (caching, code-split, endpoint tuning) for platform-lead approval." },
  ],

  // ── Retail (default gallery) - POS ───────────────────────────────────────
  "gallery-uc-06": [
    { title: "Fetch POS transaction failure events from {source1}", description: "Query all POS transaction failures across stores, terminals, and payment methods for the last 14 days." },
    { title: "Detect failure-rate spikes above 5% threshold from {source2}", description: "Identify stores or terminal groups where failure rate has crossed the 5% threshold; segment by error class." },
    { title: "Apply POS uptime policy from {knowledge1}", description: "Cross-reference detected spikes against the uptime SLA and required incident-response triggers." },
    { title: "Rank incidents by revenue exposure and repeat risk", description: "Score each failure hotspot by dollar exposure of lost transactions and by likelihood of repeat failures." },
    { title: "Generate POS incident-response action list", description: "Produce ranked interventions (restart, service, config change) with target resolution time for POS-lead approval." },
  ],
  "gallery-uc-26": [
    { title: "Fetch peripheral failure telemetry from {source1}", description: "Query scanner and printer failure events across lanes and stores for the last 30 days with timestamps and error class." },
    { title: "Detect failure clustering by lane and store from {source2}", description: "Attribute failures to specific lanes, hardware batches, or store environments using pattern detection." },
    { title: "Apply peripheral maintenance policy from {knowledge1}", description: "Cross-reference failure clusters against manufacturer guidance, warranty status, and maintenance-cadence rules." },
    { title: "Rank hotspots by checkout-throughput impact", description: "Score each hotspot by lost throughput minutes, customer-wait impact, and hardware replacement cost." },
    { title: "Generate peripheral service action list", description: "Produce a ranked dispatch list (repair, swap, replace) for facilities-lead approval." },
  ],
  "gallery-uc-27": [
    { title: "Fetch loyalty-lookup latency during peak hours from {source1}", description: "Query loyalty-lookup response times at checkout during peak hours across store formats over the last 30 days." },
    { title: "Detect latency-driven checkout slowdown from {source2}", description: "Attribute peak-hour slowdown to specific loyalty-service endpoints, network segments, or store configurations." },
    { title: "Apply peak-checkout SLA from {knowledge1}", description: "Cross-reference latency observations against loyalty-service SLA and peak-hour performance thresholds." },
    { title: "Rank stores by peak-hour slowdown exposure", description: "Score each affected store by minutes-of-checkout-delay per peak hour and by customer-experience impact." },
    { title: "Generate loyalty-lookup performance action list", description: "Produce ranked fixes (cache, endpoint tuning, timeout policy) for POS-lead approval." },
  ],
  "gallery-uc-28": [
    { title: "Fetch promo-stack events and unexpected-total complaints from {source1}", description: "Query POS transactions where multiple promotions stacked and downstream complaint or refund followed." },
    { title: "Detect conflicting promo-rule interactions from {source2}", description: "Identify promo pairs that produce mathematically valid but customer-unexpected totals through stacked application." },
    { title: "Apply promo-stack policy from {knowledge1}", description: "Cross-reference detected conflicts against the promo-priority ladder and stack-exclusion rules." },
    { title: "Rank conflicts by refund exposure and repeat frequency", description: "Score each conflicting stack by refund and complaint volume and by expected recurrence in the next cycle." },
    { title: "Generate promo-stack rule cleanup list", description: "Produce ranked rule changes (exclude, cap, reorder) for pricing-lead approval." },
  ],
  "gallery-uc-46": [
    { title: "Fetch gift-card balance disputes by channel from {source1}", description: "Query customer-reported gift-card balance mismatches across store, web, and app channels for the last 60 days." },
    { title: "Detect dispute concentration patterns from {source2}", description: "Attribute mismatches to specific channels, tender-integration points, or timing-related edge cases." },
    { title: "Apply gift-card reconciliation rules from {knowledge1}", description: "Cross-reference disputes against reconciliation-timing rules and dispute-handling policy from the finance playbook." },
    { title: "Rank patterns by customer-impact and dollar exposure", description: "Score each dispute cluster by customer-complaint volume and by write-off dollar exposure." },
    { title: "Generate gift-card mismatch remediation actions", description: "Produce ranked fixes (integration patch, reconciliation-window change, policy update) for finance-lead approval." },
  ],
  "gallery-uc-56": [
    { title: "Fetch POS offline event and post-sync mismatch records from {source1}", description: "Query offline transactions and their post-sync inventory and tender reconciliation outcomes for the last 30 days." },
    { title: "Detect sync-driven inconsistency patterns from {source2}", description: "Attribute post-sync mismatches to specific offline event types, terminal firmware, or network conditions." },
    { title: "Apply offline-sync policy from {knowledge1}", description: "Cross-reference detected patterns against the offline-mode SLA and required post-sync reconciliation rules." },
    { title: "Rank stores by mismatch dollar exposure", description: "Score each affected store by inventory-and-tender variance dollars and by frequency of the underlying offline events." },
    { title: "Generate POS offline-sync remediation list", description: "Produce ranked fixes (firmware, sync-logic, reconciliation) for POS-lead approval." },
  ],
  "gallery-uc-73": [
    { title: "Fetch lane wait-time telemetry for urban stores from {source1}", description: "Query lane-level wait times during weekend peaks across urban-format stores for the last 90 days." },
    { title: "Detect wait-time breaches vs target from {source2}", description: "Identify stores and hours where lane wait times routinely exceed the internal target during weekend peaks." },
    { title: "Apply peak-queue policy from {knowledge1}", description: "Cross-reference breach patterns against staffing model, self-checkout mix rules, and expected-wait thresholds." },
    { title: "Rank stores by queue-driven customer-experience risk", description: "Score each breaching store by cumulative excess-wait minutes and by projected customer-experience impact." },
    { title: "Generate peak-queue remediation actions", description: "Produce ranked options (staffing, self-checkout, express lane) for store-ops-lead approval." },
  ],
  "gallery-uc-74": [
    { title: "Fetch wallet and contactless decline events from {source1}", description: "Query digital-tender decline events by acquirer, store format, and tender type for the last 60 days." },
    { title: "Detect acquirer-format cluster patterns from {source2}", description: "Attribute clusters to specific acquirer-format combinations using pattern detection." },
    { title: "Apply digital-tender routing rules from {knowledge1}", description: "Cross-reference detected clusters against tender-routing rules and acquirer-agreement thresholds." },
    { title: "Rank clusters by revenue exposure", description: "Score each acquirer-format cluster by declined-tender revenue and by acquirer-agreement remediation options." },
    { title: "Generate digital-tender routing recommendations", description: "Produce ranked routing changes and acquirer conversations for payments-lead approval." },
  ],

  // ── Retail (default gallery) - Support ───────────────────────────────────
  "gallery-uc-07": [
    { title: "Fetch return-request volumes by product and region from {source1}", description: "Query return-request events for the last 30 days segmented by product, region, and return reason." },
    { title: "Detect surge patterns vs baseline from {source2}", description: "Identify products or regions with return volume materially above their historical baseline; attribute to root cause." },
    { title: "Apply returns-triage policy from {knowledge1}", description: "Cross-reference surges against returns-quality thresholds, supplier-notification triggers, and recall protocols." },
    { title: "Rank surges by dollar exposure and recurrence risk", description: "Score each surge by returned-revenue dollars, incremental logistics cost, and probability of continued growth." },
    { title: "Generate return-surge action list", description: "Produce ranked interventions (supplier alert, product hold, replacement policy) for support-lead approval." },
  ],
  "gallery-uc-29": [
    { title: "Fetch contact-centre wait-time and service-level data from {source1}", description: "Query wait times, service level, and abandonment across contact queues for the last 30 days." },
    { title: "Detect service-level gaps by queue from {source2}", description: "Identify queues consistently missing service-level targets; segment by intent, tier, and time-of-day." },
    { title: "Apply queue-management policy from {knowledge1}", description: "Cross-reference gaps against staffing model, skill-based routing rules, and overflow-enablement policy." },
    { title: "Rank queues by CX and CSAT exposure", description: "Score each underperforming queue by projected customer-experience impact and CSAT correlation." },
    { title: "Generate contact-centre remediation actions", description: "Produce ranked interventions (staffing, routing, overflow) for support-lead approval." },
  ],
  "gallery-uc-30": [
    { title: "Fetch escalation records and product/policy tags from {source1}", description: "Query escalated cases from the last 90 days with product tags, policy references, and resolution outcomes." },
    { title: "Detect escalation themes and root causes from {source2}", description: "Cluster escalations into recurring themes tied to specific products, policies, or process gaps." },
    { title: "Apply root-cause analysis rules from {knowledge1}", description: "Cross-reference detected themes against root-cause categories and known-issue tracker entries." },
    { title: "Rank themes by escalation volume and repeat risk", description: "Score each theme by escalation volume, agent time cost, and expected recurrence if unaddressed." },
    { title: "Generate escalation-theme remediation list", description: "Produce ranked interventions (policy change, product fix, KB update) for support-lead approval." },
  ],
  "gallery-uc-31": [
    { title: "Fetch chatbot-containment and handoff events from {source1}", description: "Query chatbot sessions, containment outcomes, and handoff-to-agent triggers for the last 30 days." },
    { title: "Detect containment-drop patterns from {source2}", description: "Attribute containment declines to specific intents, KB gaps, or friction points in the bot flow." },
    { title: "Apply containment-target policy from {knowledge1}", description: "Cross-reference declining intents against KB coverage requirements and required bot-response quality thresholds." },
    { title: "Rank intents by containment-recovery opportunity", description: "Score each low-containment intent by agent-cost avoided and by ease of fix (KB, flow, training data)." },
    { title: "Generate chatbot containment action list", description: "Produce ranked fixes (KB, intent training, flow redesign) for support-lead approval." },
  ],
  "gallery-uc-47": [
    { title: "Fetch warranty-claim events by SKU and supplier batch from {source1}", description: "Query warranty claims from the last 90 days segmented by SKU, supplier, and batch or lot identifier." },
    { title: "Detect SKU or batch spike patterns from {source2}", description: "Identify SKUs or batches where claim rate is materially above the fleet baseline; highlight supplier concentration." },
    { title: "Apply warranty-triage rules from {knowledge1}", description: "Cross-reference spikes against supplier-quality thresholds, recall-trigger criteria, and product-hold policy." },
    { title: "Rank spikes by dollar exposure and safety risk", description: "Score each spike by warranty-cost exposure, resale revenue at risk, and any safety-related severity." },
    { title: "Generate warranty-claim response actions", description: "Produce ranked interventions (product hold, supplier alert, service bulletin) for QA-lead approval." },
  ],
  "gallery-uc-57": [
    { title: "Fetch social-sentiment signals and mention volumes from {source1}", description: "Query social-mention volume and sentiment scores with entity tags for the last 30 days." },
    { title: "Detect sentiment spikes correlated with product or policy changes from {source2}", description: "Attribute negative-mention spikes to recent product launches, policy changes, or campaign moves using time-based join." },
    { title: "Apply social-response policy from {knowledge1}", description: "Cross-reference spike severity against escalation-trigger thresholds and required response cadence." },
    { title: "Rank spikes by reach and brand-risk exposure", description: "Score each spike by mention volume, reach, and estimated brand-risk exposure." },
    { title: "Generate social-response action list", description: "Produce ranked interventions (public statement, product fix, policy revert) for PR-lead approval." },
  ],
  "gallery-uc-75": [
    { title: "Fetch post-care CSAT scores tied to shipping-issue chats from {source1}", description: "Query CSAT surveys following chat-assisted resolutions of shipping issues for the last 60 days." },
    { title: "Detect CSAT dip patterns from {source2}", description: "Attribute score drops to specific chat outcomes, resolution paths, or shipping-issue subtypes." },
    { title: "Apply CX quality rules from {knowledge1}", description: "Cross-reference dips against agent-training standards, KB coverage, and resolution-quality thresholds." },
    { title: "Rank dip patterns by score impact and volume", description: "Score each dip cluster by aggregate CSAT-point impact and by resolution-path volume." },
    { title: "Generate post-care CSAT remediation actions", description: "Produce ranked interventions (agent coaching, KB update, resolution redesign) for support-lead approval." },
  ],
  "gallery-uc-76": [
    { title: "Fetch skill-pool utilisation and intent-mix data from {source1}", description: "Query agent-skill availability, handle-time by intent, and staffing-vs-demand mix for the last 30 days." },
    { title: "Detect skill-pool imbalance from {source2}", description: "Identify skill pools where demand-intent mix and available staffing are misaligned, driving higher handle time." },
    { title: "Apply skill-based routing rules from {knowledge1}", description: "Cross-reference imbalances against skill-routing rules and cross-training policy from the support playbook." },
    { title: "Rank skill pools by handle-time cost exposure", description: "Score each imbalanced pool by handle-time cost and by expected AHT reduction from a routing or training change." },
    { title: "Generate occupancy-balance action list", description: "Produce ranked interventions (routing, cross-training, staffing) for support-lead approval." },
  ],
  "gallery-uc-77": [
    { title: "Fetch chat first-response times during promo peaks from {source1}", description: "Query chat first-response time during promotional peak hours across queues over the last 90 days." },
    { title: "Detect SLA-breach patterns from {source2}", description: "Attribute FRT breaches to specific peaks, queues, or staffing gaps using time-segmented data." },
    { title: "Apply chat-SLA policy from {knowledge1}", description: "Cross-reference breach patterns against the chat FRT SLA and overflow-enablement rules." },
    { title: "Rank peaks by SLA-breach volume and exposure", description: "Score each peak by FRT-breach count and by projected CX impact of continued misses." },
    { title: "Generate chat FRT remediation action list", description: "Produce ranked interventions (peak staffing, overflow, deflection) for support-lead approval." },
  ],
  "gallery-uc-78": [
    { title: "Fetch Tier-2 executive-response cases and aging from {source1}", description: "Query open Tier-2 cases with age since escalation and repeat-contact count for the last 90 days." },
    { title: "Detect aging-vs-policy breach patterns from {source2}", description: "Identify aging cases that exceed policy thresholds and correlate with repeat contacts from the same customer." },
    { title: "Apply Tier-2 SLA policy from {knowledge1}", description: "Cross-reference aging cases against Tier-2 SLA rules and executive-escalation triggers." },
    { title: "Rank cases by executive-risk exposure", description: "Score each aging case by executive-visibility risk, customer-lifetime-value exposure, and repeat-contact severity." },
    { title: "Generate Tier-2 aging resolution plan", description: "Produce a per-case action list with owner and target-close date for Tier-2-lead approval." },
  ],

  // ── Retail (default gallery) - Pricing ───────────────────────────────────
  "gallery-uc-08": [
    { title: "Fetch category sell-through and current pricing from {source1}", description: "Query weekly sell-through and current price by SKU across the analyzed category for the last 12 weeks." },
    { title: "Model markdown timing and depth sensitivity from {source2}", description: "Simulate expected sell-through under alternative markdown-start weeks and discount depths per SKU." },
    { title: "Apply markdown policy rules from {knowledge1}", description: "Cross-reference candidate markdown schedules against price-ladder floors and category-specific markdown limits." },
    { title: "Rank SKUs by expected recovery and margin impact", description: "Score each SKU by projected recovered inventory dollars and by net margin impact of the recommended action." },
    { title: "Generate markdown-timing and depth action list", description: "Produce a SKU-by-SKU markdown recommendation with expected sell-through lift for pricing-lead approval." },
  ],
  "gallery-uc-32": [
    { title: "Fetch competitor prices for key-value items by zone from {source1}", description: "Query competitor pricing for key-value SKUs by zone using the competitor-index feed over the last 14 days." },
    { title: "Detect zone-specific price gaps from {source2}", description: "Identify KVIs where our price is materially above market in specific zones; segment by category and format." },
    { title: "Apply pricing-competitiveness rules from {knowledge1}", description: "Cross-reference detected gaps against target price-index bands and margin-floor policy per category." },
    { title: "Rank gaps by revenue and traffic exposure", description: "Score each zone-SKU gap by revenue exposure and by projected traffic impact of continued mis-pricing." },
    { title: "Generate competitive-price adjustment list", description: "Produce ranked price changes with margin trade-off analysis for pricing-lead approval." },
  ],
  "gallery-uc-33": [
    { title: "Fetch price-test results and category elasticity data from {source1}", description: "Query completed price-test outcomes and observed elasticity estimates across categories over the last 18 months." },
    { title: "Detect uniform-testing bias vs category variability from {source2}", description: "Identify categories where price-test design does not reflect the true elasticity heterogeneity of the category." },
    { title: "Apply elasticity-testing policy from {knowledge1}", description: "Cross-reference test designs against the category-elasticity playbook and required test coverage rules." },
    { title: "Rank categories by testing-redesign opportunity", description: "Score each category by expected margin lift from an elasticity-aware test redesign." },
    { title: "Generate elasticity-aware test plan", description: "Produce a category-by-category test-design recommendation for pricing-lead approval." },
  ],
  "gallery-uc-48": [
    { title: "Fetch auto-pricing outputs and guardrail breaches from {source1}", description: "Query auto-pricing decisions and any breaches of min-margin or MAP floor for the last 30 days." },
    { title: "Detect breach patterns and rule gaps from {source2}", description: "Attribute breaches to specific pricing rules, competitor moves, or edge-case category conditions." },
    { title: "Apply pricing-guardrail policy from {knowledge1}", description: "Cross-reference detected breaches against min-margin rules, MAP-agreement policy, and manual-override triggers." },
    { title: "Rank breach patterns by margin exposure", description: "Score each breach pattern by margin-dollar exposure and by frequency of the underlying edge case." },
    { title: "Generate guardrail-rule tuning list", description: "Produce ranked rule tightening or exception recommendations for pricing-lead approval." },
  ],
  "gallery-uc-58": [
    { title: "Fetch bundle-transaction records and stacked-discount events from {source1}", description: "Query hero-kit transactions where multiple promos and bundle discounts stacked over the last 60 days." },
    { title: "Detect bundle margin erosion patterns from {source2}", description: "Attribute margin erosion to specific bundle-promo combinations that stack beyond intended discount depth." },
    { title: "Apply bundle-pricing policy from {knowledge1}", description: "Cross-reference detected combinations against bundle-stacking rules and hero-kit margin floors." },
    { title: "Rank combinations by margin exposure and volume", description: "Score each conflicting combo by cumulative margin dollars eroded and by frequency at retail." },
    { title: "Generate bundle-pricing rule cleanup list", description: "Produce ranked rule updates (exclude, cap, restructure) for pricing-lead approval." },
  ],
  "gallery-uc-79": [
    { title: "Fetch competitor digital-shelf prices for KVIs from {source1}", description: "Query competitor digital-shelf pricing for key-value SKUs using the price-index feed over the last 14 days." },
    { title: "Detect KVIs materially above market from {source2}", description: "Identify our KVIs whose digital-shelf price is materially above competitor benchmarks across priority channels." },
    { title: "Apply digital-shelf-pricing rules from {knowledge1}", description: "Cross-reference gap size against target index bands, promotional-window rules, and margin-floor policy." },
    { title: "Rank SKUs by digital-shelf revenue exposure", description: "Score each above-market KVI by digital-channel revenue exposure and by traffic-impact severity." },
    { title: "Generate digital-shelf price-adjustment list", description: "Produce ranked price changes with margin trade-off for pricing-lead approval." },
  ],
  "gallery-uc-80": [
    { title: "Fetch seasonal SKU weeks-on-hand and promo-window data from {source1}", description: "Query weeks-on-hand and promo-window position for seasonal SKUs exiting the current campaign." },
    { title: "Detect sell-through risk vs plan curve from {source2}", description: "Identify SKUs where weeks-on-hand is above the plan curve at the current window position, projecting exit overhang." },
    { title: "Apply sell-through-plan policy from {knowledge1}", description: "Cross-reference at-risk SKUs against markdown-window rules and exit-inventory targets." },
    { title: "Rank SKUs by exit-inventory exposure", description: "Score each SKU by projected exit-inventory dollars and by markdown-cost trade-off of an earlier intervention." },
    { title: "Generate sell-through-recovery action list", description: "Produce ranked options (accelerate markdown, redeploy, terminal-store push) for pricing-lead approval." },
  ],

  // ── Retail (default gallery) - Finance ───────────────────────────────────
  "gallery-uc-09": [
    { title: "Fetch gross-margin data by category, store, and channel from {source1}", description: "Query gross-margin actuals against plan and prior-year across category, store, and channel dimensions." },
    { title: "Decompose margin variance from {source2}", description: "Decompose actual-vs-plan margin variance into price, cost, mix, and markdown components for the analysis window." },
    { title: "Apply margin-analysis policy from {knowledge1}", description: "Cross-reference variance drivers against materiality thresholds and finance-close narrative requirements." },
    { title: "Rank variance drivers by dollar impact", description: "Score each driver-category-store combination by dollar impact and by ease of remediation." },
    { title: "Generate margin-analysis narrative and actions", description: "Produce a ranked variance narrative with recommended interventions for finance-lead approval." },
  ],
  "gallery-uc-36": [
    { title: "Fetch end-of-day tender-variance records by site from {source1}", description: "Query end-of-day tender reconciliations by site with variance amounts and cash-versus-tender breakdown." },
    { title: "Detect trending-variance sites from {source2}", description: "Identify sites where variance is trending up over consecutive periods; segment by cashier and shift." },
    { title: "Apply cash-reconciliation policy from {knowledge1}", description: "Cross-reference detected trends against reconciliation-tolerance thresholds and mandatory investigation triggers." },
    { title: "Rank sites by variance-dollar exposure", description: "Score each trending site by cumulative variance dollars and by probability of a fixable root cause." },
    { title: "Generate cash-reconciliation remediation plan", description: "Produce a per-site action list (audit, coaching, process change) for finance-lead approval." },
  ],
  "gallery-uc-37": [
    { title: "Fetch category-level contribution margin vs plan from {source1}", description: "Query category-contribution-margin actuals vs plan and prior-year for the last 6 rolling periods." },
    { title: "Detect mix-shift drivers eroding margin from {source2}", description: "Attribute margin erosion to specific mix shifts using category-share and unit-margin decomposition." },
    { title: "Apply contribution-margin policy from {knowledge1}", description: "Cross-reference detected shifts against category-strategy rules and profitability targets from finance." },
    { title: "Rank categories by margin-erosion exposure", description: "Score each declining category by margin-dollar erosion and by ease of mix-shift remediation." },
    { title: "Generate margin-defense action list", description: "Produce ranked interventions (assortment, promo, pricing) for finance-lead approval." },
  ],
  "gallery-uc-49": [
    { title: "Fetch intercompany recharge journals and shared-services allocations from {source1}", description: "Query intercompany recharge journals and shared-services cost allocations across retail entities over the last 4 close cycles." },
    { title: "Detect allocation disputes and mismatches from {source2}", description: "Identify recharges where retail-vs-shared-services allocation is inconsistent or disputed by an entity." },
    { title: "Apply intercompany recharge policy from {knowledge1}", description: "Cross-reference disputed allocations against the transfer-pricing playbook and finance close rules." },
    { title: "Rank disputes by dollar exposure and audit risk", description: "Score each dispute by allocation-dollar exposure and by close-narrative or audit-risk severity." },
    { title: "Generate recharge-dispute resolution actions", description: "Produce ranked resolutions (allocation change, journal correction, process fix) for finance-lead approval." },
  ],
  "gallery-uc-59": [
    { title: "Fetch category margin actuals and FX exposure from {source1}", description: "Query category-margin actuals with underlying currency exposure and applied FX rates for the last 12 months." },
    { title: "Detect margin views not reflecting FX moves from {source2}", description: "Identify categories where reported margin diverges from FX-adjusted margin due to lagging rate assumptions." },
    { title: "Apply FX policy from {knowledge1}", description: "Cross-reference detected divergences against the FX-hedging playbook and margin-reporting policy." },
    { title: "Rank categories by FX-driven margin exposure", description: "Score each affected category by FX-driven margin-dollar impact and by required close-narrative remediation." },
    { title: "Generate FX-adjustment action list", description: "Produce ranked corrections (rate refresh, hedging, margin-narrative update) for finance-lead approval." },
  ],
  "gallery-uc-81": [
    { title: "Fetch OpEx run-rate data vs budget from {source1}", description: "Query OpEx run-rate across cost centres vs plan and prior-year for the last 6 periods." },
    { title: "Detect above-plan run-rate cost centres from {source2}", description: "Attribute run-rate overspend to specific cost centres, vendors, or line items without matching revenue upside." },
    { title: "Apply OpEx-control policy from {knowledge1}", description: "Cross-reference overspend against budget-variance thresholds and expected-benefit criteria from finance." },
    { title: "Rank cost centres by overspend dollars", description: "Score each above-plan cost centre by annualised overspend dollars and by ease of corrective action." },
    { title: "Generate OpEx-control action list", description: "Produce ranked interventions (spend freeze, vendor renegotiation, reforecast) for finance-lead approval." },
  ],

  // ── Retail (default gallery) - Workforce ─────────────────────────────────
  "gallery-uc-10": [
    { title: "Fetch labor cost and revenue by store from {source1}", description: "Query labor cost and revenue by store for the last 90 days across all formats and regions." },
    { title: "Detect overstaffed stores and hours from {source2}", description: "Identify stores or hours where labor-cost-to-revenue ratio exceeds format-specific benchmarks." },
    { title: "Apply labor-productivity policy from {knowledge1}", description: "Cross-reference overstaffing signals against staffing model, service-level minimums, and productivity targets." },
    { title: "Rank overstaffing hotspots by savings potential", description: "Score each overstaffed store or hour by projected labor-cost savings without breaching service-level minimums." },
    { title: "Generate labor-cost optimisation action list", description: "Produce a store-by-store list of schedule adjustments for workforce-lead approval." },
  ],
  "gallery-uc-38": [
    { title: "Fetch overtime-hours data by store and role from {source1}", description: "Query overtime-hours records by store, role, and shift for the last 90 days." },
    { title: "Detect overtime-hotspot patterns from {source2}", description: "Identify stores or roles where overtime is spiking; attribute to coverage gaps, absenteeism, or demand spikes." },
    { title: "Apply overtime policy from {knowledge1}", description: "Cross-reference hotspots against overtime-approval limits, budget guardrails, and shift-swap policy." },
    { title: "Rank hotspots by OT-dollar exposure", description: "Score each hotspot by monthly OT-dollar spend and by fixable root-cause confidence." },
    { title: "Generate overtime-reduction action list", description: "Produce ranked interventions (hiring, cross-training, schedule restructure) for workforce-lead approval." },
  ],
  "gallery-uc-39": [
    { title: "Fetch clock-in variance vs planned hours from {source1}", description: "Query clock-in and clock-out events vs planned hours by employee and store for the last 60 days." },
    { title: "Detect adherence patterns hurting labor target from {source2}", description: "Attribute adherence gaps to specific stores, shifts, or roles that drive labor cost above plan." },
    { title: "Apply schedule-adherence policy from {knowledge1}", description: "Cross-reference gaps against required adherence thresholds and coaching triggers from the workforce playbook." },
    { title: "Rank stores by adherence-driven cost impact", description: "Score each store by cumulative adherence-driven cost impact and by coaching-fix confidence." },
    { title: "Generate schedule-adherence remediation actions", description: "Produce a store-by-store list of coaching and enforcement interventions for workforce-lead approval." },
  ],
  "gallery-uc-40": [
    { title: "Fetch training-completion status pre-peak from {source1}", description: "Query per-employee training-module completion status against peak-season deadlines." },
    { title: "Detect completion gaps by store and role from {source2}", description: "Identify stores or roles with material completion gaps that would leave the fleet non-compliant at peak." },
    { title: "Apply compliance-training policy from {knowledge1}", description: "Cross-reference gaps against mandatory training rules and pre-peak completion targets." },
    { title: "Rank stores by non-completion risk", description: "Score each store by non-completion count and by pre-peak compliance-exposure severity." },
    { title: "Generate training-completion action list", description: "Produce a store-by-store push plan (assignments, reminders, coaching) for HR-lead approval." },
  ],
  "gallery-uc-50": [
    { title: "Fetch meal-and-rest-break records by district from {source1}", description: "Query break-adherence records by employee, store, and district for the last 90 days." },
    { title: "Detect break-compliance violations by district from {source2}", description: "Identify districts where break-compliance violations are trending up; segment by store and shift." },
    { title: "Apply meal-and-rest-break policy from {knowledge1}", description: "Cross-reference detected violations against labor-law requirements and internal break-compliance targets." },
    { title: "Rank districts by compliance-risk exposure", description: "Score each district by violation volume and by projected regulatory or legal exposure." },
    { title: "Generate break-compliance remediation actions", description: "Produce a district-by-district action list (scheduling, coaching, alerts) for workforce-lead approval." },
  ],
  "gallery-uc-60": [
    { title: "Fetch sales-per-labor-hour by role and region from {source1}", description: "Query sales-per-labor-hour metrics across regions for equivalent role mix over the last 90 days." },
    { title: "Detect productivity divergence across regions from {source2}", description: "Identify regions where sales-per-labor-hour diverges materially from the peer benchmark for the same role mix." },
    { title: "Apply productivity-benchmark policy from {knowledge1}", description: "Cross-reference divergences against role-productivity benchmarks and store-format expectations." },
    { title: "Rank regions by productivity-lift opportunity", description: "Score each underperforming region by projected productivity lift and by ease of intervention." },
    { title: "Generate productivity-lift action list", description: "Produce ranked interventions (coaching, best-practice transfer, task redesign) for workforce-lead approval." },
  ],
  "gallery-uc-82": [
    { title: "Fetch premium-hour and headcount data by district from {source1}", description: "Query premium-hours (overtime, holiday, weekend) and headcount vacancy by district for the last 60 days." },
    { title: "Detect creep in premium-hours from chronic understaffing from {source2}", description: "Identify districts where premium-hour growth is correlated with sustained understaffing signals." },
    { title: "Apply premium-hours governance policy from {knowledge1}", description: "Cross-reference creep patterns against premium-hours approval rules and hiring-plan targets." },
    { title: "Rank districts by premium-hours dollar exposure", description: "Score each affected district by premium-hours-dollar exposure and by hiring-fix confidence." },
    { title: "Generate premium-hours mitigation actions", description: "Produce ranked interventions (backfill hires, cross-train, schedule reset) for workforce-lead approval." },
  ],

  // ── Middleby analytics use cases ─────────────────────────────────────────
  "middleby-uc-02": [
    { title: "Fetch RFQ funnel step events from {source1}", description: "Query GA session events across every RFQ funnel step (view, add-to-quote, form-open, form-submit) for the last 30 days." },
    { title: "Detect biggest funnel drop-off vs baseline from {source2}", description: "Compare step-conversion vs prior period baseline; isolate the single step contributing the largest RFQ decline." },
    { title: "Apply funnel-diagnosis rules from {knowledge1}", description: "Cross-reference step-drop signals against known-issue library, deploy calendar, and expected-behavior baselines." },
    { title: "Rank leaks by pipeline recovery opportunity", description: "Score each funnel leak by pipeline dollars recoverable if fixed and by confidence in the underlying diagnosis." },
    { title: "Generate funnel-fix worklist for digital and dev teams", description: "Produce a ranked worklist of the tallest drop-offs first for digital-and-eng-lead approval." },
  ],
  "middleby-uc-03": [
    { title: "Fetch L1/L2 taxonomy demand and GA brand sessions from {source1}", description: "Query product-taxonomy demand from shop_middleby_products alongside GA brand-level sessions for the last 30 days." },
    { title: "Detect rising-demand / low-conversion categories from {source2}", description: "Identify L1/L2 categories where demand is trending up but quote-conversion is flat or declining." },
    { title: "Apply category-opportunity rules from {knowledge1}", description: "Cross-reference detected categories against merchandising priority and dealer-coverage rules." },
    { title: "Rank categories by opportunity dollars", description: "Score each rising-demand category by quote-pipeline opportunity from closing the conversion gap." },
    { title: "Generate category-opportunity action list", description: "Produce ranked interventions (dealer push, content investment, targeted campaign) for merchandising-lead approval." },
  ],
  "middleby-uc-04": [
    { title: "Fetch open quote worklist per sales rep from {source1}", description: "Query open HubSpot quotes segmented by sales rep with urgency signals (timeframe, consultation flag, brand)." },
    { title: "Detect urgent follow-ups needing immediate outreach from {source2}", description: "Highlight quotes flagged urgent (0-1 month timeframe or consultation=yes) that lack recent rep activity." },
    { title: "Apply follow-up cadence rules from {knowledge1}", description: "Cross-reference urgent quotes against required-response-time policy and consultation-priority rules." },
    { title: "Rank quotes by pipeline dollars and urgency", description: "Score each urgent quote by pipeline dollars at stake and by hours since last rep touch." },
    { title: "Generate sales-rep worklist for today", description: "Produce a rep-by-rep prioritised worklist with recommended talking points for sales-lead approval." },
  ],
  "middleby-uc-05": [
    { title: "Fetch product-family journey events from {source1}", description: "Query GA and LogRocket events for one product family, sequenced from first visit to RFQ or drop-off." },
    { title: "Reconstruct browse-to-quote journey narrative from {source2}", description: "Sequence sessions into a journey with the stall moments, re-engagement points, and drop-off signals annotated." },
    { title: "Apply journey-analysis rules from {knowledge1}", description: "Cross-reference the reconstructed journey against expected happy-path steps and known friction patterns." },
    { title: "Rank stall moments by pipeline exposure", description: "Score each stall moment by pipeline dollars lost and by ease of intervention (content, dealer, retargeting)." },
    { title: "Generate journey-improvement recommendations", description: "Produce ranked recommendations for dealer and ops teams to address the biggest stalls in the family journey." },
  ],
  "middleby-uc-06": [
    { title: "Fetch dealer-region RFQ outcomes from {source1}", description: "Query RFQ submissions and outcomes grouped by rep-group and dealer region for the last 90 days." },
    { title: "Detect leakage-hotspot regions from {source2}", description: "Identify dealer regions with disproportionate lost or unactioned quotes; filter spam and test deals per policy." },
    { title: "Apply dealer-performance rules from {knowledge1}", description: "Cross-reference leakage hotspots against dealer-agreement thresholds and rep-group coverage rules." },
    { title: "Rank dealer regions by lost-opportunity dollars", description: "Score each leakage region by lost pipeline dollars and by fixable-root-cause confidence." },
    { title: "Generate dealer-performance action list", description: "Produce a region-by-region intervention list (coverage change, training, rep coaching) for sales-lead approval." },
  ],
  "middleby-uc-07": [
    { title: "Fetch executive-level headline metrics from {source1}", description: "Query weekly quote volume, RFQ conversion, and deal-stage distribution for the executive summary window." },
    { title: "Detect narrative-worthy drivers from {source2}", description: "Attribute weekly movements to specific brands, regions, or funnel stages worth executive attention." },
    { title: "Apply executive-narrative rules from {knowledge1}", description: "Cross-reference detected drivers against materiality thresholds and executive-briefing style guide." },
    { title: "Rank drivers by executive-attention priority", description: "Score each driver by revenue impact, novelty, and strategic-relevance to executive priorities." },
    { title: "Generate executive AI-analyst summary", description: "Produce a narrative summary with headline metrics and driver callouts for executive review." },
  ],
  "middleby-uc-08": [
    { title: "Fetch LogRocket UX-issue signals from {source1}", description: "Query LogRocket issues with sessions-affected count and correlate to page-type and brand." },
    { title: "Convert affected sessions into pipeline at risk from {source2}", description: "Estimate pipeline-at-risk by joining affected sessions to their intent scores and historical conversion rates." },
    { title: "Apply UX-severity policy from {knowledge1}", description: "Cross-reference issues against severity thresholds, expected-behavior baselines, and deployment-triage rules." },
    { title: "Rank UX issues by pipeline dollars at risk", description: "Score each issue by pipeline-at-risk dollars and by fix-effort estimate from engineering." },
    { title: "Generate UX-fix worklist for digital and eng teams", description: "Produce a ranked issue list highlighting the single fix that moves the number most, for digital and eng approval." },
  ],
  "middleby-uc-09": [
    { title: "Fetch HubSpot quote-form timeframe fields from {source1}", description: "Query submitted quotes with purchase-timeframe and consultation-request fields for the last 60 days." },
    { title: "Compute buyer-intent mix distribution from {source2}", description: "Compute the share of quotes wanting 0-1 month delivery vs longer horizons and consultation vs self-serve." },
    { title: "Apply intent-mix analysis rules from {knowledge1}", description: "Cross-reference the mix against sales-capacity assumptions and consultation-service capacity thresholds." },
    { title: "Rank timeframe segments by pipeline value", description: "Score each intent segment by pipeline dollars and by conversion probability given the timeframe." },
    { title: "Generate intent-mix executive briefing", description: "Produce an intent-mix breakdown highlighting immediate-buy pipeline and consultation demand for sales-lead review." },
  ],
  "middleby-uc-10": [
    { title: "Fetch consultation-flag records and dealer-response events from {source1}", description: "Query quotes with consultation=yes vs consultation=no alongside dealer-response timestamps and outcomes." },
    { title: "Compare consultation vs self-serve outcomes from {source2}", description: "Compute follow-up time, conversion rate, and average deal size for consultation-led vs self-serve quotes." },
    { title: "Apply consultation-performance rules from {knowledge1}", description: "Cross-reference the comparison against dealer-response SLA and consultation-quality thresholds." },
    { title: "Rank outcomes by consultation-lift dollars", description: "Score the consultation cohort by conversion-lift dollars vs self-serve at the same brand and region." },
    { title: "Generate consultation-pipeline recommendations", description: "Produce ranked recommendations (scale consultation, dealer coaching, self-serve UX) for sales-lead approval." },
  ],
  "middleby-uc-11": [
    { title: "Fetch quote history with prior-brand signals from {source1}", description: "Query HubSpot quotes with derived prior-brand-buyer flags built from contact and deal history." },
    { title: "Compare repeat vs first-time buyer conversion from {source2}", description: "Compute quote frequency, conversion, and average deal size for repeat-brand vs first-time buyer segments." },
    { title: "Apply segmentation-analysis rules from {knowledge1}", description: "Cross-reference derived segments against the segmentation playbook and known data-caveats about the prior-brand field." },
    { title: "Rank segments by lifecycle pipeline value", description: "Score each buyer segment by lifecycle pipeline value and by retention-vs-acquisition trade-offs." },
    { title: "Generate buyer-segment recommendations", description: "Produce ranked recommendations (retention program, acquisition campaign, dealer scripting) for marketing-lead approval." },
  ],
  "middleby-uc-12": [
    { title: "Fetch HubSpot deal geographic fields from {source1}", description: "Query deal state, zip, and install-vs-delivery preference fields for quotes in the last 90 days." },
    { title: "Aggregate demand by region and preference from {source2}", description: "Aggregate demand into a state and zip heatmap segmented by install-heavy vs delivery-sensitive preferences." },
    { title: "Apply install-and-delivery capacity rules from {knowledge1}", description: "Cross-reference regional demand against install-partner capacity and delivery-network coverage rules." },
    { title: "Rank regions by capacity-vs-demand pressure", description: "Score each region by capacity-vs-demand gap and by pipeline dollars exposed to unmet preference." },
    { title: "Generate install-and-delivery capacity plan", description: "Produce a region-by-region capacity recommendation for ops-lead approval." },
  ],
  "middleby-uc-13": [
    { title: "Fetch pipeline data by brand, region, and stage from {source1}", description: "Query HubSpot pipeline segmented by brand, region, and stage with spam and test deals filtered out." },
    { title: "Detect stall patterns and stage-drop signals from {source2}", description: "Attribute pipeline stalls to specific brand-region-stage combinations using deal-age analysis." },
    { title: "Apply pipeline-management rules from {knowledge1}", description: "Cross-reference stalls against expected stage duration and required rep-action triggers." },
    { title: "Rank pipeline pockets by stalled dollars", description: "Score each stalled combo by stalled pipeline dollars and by intervention effort required to unblock." },
    { title: "Generate pipeline-acceleration action list", description: "Produce ranked interventions (rep push, content, dealer coordination) for sales-lead approval." },
  ],
  "middleby-uc-14": [
    { title: "Fetch traffic-channel and engagement data from {source1}", description: "Query channel-mix session and engagement metrics from the analytics platform for the last 30 days." },
    { title: "Detect deep-engagement vs bounce patterns from {source2}", description: "Segment channels by depth of product engagement vs bounce rate to reveal channels driving quality traffic." },
    { title: "Apply marketing-channel policy from {knowledge1}", description: "Cross-reference channel performance against acquisition-budget policy and quality-vs-volume trade-offs." },
    { title: "Rank channels by quality-adjusted pipeline value", description: "Score each channel by quality-adjusted pipeline value (deep engagement plus quote-conversion likelihood)." },
    { title: "Generate channel-mix recommendations", description: "Produce ranked budget-shifts and campaign-tuning options for marketing-lead approval." },
  ],
  "middleby-uc-15": [
    { title: "Fetch session-level brand-visit records from {source1}", description: "Query sessions with the distribution of Middleby brands viewed per visit over the last 30 days." },
    { title: "Detect multi-brand browsing behavior from {source2}", description: "Compute the share of sessions that browse 2+ brands before submitting a quote vs single-brand sessions." },
    { title: "Apply portfolio-cross-sell rules from {knowledge1}", description: "Cross-reference multi-brand behavior against cross-sell targeting rules and brand-adjacency mappings." },
    { title: "Rank brand pairs by cross-sell attach rate", description: "Score each brand pair by observed multi-brand co-visit and by expected quote-attach opportunity." },
    { title: "Generate multi-brand cross-sell recommendations", description: "Produce ranked cross-sell surfaces (recommendation cards, nav, RFQ cart) for merchandising-lead approval." },
  ],
  "middleby-uc-16": [
    { title: "Fetch weekly-changes across traffic, errors, quotes from {source1}", description: "Query week-over-week deltas in traffic, LogRocket-errors, and RFQ metrics for the last 4 weeks." },
    { title: "Detect the 2-3 briefing-worthy changes from {source2}", description: "Isolate the two or three shifts that pass materiality and are worth executive attention this week." },
    { title: "Apply executive-briefing rules from {knowledge1}", description: "Cross-reference detected shifts against briefing-style guide and audience-priority thresholds." },
    { title: "Rank shifts by executive-attention value", description: "Score each shift by dollar impact, strategic relevance, and driver-narrative clarity." },
    { title: "Generate weekly executive briefing cards", description: "Produce weekly-briefing cards with up/down/flat indicators and driver lines for executive review." },
  ],
  "middleby-uc-17": [
    { title: "Fetch NL-prompt-referenced GA brand-session data from {source1}", description: "Query real GA brand-level session data covering the user's natural-language date-and-page prompt." },
    { title: "Resolve prompt to chart specification from {source2}", description: "Parse the NL prompt into a chart specification (dimensions, filters, series) using the analytics-schema map." },
    { title: "Apply NL-prompt safety rules from {knowledge1}", description: "Cross-reference resolved specification against allowed-dataset rules and disallow-list filters." },
    { title: "Rank result quality and confidence", description: "Score the resolved chart by prompt-fit confidence and by data-freshness of the underlying GA fields." },
    { title: "Generate chart output for executive prompt", description: "Produce the requested chart with methodology footnote and data-caveats for executive review." },
  ],
  "middleby-uc-19": [
    { title: "Fetch product-level browse and quote data from {source1}", description: "Query per-SKU browse volume and quote counts using GA product-detail and HubSpot deal-line signals." },
    { title: "Detect high-browse / low-quote outliers from {source2}", description: "Identify products or SKUs with disproportionately high browsing but low or zero quote requests." },
    { title: "Apply catalog-gap rules from {knowledge1}", description: "Cross-reference outliers against catalog policy on discontinuation, PDP quality, and dealer-coverage rules." },
    { title: "Rank outliers by lost-quote pipeline exposure", description: "Score each outlier by lost-quote pipeline dollars and by hypothesised catalog-gap severity." },
    { title: "Generate catalog and SKU-gap action list", description: "Produce ranked catalog interventions (PDP improvement, discontinuation, dealer awareness) for merchandising-lead approval." },
  ],
  "middleby-uc-20": [
    { title: "Fetch site-error rate and quote-volume timelines from {source1}", description: "Query site-error rates and RFQ volumes around each recent release deploy from the deploy calendar." },
    { title: "Detect regression correlations post-release from {source2}", description: "Attribute error-rate spikes and quote-volume dips to specific release deploys using timeline correlation." },
    { title: "Apply post-release regression rules from {knowledge1}", description: "Cross-reference detected regressions against release-quality thresholds and rollback-trigger criteria." },
    { title: "Rank releases by regression impact", description: "Score each recent release by aggregate error-and-quote-impact dollars and by rollback-vs-fix trade-off." },
    { title: "Generate regression-response action list", description: "Produce ranked response options (rollback, hotfix, monitor) for engineering-lead approval." },
  ],
  "middleby-uc-21": [
    { title: "Fetch product page views vs quote counts from {source1}", description: "Query product-detail-page views and downstream quote counts per page for the last 30 days." },
    { title: "Detect view-heavy / quote-light pages from {source2}", description: "Identify pages with strong views but weak quote-conversion; segment by page_type and brand." },
    { title: "Apply page-effectiveness rules from {knowledge1}", description: "Cross-reference weak pages against PDP-quality standards, content-freshness, and merchandising rules." },
    { title: "Rank pages by conversion-lift opportunity", description: "Score each weak page by projected quote lift from a PDP improvement and by dollar impact." },
    { title: "Generate PDP-improvement action list", description: "Produce ranked content and merchandising changes for digital-lead approval." },
  ],
  "middleby-uc-22": [
    { title: "Fetch anonymous session behavior records from {source1}", description: "Query anonymous session-behavior signals (dwell, page depth, repeat visits) for sessions that did not submit an RFQ." },
    { title: "Cluster into high-intent segments from {source2}", description: "Cluster non-quoting sessions into behavior-defined segments that most closely resemble known buyer patterns." },
    { title: "Apply high-intent-detection rules from {knowledge1}", description: "Cross-reference clusters against intent-scoring policy and known-buyer-pattern definitions." },
    { title: "Rank segments by hidden-pipeline dollars", description: "Score each high-intent segment by hidden-pipeline exposure and by activatability through re-marketing or content." },
    { title: "Generate high-intent segment activation list", description: "Produce ranked activation plays (re-marketing, content, on-site prompt) for marketing-lead approval." },
  ],
  "middleby-uc-23": [
    { title: "Fetch headline metrics from GA, LogRocket, HubSpot from {source1}", description: "Query weekly headline metrics from the three siloed analytics tools for the same reporting window." },
    { title: "Detect divergence between tool signals from {source2}", description: "Identify metrics where GA, LogRocket, and HubSpot signals diverge (e.g. traffic up, quotes down) and attribute cause." },
    { title: "Apply unified-analytics rules from {knowledge1}", description: "Cross-reference divergences against expected-behavior baselines and unified-schema mapping rules." },
    { title: "Rank divergences by executive-narrative value", description: "Score each divergence by executive-attention worth and by pipeline dollars implicated." },
    { title: "Generate unified analytics narrative", description: "Produce a unified narrative reconciling traffic, UX, and quote signals for executive review." },
  ],
  "middleby-uc-24": [
    { title: "Fetch HubSpot deal IDs with example session IDs from {source1}", description: "Query recent submitted quotes with linked session IDs where the deal-session stitch is available." },
    { title: "Reconstruct session-to-quote narrative from {source2}", description: "Reconstruct the specific site actions leading up to the quote submission for the requested deal." },
    { title: "Apply session-replay privacy rules from {knowledge1}", description: "Cross-reference replay assets against PII-masking policy and internal-consumption-only rules." },
    { title: "Rank replay findings by dealer-coaching value", description: "Score each replay by dealer-coaching value and by product-content improvement signals." },
    { title: "Generate replay-linked briefing for dealer or ops", description: "Produce a specific quote-replay briefing with talking points for sales-lead review." },
  ],
  "middleby-uc-25": [
    { title: "Fetch multi-product RFQ carts from {source1}", description: "Query RFQ submissions containing more than one SKU line in the same cart over the last 90 days." },
    { title: "Detect top co-quoted SKU pairs from {source2}", description: "Identify SKU pairs with the highest co-quote frequency and compute attach-rate vs base-rate." },
    { title: "Apply cross-sell rules from {knowledge1}", description: "Cross-reference identified pairs against merchandising cross-sell rules and dealer-preferred bundles." },
    { title: "Rank pairs by attach-rate opportunity", description: "Score each SKU pair by projected quote-attach lift if surfaced as a recommendation." },
    { title: "Generate cross-sell recommendation list", description: "Produce ranked SKU pairs for the RFQ cart recommendation surface for merchandising-lead approval." },
  ],
  "middleby-uc-26": [
    { title: "Fetch quote-journey records by user type from {source1}", description: "Query RFQ journeys segmented by guest, registered buyer, and sales-rep origination over the last 60 days." },
    { title: "Compare journey patterns and outcomes from {source2}", description: "Compute conversion rate, journey length, and average deal size for each user-type cohort." },
    { title: "Apply user-type journey rules from {knowledge1}", description: "Cross-reference cohort differences against role-specific UX rules and rep-workflow policy." },
    { title: "Rank cohorts by opportunity dollars", description: "Score each cohort by conversion-lift opportunity and by UX-fix confidence." },
    { title: "Generate user-type journey recommendations", description: "Produce ranked UX and workflow interventions per cohort for digital-lead approval." },
  ],
  "middleby-uc-27": [
    { title: "Fetch quote-cart contents pre-submit from {source1}", description: "Query in-flight RFQ carts capturing SKU composition and dwell-time before submission." },
    { title: "Detect complementary-SKU recommendation candidates from {source2}", description: "Identify complementary SKUs likely to attach to the current cart using co-quote history and taxonomy adjacency." },
    { title: "Apply cross-sell surface rules from {knowledge1}", description: "Cross-reference candidate recommendations against merchandising priority and cart-side surface rules." },
    { title: "Rank recommendations by attach-lift confidence", description: "Score each recommendation by expected attach-lift and by dealer-support confidence for that pairing." },
    { title: "Generate cart-side cross-sell cards", description: "Produce a ranked recommendation set for the cart-side pre-submit surface for merchandising-lead approval." },
  ],
  "middleby-ex-01": [
    { title: "Fetch RFQ funnel step outcomes from {source1}", description: "Query RFQ funnel step-conversion data segmented by week for the last 3 months." },
    { title: "Detect the decline drivers vs prior period from {source2}", description: "Attribute the RFQ-conversion decline to specific funnel steps, brands, or campaigns using variance decomposition." },
    { title: "Apply decline-diagnosis rules from {knowledge1}", description: "Cross-reference detected drivers against known-issue log, deploy calendar, and campaign calendar." },
    { title: "Rank decline drivers by dollar impact", description: "Score each driver by pipeline-dollar impact and by remediation confidence." },
    { title: "Generate decline-diagnosis narrative and actions", description: "Produce a ranked narrative of decline drivers with recommended fixes for executive-lead approval." },
  ],
  "middleby-ex-02": [
    { title: "Fetch GA brand-session interest data from {source1}", description: "Query GA brand-session volumes as an interest proxy for every product family over the last 90 days." },
    { title: "Attribute interest to quote-revenue from {source2}", description: "Attribute interest signals to downstream quote revenue by joining with HubSpot deal outcomes at the brand level." },
    { title: "Apply attribution rules from {knowledge1}", description: "Cross-reference derived attribution against attribution-policy caveats and known-data limitations." },
    { title: "Rank products by interest-to-revenue gap", description: "Score each product by interest-vs-revenue gap and by expected pipeline lift from closing the gap." },
    { title: "Generate interest-to-revenue action list", description: "Produce ranked interventions (content, dealer, PDP) for merchandising-lead approval." },
  ],
  "middleby-ex-03": [
    { title: "Fetch HubSpot lead-quality fields and follow-up timestamps from {source1}", description: "Query lead-quality signals (fit score, source, timeframe) and rep-follow-up timestamps for the last 60 days." },
    { title: "Detect high-quality slow-follow-up leads from {source2}", description: "Identify leads with high fit scores that lack timely rep follow-up per the response-time SLA." },
    { title: "Apply lead-response policy from {knowledge1}", description: "Cross-reference slow-follow-up leads against response-time SLA and rep-workload rules." },
    { title: "Rank leads by lost-pipeline dollars and repeat risk", description: "Score each slow-followed lead by pipeline-dollars-at-risk and by probability of loss to a competitor." },
    { title: "Generate lead-response action list", description: "Produce a rep-and-lead action list with owner and target response time for sales-lead approval." },
  ],
  "middleby-ex-04": [
    { title: "Fetch site-search terms and zero-result records from {source1}", description: "Query top site-search terms and zero-result queries from Adobe or the site-search sample for the last 30 days." },
    { title: "Detect content and product-discovery gaps from {source2}", description: "Attribute zero-result queries to catalog coverage gaps, synonym mapping gaps, or missing content." },
    { title: "Apply discovery-optimisation rules from {knowledge1}", description: "Cross-reference detected gaps against search-tuning policy, synonym rules, and catalog-mapping playbook." },
    { title: "Rank query gaps by pipeline exposure", description: "Score each gap by lost-quote pipeline exposure and by fix-effort (synonym, redirect, content, catalog)." },
    { title: "Generate site-search remediation actions", description: "Produce ranked fixes (synonyms, redirects, PDPs) for digital-lead approval." },
  ],
  "middleby-ex-05": [
    { title: "Fetch category-level quote-pipeline data from {source1}", description: "Query pipeline dollars at risk by product category for open and stalled quotes over the last 90 days." },
    { title: "Attribute pipeline-at-risk to root cause from {source2}", description: "Attribute at-risk pipeline to specific stalled stages, dealer-coverage gaps, or product-availability constraints." },
    { title: "Apply category-risk rules from {knowledge1}", description: "Cross-reference at-risk pipeline against category-priority policy and executive-visibility thresholds." },
    { title: "Rank categories by revenue-at-risk dollars", description: "Score each category by dollar exposure of losing at-risk pipeline and by intervention-cost trade-off." },
    { title: "Generate category revenue-at-risk plan", description: "Produce ranked recoveries (dealer push, content, pricing move) for sales-lead approval." },
  ],
  "middleby-ex-06": [
    { title: "Fetch executive growth-KPI strip from {source1}", description: "Query weekly executive KPIs (traffic, quotes, conversion, pipeline dollars) for the current period vs prior year." },
    { title: "Detect growth-narrative drivers from {source2}", description: "Attribute growth or decline to specific brands, regions, or channels using variance decomposition." },
    { title: "Apply executive-narrative rules from {knowledge1}", description: "Cross-reference detected drivers against briefing-style rules and executive priority frameworks." },
    { title: "Rank drivers by narrative importance", description: "Score each driver by dollar impact and by strategic-relevance to leadership priorities this quarter." },
    { title: "Generate executive growth dashboard narrative", description: "Produce a narrative overlay for the executive KPI strip with driver commentary for executive review." },
  ],
  "middleby-ns-01": [
    { title: "Fetch open UX-issue and pipeline-impact estimates from {source1}", description: "Query open LogRocket issues and their estimated pipeline-at-risk dollars for the last 30 days." },
    { title: "Simulate fix scenarios and recovery from {source2}", description: "Simulate expected quote-pipeline recovery under alternative fix-priority scenarios using the impact model." },
    { title: "Apply prioritisation rules from {knowledge1}", description: "Cross-reference scenarios against engineering-effort thresholds and pipeline-materiality rules." },
    { title: "Rank fixes by recoverable pipeline dollars", description: "Score each fix by recoverable pipeline dollars per engineering-effort unit for prioritised selection." },
    { title: "Generate recovery-simulator recommendation list", description: "Produce ranked fixes with expected pipeline recovery for engineering-and-digital-lead approval." },
  ],
  "middleby-ns-02": [
    { title: "Fetch account-activity signals and pipeline dollars from {source1}", description: "Query account-level activity (visit, cart-add, quote-open, stalled) alongside pipeline dollars at stake." },
    { title: "Rank accounts by act-now score from {source2}", description: "Compute an act-now score per account combining pipeline dollars, activity urgency, and rep-relationship strength." },
    { title: "Apply next-best-action rules from {knowledge1}", description: "Cross-reference the worklist against rep-workload policy, dealer-coverage rules, and outreach-frequency limits." },
    { title: "Rank accounts by dollar-and-urgency composite", description: "Score each account by composite priority and attach the specific reason for prioritisation." },
    { title: "Generate sales next-best-action worklist", description: "Produce a rep-by-rep prioritised worklist with reason-per-account for sales-lead approval." },
  ],
  "middleby-ns-03": [
    { title: "Fetch brand-level session and quote metrics week-over-week from {source1}", description: "Query brand-level GA sessions and HubSpot quote metrics with week-over-week deltas for the last 8 weeks." },
    { title: "Detect momentum shifts by brand from {source2}", description: "Compute momentum scores per brand using session, quote, and win-rate trends; highlight gainers and losers." },
    { title: "Apply momentum-radar rules from {knowledge1}", description: "Cross-reference momentum shifts against seasonal expectation and campaign-calendar effects." },
    { title: "Rank brands by momentum-change severity", description: "Score each brand by absolute momentum shift and by strategic relevance to Middleby portfolio priorities." },
    { title: "Generate brand-momentum radar report", description: "Produce a brand-ranked radar highlighting notable gainers and losers for merchandising-and-dealer-lead review." },
  ],
  "middleby-ns-04": [
    { title: "Fetch historical pipeline trajectory data from {source1}", description: "Query historical monthly pipeline outcomes and driver features for the last 24 months across brands." },
    { title: "Project next-month pipeline and drivers from {source2}", description: "Project pipeline for the next month using the trained forecast and identify the top drivers of the trajectory." },
    { title: "Apply forecast-and-what-if rules from {knowledge1}", description: "Cross-reference the forecast against known upcoming campaigns, seasonality effects, and scheduled site releases." },
    { title: "Rank scenarios by pipeline-swing magnitude", description: "Score each what-if scenario (campaign push, fix, dealer expansion) by expected pipeline swing dollars." },
    { title: "Generate pipeline forecast and scenario report", description: "Produce a pipeline forecast with ranked what-if scenarios for executive-lead review." },
  ],
  "middleby-kpi-01": [
    { title: "Fetch GA sessions by channel and device from {source1}", description: "Query sessions and downstream quote counts segmented by traffic source and device type (desktop/mobile/tablet)." },
    { title: "Compute conversion by channel-device from {source2}", description: "Compute session-to-quote conversion for every channel-device combination over the last 30 days." },
    { title: "Apply channel-device analysis rules from {knowledge1}", description: "Cross-reference conversion patterns against acquisition-budget policy and device-UX standards." },
    { title: "Rank channel-device pairs by pipeline opportunity", description: "Score each channel-device pair by opportunity dollars from closing conversion gaps." },
    { title: "Generate channel-and-device optimisation actions", description: "Produce ranked interventions (budget shift, mobile UX, campaign tuning) for marketing-lead approval." },
  ],
  "middleby-kpi-02": [
    { title: "Fetch HubSpot closed-won and closed-lost data from {source1}", description: "Query closed-won rate and closed-lost reason tallies from HubSpot for the last 6 months." },
    { title: "Detect closed-lost concentration by reason from {source2}", description: "Attribute closed-lost pipeline to specific reasons (price, timing, competitor, spec-mismatch) with monthly counts." },
    { title: "Apply win-rate diagnosis rules from {knowledge1}", description: "Cross-reference closed-lost reasons against known-issue library and rep-training coverage." },
    { title: "Rank lost-reason categories by dollar exposure", description: "Score each closed-lost reason by aggregated lost-pipeline dollars and by fixable-root-cause confidence." },
    { title: "Generate win-rate-improvement action list", description: "Produce ranked interventions (pricing, spec-guides, competitive-training) for sales-lead approval." },
  ],
  "middleby-kpi-03": [
    { title: "Fetch HubSpot live-chat buy-intent transcripts from {source1}", description: "Query live-chat conversations that start with buy-intent phrases (Can I buy, Buy Now Online) for the last 30 days." },
    { title: "Compute buy-intent-to-quote conversion from {source2}", description: "Compute the share of buy-intent chats that resulted in a submitted quote and attribute drop-off root cause." },
    { title: "Apply chat-conversion rules from {knowledge1}", description: "Cross-reference detected drop-off patterns against chat-agent scripting and handoff-quality standards." },
    { title: "Rank chat-flow gaps by pipeline-conversion loss", description: "Score each drop-off pattern by pipeline dollars lost and by fixable-scripting or handoff-fix confidence." },
    { title: "Generate live-chat buy-intent optimisation actions", description: "Produce ranked interventions (agent scripting, handoff redesign, product info) for support-and-sales-lead approval." },
  ],
  "middleby-kpi-04": [
    { title: "Fetch guided-shopping-tool session and quote outcomes from {source1}", description: "Query LogRocket sessions using guided shopping tools and downstream quote submissions." },
    { title: "Compute tool-by-tool conversion from {source2}", description: "Compute conversion rate per guided shopping tool from tool-start to quote-submit; highlight best and worst tools." },
    { title: "Apply guided-shopping rules from {knowledge1}", description: "Cross-reference conversion outcomes against expected-behavior baselines and tool-UX standards." },
    { title: "Rank guided tools by conversion opportunity", description: "Score each guided shopping tool by conversion lift opportunity and by projected pipeline impact." },
    { title: "Generate guided-tool improvement actions", description: "Produce ranked interventions (UX redesign, content, path simplification) for digital-lead approval." },
  ],
  "middleby-kpi-05": [
    { title: "Fetch GA4 entry-page bounce, depth, and duration from {source1}", description: "Query entry-page bounce rate, page depth, and average session duration for the last 30 days." },
    { title: "Detect high-bounce / low-engagement entry pages from {source2}", description: "Identify entry pages with disproportionately high bounce rate and low session-depth vs peer pages." },
    { title: "Apply entry-page policy from {knowledge1}", description: "Cross-reference weak entry pages against content-freshness policy and SEO-and-UX standards." },
    { title: "Rank entry pages by traffic-exposure and fix confidence", description: "Score each weak entry page by traffic volume exposed and by expected engagement lift from a fix." },
    { title: "Generate entry-page improvement actions", description: "Produce ranked fixes (content refresh, CTA redesign, nav tweak) for digital-lead approval." },
  ],
  "middleby-kpi-06": [
    { title: "Fetch closed-won brand deals with PO-reference status from {source1}", description: "Query closed-won HubSpot deals per brand with PO-reference-populated status for the last quarter." },
    { title: "Detect brands with missing PO-reference coverage from {source2}", description: "Identify brands with a materially lower share of closed-won deals carrying a PO reference." },
    { title: "Apply PO-reference policy from {knowledge1}", description: "Cross-reference missing-coverage brands against finance-close policy and dealer-agreement PO requirements." },
    { title: "Rank brands by PO-coverage gap and revenue exposure", description: "Score each brand by revenue exposure of PO-missing deals and by finance-close audit risk." },
    { title: "Generate PO-reference-coverage action list", description: "Produce ranked interventions (dealer coaching, form update, close-checklist enforcement) for finance-lead approval." },
  ],
  "middleby-kpi-07": [
    { title: "Fetch quote volume and closed-won rate by sales territory from {source1}", description: "Query quotes generated and closed-won rates by sales-rep territory for the last quarter." },
    { title: "Detect territory-performance divergence from {source2}", description: "Identify territories over- or under-performing on quote volume vs peer territories at the same portfolio mix." },
    { title: "Apply territory-management rules from {knowledge1}", description: "Cross-reference divergences against territory-plan targets and rep-quota policy." },
    { title: "Rank territories by pipeline opportunity", description: "Score each underperforming territory by pipeline-opportunity dollars and by best-practice-transfer confidence." },
    { title: "Generate territory-performance action list", description: "Produce a territory-by-territory intervention list (coaching, dealer support, quota reset) for sales-lead approval." },
  ],
  "middleby-kpi-08": [
    { title: "Fetch quote volume and win-rate by customer segment from {source1}", description: "Query quotes and win rate segmented by HubSpot customer-segment field for the last quarter." },
    { title: "Detect segment-performance patterns from {source2}", description: "Identify segments over-indexing on quote generation or win rate and segments falling behind their potential." },
    { title: "Apply segment-strategy rules from {knowledge1}", description: "Cross-reference detected patterns against segment-strategy priorities and dealer-alignment rules." },
    { title: "Rank segments by strategic opportunity", description: "Score each segment by pipeline-opportunity dollars and by dealer-scaling readiness." },
    { title: "Generate segment-strategy actions", description: "Produce ranked recommendations (dealer routing, campaign focus, product bundling) for sales-lead approval." },
  ],
  "middleby-kpi-09": [
    { title: "Fetch GA4 product-detail-page view counts from {source1}", description: "Query the most-viewed product-detail-pages segmented by brand, SKU, and category over the last 7 days." },
    { title: "Detect top-viewed vs quote-conversion divergence from {source2}", description: "Cross-reference top-viewed PDPs with downstream quote counts to spot high-attention low-conversion outliers." },
    { title: "Apply PDP-attention rules from {knowledge1}", description: "Cross-reference divergences against PDP-quality standards and merchandising priority." },
    { title: "Rank PDPs by attention-to-quote opportunity", description: "Score each top-viewed PDP by pipeline opportunity from closing the attention-to-quote gap." },
    { title: "Generate top-PDP action list", description: "Produce ranked interventions (content, CTA, dealer routing) for digital-lead approval." },
  ],
  "middleby-kpi-10": [
    { title: "Fetch HubSpot quotes rolled up to L1 taxonomy from {source1}", description: "Query HubSpot quote volume rolled up to L1 shop taxonomy categories over the last quarter." },
    { title: "Detect category-level quote-demand patterns from {source2}", description: "Attribute quote-volume shifts to specific L1 categories with quarter-over-quarter growth or decline." },
    { title: "Apply category-strategy rules from {knowledge1}", description: "Cross-reference patterns against category-strategy priorities and merchandising alignment." },
    { title: "Rank categories by quote-demand growth", description: "Score each category by absolute quote demand and by growth momentum vs plan." },
    { title: "Generate category demand action list", description: "Produce ranked interventions (dealer emphasis, campaign, PDP investment) for merchandising-lead approval." },
  ],
  "middleby-kpi-11": [
    { title: "Fetch US quote volume and win rate by state from {source1}", description: "Query HubSpot quote volume and closed-won rate for every US state in the current reporting period." },
    { title: "Roll up state metrics to sales territory from {source2}", description: "Aggregate state-level quotes and win rate into sales territory groupings for coverage and win-rate analysis." },
    { title: "Apply state-and-territory analysis rules from {knowledge1}", description: "Cross-reference state performance against territory-plan targets and dealer-coverage rules." },
    { title: "Rank states by quote-volume and win-rate strength", description: "Score each state by absolute quote volume and by win-rate strength relative to its territory peers." },
    { title: "Generate state-level territory action list", description: "Produce ranked interventions (dealer support, territory rebalance, coaching) for sales-lead approval." },
  ],
  "middleby-kpi-12": [
    { title: "Fetch brand-territory quote-volume and win-rate matrix from {source1}", description: "Query HubSpot quote volume and win rate for every brand within every sales territory over the last quarter." },
    { title: "Detect brand-territory over- and under-performance from {source2}", description: "Identify brand-territory pairs where win rate materially exceeds or falls short of the brand or territory average." },
    { title: "Apply brand-territory analysis rules from {knowledge1}", description: "Cross-reference the matrix against dealer coverage, competitive positioning, and portfolio strategy rules." },
    { title: "Rank brand-territory pairs by opportunity", description: "Score each pair by opportunity dollars from doubling down on strong pockets or fixing weak pockets." },
    { title: "Generate brand-territory action list", description: "Produce ranked interventions (dealer emphasis, marketing, pricing) for merchandising-and-sales-lead approval." },
  ],
  "middleby-kpi-13": [
    { title: "Fetch customer-segment quote mix across L1 categories from {source1}", description: "Query how each customer segment splits its quote volume across L1 product categories for the last quarter." },
    { title: "Detect concentration patterns per segment from {source2}", description: "Identify each segment's dominant categories and the categories it under-buys relative to portfolio potential." },
    { title: "Apply segment-category rules from {knowledge1}", description: "Cross-reference the concentration matrix against segment-strategy and category-team account plans." },
    { title: "Rank segment-category combinations by cross-sell opportunity", description: "Score each segment-category pair by projected cross-sell pipeline lift and by dealer-support readiness." },
    { title: "Generate segment-cross-sell recommendations", description: "Produce ranked interventions (targeted campaigns, dealer routing, bundle offers) for merchandising-and-sales-lead approval." },
  ],
};

// ── Layer 2: entry-derived generator ────────────────────────────────────────
// Uses the catalog entry's own metadata (title, problemSignal, items[0],
// businessArea, actionType) to produce 5 bespoke-feeling steps for any
// seeded entry - so use cases that share a businessArea still get their own
// distinct plan items.

/** Verb → past-participle / noun form for step labels. */
const ACTION_VERB_TO_NOUN = {
  Analyze: "analysis",
  Diagnose: "diagnosis",
  Improve: "improvement",
  Convert: "conversion",
  Summarize: "summary",
  Prioritize: "priority list",
};

/** Extract a short noun phrase from the use case title. */
function shortenTitle(title) {
  const s = String(title || "").trim();
  if (!s) return "workflow";
  if (s.length <= 42) return s;
  return s.slice(0, 42).replace(/[,;:]\s*[^,;:]*$/, "");
}

/** Pull the first meaningful sentence out of a problem signal / items[0]. */
function firstSentence(text) {
  const s = String(text || "").trim();
  if (!s) return "";
  const cut = s.search(/[.;]\s/);
  return cut > 0 ? s.slice(0, cut) : s;
}

/**
 * Produce 5 bespoke plan-items from a catalog entry when we don't have a
 * hand-authored override. References the entry's own title, problem signal,
 * and items[0] so no two use cases render the same list.
 */
function planItemsFromEntry(entry, source1, source2, knowledge1) {
  const title = shortenTitle(entry?.title);
  const signal = firstSentence(entry?.problemSignal);
  const brief = firstSentence(entry?.items?.[0]);
  const question = String(entry?.sampleQuestion || "").trim();
  const actionNoun = ACTION_VERB_TO_NOUN[entry?.actionType] || "analysis";
  const contextLine = signal || brief || question || `${title} signal`;

  return [
    {
      title: `Fetch data supporting ${title} from ${source1}`,
      description: brief
        ? `Query datasets needed to ${brief.charAt(0).toLowerCase() + brief.slice(1)}. Validate freshness before ${actionNoun} begins.`
        : `Pull the records required to run ${title}. Validate data freshness before ${actionNoun} begins.`,
    },
    {
      title: `Detect signal for "${contextLine.length > 60 ? contextLine.slice(0, 57) + "..." : contextLine}" from ${source2}`,
      description: signal
        ? `Run pattern detection tuned to this signal: ${signal}. Highlight anomalies beyond the historical baseline.`
        : `Run pattern detection on the retrieved data. Highlight anomalies specific to ${title}.`,
    },
    {
      title: `Apply business rules from ${knowledge1}`,
      description: `Cross-reference ${title} findings against confirmed policy, thresholds, and playbooks in the knowledge base.`,
    },
    {
      title: `Rank ${title} findings by business impact`,
      description: `Score each finding by dollar exposure, urgency, and confidence. Prioritize the actions with the highest expected impact.`,
    },
    {
      title: `Generate ${title} recommendations`,
      description: brief
        ? `Produce a ranked action list to ${brief.charAt(0).toLowerCase() + brief.slice(1)}. Route as Decision Cards for approval or execute within guardrails.`
        : `Produce a ranked action list for ${title}. Route as Decision Cards for approval or execute within guardrails.`,
    },
  ];
}

// ── Layer 3+4: category and title fallbacks live in use-case-draft.js ───────

/**
 * Resolve plan items for a catalog entry (or partial entry from Builder draft).
 *
 * Returns null when this module cannot produce items - caller should then fall
 * through to the title/category fallback logic in use-case-draft.js.
 *
 * @param {object|null} entry Catalog entry (or partial with title/businessArea)
 * @param {string} source1 First confirmed data source name
 * @param {string} source2 Second confirmed data source name
 * @param {string} knowledge1 First confirmed knowledge base name
 * @returns {Array<{title:string,description:string}>|null}
 */
export function planItemsForCatalogEntry(entry, source1, source2, knowledge1) {
  if (!entry) return null;

  // Layer 1: hand-authored override
  if (entry.id && PLAN_ITEMS_BY_CATALOG_ID[entry.id]) {
    return PLAN_ITEMS_BY_CATALOG_ID[entry.id].map((item) => ({
      title: item.title
        .replace("{source1}", source1)
        .replace("{source2}", source2)
        .replace("{knowledge1}", knowledge1),
      description: item.description
        .replace("{source1}", source1)
        .replace("{source2}", source2)
        .replace("{knowledge1}", knowledge1),
    }));
  }

  // Layer 2: entry-derived generator. Requires at least a title to produce
  // meaningful bespoke steps; return null so the caller falls through to
  // title/category templates when title is missing.
  if (!entry.title) return null;
  return planItemsFromEntry(entry, source1, source2, knowledge1);
}
