/**
 * Per-catalog-id seed decision cards. Shared between the Builder Step 8
 * Launch handler (writes cards into localStorage on activation) and the
 * activation-flow page (auto-seeds cards for activated use cases that were
 * activated before the persistence merge-fix landed, so the Review Decisions
 * button appears for every use case that has cards defined).
 *
 * See UCBuilderStep10Completion.jsx for the category-level template fallback
 * used for user-created use cases (no catalog id match here).
 */

// Use cases that are pure analytics / reporting - no decisions the human
// needs to approve. Step 9 shows an "Insights delivered" state for these.
export const NO_DECISION_CARDS_USE_CASES = new Set([
  // GFI
  "gfi-uc-01-perishable-forecast",
  "gfi-uc-07-vendor-scorecards",
  "gfi-uc-12-customer-demand-intelligence",
  "gfi-uc-15-competitive-positioning",
  // Retail default
  "gallery-uc-09",
  "gallery-uc-11",
  "gallery-uc-16",
  "gallery-uc-17",
  "gallery-uc-20",
  // Media — pure analytics / diagnostic use cases (no human approvals required)
  "media-uc-01-content-conversation-trends",
  "media-uc-04-digital-market-landscape",
  "media-uc-05-content-gap-analysis",
  "media-uc-06-title-launch-performance",
  "media-uc-07-social-sentiment-releases",
  "media-uc-08-regional-language-performance",
  "media-uc-10-aeo-geo-visibility",
  "media-uc-16-catalog-health-metadata",
  "media-uc-18-launch-war-room-readiness",
  "media-uc-19-playout-schedule-optimization",
  "media-uc-20-cdn-regional-performance",
  "media-uc-21-signup-funnel-conversion",
  "media-uc-24-telco-bundle-attribution",
  "media-uc-25-content-roi-diagnostics",
  "media-uc-26-editorial-productivity",
]);

