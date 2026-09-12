import { readLocalJson, writeLocalJson } from "./local-json";
import { DEFAULT_DEMO_PROFILE_ID, GOURMET_FOODS_PROFILE_ID, MEDIA_PROFILE_ID, MIDDLEBY_PROFILE_ID, normalizeDemoProfileId } from "../domain/demo-profiles";
import { MIDDLEBY_PROCESSES_SEED } from "../domain/middleby-operational-seeds";
import { MEDIA_PROCESSES_SEED } from "../domain/media-operational-seeds";
import { BUSINESS_AREAS } from "../domain/business-areas";
import { getBusinessAreasSync } from "./business-areas";
import { storageKeysForProfile } from "./keys";

/** Bump when the seed taxonomy changes incompatibly (forces re-seed on next load). */
// Bumped 12 → 13 (2026-08-14) after audit found media-pub-2 (Launch
// War-Room Coordination) still pointing at the retired "orders" business
// area — should be "merchandise" (Content) after the taxonomy consolidation.
export const PROCESSES_SEED_VERSION = 13;

/** Lucide icon name → resolved in UI (serializable in JSON). */
export const PROCESS_ICON_KEYS = ["PackageCheck", "ClipboardCheck", "Repeat", "Tag", "Store"];

/**
 * Turn comma-separated process ids into user-facing names using the current process list.
 * Unknown ids fall back to the original token.
 */
export function formatProcessFieldForUi(value, processes) {
 const list = Array.isArray(processes) ? processes : [];
 const map = Object.fromEntries(
 list
 .filter((p) => p && p.id)
 .map((p) => {
 const id = String(p.id);
 const name = String(p.name || "").trim();
 return [id, name || id];
 })
 );
 const raw = String(value ?? "").trim();
 if (!raw) return "";
 return raw
 .split(",")
 .map((s) => s.trim())
 .filter(Boolean)
 .map((token) => map[token] || token)
 .join(", ");
}

