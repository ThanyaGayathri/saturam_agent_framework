import { useNavigate } from "react-router-dom";
import {
  Rocket, Tag, HelpCircle, Database, Bot, Calendar,
  AlertTriangle, Shield, DollarSign, Users,
} from "lucide-react";
import UCBuilderLayout from "./shared/UCBuilderLayout";
import { Card } from "./ui";
import { useUseCaseDraft } from "../context/UseCaseDraftContext";
import { useDemoProfile } from "../context/DemoProfileContext";
import { useUseCaseCatalog } from "../hooks/useUseCaseCatalog";
import { appendCatalogFromDraft, saveDraftEntryToCatalog } from "../storage/use-cases-catalog";
import { USE_CASE_LAUNCH_SUMMARY_KEY, COPILOT_DECISIONS_KEY } from "../storage/keys";
import { buildLaunchSummaryPayload } from "../domain/use-case-launch-handoff";
import {
  SEED_DECISION_CARDS as CATALOG_SEED_DECISION_CARDS,
  NO_DECISION_CARDS_USE_CASES as CATALOG_NO_DECISION_CARDS,
} from "../domain/decision-cards-catalog";

const CATEGORY_LABELS = {
  inventory: "Inventory", orders: "Orders", "store-ops": "Store Ops",
  ecom: "E-Commerce", pos: "POS", support: "Support",
  pricing: "Pricing", merchandise: "Merchandise", finance: "Finance", workforce: "Workforce",
};

const SCHEDULE_OPTIONS = [
  { value: "once", label: "Run once", desc: "Execute immediately on activation." },
  { value: "daily", label: "Daily", desc: "Run every day at the configured time." },
  { value: "weekly", label: "Weekly", desc: "Run every week on the configured day." },
];

// Category-aware decision card templates. Each generator receives the draft
// and a small context object; returns [] when no decisions are needed (pure
// analytics/reporting use cases).
const DECISION_CARD_TEMPLATES = {
  inventory: (_draft, ctx) => [
    {
      id: "dc-inv-1",
      title: "Reorder recommendation - SKU-4821 (Sony WH-1000XM5)",
      reasoning: `Stock at 3 days remaining based on ${ctx.sourceLabel}. 14-day forecast shows demand of 115 units. Reorder threshold: 7 days.`,
      action: "Raise PO: 120 units from TechDistrib Ltd. Est. delivery in 7 days.",
      value: 18400,
      supplier: "TechDistrib Ltd",
      status: "pending",
    },
    {
      id: "dc-inv-2",
      title: "Transfer recommendation - SKU-3311 (LG C4 OLED 55\")",
      reasoning: `Store 42 will stock out in 6 hours. Store 17 has 45 units of overstock (18 days on hand). Same region, standard fleet available.`,
      action: "Approve store-to-store transfer: 40 units, Store 17 to Store 42.",
      value: 6800,
      supplier: "Internal transfer",
      status: "pending",
    },
  ],
  orders: (_draft, ctx) => [
    {
      id: "dc-ord-1",
      title: "Fulfillment routing - Order #OR-49812",
      reasoning: `Order includes 4 SKUs across 2 DCs. Split shipment cost is $22; single-DC route from DC-3 is feasible at $8 with 1 extra day of transit.`,
      action: "Consolidate to DC-3, save $14 in split-shipment cost.",
      value: 14,
      supplier: "Fulfillment routing",
      status: "pending",
    },
    {
      id: "dc-ord-2",
      title: "SLA breach - deal MB-24589 (34 days open)",
      reasoning: `Overdue quote past the 14-day SLA policy. Rep group workload is at capacity; escalation to senior rep is warranted.`,
      action: "Escalate to Northeast senior rep and notify sales leadership.",
      value: 185000,
      supplier: "Sales leadership",
      status: "pending",
    },
  ],
  merchandise: (_draft, ctx) => [
    {
      id: "dc-mer-1",
      title: "Markdown recommendation - Category: Outdoor & sports",
      reasoning: `Sell-through at 22% below plan. Competitor pricing dropped 12% last week. Recommend markdown to protect margin on remaining inventory.`,
      action: "Apply 15% markdown across seasonal outdoor SKUs.",
      value: 42000,
      supplier: "Pricing team",
      status: "pending",
    },
    {
      id: "dc-mer-2",
      title: "Assortment change - underperformer SKU-9982",
      reasoning: `Sales below 20% of category average for 8 weeks. No planned promo. Space could be reallocated to top-mover.`,
      action: "Retire SKU-9982; reallocate space to SKU-4831.",
      value: 0,
      supplier: "Assortment planning",
      status: "pending",
    },
  ],
  "store-ops": (_draft, ctx) => [
    {
      id: "dc-ops-1",
      title: "Shift adjustment - Store 42, Saturday",
      reasoning: `Forecast foot traffic +18% vs baseline. Current schedule has 2 associates short vs demand. Overtime approval within budget.`,
      action: "Add 4 associate-hours to Saturday shift, Store 42.",
      value: 92,
      supplier: "Workforce ops",
      status: "pending",
    },
  ],
  ecom: (_draft, ctx) => [
    {
      id: "dc-ecom-1",
      title: "Cart abandonment recovery - segment: high-intent, price-sensitive",
      reasoning: `2,140 sessions abandoned checkout on shipping-cost page. 62% flagged as price-sensitive by ML model. Free-shipping offer historically converts at 8%.`,
      action: "Send free-shipping offer to 2,140 abandoned-cart addresses.",
      value: 17100,
      supplier: "Marketing automation",
      status: "pending",
    },
    {
      id: "dc-ecom-2",
      title: "Site search fix - top zero-result term \"guided knives\"",
      reasoning: `287 zero-result searches this week. Nearest match (SKU-JR-6620, Jade Range charbroiler set) has 6 relevant knives in bundle listing.`,
      action: "Add search synonym mapping \"guided knives\" -> Jade Range charbroiler set.",
      value: 0,
      supplier: "Search team",
      status: "pending",
    },
  ],
  pos: (_draft, ctx) => [
    {
      id: "dc-pos-1",
      title: "Peripheral failure - Store 17, receipt printer #3",
      reasoning: `Printer down for 4 hours. Adjacent registers rerouting traffic; average transaction time up 22%. Field tech can swap within 2 hours.`,
      action: "Dispatch field tech and swap printer today.",
      value: 260,
      supplier: "Field service",
      status: "pending",
    },
  ],
  support: (_draft, ctx) => [
    {
      id: "dc-sup-1",
      title: "Return triage - RET-88214 (damaged, in warranty)",
      reasoning: `Customer reports damage on arrival, item is in warranty window. Photo evidence supports claim. Refurb path recovers 60% of unit cost.`,
      action: "Refund customer, route unit to refurb queue.",
      value: 1400,
      supplier: "Reverse logistics",
      status: "pending",
    },
    {
      id: "dc-sup-2",
      title: "Escalation - contact center wait time trend",
      reasoning: `Average wait time up 28% vs 4-week baseline. Two agents on PTO next week compound the risk. Third-party overflow available at $3.20/call.`,
      action: "Enable third-party overflow queue for next week.",
      value: 1900,
      supplier: "Contact center ops",
      status: "pending",
    },
  ],
  pricing: (_draft, ctx) => [
    {
      id: "dc-prc-1",
      title: "Markdown slate - 24 slow-moving SKUs",
      reasoning: `Sell-through 30% below plan across 24 SKUs. Elasticity model suggests 12-18% markdown maximises margin recovery before end-of-season.`,
      action: "Apply 15% markdown to 24 SKUs, hold list for 10 days.",
      value: 89000,
      supplier: "Pricing team",
      status: "pending",
    },
    {
      id: "dc-prc-2",
      title: "Competitor response - SKU-3311",
      reasoning: `Competitor dropped price by 8% on identical model. Margin cushion allows a 5% match while preserving 22% gross margin.`,
      action: "Match competitor price with 5% price adjustment on SKU-3311.",
      value: 6100,
      supplier: "Pricing team",
      status: "pending",
    },
  ],
  finance: (_draft, ctx) => [
    {
      id: "dc-fin-1",
      title: "Margin protection - Category: Outdoor & Sports",
      reasoning: `Gross margin -1.4pt WoW driven by seasonal exit markdown depth. Vendor co-op negotiation window closes in 5 days.`,
      action: "Approve co-op ask of $28K from Vendor Beta to offset markdown.",
      value: 28000,
      supplier: "Vendor Beta",
      status: "pending",
    },
  ],
  workforce: (_draft, ctx) => [
    {
      id: "dc-wf-1",
      title: "Overtime approval - DC-3, Thursday",
      reasoning: `Forecast inbound volume +34% vs plan. Current headcount short 6 picker-hours. Overtime within budget guardrail.`,
      action: "Approve 8 hours of overtime, DC-3 pick team, Thursday.",
      value: 184,
      supplier: "DC ops",
      status: "pending",
    },
  ],
};