export const SEED_DECISION_CARDS = {
  // ==============================================================
  //  GFI seed use cases
  // ==============================================================
  "gfi-uc-02-inter-dc-rebalance": [
    { id: "dc-gfi-reb-1", title: "Approve transfer - Denver DC to NJ DC (Brie de Meaux)", reasoning: "Denver has 260 lbs of Brie at 5 days to code; NJ will stock out in 3 days. Multi-temp fleet has empty return leg tonight.", action: "Transfer 240 lbs Brie de Meaux, Denver -> NJ, dispatch 6 PM.", value: 4320, supplier: "Fleet ops" },
    { id: "dc-gfi-reb-2", title: "Approve transfer - Seattle DC to Atlanta DC (Aged Cheddar)", reasoning: "Seattle has 40 lbs of aged cheddar at 8 days to code; Atlanta at 4 days. Standard fleet available Wednesday on this lane.", action: "Transfer 32 lbs Aged Cheddar, Seattle -> Atlanta, Wednesday.", value: 710, supplier: "Fleet ops" },
  ],
  "gfi-uc-03-supplier-risk": [
    { id: "dc-gfi-sup-1", title: "Pre-approve alternate supplier - Fjord Salmon backup", reasoning: "Fjord Salmon impacted by Bergen port storm. Norwegian Coast Fisheries has capacity and previously supplied at 5% price premium.", action: "Pre-approve Norwegian Coast Fisheries as alt supplier for smoked salmon line.", value: 318000, supplier: "Norwegian Coast Fisheries" },
    { id: "dc-gfi-sup-2", title: "Hold shipment - Anatolian Olive Cooperative (FSMA risk)", reasoning: "FSMA 204 compliance risk flagged on Anatolian small-producer network. Holding incoming shipment prevents traceability audit gap.", action: "Hold incoming Anatolian Olive shipment pending compliance review.", value: 186000, supplier: "Compliance ops" },
  ],
  "gfi-uc-04-markdown-optimization": [
    { id: "dc-gfi-md-1", title: "Wave 1 markdown - Brie de Meaux (5 days to code)", reasoning: "260 lbs across 3 DCs, 5 days to code expiry. Elasticity model suggests 15% markdown recovers ~78% of retail vs projected 40% write-off at expiry.", action: "Apply 15% markdown for 48 hours on Brie de Meaux across 3 DCs.", value: 3240, supplier: "Perishable ops" },
    { id: "dc-gfi-md-2", title: "Wave 2 markdown - Prosciutto di Parma (7 days to code)", reasoning: "180 lbs at 3 DCs, 7-day window. First-wave markdown at 10% cleared similar SKUs at 82% sell-through last month.", action: "Apply 10% markdown for 72 hours on Prosciutto di Parma.", value: 2100, supplier: "Perishable ops" },
  ],
  "gfi-uc-05-labor-optimization": [
    { id: "dc-gfi-lab-1", title: "Overtime approval - Denver DC, Thursday", reasoning: "Forecast inbound volume +34% vs plan (Formaggio Alpino shipment inbound). Pick team short 8 hours vs demand. Overtime within budget guardrail.", action: "Approve 8 hours overtime for Denver DC pick team, Thursday.", value: 184, supplier: "DC ops" },
    { id: "dc-gfi-lab-2", title: "Cross-train recommendation - NJ DC, cheese cage", reasoning: "3 cheese-cage certified pickers PTO next week. Cross-training 2 refrigerated-zone pickers takes 2 days but restores coverage.", action: "Enrol 2 refrigerated-zone pickers in cheese-cage cross-training.", value: 0, supplier: "DC training" },
  ],
  "gfi-uc-06-multi-temp-routing": [
    { id: "dc-gfi-rt-1", title: "Route re-plan - Route 14 (Northeast frozen)", reasoning: "Route 14 empty return leg tonight overlaps with a proposed Denver -> NJ Brie transfer. Combining saves $180 in fleet cost with no SLA risk.", action: "Combine Route 14 with Denver -> NJ Brie transfer.", value: 180, supplier: "Fleet ops" },
    { id: "dc-gfi-rt-2", title: "Temperature setpoint change - Truck FL-207", reasoning: "Truck FL-207 carrying multi-temp load; refrigerated compartment set 2C higher than aged-cheese spec. Adjustment needed before departure.", action: "Update compartment setpoint from 5C to 3C on FL-207.", value: 0, supplier: "Fleet ops" },
  ],
  "gfi-uc-08-npi-tracking": [
    { id: "dc-gfi-npi-1", title: "Launch decision - SKU-NPI-4412 (Aged Manchego 30-month)", reasoning: "Pilot in 3 DCs over 6 weeks: 78% sell-through, 4.4/5 dealer NPS. Above launch threshold for national rollout.", action: "Roll SKU-NPI-4412 to all 14 DCs.", value: 128000, supplier: "Merchandising" },
    { id: "dc-gfi-npi-2", title: "Discontinue candidate - SKU-NPI-4302 (Truffle Ricotta)", reasoning: "Pilot at 12 weeks: 34% sell-through, high spoilage rate. Below launch threshold.", action: "Discontinue SKU-NPI-4302 pilot; return remaining inventory to supplier.", value: 0, supplier: "Merchandising" },
  ],
  "gfi-uc-09-fsma-traceability": [
    { id: "dc-gfi-fsma-1", title: "Traceability record gap - Lot 42918 (Fjord Salmon)", reasoning: "Lot 42918 received at NJ DC without the CTE receipt scan. FSMA 204 mandates KDE capture on receipt; regulator audit trail incomplete.", action: "Retro-capture receipt KDE fields from paper log and update WMS record for Lot 42918.", value: 0, supplier: "Compliance ops" },
  ],
  "gfi-uc-10-order-routing": [
    { id: "dc-gfi-or-1", title: "Fulfillment routing - Order GFI-49812 (Boston restaurant)", reasoning: "Order includes 6 SKUs across 3 DCs. Consolidated routing from DC-1 (Boston) is feasible with 1 extra day transit; saves $22 in split-shipment cost.", action: "Consolidate to DC-1 (Boston), single-truck delivery.", value: 22, supplier: "Fulfillment routing" },
  ],
  "gfi-uc-11-infra-curated-programs": [
    { id: "dc-gfi-infra-1", title: "Assortment update - INFRA retailer #48 (Chicago)", reasoning: "Retailer #48 has 3 slow-mover SKUs and no coverage on trending specialty cheese. Model recommends 2 SKU swaps to lift category performance.", action: "Propose 2-SKU swap (retire SKU-A, add SKU-B, add SKU-C) to retailer #48.", value: 4200, supplier: "INFRA merchandising" },
  ],
  "gfi-uc-13-shrinkage-detection": [
    { id: "dc-gfi-shr-1", title: "Investigate variance - Lane 7, Atlanta DC", reasoning: "Lane 7 has 3.2% shrink vs 1.4% network average, 12-week trend. Temperature log shows two overnight excursions above spec.", action: "Schedule refrigeration audit and investigate lane 7 shift handover.", value: 6800, supplier: "Loss prevention" },
    { id: "dc-gfi-shr-2", title: "Supplier variance - Anatolian Olive Cooperative", reasoning: "Receiving weight variance +0.9% above tolerance across 8 recent shipments. Compliance risk if unaddressed; supplier co-op review triggered.", action: "Escalate variance investigation with Anatolian Olive Cooperative.", value: 3400, supplier: "Supplier quality" },
    { id: "dc-gfi-shr-3", title: "Shift handover - Denver DC, Friday PM", reasoning: "Miscount variance concentrated in Friday PM handover for 6 consecutive weeks. Retrain the handover script and re-audit.", action: "Approve shift-handover retraining for Denver Friday PM crew.", value: 0, supplier: "DC ops" },
  ],
  "gfi-uc-14-dynamic-specialty-pricing": [
    { id: "dc-gfi-price-1", title: "Price change - Italian Truffle Oil, US Northeast", reasoning: "Euro/USD moved 4.2% since last price refresh. Competitor imports repriced last week. Elasticity model supports +3% pass-through while preserving share.", action: "Apply +3% price adjustment on Italian Truffle Oil, US Northeast region.", value: 4400, supplier: "Pricing ops" },
  ],
  // ==============================================================
  //  Retail default seed use cases
  // ==============================================================
  "gallery-uc-01": [
    { id: "dc-uc01-1", title: "Cross-store transfer - SKU-9821 (Sony WH-1000XM5)", reasoning: "Store 42 will stock out in 6 hours. Store 17 has 45 units of overstock (18 days on hand). Standard fleet has an outbound truck at 3 PM.", action: "Approve transfer: 40 units, Store 17 -> Store 42.", value: 8400, supplier: "Internal transfer" },
    { id: "dc-uc01-2", title: "Reorder recommendation - SKU-7234 (Samsung 65\" QLED)", reasoning: "Regional stock covers 5 days at current sell-through. Seasonal uplift of 35% expected next week. Reorder threshold: 10 days.", action: "Raise PO: 80 units from Samsung Direct.", value: 11200, supplier: "Samsung Direct" },
  ],
  "gallery-uc-02": [
    { id: "dc-uc02-1", title: "Fulfillment routing - Order #OR-49812", reasoning: "Order spans 4 SKUs across 2 DCs. Consolidated route from DC-3 saves $14 vs split shipment with 1 extra day of transit.", action: "Consolidate to DC-3, single-truck fulfillment.", value: 14, supplier: "Fulfillment routing" },
  ],
  "gallery-uc-03": [
    { id: "dc-uc03-1", title: "Assortment gap - introduce SKU-A2841 in Kitchenware", reasoning: "Category traffic +18% WoW but current assortment missing mid-price bakeware. Competitor listing shows unmet demand pattern.", action: "Add SKU-A2841 (ceramic bakeware set) to Kitchenware assortment in 24 stores.", value: 42000, supplier: "Merchandising" },
    { id: "dc-uc03-2", title: "Retire underperformer - SKU-B3352 (Home Fragrance)", reasoning: "8 weeks below 25% of category average sell-through. No planned promo. Space could go to top-mover SKU-B4102.", action: "Retire SKU-B3352; reallocate shelf to SKU-B4102.", value: 0, supplier: "Merchandising" },
  ],
  "gallery-uc-04": [
    { id: "dc-uc04-1", title: "Shift adjustment - Store 42 Saturday PM", reasoning: "Foot traffic forecast +22% vs baseline for this Saturday PM. Current schedule 3 associates short vs demand. Overtime within budget.", action: "Add 12 associate-hours to Saturday PM shift, Store 42.", value: 276, supplier: "Workforce ops" },
    { id: "dc-uc04-2", title: "Cross-store shift reallocation - Store 17 to 42", reasoning: "Store 17 forecast is soft; Store 42 is overloaded. Two associates can flex 4 hours to Store 42 (10-mile distance).", action: "Flex 8 associate-hours from Store 17 to Store 42.", value: 0, supplier: "Workforce ops" },
  ],
  "gallery-uc-05": [
    { id: "dc-uc05-1", title: "Recovery offer - high-intent price-sensitive segment", reasoning: "2,140 sessions abandoned at shipping-cost page. ML segment classifier flags 62% as price-sensitive. Free-shipping historically converts at 8%.", action: "Send free-shipping recovery offer to 2,140 abandoned-cart addresses.", value: 17100, supplier: "Marketing automation" },
    { id: "dc-uc05-2", title: "Guest-checkout uplift - reduce required fields", reasoning: "Guest checkout drop-off 34% at address form. A/B test shows removing 3 optional fields lifts completion by 6.2%.", action: "Deploy simplified guest checkout form to 100% traffic.", value: 8400, supplier: "E-Com product" },
  ],
  "gallery-uc-06": [
    { id: "dc-uc06-1", title: "Peripheral swap - Store 17, receipt printer #3", reasoning: "Printer offline 4 hours; adjacent registers rerouting traffic. Field tech in region with 2-hour swap ETA.", action: "Dispatch field tech to swap printer today.", value: 260, supplier: "Field service" },
    { id: "dc-uc06-2", title: "Preventive maintenance - aging pin-pad fleet (3 stores)", reasoning: "3 stores have pin-pads over 40 months old, at 65% of expected fail rate. Preventive swap costs 12% of unplanned incident cost.", action: "Schedule preventive pin-pad swap for Store 22, 30, 47.", value: 1400, supplier: "Field service" },
  ],
  "gallery-uc-07": [
    { id: "dc-uc07-1", title: "Return disposition - RET-88214 (in-warranty damage)", reasoning: "Customer photo evidence supports damage claim. Refurb path recovers 60% of unit cost. Restocking as new not viable.", action: "Refund customer; route unit to refurb queue.", value: 1400, supplier: "Reverse logistics" },
    { id: "dc-uc07-2", title: "Return triage - RET-88232 (buyer's remorse, out of window)", reasoning: "Return request 42 days post-purchase; policy is 30 days. Store credit is within manager authority; refund requires escalation.", action: "Offer store credit at 90% of purchase price.", value: 0, supplier: "Customer support" },
  ],
  "gallery-uc-08": [
    { id: "dc-uc08-1", title: "Markdown slate - 24 slow-moving SKUs", reasoning: "Sell-through 30% below plan across 24 SKUs. Elasticity suggests 12-18% markdown maximises margin recovery before end-of-season.", action: "Apply 15% markdown to 24 SKUs, hold list for 10 days.", value: 89000, supplier: "Pricing team" },
  ],
  "gallery-uc-10": [
    { id: "dc-uc10-1", title: "Weekend headcount reduction - 6 overstaffed stores", reasoning: "Sunday demand at 6 stores consistently 20% below staffed capacity. Reducing 2 associate-hours per store saves without SLA risk.", action: "Reduce Sunday PM headcount by 2 hours at 6 flagged stores.", value: 2760, supplier: "Workforce ops" },
  ],
  "gallery-uc-12": [
    { id: "dc-uc12-1", title: "Markdown slate - 12 slow-moving SKUs", reasoning: "12 SKUs at 24% of category average sell-through for 6 weeks. Aging inventory; markdown depth of 20% clears within 3 weeks per elasticity model.", action: "Apply 20% markdown to 12 slow-mover SKUs for 3 weeks.", value: 34000, supplier: "Pricing team" },
    { id: "dc-uc12-2", title: "Transfer slow-mover - SKU-4419 (Bose QC45) to higher-demand region", reasoning: "SKU-4419 has 48 days on hand in West region, 4 days in Northeast. Regional transfer takes 3 days and avoids markdown.", action: "Transfer 60 units from West to Northeast region.", value: 6400, supplier: "Internal transfer" },
  ],
  "gallery-uc-13": [
    { id: "dc-uc13-1", title: "Cross-DC rebalance - DC-1 overstock to DC-3 shortage", reasoning: "DC-1 has 45 days of on-hand cover on SKU-3311; DC-3 will stock out in 3 days at current velocity. Standard fleet lane available Thursday.", action: "Approve 200-unit transfer from DC-1 to DC-3, Thursday dispatch.", value: 12800, supplier: "Fulfillment ops" },
  ],
  "gallery-uc-14": [
    { id: "dc-uc14-1", title: "Route order to single DC - OR-58291", reasoning: "Order spans 5 SKUs across 3 DCs. Consolidated route from DC-3 is feasible at $8 vs $28 split shipment; 1 extra day transit.", action: "Consolidate to DC-3 with 1-day extended promise.", value: 20, supplier: "Fulfillment ops" },
    { id: "dc-uc14-2", title: "Update allocation rule - SKU-9821 default routing", reasoning: "SKU-9821 has generated 34 split shipments in 30 days at $18 avg surcharge. Changing default routing to nearest-DC-with-stock cuts split rate 60%.", action: "Update SKU-9821 fulfillment rule to nearest-DC-with-stock.", value: 372, supplier: "Fulfillment ops" },
  ],
  "gallery-uc-15": [
    { id: "dc-uc15-1", title: "Escalate breached order - OR-48912 (SLA +36 hrs)", reasoning: "Order past promised delivery by 36 hours. Root cause: warehouse pick delay. Expedited shipping recovers SLA and preserves customer relationship.", action: "Expedite OR-48912 with next-day shipping.", value: 42, supplier: "Fulfillment ops" },
    { id: "dc-uc15-2", title: "Process fix - retrain DC-3 pick team on high-value SKUs", reasoning: "70% of SLA breaches trace back to DC-3 pick delays on high-value SKUs. Root cause analysis suggests handoff protocol issue.", action: "Retrain DC-3 pick team on high-value handoff protocol.", value: 0, supplier: "DC ops" },
  ],
  "gallery-uc-18": [
    { id: "dc-uc18-1", title: "Roll out NPI - SKU-NP-8842 (Smart Home Hub)", reasoning: "Pilot in 24 stores over 8 weeks: 82% sell-through, 4.3/5 NPS. Above launch threshold for national rollout.", action: "Roll SKU-NP-8842 to all 340 stores.", value: 224000, supplier: "Merchandising" },
    { id: "dc-uc18-2", title: "Discontinue NPI - SKU-NP-7702 (LED Desk Lamp)", reasoning: "Pilot at 12 weeks: 28% sell-through, 3.1/5 NPS. Below launch threshold.", action: "Discontinue SKU-NP-7702; return remaining inventory to vendor.", value: 0, supplier: "Merchandising" },
  ],
  "gallery-uc-19": [
    { id: "dc-uc19-1", title: "Wave 1 markdown - Outdoor & Sports seasonal exit", reasoning: "Season ends in 6 weeks. 14 SKUs at 40+ days on hand. Wave 1 at 10% clears roughly 45% of remaining units per historical cadence.", action: "Start Wave 1 markdown at 10% depth for 14 seasonal SKUs.", value: 32000, supplier: "Pricing team" },
    { id: "dc-uc19-2", title: "Delay Wave 2 by 1 week - Beach & Water gear", reasoning: "Sell-through on Wave 1 running 12% above forecast; deferring Wave 2 preserves margin without inventory risk.", action: "Push Wave 2 markdown from July 30 to August 6.", value: 5800, supplier: "Pricing team" },
  ],
  "gallery-uc-21": [
    { id: "dc-uc21-1", title: "Investigate variance - Cashier ID 42891 at Store 33", reasoning: "Recurring positive variance ($8-$14) across 6 weeks. Not tied to shift, register, or product mix. Warrants investigation but not immediate escalation.", action: "Schedule loss-prevention review with Cashier ID 42891.", value: 320, supplier: "Loss prevention" },
    { id: "dc-uc21-2", title: "POS handoff retraining - Store 42 evening shift", reasoning: "Store 42 evening shift shows consistent $12 avg variance at handoff. Retraining resolves in 78% of similar cases.", action: "Schedule retrain session for Store 42 evening shift.", value: 0, supplier: "Store ops" },
  ],
  "gallery-uc-22": [
    { id: "dc-uc22-1", title: "Coach store manager - Store 09, morning routine gap", reasoning: "Store 09 opening checklist compliance at 68% vs 92% network average over 4 weeks. Coaching resolves similar cases in 3 weeks.", action: "Schedule 1:1 coaching for Store 09 manager on opening routine.", value: 0, supplier: "Store ops" },
  ],
  "gallery-uc-23": [
    { id: "dc-uc23-1", title: "Synonym mapping - top zero-result term \"guided knives\"", reasoning: "287 zero-result searches this week for \"guided knives\". Nearest match SKU-JR-6620 has 6 relevant knives bundled.", action: "Add synonym \"guided knives\" -> SKU-JR-6620 bundle.", value: 0, supplier: "Search team" },
    { id: "dc-uc23-2", title: "Rebuild search index - Home Textiles category", reasoning: "Home Textiles CTR 3.1% vs 8.4% site average. Search relevance audit suggests stale index; rebuild takes 2 hours.", action: "Rebuild search index for Home Textiles category.", value: 0, supplier: "Search team" },
  ],
  "gallery-uc-24": [
    { id: "dc-uc24-1", title: "Fail over to backup gateway - Elavon during 6-8 PM window", reasoning: "Primary processor error rate 4.2% during 6-8 PM window last 4 nights. Backup gateway response time within 200ms of primary.", action: "Route 6-8 PM traffic to backup gateway for 7 days.", value: 5200, supplier: "E-Com ops" },
    { id: "dc-uc24-2", title: "Escalate to processor - recurring error code 5011", reasoning: "Error code 5011 (velocity check) triggering 128 legitimate declines/day. Processor review needed to tune threshold.", action: "Open ticket with primary processor to review error code 5011.", value: 0, supplier: "Payments" },
  ],
  "gallery-uc-25": [
    { id: "dc-uc25-1", title: "Deploy hotfix - mobile checkout regression", reasoning: "Mobile checkout completion dropped 6pt after v4.2 release. LogRocket traces implicate the new address auto-fill component.", action: "Deploy hotfix disabling address auto-fill on mobile.", value: 12400, supplier: "E-Com product" },
    { id: "dc-uc25-2", title: "A/B test - simplified mobile checkout form", reasoning: "Mobile completion trails desktop by 14pt on the same funnel. Simplified form pilot in 5% of traffic showed 8pt uplift.", action: "Roll simplified mobile checkout to 50% of traffic.", value: 6200, supplier: "E-Com product" },
  ],
  "gallery-uc-26": [
    { id: "dc-uc26-1", title: "Replace peripheral - Store 22 (4 pin-pad failures in 30 days)", reasoning: "Store 22 pin-pad #2 has 4 failures in 30 days vs network baseline of 0.4. Age 46 months, well past expected replacement window.", action: "Replace pin-pad #2 at Store 22 this week.", value: 480, supplier: "Field service" },
  ],
  "gallery-uc-27": [
    { id: "dc-uc27-1", title: "Enable edge cache - loyalty lookup service", reasoning: "Loyalty lookup p95 latency at 1,240ms vs 400ms target. Edge cache reduces average by 70% based on similar deployments.", action: "Enable edge cache in front of loyalty lookup service.", value: 0, supplier: "Platform engineering" },
  ],
  "gallery-uc-28": [
    { id: "dc-uc28-1", title: "Resolve promo conflict - Save20 + BOGO stack", reasoning: "Save20 and BOGO promos both applied to 342 transactions last week, causing $18 avg margin leak. Rule update prevents stacking.", action: "Add exclusion rule preventing Save20 + BOGO stack.", value: 6156, supplier: "Pricing ops" },
  ],
  "gallery-uc-29": [
    { id: "dc-uc29-1", title: "Enable overflow queue - Tuesday-Thursday peak window", reasoning: "Avg wait time up 28% during peak. Third-party overflow queue at $3.20/call vs $12 hire cost, immediately deployable.", action: "Enable third-party overflow queue for Tue-Thu peak hours.", value: 1900, supplier: "Contact center ops" },
    { id: "dc-uc29-2", title: "Cross-train - move 3 tier-2 agents to overflow tier-1", reasoning: "Tier-2 utilization at 62%; tier-1 backlog growing. Cross-training 3 agents temporarily flexes coverage without new hires.", action: "Cross-train 3 tier-2 agents on tier-1 overflow.", value: 0, supplier: "Contact center ops" },
  ],
  "gallery-uc-30": [
    { id: "dc-uc30-1", title: "Update knowledge base - top recurring escalation topic", reasoning: "\"Order not delivered\" accounts for 34% of tier-2 escalations, up from 22%. Updated KB article and macro resolve at tier-1 in 78% of test cases.", action: "Publish updated KB article on \"order not delivered\" resolution.", value: 0, supplier: "Support ops" },
    { id: "dc-uc30-2", title: "Retrain tier-1 agents - refund policy escalation category", reasoning: "Refund-policy escalations up 42% WoW. Tier-1 agents are escalating cases within their authority per audit sample.", action: "Retrain tier-1 team on refund policy authority.", value: 0, supplier: "Support ops" },
  ],

  // ==============================================================
  //  Media & OTT decision cards
  // ==============================================================
  "media-uc-02-future-content-opportunity": [
    { id: "dc-media-scope-1", title: "Approve scoping research - regional mythology (Marathi + Tamil)", reasoning: "4-week sustained momentum (74/100); trajectory rising. Absent in current commissioning pipeline; matches our historical mythology hit pattern.", action: "Open a formal commissioning-research brief for regional mythology within 5 working days.", value: 0, supplier: "Content Development" },
    { id: "dc-media-scope-2", title: "Approve scoping research - women-led mythology reboots", reasoning: "4-week sustained momentum (69/100); trajectory rising. One title in development, none in market; strong parallel to family-drama slate.", action: "Add women-led mythology reboots to the next content-development ideation sprint.", value: 0, supplier: "Content Development" },
  ],
  "media-uc-03-competitor-content-benchmark": [
    { id: "dc-media-comp-1", title: "Approve Hindi thriller acquisition sweep", reasoning: "Thriller catalog depth is 3 titles behind top two rivals on Hindi; competitor title overlap climbed to 28%. Acquisitions can close gap this quarter.", action: "Brief acquisitions on 3-title Hindi thriller sweep; target $1.8M budget envelope.", value: 42000, supplier: "Content Acquisitions" },
  ],
  "media-uc-09-recommendation-uplift": [
    { id: "dc-media-rec-1", title: "Promote personalised row - regional-language homepage", reasoning: "A/B test showed +8% incremental watch minutes when personalised regional row replaces editorial slot 3. Rollout risk low; can revert instantly.", action: "Roll personalised regional row to 100% of regional-language cohorts.", value: 68000, supplier: "Product & Discovery" },
  ],
  "media-uc-11-subscription-churn-predictor": [
    { id: "dc-media-churn-1", title: "Stage save offer - at-risk monthly cohort (42K subscribers)", reasoning: "Renewal-window churn on monthly cohort +3.4 pts after marquee title finished. Save-offer eligibility window closes in 48 hours.", action: "Send 20% first-month discount + mythology-adjacent title bundle to 42K at-risk subscribers.", value: 186000, supplier: "Subscriber Retention" },
    { id: "dc-media-churn-2", title: "Downgrade offer - price-sensitive cohort (11K subscribers)", reasoning: "Cluster shows price sensitivity vs. new competitor mobile-only cut. Downgrade to mobile-only retains 68% vs. 42% who otherwise churn.", action: "Offer standard-to-mobile-only downgrade with content-affinity preserved (11K subscribers).", value: 42000, supplier: "Subscriber Retention" },
  ],
  "media-uc-12-plan-pricing-optimization": [
    { id: "dc-media-plan-1", title: "Approve defensive marketing counter - competitor -19% mobile cut", reasoning: "Rival cut mobile-only plan 19% mid-week; simulation shows a 4-week free-trial extension retains 78% of at-risk mobile cohort at lower ARPU cost than matching the cut.", action: "Extend mobile-only free trial from 7 to 14 days for 6 weeks; monitor conversion.", value: 94000, supplier: "Growth Marketing" },
  ],
  "media-uc-13-ad-yield-optimization": [
    { id: "dc-media-ad-1", title: "Restore prior floors - connected TV inventory", reasoning: "Fill on connected TV at 72% vs 80% target for 24 hours after last week's floor adjustment. Restoring priors recovers 5-8 pts fill within one auction cycle.", action: "Roll back last week's connected-TV floor adjustment; monitor for 48 hours.", value: 22000, supplier: "Ad-Ops" },
    { id: "dc-media-ad-2", title: "Rebalance demand-partner mix - programmatic waterfall", reasoning: "Partner B timeout rate 4.1% vs 1.2% baseline; deprioritising B in the waterfall recovers roughly 3% blended eCPM based on last month's A/B.", action: "Move Partner B two positions down in the header-bidding waterfall.", value: 14000, supplier: "Ad-Ops" },
  ],
  "media-uc-14-piracy-leak-monitoring": [
    { id: "dc-media-pir-1", title: "Approve takedown notices - marquee premiere leak", reasoning: "Unauthorised uploads of the marquee premiere detected on 2 torrent indexes and a Telegram channel within 6h of release. Legal-approved takedown templates ready.", action: "Send takedown notices to 3 hosting platforms via legal-authorised recipients.", value: 0, supplier: "Content Protection" },
    { id: "dc-media-pir-2", title: "Escalate — persistent leak after first takedown wave", reasoning: "One torrent index has not honoured first takedown after 6h. Legal team's second-wave process typically resolves within 24h.", action: "Escalate to Content Protection legal team for second-wave takedown.", value: 0, supplier: "Legal" },
  ],
  "media-uc-15-rights-window-expiry": [
    { id: "dc-media-rights-1", title: "Approve renewal negotiation - top-watched title (window closes in 30 days)", reasoning: "Title in top-20 by watch hours across last 90 days; renewal cost within 8% of previous window. Losing this title creates a measurable regional-catalog gap.", action: "Authorise licensing team to open renewal at prior-window terms.", value: 0, supplier: "Licensing" },
  ],
  "media-uc-17-content-ingestion-pipeline": [
    { id: "dc-media-ingest-1", title: "Approve overtime — subtitle QC for marquee launch", reasoning: "Subtitle QC queue is 4 hours behind for launch T-24h. Overtime clears the queue within launch window with no publishing slip.", action: "Approve 6 hours of subtitle QC overtime tonight.", value: 0, supplier: "Content Publishing" },
  ],
  "media-uc-22-payment-decline-recovery": [
    { id: "dc-media-pay-1", title: "Approve BIN-specific dunning retry — top 5 declining BINs", reasoning: "5 BINs account for 38% of failed renewals this week. BIN-uptime-aware retry recovers 41% vs 22% baseline retry cadence in shadow test.", action: "Roll BIN-aware retry to 5 highest-decline BINs.", value: 62000, supplier: "Payments" },
  ],
  "media-uc-23-content-integrity-brand-safety": [
    { id: "dc-media-safety-1", title: "Approve ad-adjacency exclusion — sensitive-content title set", reasoning: "Brand-safety review flagged 12 titles as ad-adjacency risk for the current major-brand campaign. Excluding them protects contract compliance without hurting fill.", action: "Exclude the 12 flagged titles from the current campaign's ad-adjacency set.", value: 0, supplier: "Ad-Ops" },
  ],
  "media-uc-27-adops-team-workload": [
    { id: "dc-media-adops-team-1", title: "Reallocate 2 ad-ops FTEs to pacing-risk campaigns", reasoning: "3 campaigns are at 62% delivered with 25% flight remaining; pacing analysis shows manual intervention recovers 90%+ vs 65% end-of-flight.", action: "Reassign 2 ad-ops FTEs from house-sold housekeeping to pacing-risk queue this week.", value: 38000, supplier: "Ad-Ops" },
  ],
};

/**
 * Returns the seed decision cards for a catalog id, stamped with the
 * useCase context. Returns [] for use cases in NO_DECISION_CARDS_USE_CASES
 * or with no override defined.
 */
export function getSeedDecisionCardsForCatalog(catalogId, useCaseTitle) {
  if (!catalogId) return [];
  if (NO_DECISION_CARDS_USE_CASES.has(catalogId)) return [];
  const cards = SEED_DECISION_CARDS[catalogId];
  if (!cards) return [];
  return cards.map((c) => ({
    status: "pending",
    ...c,
    useCaseId: catalogId,
    useCaseTitle: useCaseTitle || catalogId,
  }));
}