/** Default retail demo processes - granular, executable workflow steps per business area. */
export const PROCESSES_SEED = [
 // ── Inventory ───────────────────────────────────────────────────────────
 {
 id: "inv-1",
 name: "Demand Forecasting Run",
 businessAreaId: "inventory",
 subjectArea: "Stock Health",
 iconKey: "PackageCheck",
 description: "Generate SKU-level demand forecasts using sales history, seasonality, and external signals to inform replenishment.",
 impact: "Reduce forecast error 20-35%; cut overstock and stockouts.",
 seed: true,
 },
 {
 id: "inv-2",
 name: "Purchase Order Generation",
 businessAreaId: "inventory",
 subjectArea: "Replenishment",
 iconKey: "PackageCheck",
 description: "Create and route purchase orders to suppliers based on reorder points, lead times, and approved vendor lists.",
 impact: "Automate 80%+ of routine POs; reduce manual buying time.",
 seed: true,
 },
 {
 id: "inv-3",
 name: "Goods Receiving & Putaway",
 businessAreaId: "inventory",
 subjectArea: "Stock Health",
 iconKey: "PackageCheck",
 description: "Validate inbound shipments against POs, flag discrepancies, and assign putaway locations by velocity and storage rules.",
 impact: "Cut receiving errors 30-50%; faster shelf replenishment.",
 seed: true,
 },
 {
 id: "inv-4",
 name: "Stock Count & Reconciliation",
 businessAreaId: "inventory",
 subjectArea: "Shrinkage & Loss",
 iconKey: "PackageCheck",
 description: "Run cycle counts and full stocktakes; reconcile system vs physical inventory and investigate variances.",
 impact: "Improve inventory accuracy to 98%+; reduce shrinkage.",
 seed: true,
 },
 {
 id: "inv-5",
 name: "Safety Stock Review",
 businessAreaId: "inventory",
 subjectArea: "Replenishment",
 iconKey: "PackageCheck",
 description: "Recalibrate safety stock parameters by SKU and location based on demand variability and supplier lead-time risk.",
 impact: "Right-size safety buffers; cut excess stock 15-25%.",
 seed: true,
 },
 {
 id: "inv-6",
 name: "Inventory Revenue & Exposure Review",
 businessAreaId: "inventory",
 subjectArea: "Revenue",
 iconKey: "PackageCheck",
 description:
 "Connect stock position to lost sales and markdown exposure; quantify revenue at risk from stocking levels by region and category.",
 impact: "Recover 2-5 pts revenue previously lost to stockouts and reactive markdowns.",
 seed: true,
 },
 {
 id: "inv-7",
 name: "Supplier OTIF & Fill Rate Review",
 businessAreaId: "inventory",
 subjectArea: "Supplier Management",
 iconKey: "PackageCheck",
 description:
 "Score suppliers on on-time-in-full, lead-time variance, and case-fill accuracy; prioritize containment when service slips.",
 impact: "Improve inbound reliability 15-25%; cut expedite spend.",
 seed: true,
 },

 // ── Orders ──────────────────────────────────────────────────────────────
 {
 id: "ord-1",
 name: "Order Capture & Validation",
 businessAreaId: "orders",
 subjectArea: "Order Fulfillment",
 iconKey: "ClipboardCheck",
 description: "Ingest orders from all channels, apply fraud checks, validate against inventory availability, and confirm with customers.",
 impact: "Reduce fraud losses; cut order-entry errors 40-60%.",
 seed: true,
 },
 {
 id: "ord-2",
 name: "Fulfillment Routing",
 businessAreaId: "orders",
 subjectArea: "Order Fulfillment",
 iconKey: "ClipboardCheck",
 description: "Assign each order to the optimal fulfillment node - store, DC, or drop-ship - based on cost, SLA, and availability.",
 impact: "Cut fulfillment cost 15-25%; reduce split shipments 30%.",
 seed: true,
 },
 {
 id: "ord-3",
 name: "Delivery & Carrier Management",
 businessAreaId: "orders",
 subjectArea: "Delivery Management",
 iconKey: "ClipboardCheck",
 description: "Select carriers, generate manifests, track in-transit shipments, and manage delivery exception resolution.",
 impact: "Improve on-time delivery; reduce carrier cost 10-15%.",
 seed: true,
 },
 {
 id: "ord-4",
 name: "Order Exception Handling",
 businessAreaId: "orders",
 subjectArea: "Exceptions",
 iconKey: "ClipboardCheck",
 description: "Triage and resolve order exceptions - holds, cancellations, substitutions, and SLA breaches - within agreed response windows.",
 impact: "Reduce exception resolution time 50%; improve customer NPS.",
 seed: true,
 },
 {
 id: "ord-5",
 name: "Payment Fraud & Chargeback Review",
 businessAreaId: "orders",
 subjectArea: "Fraud Detection",
 iconKey: "ClipboardCheck",
 description:
 "Analyze fraud scores, chargeback reason codes, and high-risk payment methods; tune rules without hurting good customers.",
 impact: "Cut fraud losses 20-40%; reduce false declines on high-value baskets.",
 seed: true,
 },

 // ── Merchandise ─────────────────────────────────────────────────────────
 {
 id: "merch-1",
 name: "Range & Assortment Review",
 businessAreaId: "merchandise",
 subjectArea: "Assortment Planning",
 iconKey: "ClipboardCheck",
 description: "Evaluate range productivity by category, identify assortment gaps vs demand and competitor range, and propose range changes.",
 impact: "Improve category productivity; reduce dead SKU count.",
 seed: true,
 },
 {
 id: "merch-2",
 name: "New Product Introduction",
 businessAreaId: "merchandise",
 subjectArea: "Category Performance",
 iconKey: "ClipboardCheck",
 description: "Onboard new SKUs end-to-end: supplier setup, ranging decisions, initial buy, and first-8-week sell-through tracking.",
 impact: "Faster NPI cycle; fewer failed launches.",
 seed: true,
 },
 {
 id: "merch-3",
 name: "Planogram Compliance Check",
 businessAreaId: "merchandise",
 subjectArea: "Planogram Compliance",
 iconKey: "ClipboardCheck",
 description: "Audit shelf execution against headquarters planograms; flag compliance gaps by store and category.",
 impact: "Improve on-shelf availability; enforce promotional compliance.",
 seed: true,
 },
 {
 id: "merch-4",
 name: "Vendor Performance Review",
 businessAreaId: "merchandise",
 subjectArea: "Vendor Management",
 iconKey: "ClipboardCheck",
 description: "Score vendors on fill rate, lead-time adherence, quality, and case accuracy; feed into negotiation and delisting decisions.",
 impact: "Strengthen vendor accountability; improve supply reliability.",
 seed: true,
 },

 // ── Pricing ─────────────────────────────────────────────────────────────
 {
 id: "prc-1",
 name: "Price Change Execution",
 businessAreaId: "pricing",
 subjectArea: "Profitability",
 iconKey: "Tag",
 description: "Approve and push price changes across channels; validate against margin guardrails and MAP policies before activation.",
 impact: "Faster price agility; prevent margin-breaking exceptions.",
 seed: true,
 },
 {
 id: "prc-2",
 name: "Markdown & Clearance Planning",
 businessAreaId: "pricing",
 subjectArea: "Markdown Optimisation",
 iconKey: "Tag",
 description: "Identify slow-moving and seasonal stock; recommend markdown timing, depth, and exit strategy to recover margin.",
 impact: "Improve sell-through 15-25%; cut clearance write-offs.",
 seed: true,
 },
 {
 id: "prc-3",
 name: "Promotional Setup & Validation",
 businessAreaId: "pricing",
 subjectArea: "Promotions",
 iconKey: "Tag",
 description: "Configure promotion rules, validate stacking logic, and check for conflicts before activation across POS and online.",
 impact: "Eliminate promo conflicts; protect intended promotional margin.",
 seed: true,
 },
 {
 id: "prc-4",
 name: "Competitive Price Index Review",
 businessAreaId: "pricing",
 subjectArea: "Competitor Pricing",
 iconKey: "Tag",
 description:
 "Benchmark key-value SKUs against competitors and marketplaces; flag unhealthy price gaps and MAP drift.",
 impact: "Protect price perception while defending margin on differentiated SKUs.",
 seed: true,
 },
 {
 id: "prc-5",
 name: "Sell-Through Velocity Review",
 businessAreaId: "pricing",
 subjectArea: "Sell-Through",
 iconKey: "Tag",
 description:
 "Track weeks-on-hand vs planned exit curves by category; align pricing and promo depth to hit exit targets.",
 impact: "Improve seasonal exit rates 10-20%; reduce end-of-life write-offs.",
 seed: true,
 },

 // ── Store Ops ───────────────────────────────────────────────────────────
 {
 id: "sops-1",
 name: "Labor Scheduling",
 businessAreaId: "store-ops",
 subjectArea: "Labor & Staffing",
 iconKey: "Store",
 description: "Generate store rosters aligned to foot traffic forecasts, labor budgets, and compliance rules; minimize overtime.",
 impact: "Improve sales per labor hour 10-20%; reduce overtime 25%.",
 seed: true,
 },
 {
 id: "sops-2",
 name: "Store Task Management",
 businessAreaId: "store-ops",
 subjectArea: "Task Compliance",
 iconKey: "Store",
 description: "Assign, track, and escalate operational tasks - opening checks, replenishment, maintenance - across all store locations.",
 impact: "Lift task completion rate; reduce store-readiness failures.",
 seed: true,
 },
 {
 id: "sops-3",
 name: "Store Performance Review",
 businessAreaId: "store-ops",
 subjectArea: "KPI Monitoring",
 iconKey: "Store",
 description: "Aggregate and review store KPIs - sales, conversion, shrink, compliance - versus targets and peer benchmarks.",
 impact: "Surface underperformers early; drive targeted coaching.",
 seed: true,
 },
 {
 id: "sops-4",
 name: "Foot Traffic & In-Store Conversion Pulse",
 businessAreaId: "store-ops",
 subjectArea: "Foot Traffic",
 iconKey: "Store",
 description:
 "Correlate door counts, dwell, and conversion by store and day-part; spot regions where traffic is up but sales lag.",
 impact: "Recover conversion 0.3-0.8 pts where staffing or execution drags.",
 seed: true,
 },

 // ── Finance ─────────────────────────────────────────────────────────────
 {
 id: "fin-1",
 name: "Margin & P&L Reporting",
 businessAreaId: "finance",
 subjectArea: "Gross Margin",
 iconKey: "Tag",
 description: "Produce gross margin and P&L views by category, store, and channel; reconcile actuals against plan and prior year.",
 impact: "Improve margin visibility; speed up month-end close.",
 seed: true,
 },
 {
 id: "fin-2",
 name: "Budget vs Actuals Review",
 businessAreaId: "finance",
 subjectArea: "Budget vs Actuals",
 iconKey: "Tag",
 description: "Compare actual spend and revenue against budget by cost centre; flag overspend and reforecast variances.",
 impact: "Tighten budget control; reduce budget variance 20%.",
 seed: true,
 },
 {
 id: "fin-3",
 name: "Shrinkage & Loss Investigation",
 businessAreaId: "finance",
 subjectArea: "Shrinkage & Loss Prevention",
 iconKey: "PackageCheck",
 description: "Investigate inventory shrinkage by store, region, and category; correlate loss to operational patterns and controls.",
 impact: "Reduce shrinkage rate 20-30%; target loss-prevention spend.",
 seed: true,
 },
 {
 id: "fin-4",
 name: "Cost Efficiency & OpEx Review",
 businessAreaId: "finance",
 subjectArea: "Cost Efficiency",
 iconKey: "Tag",
 description:
 "Analyze non-trade spend, DC/store operating costs, and unit economics vs benchmarks; prioritize initiatives with fastest payback.",
 impact: "Identify 3-7% structural OpEx opportunities without harming service.",
 seed: true,
 },

 // ── E-Com ───────────────────────────────────────────────────────────────
 {
 id: "ecom-1",
 name: "Catalog & Content Management",
 businessAreaId: "e-com",
 subjectArea: "Product Discovery",
 iconKey: "ClipboardCheck",
 description: "Publish and maintain product content - copy, images, attributes, SEO metadata - across digital channels and marketplaces.",
 impact: "Improve discovery and conversion; reduce content errors.",
 seed: true,
 },
 {
 id: "ecom-2",
 name: "Conversion & Funnel Review",
 businessAreaId: "e-com",
 subjectArea: "Conversion",
 iconKey: "ClipboardCheck",
 description: "Analyse checkout funnel, cart abandonment, and payment performance to identify and fix conversion leakage.",
 impact: "Increase conversion rate 0.5-1.5 pts; recover lost revenue.",
 seed: true,
 },
 {
 id: "ecom-3",
 name: "Experiment & Personalisation Backlog Review",
 businessAreaId: "e-com",
 subjectArea: "Conversion Optimisation",
 iconKey: "ClipboardCheck",
 description:
 "Prioritize A/B tests and personalisation rules by incremental revenue impact; retire conflicting experiences hurting funnel clarity.",
 impact: "Lift incremental GMV 1-3% from higher-quality experimentation throughput.",
 seed: true,
 },
 {
 id: "ecom-4",
 name: "Traffic Source Performance Review",
 businessAreaId: "e-com",
 subjectArea: "Traffic Sources",
 iconKey: "ClipboardCheck",
 description:
 "Evaluate paid and organic sources by quality of traffic, assist paths, and blended ROAS; reallocate spend from leaky channels.",
 impact: "Improve blended acquisition efficiency 10-20%.",
 seed: true,
 },
 {
 id: "ecom-5",
 name: "Site Speed & Reliability Review",
 businessAreaId: "e-com",
 subjectArea: "Site Performance",
 iconKey: "ClipboardCheck",
 description:
 "Monitor Core Web Vitals, API latency, and checkout errors by device; tie regressions to conversion drops.",
 impact: "Cut bounce from tech friction 15-30%; stabilize peak-season readiness.",
 seed: true,
 },

 // ── POS ─────────────────────────────────────────────────────────────────
 {
 id: "pos-1",
 name: "POS Reconciliation & Audit",
 businessAreaId: "pos",
 subjectArea: "Transaction Health",
 iconKey: "Store",
 description: "Reconcile end-of-day POS totals against tender, loyalty, and inventory; investigate offline sync and balance exceptions.",
 impact: "Eliminate reconciliation gaps; detect fraud patterns.",
 seed: true,
 },
 {
 id: "pos-2",
 name: "Cashier & Transaction Review",
 businessAreaId: "pos",
 subjectArea: "Staff Performance",
 iconKey: "Store",
 description: "Monitor cashier variance, checkout speed, and attachment rates; flag anomalies for coaching or investigation.",
 impact: "Improve checkout throughput; reduce cashier-driven losses.",
 seed: true,
 },
 {
 id: "pos-3",
 name: "Checkout Lane Speed & Queue Review",
 businessAreaId: "pos",
 subjectArea: "Checkout Efficiency",
 iconKey: "Store",
 description:
 "Measure queue time, items per minute, and lane staffing vs forecast traffic; target stores where speed hurts basket completion.",
 impact: "Reduce average wait 15-25% at peak without adding labor linearly.",
 seed: true,
 },
 {
 id: "pos-4",
 name: "Tender Mix & Payment Method Health",
 businessAreaId: "pos",
 subjectArea: "Payment Methods",
 iconKey: "Store",
 description:
 "Analyze declines, timeouts, and fee exposure by tender type; prioritize fixes for digital wallets, BNPL, and chip fallback.",
 impact: "Cut tender-related abandonment and reconciliation noise.",
 seed: true,
 },

 // ── Workforce ───────────────────────────────────────────────────────────
 {
 id: "wf-1",
 name: "Payroll & Labor Cost Review",
 businessAreaId: "workforce",
 subjectArea: "Labor Cost",
 iconKey: "Store",
 description: "Analyse payroll actuals vs budgets by store, role, and period; surface overtime drift, overstaffing, and cost-per-sale trends.",
 impact: "Cut labor cost variance 15-20%; identify savings opportunities.",
 seed: true,
 },
 {
 id: "wf-2",
 name: "Scheduling Compliance & Attendance",
 businessAreaId: "workforce",
 subjectArea: "Scheduling Compliance",
 iconKey: "Store",
 description: "Monitor clock-in vs planned shifts, break compliance, and attendance patterns; flag violations before they become legal exposure.",
 impact: "Reduce break violations 40-60%; tighten schedule adherence.",
 seed: true,
 },
 {
 id: "wf-3",
 name: "Training & Certification Tracking",
 businessAreaId: "workforce",
 subjectArea: "Productivity",
 iconKey: "Store",
 description: "Track mandatory training completion, certification renewal, and onboarding progress; surface gaps by store and role before peak seasons.",
 impact: "Ensure 100% compliance readiness; reduce ramp-up time for new hires.",
 seed: true,
 },
 {
 id: "wf-4",
 name: "Overtime Drift & Premium Pay Review",
 businessAreaId: "workforce",
 subjectArea: "Overtime",
 iconKey: "Store",
 description:
 "Isolate overtime drivers by store, role, and week; distinguish planned surge vs chronic understaffing or schedule leakage.",
 impact: "Cut unplanned overtime 15-30%; improve wage forecast accuracy.",
 seed: true,
 },

 // ── Support ─────────────────────────────────────────────────────────────
 {
 id: "sup-1",
 name: "Returns Intake & Processing",
 businessAreaId: "support",
 subjectArea: "Returns Management",
 iconKey: "Repeat",
 description: "Validate and process customer returns; triage damage assessment, disposition product, and issue refunds or replacements.",
 impact: "Cut returns processing cost 35-50%; improve NPS 10-15 pts.",
 seed: true,
 },
 {
 id: "sup-2",
 name: "Customer Satisfaction & Feedback Pulse",
 businessAreaId: "support",
 subjectArea: "Customer Satisfaction",
 iconKey: "Repeat",
 description:
 "Aggregate CSAT, CES, and verbatims after care interactions; cluster themes driving detractors by channel and product.",
 impact: "Lift satisfaction 5-10 pts where root causes are operational.",
 seed: true,
 },
 {
 id: "sup-3",
 name: "Contact Centre Workforce Review",
 businessAreaId: "support",
 subjectArea: "Contact Centre",
 iconKey: "Repeat",
 description:
 "Forecast volume by intent; align staffing, occupancy mix, and improve first-contact resolution through routing.",
 impact: "Cut cost-per-contact 10-15%; improve FCR 5-12 pts.",
 seed: true,
 },
 {
 id: "sup-4",
 name: "Wait Time & Queue SLA Review",
 businessAreaId: "support",
 subjectArea: "Wait Times",
 iconKey: "Repeat",
 description:
 "Expose abandoned chats and calls by queue; tune thresholds, bots, and escalation paths before SLA breaches spike.",
 impact: "Reduce abandon rate 20-35%; stabilize peak-hour performance.",
 seed: true,
 },
 {
 id: "sup-5",
 name: "Escalation Backlog & Resolution Review",
 businessAreaId: "support",
 subjectArea: "Escalations",
 iconKey: "Repeat",
 description:
 "Track tier-2 aging, repeat escalations, and compensation leakage; assign executive-response triggers where brand risk is high.",
 impact: "Cut escalation backlog 40-60%; reduce repeat contacts.",
 seed: true,
 },
];