// ==========================================================================
// Per-use-case decision cards (seed use cases). Specific and thematically
// on-point for each catalog entry. Use cases that are pure analysis /
// reporting appear in NO_DECISION_CARDS_USE_CASES below and return [].
// User-created use cases (no catalogId match) fall through to the category
// template above.
// ==========================================================================

const SEED_DECISION_CARDS = {
  // ---- GFI seed use cases ----
  "gfi-uc-04-markdown-optimization": [
    {
      id: "dc-gfi-md-1",
      title: "Wave 1 markdown - Brie de Meaux (5 days to code)",
      reasoning: "260 lbs across 3 DCs, 5 days to code expiry. Elasticity model suggests 15% markdown recovers ~78% of retail vs projected 40% write-off at expiry.",
      action: "Apply 15% markdown for 48 hours on Brie de Meaux across 3 DCs.",
      value: 3240,
      supplier: "Perishable ops",
    },
    {
      id: "dc-gfi-md-2",
      title: "Wave 2 markdown - Prosciutto di Parma (7 days to code)",
      reasoning: "180 lbs at 3 DCs, 7-day window. First-wave markdown at 10% cleared similar SKUs at 82% sell-through last month.",
      action: "Apply 10% markdown for 72 hours on Prosciutto di Parma.",
      value: 2100,
      supplier: "Perishable ops",
    },
  ],
  "gfi-uc-05-labor-optimization": [
    {
      id: "dc-gfi-lab-1",
      title: "Overtime approval - Denver DC, Thursday",
      reasoning: "Forecast inbound volume +34% vs plan (Formaggio Alpino shipment inbound). Pick team short 8 hours vs demand. Overtime within budget guardrail.",
      action: "Approve 8 hours overtime for Denver DC pick team, Thursday.",
      value: 184,
      supplier: "DC ops",
    },
    {
      id: "dc-gfi-lab-2",
      title: "Cross-train recommendation - NJ DC, cheese cage",
      reasoning: "3 cheese-cage certified pickers PTO next week. Cross-training 2 refrigerated-zone pickers takes 2 days but restores coverage.",
      action: "Enrol 2 refrigerated-zone pickers in cheese-cage cross-training.",
      value: 0,
      supplier: "DC training",
    },
  ],
  "gfi-uc-06-multi-temp-routing": [
    {
      id: "dc-gfi-rt-1",
      title: "Route re-plan - Route 14 (Northeast frozen)",
      reasoning: "Route 14 empty return leg tonight overlaps with a proposed Denver -> NJ Brie transfer. Combining saves $180 in fleet cost with no SLA risk.",
      action: "Combine Route 14 with Denver -> NJ Brie transfer.",
      value: 180,
      supplier: "Fleet ops",
    },
    {
      id: "dc-gfi-rt-2",
      title: "Temperature setpoint change - Truck FL-207",
      reasoning: "Truck FL-207 carrying multi-temp load; refrigerated compartment set 2C higher than aged-cheese spec. Adjustment needed before departure.",
      action: "Update compartment setpoint from 5C to 3C on FL-207.",
      value: 0,
      supplier: "Fleet ops",
    },
  ],
  "gfi-uc-08-npi-tracking": [
    {
      id: "dc-gfi-npi-1",
      title: "Launch decision - SKU-NPI-4412 (Aged Manchego 30-month)",
      reasoning: "Pilot in 3 DCs over 6 weeks: 78% sell-through, 4.4/5 dealer NPS. Above launch threshold for national rollout.",
      action: "Roll SKU-NPI-4412 to all 14 DCs.",
      value: 128000,
      supplier: "Merchandising",
    },
    {
      id: "dc-gfi-npi-2",
      title: "Discontinue candidate - SKU-NPI-4302 (Truffle Ricotta)",
      reasoning: "Pilot at 12 weeks: 34% sell-through, high spoilage rate. Below launch threshold.",
      action: "Discontinue SKU-NPI-4302 pilot; return remaining inventory to supplier.",
      value: 0,
      supplier: "Merchandising",
    },
  ],
  "gfi-uc-09-fsma-traceability": [
    {
      id: "dc-gfi-fsma-1",
      title: "Traceability record gap - Lot 42918 (Fjord Salmon)",
      reasoning: "Lot 42918 received at NJ DC without the CTE receipt scan. FSMA 204 mandates KDE capture on receipt; regulator audit trail incomplete.",
      action: "Retro-capture receipt KDE fields from paper log and update WMS record for Lot 42918.",
      value: 0,
      supplier: "Compliance ops",
    },
  ],
  "gfi-uc-10-order-routing": [
    {
      id: "dc-gfi-or-1",
      title: "Fulfillment routing - Order GFI-49812 (Boston restaurant)",
      reasoning: "Order includes 6 SKUs across 3 DCs. Consolidated routing from DC-1 (Boston) is feasible with 1 extra day transit; saves $22 in split-shipment cost.",
      action: "Consolidate to DC-1 (Boston), single-truck delivery.",
      value: 22,
      supplier: "Fulfillment routing",
    },
  ],
  "gfi-uc-11-infra-curated-programs": [
    {
      id: "dc-gfi-infra-1",
      title: "Assortment update - INFRA retailer #48 (Chicago)",
      reasoning: "Retailer #48 has 3 slow-mover SKUs and no coverage on trending specialty cheese. Model recommends 2 SKU swaps to lift category performance.",
      action: "Propose 2-SKU swap (retire SKU-A, add SKU-B, add SKU-C) to retailer #48.",
      value: 4200,
      supplier: "INFRA merchandising",
    },
  ],
  "gfi-uc-13-shrinkage-detection": [
    {
      id: "dc-gfi-shr-1",
      title: "Investigate variance - Lane 7, Atlanta DC",
      reasoning: "Lane 7 has 3.2% shrink vs 1.4% network average, 12-week trend. Temperature log shows two overnight excursions above spec.",
      action: "Schedule refrigeration audit and investigate lane 7 shift handover.",
      value: 6800,
      supplier: "Loss prevention",
    },
    {
      id: "dc-gfi-shr-2",
      title: "Supplier variance - Anatolian Olive Cooperative",
      reasoning: "Receiving weight variance +0.9% above tolerance across 8 recent shipments. Compliance risk if unaddressed; supplier co-op review triggered.",
      action: "Escalate variance investigation with Anatolian Olive Cooperative.",
      value: 3400,
      supplier: "Supplier quality",
    },
    {
      id: "dc-gfi-shr-3",
      title: "Shift handover - Denver DC, Friday PM",
      reasoning: "Miscount variance concentrated in Friday PM handover for 6 consecutive weeks. Retrain the handover script and re-audit.",
      action: "Approve shift-handover retraining for Denver Friday PM crew.",
      value: 0,
      supplier: "DC ops",
    },
  ],
  "gfi-uc-14-dynamic-specialty-pricing": [
    {
      id: "dc-gfi-price-1",
      title: "Price change - Italian Truffle Oil, US Northeast",
      reasoning: "Euro/USD moved 4.2% since last price refresh. Competitor imports repriced last week. Elasticity model supports +3% pass-through while preserving share.",
      action: "Apply +3% price adjustment on Italian Truffle Oil, US Northeast region.",
      value: 4400,
      supplier: "Pricing ops",
    },
  ],
  // ---- Retail seed use cases ----
  "gallery-uc-01": [
    {
      id: "dc-uc01-1",
      title: "Cross-store transfer - SKU-9821 (Sony WH-1000XM5)",
      reasoning: "Store 42 will stock out in 6 hours. Store 17 has 45 units of overstock (18 days on hand). Standard fleet has an outbound truck at 3 PM.",
      action: "Approve transfer: 40 units, Store 17 -> Store 42.",
      value: 8400,
      supplier: "Internal transfer",
    },
    {
      id: "dc-uc01-2",
      title: "Reorder recommendation - SKU-7234 (Samsung 65\" QLED)",
      reasoning: "Regional stock covers 5 days at current sell-through. Seasonal uplift of 35% expected next week. Reorder threshold: 10 days.",
      action: "Raise PO: 80 units from Samsung Direct.",
      value: 11200,
      supplier: "Samsung Direct",
    },
  ],
  "gallery-uc-02": [
    {
      id: "dc-uc02-1",
      title: "Fulfillment routing - Order #OR-49812",
      reasoning: "Order spans 4 SKUs across 2 DCs. Consolidated route from DC-3 saves $14 vs split shipment with 1 extra day of transit.",
      action: "Consolidate to DC-3, single-truck fulfillment.",
      value: 14,
      supplier: "Fulfillment routing",
    },
  ],
  "gallery-uc-07": [
    {
      id: "dc-uc07-1",
      title: "Return disposition - RET-88214 (in-warranty damage)",
      reasoning: "Customer photo evidence supports damage claim. Refurb path recovers 60% of unit cost. Restocking as new not viable.",
      action: "Refund customer; route unit to refurb queue.",
      value: 1400,
      supplier: "Reverse logistics",
    },
    {
      id: "dc-uc07-2",
      title: "Return triage - RET-88232 (buyer's remorse, out of window)",
      reasoning: "Return request 42 days post-purchase; policy is 30 days. Store credit is within manager authority; refund requires escalation.",
      action: "Offer store credit at 90% of purchase price.",
      value: 0,
      supplier: "Customer support",
    },
  ],
  "gallery-uc-08": [
    {
      id: "dc-uc08-1",
      title: "Markdown slate - 24 slow-moving SKUs",
      reasoning: "Sell-through 30% below plan across 24 SKUs. Elasticity suggests 12-18% markdown maximises margin recovery before end-of-season.",
      action: "Apply 15% markdown to 24 SKUs, hold list for 10 days.",
      value: 89000,
      supplier: "Pricing team",
    },
  ],
  "gallery-uc-03": [
    {
      id: "dc-uc03-1",
      title: "Assortment gap - introduce SKU-A2841 in Kitchenware",
      reasoning: "Category traffic +18% WoW but current assortment missing mid-price bakeware. Competitor listing shows unmet demand pattern.",
      action: "Add SKU-A2841 (ceramic bakeware set) to Kitchenware assortment in 24 stores.",
      value: 42000,
      supplier: "Merchandising",
    },
    {
      id: "dc-uc03-2",
      title: "Retire underperformer - SKU-B3352 (Home Fragrance)",
      reasoning: "8 weeks below 25% of category average sell-through. No planned promo. Space could go to top-mover SKU-B4102.",
      action: "Retire SKU-B3352; reallocate shelf to SKU-B4102.",
      value: 0,
      supplier: "Merchandising",
    },
  ],
  "gallery-uc-04": [
    {
      id: "dc-uc04-1",
      title: "Shift adjustment - Store 42 Saturday PM",
      reasoning: "Foot traffic forecast +22% vs baseline for this Saturday PM. Current schedule 3 associates short vs demand. Overtime within budget.",
      action: "Add 12 associate-hours to Saturday PM shift, Store 42.",
      value: 276,
      supplier: "Workforce ops",
    },
    {
      id: "dc-uc04-2",
      title: "Cross-store shift reallocation - Store 17 to 42",
      reasoning: "Store 17 forecast is soft; Store 42 is overloaded. Two associates can flex 4 hours to Store 42 (10-mile distance).",
      action: "Flex 8 associate-hours from Store 17 to Store 42.",
      value: 0,
      supplier: "Workforce ops",
    },
  ],
  "gallery-uc-05": [
    {
      id: "dc-uc05-1",
      title: "Recovery offer - high-intent price-sensitive segment",
      reasoning: "2,140 sessions abandoned at shipping-cost page. ML segment classifier flags 62% as price-sensitive. Free-shipping historically converts at 8%.",
      action: "Send free-shipping recovery offer to 2,140 abandoned-cart addresses.",
      value: 17100,
      supplier: "Marketing automation",
    },
    {
      id: "dc-uc05-2",
      title: "Guest-checkout uplift - reduce required fields",
      reasoning: "Guest checkout drop-off 34% at address form. A/B test shows removing 3 optional fields lifts completion by 6.2%.",
      action: "Deploy simplified guest checkout form to 100% traffic.",
      value: 8400,
      supplier: "E-Com product",
    },
  ],
  "gallery-uc-06": [
    {
      id: "dc-uc06-1",
      title: "Peripheral swap - Store 17, receipt printer #3",
      reasoning: "Printer offline 4 hours; adjacent registers rerouting traffic. Field tech in region with 2-hour swap ETA.",
      action: "Dispatch field tech to swap printer today.",
      value: 260,
      supplier: "Field service",
    },
    {
      id: "dc-uc06-2",
      title: "Preventive maintenance - aging pin-pad fleet (3 stores)",
      reasoning: "3 stores have pin-pads over 40 months old, at 65% of expected fail rate. Preventive swap costs 12% of unplanned incident cost.",
      action: "Schedule preventive pin-pad swap for Store 22, 30, 47.",
      value: 1400,
      supplier: "Field service",
    },
  ],
  "gallery-uc-10": [
    {
      id: "dc-uc10-1",
      title: "Weekend headcount reduction - 6 overstaffed stores",
      reasoning: "Sunday demand at 6 stores consistently 20% below staffed capacity. Reducing 2 associate-hours per store saves without SLA risk.",
      action: "Reduce Sunday PM headcount by 2 hours at 6 flagged stores.",
      value: 2760,
      supplier: "Workforce ops",
    },
  ],
  "gallery-uc-12": [
    {
      id: "dc-uc12-1",
      title: "Markdown slate - 12 slow-moving SKUs",
      reasoning: "12 SKUs at 24% of category average sell-through for 6 weeks. Aging inventory; markdown depth of 20% clears within 3 weeks per elasticity model.",
      action: "Apply 20% markdown to 12 slow-mover SKUs for 3 weeks.",
      value: 34000,
      supplier: "Pricing team",
    },
    {
      id: "dc-uc12-2",
      title: "Transfer slow-mover - SKU-4419 (Bose QC45) to higher-demand region",
      reasoning: "SKU-4419 has 48 days on hand in West region, 4 days in Northeast. Regional transfer takes 3 days and avoids markdown.",
      action: "Transfer 60 units from West to Northeast region.",
      value: 6400,
      supplier: "Internal transfer",
    },
  ],
  "gallery-uc-13": [
    {
      id: "dc-uc13-1",
      title: "Cross-DC rebalance - DC-1 overstock to DC-3 shortage",
      reasoning: "DC-1 has 45 days of on-hand cover on SKU-3311; DC-3 will stock out in 3 days at current velocity. Standard fleet lane available Thursday.",
      action: "Approve 200-unit transfer from DC-1 to DC-3, Thursday dispatch.",
      value: 12800,
      supplier: "Fulfillment ops",
    },
  ],
  "gallery-uc-14": [
    {
      id: "dc-uc14-1",
      title: "Route order to single DC - OR-58291",
      reasoning: "Order spans 5 SKUs across 3 DCs. Consolidated route from DC-3 is feasible at $8 vs $28 split shipment; 1 extra day transit.",
      action: "Consolidate to DC-3 with 1-day extended promise.",
      value: 20,
      supplier: "Fulfillment ops",
    },
    {
      id: "dc-uc14-2",
      title: "Update allocation rule - SKU-9821 default routing",
      reasoning: "SKU-9821 has generated 34 split shipments in 30 days at $18 avg surcharge. Changing default routing to nearest-DC-with-stock cuts split rate 60%.",
      action: "Update SKU-9821 fulfillment rule to nearest-DC-with-stock.",
      value: 372,
      supplier: "Fulfillment ops",
    },
  ],
  "gallery-uc-15": [
    {
      id: "dc-uc15-1",
      title: "Escalate breached order - OR-48912 (SLA +36 hrs)",
      reasoning: "Order past promised delivery by 36 hours. Root cause: warehouse pick delay. Expedited shipping recovers SLA and preserves customer relationship.",
      action: "Expedite OR-48912 with next-day shipping.",
      value: 42,
      supplier: "Fulfillment ops",
    },
    {
      id: "dc-uc15-2",
      title: "Process fix - retrain DC-3 pick team on high-value SKUs",
      reasoning: "70% of SLA breaches trace back to DC-3 pick delays on high-value SKUs. Root cause analysis suggests handoff protocol issue.",
      action: "Retrain DC-3 pick team on high-value handoff protocol.",
      value: 0,
      supplier: "DC ops",
    },
  ],
  "gallery-uc-18": [
    {
      id: "dc-uc18-1",
      title: "Roll out NPI - SKU-NP-8842 (Smart Home Hub)",
      reasoning: "Pilot in 24 stores over 8 weeks: 82% sell-through, 4.3/5 NPS. Above launch threshold for national rollout.",
      action: "Roll SKU-NP-8842 to all 340 stores.",
      value: 224000,
      supplier: "Merchandising",
    },
    {
      id: "dc-uc18-2",
      title: "Discontinue NPI - SKU-NP-7702 (LED Desk Lamp)",
      reasoning: "Pilot at 12 weeks: 28% sell-through, 3.1/5 NPS. Below launch threshold.",
      action: "Discontinue SKU-NP-7702; return remaining inventory to vendor.",
      value: 0,
      supplier: "Merchandising",
    },
  ],
  "gallery-uc-19": [
    {
      id: "dc-uc19-1",
      title: "Wave 1 markdown - Outdoor & Sports seasonal exit",
      reasoning: "Season ends in 6 weeks. 14 SKUs at 40+ days on hand. Wave 1 at 10% clears roughly 45% of remaining units per historical cadence.",
      action: "Start Wave 1 markdown at 10% depth for 14 seasonal SKUs.",
      value: 32000,
      supplier: "Pricing team",
    },
    {
      id: "dc-uc19-2",
      title: "Delay Wave 2 by 1 week - Beach & Water gear",
      reasoning: "Sell-through on Wave 1 running 12% above forecast; deferring Wave 2 preserves margin without inventory risk.",
      action: "Push Wave 2 markdown from July 30 to August 6.",
      value: 5800,
      supplier: "Pricing team",
    },
  ],
  "gallery-uc-21": [
    {
      id: "dc-uc21-1",
      title: "Investigate variance - Cashier ID 42891 at Store 33",
      reasoning: "Recurring positive variance ($8-$14) across 6 weeks. Not tied to shift, register, or product mix. Warrants investigation but not immediate escalation.",
      action: "Schedule loss-prevention review with Cashier ID 42891.",
      value: 320,
      supplier: "Loss prevention",
    },
    {
      id: "dc-uc21-2",
      title: "POS handoff retraining - Store 42 evening shift",
      reasoning: "Store 42 evening shift shows consistent $12 avg variance at handoff. Retraining resolves in 78% of similar cases.",
      action: "Schedule retrain session for Store 42 evening shift.",
      value: 0,
      supplier: "Store ops",
    },
  ],
  "gallery-uc-22": [
    {
      id: "dc-uc22-1",
      title: "Coach store manager - Store 09, morning routine gap",
      reasoning: "Store 09 opening checklist compliance at 68% vs 92% network average over 4 weeks. Coaching resolves similar cases in 3 weeks.",
      action: "Schedule 1:1 coaching for Store 09 manager on opening routine.",
      value: 0,
      supplier: "Store ops",
    },
  ],
  "gallery-uc-23": [
    {
      id: "dc-uc23-1",
      title: "Synonym mapping - top zero-result term \"guided knives\"",
      reasoning: "287 zero-result searches this week for \"guided knives\". Nearest match SKU-JR-6620 has 6 relevant knives bundled.",
      action: "Add synonym \"guided knives\" -> SKU-JR-6620 bundle.",
      value: 0,
      supplier: "Search team",
    },
    {
      id: "dc-uc23-2",
      title: "Rebuild search index - Home Textiles category",
      reasoning: "Home Textiles CTR 3.1% vs 8.4% site average. Search relevance audit suggests stale index; rebuild takes 2 hours.",
      action: "Rebuild search index for Home Textiles category.",
      value: 0,
      supplier: "Search team",
    },
  ],
  "gallery-uc-24": [
    {
      id: "dc-uc24-1",
      title: "Fail over to backup gateway - Elavon during 6-8 PM window",
      reasoning: "Primary processor error rate 4.2% during 6-8 PM window last 4 nights. Backup gateway response time within 200ms of primary.",
      action: "Route 6-8 PM traffic to backup gateway for 7 days.",
      value: 5200,
      supplier: "E-Com ops",
    },
    {
      id: "dc-uc24-2",
      title: "Escalate to processor - recurring error code 5011",
      reasoning: "Error code 5011 (velocity check) triggering 128 legitimate declines/day. Processor review needed to tune threshold.",
      action: "Open ticket with primary processor to review error code 5011.",
      value: 0,
      supplier: "Payments",
    },
  ],
  "gallery-uc-25": [
    {
      id: "dc-uc25-1",
      title: "Deploy hotfix - mobile checkout regression",
      reasoning: "Mobile checkout completion dropped 6pt after v4.2 release. LogRocket traces implicate the new address auto-fill component.",
      action: "Deploy hotfix disabling address auto-fill on mobile.",
      value: 12400,
      supplier: "E-Com product",
    },
    {
      id: "dc-uc25-2",
      title: "A/B test - simplified mobile checkout form",
      reasoning: "Mobile completion trails desktop by 14pt on the same funnel. Simplified form pilot in 5% of traffic showed 8pt uplift.",
      action: "Roll simplified mobile checkout to 50% of traffic.",
      value: 6200,
      supplier: "E-Com product",
    },
  ],
  "gallery-uc-26": [
    {
      id: "dc-uc26-1",
      title: "Replace peripheral - Store 22 (4 pin-pad failures in 30 days)",
      reasoning: "Store 22 pin-pad #2 has 4 failures in 30 days vs network baseline of 0.4. Age 46 months, well past expected replacement window.",
      action: "Replace pin-pad #2 at Store 22 this week.",
      value: 480,
      supplier: "Field service",
    },
  ],
  "gallery-uc-27": [
    {
      id: "dc-uc27-1",
      title: "Enable edge cache - loyalty lookup service",
      reasoning: "Loyalty lookup p95 latency at 1,240ms vs 400ms target. Edge cache reduces average by 70% based on similar deployments.",
      action: "Enable edge cache in front of loyalty lookup service.",
      value: 0,
      supplier: "Platform engineering",
    },
  ],
  "gallery-uc-28": [
    {
      id: "dc-uc28-1",
      title: "Resolve promo conflict - Save20 + BOGO stack",
      reasoning: "Save20 and BOGO promos both applied to 342 transactions last week, causing $18 avg margin leak. Rule update prevents stacking.",
      action: "Add exclusion rule preventing Save20 + BOGO stack.",
      value: 6156,
      supplier: "Pricing ops",
    },
  ],
  "gallery-uc-29": [
    {
      id: "dc-uc29-1",
      title: "Enable overflow queue - Tuesday-Thursday peak window",
      reasoning: "Avg wait time up 28% during peak. Third-party overflow queue at $3.20/call vs $12 hire cost, immediately deployable.",
      action: "Enable third-party overflow queue for Tue-Thu peak hours.",
      value: 1900,
      supplier: "Contact center ops",
    },
    {
      id: "dc-uc29-2",
      title: "Cross-train - move 3 tier-2 agents to overflow tier-1",
      reasoning: "Tier-2 utilization at 62%; tier-1 backlog growing. Cross-training 3 agents temporarily flexes coverage without new hires.",
      action: "Cross-train 3 tier-2 agents on tier-1 overflow.",
      value: 0,
      supplier: "Contact center ops",
    },
  ],
  "gallery-uc-30": [
    {
      id: "dc-uc30-1",
      title: "Update knowledge base - top recurring escalation topic",
      reasoning: "\"Order not delivered\" accounts for 34% of tier-2 escalations, up from 22%. Updated KB article and macro resolve at tier-1 in 78% of test cases.",
      action: "Publish updated KB article on \"order not delivered\" resolution.",
      value: 0,
      supplier: "Support ops",
    },
    {
      id: "dc-uc30-2",
      title: "Retrain tier-1 agents - refund policy escalation category",
      reasoning: "Refund-policy escalations up 42% WoW. Tier-1 agents are escalating cases within their authority per audit sample.",
      action: "Retrain tier-1 team on refund policy authority.",
      value: 0,
      supplier: "Support ops",
    },
  ],

  // ---- GFI seed use cases - previously fell to category fallback ----
  "gfi-uc-02-inter-dc-rebalance": [
    {
      id: "dc-gfi-reb-1",
      title: "Approve transfer - Denver DC to NJ DC (Brie de Meaux)",
      reasoning: "Denver has 260 lbs of Brie at 5 days to code; NJ will stock out in 3 days. Multi-temp fleet has empty return leg tonight.",
      action: "Transfer 240 lbs Brie de Meaux, Denver -> NJ, dispatch 6 PM.",
      value: 4320,
      supplier: "Fleet ops",
    },
    {
      id: "dc-gfi-reb-2",
      title: "Approve transfer - Seattle DC to Atlanta DC (Aged Cheddar)",
      reasoning: "Seattle has 40 lbs of aged cheddar at 8 days to code; Atlanta at 4 days. Standard fleet available Wednesday on this lane.",
      action: "Transfer 32 lbs Aged Cheddar, Seattle -> Atlanta, Wednesday.",
      value: 710,
      supplier: "Fleet ops",
    },
  ],
  "gfi-uc-03-supplier-risk": [
    {
      id: "dc-gfi-sup-1",
      title: "Pre-approve alternate supplier - Fjord Salmon backup",
      reasoning: "Fjord Salmon impacted by Bergen port storm. Norwegian Coast Fisheries has capacity and previously supplied at 5% price premium.",
      action: "Pre-approve Norwegian Coast Fisheries as alt supplier for smoked salmon line.",
      value: 318000,
      supplier: "Norwegian Coast Fisheries",
    },
    {
      id: "dc-gfi-sup-2",
      title: "Hold shipment - Anatolian Olive Cooperative (FSMA risk)",
      reasoning: "FSMA 204 compliance risk flagged on Anatolian small-producer network. Holding incoming shipment prevents traceability audit gap.",
      action: "Hold incoming Anatolian Olive shipment pending compliance review.",
      value: 186000,
      supplier: "Compliance ops",
    },
  ],
};