/** GFI demo processes - B2B specialty distribution, 14 DCs, perishable focus. */
export const GFI_PROCESSES_SEED = [
 // ── Inventory ───────────────────────────────────────────────────────────
 {
 id: "gfi-inv-1",
 name: "Perishable Demand Planning Cycle",
 businessAreaId: "inventory",
 subjectArea: "Stock Health",
 iconKey: "PackageCheck",
 description: "Weekly cycle for setting demand expectations across 11,340+ perishable SKUs by DC; aligns merchandising, replenishment, and DC capacity ahead of each cycle.",
 impact: "Cut spoilage 20-30%; reduce stockouts 40-60%.",
 seed: true,
 },
 {
 id: "gfi-inv-2",
 name: "Supplier Purchase Order Generation",
 businessAreaId: "inventory",
 subjectArea: "Replenishment",
 iconKey: "PackageCheck",
 description: "Auto-generate POs to 2,000+ suppliers based on DC-level demand plans, lead times, and min-order constraints.",
 impact: "Automate routine POs; improve supplier fill rate.",
 seed: true,
 },
 {
 id: "gfi-inv-3",
 name: "Cold Chain Receiving & Putaway",
 businessAreaId: "inventory",
 subjectArea: "Stock Health",
 iconKey: "PackageCheck",
 description: "Validate inbound temperature-controlled shipments, log excursions, and assign putaway to the correct cold zone.",
 impact: "Reduce receiving errors; enforce cold-chain compliance.",
 seed: true,
 },
 {
 id: "gfi-inv-4",
 name: "Inter-DC Inventory Transfer",
 businessAreaId: "inventory",
 subjectArea: "Replenishment",
 iconKey: "PackageCheck",
 description: "Detect regional imbalances and generate temperature-aware transfer recommendations across 14 DCs.",
 impact: "Reduce stranded stock; improve network fill rate.",
 seed: true,
 },
 {
 id: "gfi-inv-5",
 name: "Lot Traceability & Compliance",
 businessAreaId: "inventory",
 subjectArea: "Shrinkage & Loss",
 iconKey: "PackageCheck",
 description: "Maintain lot-level records for regulated SKUs from receipt to delivery; support FSMA 204 mock recalls.",
 impact: "100% traceability coverage; audit-ready by 2026.",
 seed: true,
 },

 // ── Orders ──────────────────────────────────────────────────────────────
 {
 id: "gfi-ord-1",
 name: "B2B Order Capture & Validation",
 businessAreaId: "orders",
 subjectArea: "Order Fulfillment",
 iconKey: "ClipboardCheck",
 description: "Ingest B2B orders from portal and EDI; validate against contracted pricing, minimums, and inventory availability.",
 impact: "Cut order-entry errors; reduce manual corrections.",
 seed: true,
 },
 {
 id: "gfi-ord-2",
 name: "Multi-Temp Fulfillment Routing",
 businessAreaId: "orders",
 subjectArea: "Order Fulfillment",
 iconKey: "ClipboardCheck",
 description: "Route B2B orders to the best DC by temperature lane, availability, and delivery SLA for GFI's own fleet.",
 impact: "Reduce split shipments 30%; cut fulfillment cost 15-25%.",
 seed: true,
 },
 {
 id: "gfi-ord-3",
 name: "Route Planning & Dispatch",
 businessAreaId: "orders",
 subjectArea: "Delivery Management",
 iconKey: "ClipboardCheck",
 description: "Build delivery routes respecting frozen/chilled/ambient zones, customer time windows, and backhaul opportunities.",
 impact: "Improve route utilization; reduce cost-per-delivery.",
 seed: true,
 },
 {
 id: "gfi-ord-4",
 name: "Claims & Exception Handling",
 businessAreaId: "orders",
 subjectArea: "Exceptions",
 iconKey: "ClipboardCheck",
 description: "Process damage-in-transit, mis-ship, and quality claims; issue credit memos and disposition returned product.",
 impact: "Faster credit cycles; lower claims processing cost.",
 seed: true,
 },

 // ── Merchandise ─────────────────────────────────────────────────────────
 {
 id: "gfi-merch-1",
 name: "Assortment & Range Review",
 businessAreaId: "merchandise",
 subjectArea: "Assortment Planning",
 iconKey: "ClipboardCheck",
 description: "Evaluate 6,000+ brand assortment by sell-through, margin, and customer demand; delist underperformers.",
 impact: "Improve range productivity; reduce dead stock.",
 seed: true,
 },
 {
 id: "gfi-merch-2",
 name: "New Product & Brand Introduction",
 businessAreaId: "merchandise",
 subjectArea: "Category Performance",
 iconKey: "ClipboardCheck",
 description: "Onboard new SKUs and brands; track first-8-week sell-through vs forecast to scale winners and cut losers.",
 impact: "Faster NPI feedback; fewer failed launches.",
 seed: true,
 },
 {
 id: "gfi-merch-3",
 name: "Vendor Scorecard Review",
 businessAreaId: "merchandise",
 subjectArea: "Vendor Management",
 iconKey: "ClipboardCheck",
 description: "Score 2,000+ suppliers on fill rate, quality incidents, lead-time variance, and case accuracy for negotiation.",
 impact: "Strengthen vendor accountability; improve supply reliability.",
 seed: true,
 },

 // ── Pricing ─────────────────────────────────────────────────────────────
 {
 id: "gfi-prc-1",
 name: "Perishable Markdown Execution",
 businessAreaId: "pricing",
 subjectArea: "Markdown Optimisation",
 iconKey: "Tag",
 description: "Time multi-wave markdowns on short-code product to maximize recovery before write-off across temperature lanes.",
 impact: "Gross margin +2-5 pts; 20-40% less markdown waste.",
 seed: true,
 },
 {
 id: "gfi-prc-2",
 name: "Specialty Import Pricing Review",
 businessAreaId: "pricing",
 subjectArea: "Competitor Pricing",
 iconKey: "Tag",
 description: "Respond to tariff moves, FX shifts, and competitor specialty indices while protecting premium SKU positioning.",
 impact: "Protect import margins; faster tariff response.",
 seed: true,
 },

 // ── Store Ops (Warehouse) ────────────────────────────────────────────────
 {
 id: "gfi-wh-1",
 name: "DC Labor Scheduling",
 businessAreaId: "store-ops",
 subjectArea: "Labor & Staffing",
 iconKey: "Store",
 description: "Align pick teams and shift waves to volume forecasts at each of 14 DCs; flag overtime and burnout pockets early.",
 impact: "Picks per labor hour +10-20%; overtime down 25%.",
 seed: true,
 },
 {
 id: "gfi-wh-2",
 name: "Warehouse Pick/Pack/Ship Review",
 businessAreaId: "store-ops",
 subjectArea: "KPI Monitoring",
 iconKey: "Store",
 description: "Monitor pick accuracy, packing compliance, and ship confirmation rates; surface DC-level bottlenecks.",
 impact: "Reduce pick errors; improve order accuracy rates.",
 seed: true,
 },

 // ── Finance ─────────────────────────────────────────────────────────────
 {
 id: "gfi-fin-1",
 name: "Margin & Cost-per-Delivery Reporting",
 businessAreaId: "finance",
 subjectArea: "Gross Margin",
 iconKey: "Tag",
 description: "Produce margin by category and DC; calculate cost-per-delivery by route and temperature lane vs plan.",
 impact: "Clearer margin story; identify high-cost delivery lanes.",
 seed: true,
 },
 {
 id: "gfi-fin-2",
 name: "Shrinkage & Spoilage Investigation",
 businessAreaId: "finance",
 subjectArea: "Shrinkage & Loss Prevention",
 iconKey: "PackageCheck",
 description: "Correlate spoilage, damage, and miscounts to shifts, DCs, and suppliers; target loss-prevention actions.",
 impact: "Drive below 1.5% COGS shrink discipline.",
 seed: true,
 },

 // ── E-Com ───────────────────────────────────────────────────────────────
 {
 id: "gfi-ecom-1",
 name: "B2B Portal & Catalog Management",
 businessAreaId: "e-com",
 subjectArea: "Product Discovery",
 iconKey: "ClipboardCheck",
 description: "Maintain product catalog, pricing, and curated program content in the GFI B2B ordering portal for key accounts.",
 impact: "Improve portal adoption; increase wallet share per account.",
 seed: true,
 },
];

function iconKeyFromText(text) {
 const t = String(text || "").toLowerCase();
 if (t.includes("return") || t.includes("refund") || t.includes("exchange") || t.includes("intake")) return "Repeat";
 if (t.includes("price") || t.includes("pricing") || t.includes("markdown") || t.includes("promo") || t.includes("margin") || t.includes("budget") || t.includes("clearance")) return "Tag";
 if (t.includes("store") || t.includes("labor") || t.includes("staff") || t.includes("ops") || t.includes("scheduling") || t.includes("task") || t.includes("pos") || t.includes("cashier") || t.includes("warehouse") || t.includes("pick")) return "Store";
 if (t.includes("order") || t.includes("fulfill") || t.includes("fraud") || t.includes("delivery") || t.includes("ship") || t.includes("vendor") || t.includes("assortment") || t.includes("planogram") || t.includes("catalog") || t.includes("conversion")) return "ClipboardCheck";
 if (t.includes("restock") || t.includes("inventory") || t.includes("replenish") || t.includes("stock") || t.includes("forecast") || t.includes("receiving") || t.includes("putaway") || t.includes("shrink") || t.includes("lot") || t.includes("traceability")) return "PackageCheck";
 return "PackageCheck";
}

function nextProcessId(list) {
 const customNums = list
 .map((p) => {
 const m = /^custom-(\d+)$/i.exec(p.id || "");
 return m ? parseInt(m[1], 10) : 0;
 })
 .filter(Boolean);
 const max = customNums.length ? Math.max(...customNums) : 0;
 return `custom-${max + 1}`;
}