// Use cases that are pure analytics / reporting - no decisions the human
// needs to approve. Step 9 shows a "Delivered" state for these instead of
// the decision card queue.
const NO_DECISION_CARDS_USE_CASES = new Set([
  // GFI
  "gfi-uc-01-perishable-forecast",   // forecasting: advisory only
  "gfi-uc-07-vendor-scorecards",     // scorecards: analysis only
  "gfi-uc-12-customer-demand-intelligence", // insights only
  "gfi-uc-15-competitive-positioning", // strategic analysis
  // Retail default
  "gallery-uc-09",  // Gross Margin Analysis
  "gallery-uc-11",  // Demand Forecast Accuracy
  "gallery-uc-16",  // BOPIS Fulfillment Reliability (analysis)
  "gallery-uc-17",  // Planogram Compliance (analysis)
  "gallery-uc-20",  // Task Completion Rates (analysis)
]);

// Decision cards tailored to the specific use case first, then falling back to
// category templates. Returns [] when the use case is pure analysis / reporting
// and has no decisions the human needs to approve. Every card is stamped with
// the source use case (id + title) so the Decision Feed page can show context.
function buildDecisionCards(draft, newEntry = null) {
  const catalogId = draft.builderSession?.reopenedCatalogId || newEntry?.id || null;
  const useCaseTitle = draft.step1?.title?.trim() || newEntry?.title || "Use case";
  const stamp = (cards) =>
    cards.map((c) => ({
      status: "pending",
      ...c,
      useCaseId: catalogId,
      useCaseTitle,
    }));

  // Explicit analysis-only list -> no cards. Checks the shared catalog
  // constant first (contains media entries) then the local retail/GFI set.
  if (catalogId && (CATALOG_NO_DECISION_CARDS.has(catalogId) || NO_DECISION_CARDS_USE_CASES.has(catalogId))) {
    return [];
  }

  // Per-use-case override (seed use cases with tailored cards). Shared
  // catalog wins so media hero use cases (M-series) surface their cards
  // even if this file's local map hasn't been updated.
  if (catalogId && CATALOG_SEED_DECISION_CARDS[catalogId]) {
    return stamp(CATALOG_SEED_DECISION_CARDS[catalogId]);
  }
  if (catalogId && SEED_DECISION_CARDS[catalogId]) {
    return stamp(SEED_DECISION_CARDS[catalogId]);
  }

  // Safety net for the Media profile: never fall through to the retail
  // category-template fallback below (which produces "Reorder recommendation
  // - SKU-4821" style cards on Inventory / Orders / Merchandise). If a media
  // catalog id ends up here it means the shared catalog is missing coverage,
  // and we'd rather ship an empty decision queue than surface a retail SKU
  // card on a media use case.
  if (typeof catalogId === "string" && catalogId.startsWith("media-")) {
    return [];
  }

  // Category-level fallback for user-created use cases.
  const category = String(draft.step1?.category || "").toLowerCase();
  const sources = (draft.step3?.confirmedSources ?? []).filter((s) => s.enabled);
  const ctx = {
    sourceLabel: sources[0]?.name || "the connected data source",
    title: draft.step1?.title || "this use case",
    question: draft.step2?.primaryQuestion || "",
  };
  const template = DECISION_CARD_TEMPLATES[category];
  if (!template) return [];
  const cards = template(draft, ctx) || [];
  return stamp(cards);
}

export default function UCBuilderStep10Completion() {
  const navigate = useNavigate();
  const { draft, resetDraft } = useUseCaseDraft();
  const { catalog, setData, addFromDraft, updateFromDraft, supabaseEnabled } = useUseCaseCatalog();
  const { profileId } = useDemoProfile();

  const title = draft.step1.title.trim() || "Untitled use case";
  const category = CATEGORY_LABELS[draft.step1.category] || draft.step1.category || "-";
  const autonomy = draft.step1.autonomyLevel === "copilot" ? "Co-Pilot" : "Autopilot";
  const qCount = (draft.step2.primaryQuestion.trim() ? 1 : 0) + draft.step2.supportingQuestions.length;
  const sourceCount = (draft.step3?.confirmedSources ?? []).filter((s) => s.enabled).length;
  const knowledgeCount = (draft.step3?.confirmedKnowledge ?? []).filter((k) => k.enabled).length;
  const taskCount = draft.step5?.tasks?.length ?? 6;
  const schedule = draft.step8?.schedule ?? "once";
  const guardrailAmount = draft.step8?.guardrailAmount ?? "5000";

  // Pre-flight warnings carry-forward
  const confirmedSources = (draft.step3?.confirmedSources ?? []).filter((s) => s.enabled);
  const staleSource = confirmedSources.find((s) => s.syncAge && (s.syncAge.includes("day") || s.syncAge.includes("week")));

  const setSchedule = (val) =>
    navigate(".", { state: null }); // no-op - handled via draft

  const handleActivate = () => {
    const { catalog: nextCatalog, newEntry } = appendCatalogFromDraft(draft, catalog, profileId);
    // Route the Supabase write by reopen type so we don't duplicate rows
    // when relaunching a use case:
    //   - reopen of a Supabase-backed use case  → UPDATE the sb: row
    //   - reopen of a seed use case             → skip Supabase entirely
    //                                             (seed activation is local)
    //   - brand new                             → INSERT a new sb: row
    if (supabaseEnabled) {
      const reopenedId = draft?.builderSession?.reopenedCatalogId || null;
      const isReopenedSupabase = typeof reopenedId === "string" && reopenedId.startsWith("sb:");
      const isReopenedSeed = reopenedId && !isReopenedSupabase;
      if (isReopenedSupabase) {
        void updateFromDraft?.(reopenedId.slice(3), draft);
      } else if (isReopenedSeed) {
        // no-op: seed use cases live in code; activation is local UI state
      } else {
        void addFromDraft?.(draft);
      }
    }
    const payload = buildLaunchSummaryPayload(draft, newEntry);
    // Augment payload with question + category for contextual Step 9 stats
    const augmented = {
      ...payload,
      primaryQuestion: draft.step2?.primaryQuestion?.trim() ?? "",
      category: draft.step1?.category ?? "",
      taskCount: draft.step5?.tasks?.length ?? 5,
      title: title,
      id: newEntry?.id,
      autonomy: draft.step1?.autonomyLevel === "copilot" ? "copilot" : "autopilot",
    };
    try {
      // Launch summary is short-lived (only used by Step 9 on immediate arrival),
      // so keep it in sessionStorage.
      sessionStorage.setItem(USE_CASE_LAUNCH_SUMMARY_KEY, JSON.stringify(augmented));
      if (draft.step1.autonomyLevel === "copilot") {
        // Decision cards persist across sessions in localStorage. When a new
        // use case is launched, merge its cards with any existing ones from
        // other use cases (dropping only prior cards for the SAME use case so
        // a re-launch replaces rather than duplicates).
        const newCards = buildDecisionCards(draft, newEntry);
        try {
          const raw = localStorage.getItem(COPILOT_DECISIONS_KEY);
          const existing = raw ? JSON.parse(raw) : [];
          const currentUcId = newCards[0]?.useCaseId ?? null;
          const kept = existing.filter((c) => !currentUcId || c.useCaseId !== currentUcId);
          const merged = [...kept, ...newCards];
          localStorage.setItem(COPILOT_DECISIONS_KEY, JSON.stringify(merged));
        } catch {
          localStorage.setItem(COPILOT_DECISIONS_KEY, JSON.stringify(newCards));
        }
      }
    } catch (err) {
      console.error(err);
    }
    const launchedTitle = (newEntry?.title || "").trim().toLowerCase();
    if (supabaseEnabled) {
      // Seed reopens: appendCatalogFromDraft updated the seed entry in place
      // (same id), so use nextCatalog to keep that update. Brand-new + sb:
      // reopens: appendCatalogFromDraft appended a new user- entry — drop it
      // so we don't render both the local copy and the Supabase overlay.
      const reopenedId = draft?.builderSession?.reopenedCatalogId || null;
      const isReopenedSeed = reopenedId && !reopenedId.startsWith("sb:");
      const base = isReopenedSeed ? nextCatalog.entries : (catalog.entries || []);
      const cleaned = launchedTitle
        ? base.filter(
            (e) =>
              !(
                e.source === "user" &&
                e.status === "draft" &&
                (e.title || "").trim().toLowerCase() === launchedTitle
              )
          )
        : base;
      setData({ ...catalog, entries: cleaned });
    } else {
      const dedupedEntries = launchedTitle
        ? nextCatalog.entries.filter(
            (e) =>
              !(
                e.source === "user" &&
                e.status === "draft" &&
                e.id !== newEntry.id &&
                (e.title || "").trim().toLowerCase() === launchedTitle
              )
          )
        : nextCatalog.entries;
      setData({ ...nextCatalog, entries: dedupedEntries });
    }
    resetDraft();
    navigate("/use-cases/builder/step-9");
  };

  return (
    <UCBuilderLayout
      currentStep={8}
      title="Activation"
      subtitle="Review your full configuration, set a schedule, and activate."
      backPath="/use-cases/builder/step-7"
    >
      <div className="max-w-2xl space-y-6">
        {/* Amber carry-forward */}
        {staleSource && (
          <div className="flex items-start gap-3 rounded-xl border border-[#fde68a] bg-[#fffaeb] px-4 py-3">
            <AlertTriangle size={16} className="mt-0.5 shrink-0 text-[#b54708]" />
            <p className="text-sm text-[#92400e]">
              <span className="font-semibold">Warning carried forward:</span> {staleSource.name} was last synced {staleSource.syncAge}. You acknowledged this in the Pre-flight Check.
            </p>
          </div>
        )}

        {/* Summary card */}
        <Card>
          <h3 className="mb-4 text-sm font-semibold text-text-primary">Configuration summary</h3>
          <div className="divide-y divide-[#f2f2f5]">
            {[
              { icon: Tag, label: "Use case", value: title },
              { icon: Tag, label: "Business area", value: category },
              { icon: HelpCircle, label: "Questions", value: `${qCount} configured` },
              { icon: Database, label: "Data sources", value: `${sourceCount} sources, ${knowledgeCount} knowledge bases` },
              { icon: Bot, label: "Tasks", value: `${taskCount} tasks across agents` },
              { icon: Bot, label: "Autonomy", value: autonomy },
            ].map(({ icon: Icon, label, value }) => (
              <div key={label} className="flex items-center justify-between gap-4 py-2.5">
                <div className="flex items-center gap-2 text-sm text-text-secondary">
                  <Icon size={15} />
                  {label}
                </div>
                <p className="text-sm font-medium text-text-primary">{value}</p>
              </div>
            ))}
          </div>
        </Card>

        {/* Schedule */}
        <Card>
          <div className="mb-4 flex items-center gap-2">
            <Calendar size={16} className="text-primary" />
            <h3 className="text-sm font-semibold text-text-primary">Schedule</h3>
          </div>
          <div className="space-y-2">
            {SCHEDULE_OPTIONS.map((opt) => {
              const sel = schedule === opt.value;
              return (
                <label
                  key={opt.value}
                  className={`flex cursor-pointer items-start gap-3 rounded-lg border px-3 py-2.5 transition ${sel ? "border-primary bg-primary-light dark:bg-primary/15" : "border-border hover:border-border-light"}`}
                >
                  <input
                    type="radio"
                    name="schedule"
                    value={opt.value}
                    checked={sel}
                    onChange={() =>
                      // eslint-disable-next-line no-undef
                      useUseCaseDraft && undefined
                    }
                    className="mt-0.5 h-4 w-4 border-border-light text-primary focus:ring-[#6941c6]"
                    readOnly
                  />
                  <div>
                    <p className="text-sm font-medium text-text-primary">{opt.label}</p>
                    <p className="text-xs text-text-secondary">{opt.desc}</p>
                  </div>
                </label>
              );
            })}
          </div>
        </Card>

        {/* Autonomy guardrails */}
        <Card>
          <div className="mb-4 flex items-center gap-2">
            <Shield size={16} className="text-primary" />
            <h3 className="text-sm font-semibold text-text-primary">Autonomy and guardrails</h3>
          </div>

          {draft.step1.autonomyLevel === "copilot" ? (
            <div className="space-y-3">
              <div className="rounded-lg border border-primary/30 bg-primary-light px-4 py-3">
                <p className="text-sm font-semibold text-primary">Co-Pilot mode active</p>
                <p className="mt-1 text-sm text-text-secondary">
                  No actions will be taken without your approval. The AI will prepare Decision Cards and route them to the Co-Pilot Decision Feed for review.
                </p>
              </div>
              <div className="flex items-center gap-3 rounded-lg border border-border bg-surface px-4 py-3">
                <Users size={15} className="shrink-0 text-text-muted" />
                <div>
                  <p className="text-sm font-medium text-text-primary">Approver</p>
                  <p className="text-xs text-text-secondary">Category Manager (your current role)</p>
                </div>
              </div>
            </div>
          ) : (
            <div className="space-y-3">
              <div className="rounded-lg border border-primary/35 bg-primary-light dark:bg-primary/15 px-4 py-3">
                <p className="text-sm font-semibold text-primary">Autopilot mode active</p>
                <p className="mt-1 text-sm text-text-secondary">
                  The AI will act autonomously within the guardrails below. All actions are logged in Monitor.
                </p>
              </div>
              <div className="flex items-center gap-3 rounded-lg border border-border bg-surface px-4 py-3">
                <DollarSign size={15} className="shrink-0 text-text-muted" />
                <div className="flex-1">
                  <p className="text-sm font-medium text-text-primary">Max spend per action</p>
                  <p className="text-xs text-text-secondary">Human approval required above this threshold</p>
                </div>
                <div className="flex items-center gap-1">
                  <span className="text-sm text-text-secondary">$</span>
                  <input
                    type="number"
                    value={guardrailAmount}
                    onChange={() => {}}
                    className="w-20 rounded-lg border border-border px-2 py-1 text-sm text-text-primary focus:border-primary focus:outline-none focus:ring-1 focus:ring-[#6941c6]"
                    readOnly
                  />
                </div>
              </div>
            </div>
          )}
        </Card>

        {/* Action buttons */}
        <div className="flex items-center justify-end gap-3 border-t border-border pt-4">
          <button
            type="button"
            className="rounded-lg border border-border-light px-4 py-2.5 text-sm font-semibold text-text-tertiary hover:bg-surface-secondary"
            onClick={() => {
              const { catalog: nextCatalog } = saveDraftEntryToCatalog(draft, catalog, "/use-cases/builder/step-8", profileId);
              setData(nextCatalog);
              navigate("/use-cases");
            }}
          >
            Save as Draft
          </button>
          <button
            type="button"
            onClick={handleActivate}
            className="flex items-center gap-2 rounded-lg bg-primary px-5 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-primary-hover"
          >
            <Rocket size={15} />
            Activate Use Case
          </button>
        </div>
      </div>
    </UCBuilderLayout>
  );
}