function resolveAreasForProfile(profileId) {
 try {
 const key = storageKeysForProfile(profileId).BUSINESS_AREAS;
 const areas = getBusinessAreasSync(key, profileId);
 if (areas?.length) return areas;
 } catch {
 /* fall through to seed */
 }
 return BUSINESS_AREAS;
}

function normalizeSubjectArea(p, areas) {
 const pool = areas || BUSINESS_AREAS;
 const ba = pool.find((b) => b.id === p.businessAreaId);
 const list = ba?.subjectAreas ?? [];
 const s = p.subjectArea != null ? String(p.subjectArea).trim() : "";
 if (s && list.includes(s)) return s;
 if (list.length) return list[0];
 return s || "";
}

function normalizeProcesses(raw, profileId = DEFAULT_DEMO_PROFILE_ID) {
 const fallback = profileProcessSeed(profileId);
 if (!raw || !Array.isArray(raw)) return structuredClone(fallback);
 const areas = resolveAreasForProfile(profileId);
 return raw.map((p) => ({
 ...p,
 iconKey: p.iconKey && PROCESS_ICON_KEYS.includes(p.iconKey) ? p.iconKey : "PackageCheck",
 subjectArea: normalizeSubjectArea(p, areas),
 }));
}

function profileProcessSeed(profileId) {
 const id = normalizeDemoProfileId(profileId);
 if (id === GOURMET_FOODS_PROFILE_ID) {
 return [...GFI_PROCESSES_SEED];
 }
 if (id === MIDDLEBY_PROFILE_ID) {
 return [...MIDDLEBY_PROCESSES_SEED];
 }
 if (id === MEDIA_PROFILE_ID) {
 return [...MEDIA_PROCESSES_SEED];
 }
 return [...PROCESSES_SEED];
}

/** Ensure every profile seed row exists. Preserves stored rows and user-added processes. */
function mergeProcessesWithSeed(storedList, profileId = DEFAULT_DEMO_PROFILE_ID) {
 const seedList = profileProcessSeed(profileId);
 const seedById = new Map(seedList.map((s) => [s.id, structuredClone(s)]));
 const byId = new Map();
 for (const p of storedList || []) {
 if (p && p.id) byId.set(p.id, p);
 }
 for (const [id, seed] of seedById) {
 if (!byId.has(id)) byId.set(id, seed);
 }
 const seedOrder = seedList.map((s) => s.id);
 const extraIds = [...byId.keys()].filter((id) => !seedById.has(id));
 extraIds.sort((a, b) => {
 const ma = /^custom-(\d+)$/i.exec(a);
 const mb = /^custom-(\d+)$/i.exec(b);
 if (ma && mb) return parseInt(ma[1], 10) - parseInt(mb[1], 10);
 return String(a).localeCompare(String(b));
 });
 return [...seedOrder.map((id) => byId.get(id)).filter(Boolean), ...extraIds.map((id) => byId.get(id))];
}

/**
 * @param {string} storageKey
 * @param {string} [profileId]
 */
export function loadProcesses(storageKey, profileId = DEFAULT_DEMO_PROFILE_ID) {
 const parsed = readLocalJson(storageKey, null);
 const seedList = profileProcessSeed(profileId);

 if (!parsed || typeof parsed !== "object") {
 const initial = {
 version: PROCESSES_SEED_VERSION,
 processes: normalizeProcesses(structuredClone(seedList), profileId),
 };
 writeLocalJson(storageKey, initial);
 return initial;
 }

 const storedVersion = parsed?.version ?? 1;
 if (storedVersion < PROCESSES_SEED_VERSION) {
 const prev = Array.isArray(parsed.processes) ? parsed.processes : [];
 const seedIds = new Set(seedList.map((s) => s.id));
 const preserved = prev.filter((p) => p?.id && !seedIds.has(p.id));
 const merged = mergeProcessesWithSeed(preserved, profileId);
 const initial = {
 version: PROCESSES_SEED_VERSION,
 processes: normalizeProcesses(merged, profileId),
 };
 writeLocalJson(storageKey, initial);
 return initial;
 }

 if (!parsed.processes) {
 const initial = {
 version: PROCESSES_SEED_VERSION,
 processes: normalizeProcesses(mergeProcessesWithSeed(Array.isArray(parsed) ? parsed : [], profileId)),
 };
 writeLocalJson(storageKey, initial);
 return initial;
 }
 const merged = mergeProcessesWithSeed(parsed.processes, profileId);
 const normalized = normalizeProcesses(merged, profileId);
 const out = { version: PROCESSES_SEED_VERSION, processes: normalized };
 const hadAllSeedIds = seedList.every((s) => parsed.processes.some((p) => p.id === s.id));
 if (!hadAllSeedIds) writeLocalJson(storageKey, out);
 return out;
}

export function saveProcesses(data, storageKey) {
 writeLocalJson(storageKey, {
 version: data.version ?? 1,
 processes: normalizeProcesses(data.processes),
 });
}

export function createProcess(list, { name, description, impact, iconKey, businessAreaId, subjectArea }) {
 const draft = {
 name: name.trim(),
 businessAreaId: businessAreaId || "",
 subjectArea: subjectArea != null ? String(subjectArea).trim() : "",
 };
 return {
 id: nextProcessId(list),
 name: name.trim(),
 description: description.trim(),
 impact: (impact || "").trim(),
 businessAreaId: businessAreaId || "",
 subjectArea: normalizeSubjectArea(draft),
 iconKey:
 PROCESS_ICON_KEYS.includes(iconKey)
 ? iconKey
 : iconKeyFromText(`${name} ${description} ${impact}`),
 seed: false,
 };
}

export function pickProcessIconKey({ name, description, impact }) {
 return iconKeyFromText(`${name} ${description} ${impact}`);
}
