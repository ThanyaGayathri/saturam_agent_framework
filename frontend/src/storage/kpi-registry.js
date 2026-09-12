import { readLocalJson, writeLocalJson } from "./local-json";
import { inferBusinessAreaIdForSubjectArea } from "../domain/business-areas";
import { DEFAULT_DEMO_PROFILE_ID, GOURMET_FOODS_PROFILE_ID, MEDIA_PROFILE_ID, MIDDLEBY_PROFILE_ID, normalizeDemoProfileId } from "../domain/demo-profiles";
import { GFI_KPI_REGISTRY_SEED } from "../domain/gfi-demo-seed";
import { MIDDLEBY_KPI_REGISTRY_SEED } from "../domain/middleby-kpi-seed";
import { MEDIA_KPI_REGISTRY_SEED } from "../domain/media-demo-seed";

export { MIDDLEBY_KPI_REGISTRY_SEED };

// Bumped 11 → 12 (2026-08-14) so persisted media KPIs pick up the empty
// level field (Strategic / Operational / Tactical don't apply to Default
// Media — media has no equivalent 3-tier categorisation).
export const KPI_SEED_VERSION = 13;

export const KPI_REGISTRY_SEED = [
  {
    category: "Enterprise KPIs",
    sections: [
      {
        name: "Revenue",
        kpis: [
          {
            name: "Gross Revenue",
            desc: "Total revenue before discounts and returns; primary topline view for enterprise and channel performance.",
            active: true,
            seed: true,
            datasetIds: ["ds-sap-sales-trans"],
            businessAreaId: "inventory",
            subjectArea: "Revenue",
            level: "Strategic",
            processMapping: ["inv-1", "prc-1"],
            unit: "$",
            industryBenchmark: "",
          },
        ],
      },
      {
        name: "Profitability",
        kpis: [
          {
            name: "Gross Margin Rate",
            desc: "Gross profit as a share of revenue; reflects pricing power and cost-of-goods efficiency.",
            active: true,
            seed: true,
            datasetIds: ["ds-pg-margin"],
            businessAreaId: "pricing",
            subjectArea: "Profitability",
            level: "Strategic",
            processMapping: ["prc-1", "fin-1"],
            unit: "%",
            industryBenchmark: "50-60% apparel",
          },
        ],
      },
      {
        name: "Customer Satisfaction",
        kpis: [
          {
            name: "Net Promoter Score (NPS)",
            desc: "Customer loyalty index from surveys on likelihood to recommend; benchmark for experience programs.",
            active: true,
            seed: true,
            datasetIds: ["ds-sf-cc-sla"],
            businessAreaId: "support",
            subjectArea: "Customer Satisfaction",
            level: "Strategic",
            processMapping: ["sup-1"],
            unit: "Score",
            industryBenchmark: "30-50 retail avg",
          },
        ],
      },
      {
        name: "Stock Health",
        kpis: [
          {
            name: "Stockout Rate",
            desc: "Share of SKUs or locations where demand exceeds on-hand stock; drives lost sales risk.",
            active: true,
            seed: true,
            datasetIds: ["ds-sap-inv-snapshot", "ds-snow-forecast"],
            businessAreaId: "inventory",
            subjectArea: "Stock Health",
            level: "Operational",
            processMapping: ["inv-5"],
            unit: "%",
            industryBenchmark: "2-5% retail avg",
          },
          {
            name: "Inventory Turnover",
            desc: "How often inventory sells and replenishes in a period; higher velocity usually indicates healthier working capital use.",
            active: true,
            seed: true,
            businessAreaId: "inventory",
            subjectArea: "Stock Health",
            level: "Operational",
            processMapping: ["inv-1"],
            unit: "Ratio",
            industryBenchmark: "4-8x per year",
          },
        ],
      },
      {
        name: "Replenishment",
        kpis: [
          {
            name: "Days of Stock Remaining",
            desc: "Estimated days until stockout at current sell-through and inbound replenishment assumptions.",
            active: true,
            seed: true,
            datasetIds: ["ds-sap-inv-snapshot", "ds-snow-forecast"],
            businessAreaId: "inventory",
            subjectArea: "Replenishment",
            level: "Operational",
            processMapping: ["inv-5"],
            unit: "Days",
            industryBenchmark: "14-30 days safety stock",
          },
          {
            name: "Safety Stock Coverage",
            desc: "Days of demand the safety stock parameter covers at each SKU and location, against the lead-time variability it is meant to absorb.",
            active: true,
            seed: true,
            datasetIds: ["ds-sap-inv-snapshot", "ds-sap-supplier"],
            businessAreaId: "inventory",
            subjectArea: "Replenishment",
            level: "Operational",
            processMapping: ["inv-5"],
            unit: "Days",
            industryBenchmark: "14-60 days by category",
          },
        ],
      },
      {
        name: "Supplier Management",
        kpis: [
          {
            name: "Supplier Lead Time",
            desc: "Average time from purchase order placement to goods receipt; feeds safety stock and planning.",
            active: true,
            seed: true,
            datasetIds: ["ds-sap-supplier"],
            businessAreaId: "inventory",
            subjectArea: "Supplier Management",
            level: "Operational",
            processMapping: ["inv-2"],
            unit: "Days",
            industryBenchmark: "7-14 days standard",
          },
        ],
      },
      {
        name: "Order Fulfillment",
        kpis: [
          {
            name: "Order Fill Rate",
            desc: "Share of order lines or units fulfilled complete versus ordered; core availability metric for fulfillment.",
            active: true,
            seed: true,
            datasetIds: ["ds-pg-orders"],
            businessAreaId: "orders",
            subjectArea: "Order Fulfillment",
            level: "Operational",
            processMapping: ["ord-2"],
            unit: "%",
            industryBenchmark: "95-98%",
          },
        ],
      },
      {
        name: "Delivery Management",
        kpis: [
          {
            name: "On-Time Delivery Rate",
            desc: "Shipments arriving by the committed customer or carrier date; supports SLA and satisfaction.",
            active: true,
            seed: true,
            datasetIds: ["ds-pg-orders"],
            businessAreaId: "orders",
            subjectArea: "Delivery Management",
            level: "Operational",
            processMapping: ["ord-3"],
            unit: "%",
            industryBenchmark: "92-96%",
          },
        ],
      },
      {
        name: "Fraud Detection",
        kpis: [
          {
            name: "Fraud Detection Rate",
            desc: "Orders confirmed or flagged as fraudulent as a share of transactions; balances loss prevention and friction.",
            active: true,
            seed: true,
            businessAreaId: "orders",
            subjectArea: "Fraud Detection",
            level: "Operational",
            processMapping: ["ord-1"],
            unit: "%",
            industryBenchmark: "0.1-0.5% of orders",
          },
        ],
      },
      {
        name: "Returns Management",
        kpis: [
          {
            name: "Return Rate",
            desc: "Returned units or value as a percentage of gross sales; indicates quality, fit, and policy effectiveness.",
            active: true,
            seed: true,
            businessAreaId: "support",
            subjectArea: "Returns Management",
            level: "Operational",
            processMapping: ["sup-1"],
            unit: "%",
            industryBenchmark: "8-12% e-com, 5-8% in-store",
          },
          {
            name: "Return Processing Time",
            desc: "Average days from customer return initiation to refund, exchange, or restock completion.",
            active: true,
            seed: true,
            businessAreaId: "support",
            subjectArea: "Returns Management",
            level: "Tactical",
            processMapping: ["sup-1"],
            unit: "Days",
            industryBenchmark: "3-5 days",
          },
        ],
      },
      {
        name: "Conversion",
        kpis: [
          {
            name: "Cart Abandonment Rate",
            desc: "Share of started carts that do not complete checkout; highlights friction, pricing, and payment issues.",
            active: true,
            seed: true,
            datasetIds: ["ds-sap-cart-events", "ds-snow-traffic"],
            businessAreaId: "e-com",
            subjectArea: "Conversion",
            level: "Operational",
            processMapping: ["ecom-2"],
            unit: "%",
            industryBenchmark: "65-75% avg",
          },
          {
            name: "Conversion Rate",
            desc: "Completed purchases divided by sessions or visits; core measure of funnel and merchandising effectiveness.",
            active: true,
            seed: true,
            datasetIds: ["ds-snow-traffic", "ds-sap-sales-trans"],
            businessAreaId: "e-com",
            subjectArea: "Conversion",
            level: "Operational",
            processMapping: ["ecom-2"],
            unit: "%",
            industryBenchmark: "2-4% e-com avg",
          },
        ],
      },
      {
        name: "Markdown Optimisation",
        kpis: [
          {
            name: "Sell-Through Rate",
            desc: "Units sold as a percentage of inventory available for a season or assortment; guides allocation and markdowns.",
            active: true,
            seed: true,
            datasetIds: ["ds-pg-sellthrough"],
            businessAreaId: "pricing",
            subjectArea: "Markdown Optimisation",
            level: "Operational",
            processMapping: ["prc-2"],
            unit: "%",
            industryBenchmark: "80-90% target",
          },
          {
            name: "Markdown Rate",
            desc: "Revenue or units sold at markdown as a share of relevant sales; tracks promotional depth and clearance.",
            active: true,
            seed: true,
            datasetIds: ["ds-pg-sellthrough"],
            businessAreaId: "pricing",
            subjectArea: "Markdown Optimisation",
            level: "Operational",
            processMapping: ["prc-2"],
            unit: "%",
            industryBenchmark: "<10% of revenue",
          },
        ],
      },
      {
        name: "Productivity",
        kpis: [
          {
            name: "Sales per Labor Hour",
            desc: "Net sales per labor hour scheduled; productivity benchmark for scheduling and labor models.",
            active: true,
            seed: true,
            businessAreaId: "workforce",
            subjectArea: "Productivity",
            level: "Operational",
            processMapping: ["wf-1"],
            unit: "$",
            industryBenchmark: "Varies by category",
          },
        ],
      },
      {
        name: "Labor Cost",
        kpis: [
          {
            name: "Labor Cost as % of Revenue",
            desc: "Total labor spend as a percentage of net revenue; balances service levels with payroll efficiency.",
            active: true,
            seed: true,
            businessAreaId: "workforce",
            subjectArea: "Labor Cost",
            level: "Operational",
            processMapping: ["wf-1"],
            unit: "%",
            industryBenchmark: "15-25% retail avg",
          },
          {
            name: "Overtime Rate",
            desc: "Overtime hours as a percentage of total paid hours; early signal of understaffing or schedule drift.",
            active: true,
            seed: true,
            businessAreaId: "workforce",
            subjectArea: "Labor Cost",
            level: "Tactical",
            processMapping: ["wf-1"],
            unit: "%",
            industryBenchmark: "<5% of total hours",
          },
        ],
      },
      {
        name: "Foot Traffic",
        kpis: [
          {
            name: "Foot Traffic",
            desc: "Store visitor counts over time; basis for conversion, labor planning, and marketing lift.",
            active: true,
            seed: true,
            businessAreaId: "store-ops",
            subjectArea: "Foot Traffic",
            level: "Tactical",
            processMapping: ["sops-3"],
            unit: "Count",
            industryBenchmark: "Varies by location",
          },
        ],
      },
      {
        name: "Checkout Efficiency",
        kpis: [
          {
            name: "Avg Checkout Wait Time",
            desc: "Average minutes customers wait in queue before a lane begins checkout; drives experience and abandonment.",
            active: true,
            seed: true,
            businessAreaId: "pos",
            subjectArea: "Checkout Efficiency",
            level: "Tactical",
            processMapping: ["pos-2", "sops-2"],
            unit: "Minutes",
            industryBenchmark: "<3 min target",
          },
        ],
      },
      {
        name: "Transaction Health",
        kpis: [
          {
            name: "POS Transaction Failure Rate",
            desc: "Failed or cancelled POS attempts as a share of transactions; points to hardware, network, or tender issues.",
            active: true,
            seed: true,
            businessAreaId: "pos",
            subjectArea: "Transaction Health",
            level: "Tactical",
            processMapping: ["pos-1", "ord-4"],
            unit: "%",
            industryBenchmark: "<1%",
          },
        ],
      },
      {
        name: "Shrinkage & Loss Prevention",
        kpis: [
          {
            name: "Shrinkage Rate",
            desc: "Inventory loss from theft, damage, or process error as a percentage of revenue; finance and LP alignment.",
            active: true,
            seed: true,
            businessAreaId: "finance",
            subjectArea: "Shrinkage & Loss Prevention",
            level: "Operational",
            processMapping: ["fin-3", "sops-3"],
            unit: "%",
            industryBenchmark: "1.5-2% of revenue avg",
          },
        ],
      },
      {
        name: "Gross Margin",
        kpis: [
          {
            name: "Gross Margin by Category",
            desc: "Gross margin percentage by merchandise or reporting category; supports mix, pricing, and promo decisions.",
            active: true,
            seed: true,
            datasetIds: ["ds-pg-margin"],
            businessAreaId: "finance",
            subjectArea: "Gross Margin",
            level: "Strategic",
            processMapping: ["fin-1", "prc-1"],
            unit: "%",
            industryBenchmark: "Varies by category",
          },
        ],
      },
      {
        name: "Contact Centre",
        kpis: [
          {
            name: "Contact Centre SLA Compliance",
            desc: "Inbound contacts answered or handled within the defined service-level window (e.g. speed-to-answer).",
            active: true,
            seed: true,
            datasetIds: ["ds-sf-cc-sla"],
            businessAreaId: "support",
            subjectArea: "Contact Centre",
            level: "Tactical",
            processMapping: ["sup-1"],
            unit: "%",
            industryBenchmark: ">85% calls answered in 20s",
          },
        ],
      },
    ],
  },
];

function recomputeMeta(categories) {
  return categories.map((cat) => {
    const sections = cat.sections.map((sec) => {
      const kpis = sec.kpis || [];
      const subjectArea = sec.name;
      const businessAreaId =
        sec.businessAreaId || inferBusinessAreaIdForSubjectArea(subjectArea);
      const normalizedKpis = kpis.map((k) => ({
        ...k,
        subjectArea: k.subjectArea || subjectArea,
        businessAreaId: k.businessAreaId || businessAreaId,
      }));
      return {
        ...sec,
        businessAreaId,
        count: normalizedKpis.length,
        kpis: normalizedKpis,
      };
    });
    const totalKpis = sections.reduce((n, s) => n + s.kpis.length, 0);
    return {
      ...cat,
      subjectAreas: sections.length,
      totalKpis,
      sections,
    };
  });
}

/** Stable key so seed rows sync with edits in localStorage (like use-case catalog merge). */
function kpiMergeKey(kpi, sectionName) {
  const s = (kpi.subjectArea || sectionName || "").trim().toLowerCase();
  const n = (kpi.name || "").trim().toLowerCase();
  return `${s}::${n}`;
}

function flattenRegistry(categories) {
  const out = [];
  for (const cat of categories || []) {
    for (const sec of cat.sections || []) {
      for (const kpi of sec.kpis || []) {
        out.push({ kpi, sectionName: sec.name });
      }
    }
  }
  return out;
}

/** Append a user-created (non-seed) KPI into the first category, creating a section if needed. */
function placeUserKpi(categories, kpi, sectionNameHint) {
  const cat = categories[0];
  if (!cat) return;
  const subject =
    (kpi.subjectArea || sectionNameHint || "").trim() || "Uncategorized";
  let sec = cat.sections.find((s) => s.name === subject);
  if (!sec) {
    sec = {
      name: subject,
      businessAreaId:
        kpi.businessAreaId || inferBusinessAreaIdForSubjectArea(subject),
      kpis: [],
    };
    cat.sections.push(sec);
  }
  sec.kpis = sec.kpis || [];
  sec.kpis.push({ ...kpi });
}

/**
 * Start from the canonical spreadsheet seed, overlay matching rows from localStorage
 * (name + subject area identity), then append stored rows that only exist in user data.
 * Ensures new seed KPIs always appear while persisting edits and user-added KPIs.
 */
function mergeKpiCategoriesWithSeed(storedCategories, profileId = DEFAULT_DEMO_PROFILE_ID) {
  const id = normalizeDemoProfileId(profileId);
  const seedBase = structuredClone(
    id === GOURMET_FOODS_PROFILE_ID
      ? GFI_KPI_REGISTRY_SEED
      : id === MIDDLEBY_PROFILE_ID
        ? MIDDLEBY_KPI_REGISTRY_SEED
        : id === MEDIA_PROFILE_ID
          ? MEDIA_KPI_REGISTRY_SEED
          : KPI_REGISTRY_SEED
  );
  if (!storedCategories?.length) {
    return seedBase;
  }

  const storedByKey = new Map();
  for (const { kpi, sectionName } of flattenRegistry(storedCategories)) {
    storedByKey.set(kpiMergeKey(kpi, sectionName), kpi);
  }

  const seedKeys = new Set();
  for (const cat of seedBase) {
    for (const sec of cat.sections || []) {
      for (const kpi of sec.kpis || []) {
        seedKeys.add(kpiMergeKey(kpi, sec.name));
      }
    }
  }

  const matchedKeys = new Set();

  for (const cat of seedBase) {
    for (const sec of cat.sections || []) {
      sec.kpis = (sec.kpis || []).map((kpi) => {
        const key = kpiMergeKey(kpi, sec.name);
        const st = storedByKey.get(key);
        if (st) {
          matchedKeys.add(key);
          const merged = { ...kpi, ...st };
          if (!String(merged.desc ?? "").trim()) merged.desc = kpi.desc;
          if (!Array.isArray(merged.datasetIds) || merged.datasetIds.length === 0) {
            if (Array.isArray(kpi.datasetIds) && kpi.datasetIds.length > 0) merged.datasetIds = kpi.datasetIds;
          }
          return merged;
        }
        return kpi;
      });
    }
  }

  for (const { kpi, sectionName } of flattenRegistry(storedCategories)) {
    const key = kpiMergeKey(kpi, sectionName);
    if (matchedKeys.has(key)) continue;
    if (seedKeys.has(key)) continue;
    placeUserKpi(seedBase, kpi, sectionName);
  }

  return seedBase;
}

function normalizeKpiRegistry(raw, profileId = DEFAULT_DEMO_PROFILE_ID) {
  if (!raw || !Array.isArray(raw)) {
    const id = normalizeDemoProfileId(profileId);
    const fallback =
      id === GOURMET_FOODS_PROFILE_ID
        ? GFI_KPI_REGISTRY_SEED
        : id === MIDDLEBY_PROFILE_ID
          ? MIDDLEBY_KPI_REGISTRY_SEED
          : KPI_REGISTRY_SEED;
    return recomputeMeta(structuredClone(fallback));
  }
  return recomputeMeta(JSON.parse(JSON.stringify(raw)));
}

/**
 * @param {string} storageKey
 * @param {string} [profileId]
 */
export function loadKpiRegistry(storageKey, profileId = DEFAULT_DEMO_PROFILE_ID) {
  const parsed = readLocalJson(storageKey, null);
  // If stored version is older than current seed version, discard stored data so
  // updated processMapping (and other seed fields) take effect on next load.
  const storedVersion = parsed?.version ?? 0;
  const stored =
    storedVersion >= KPI_SEED_VERSION && typeof parsed === "object" && Array.isArray(parsed.categories)
      ? parsed.categories
      : [];
  const merged = mergeKpiCategoriesWithSeed(stored, profileId);
  const categories = normalizeKpiRegistry(merged, profileId);
  const out = { version: KPI_SEED_VERSION, categories };
  writeLocalJson(storageKey, out);
  return out;
}

export function saveKpiRegistry(data, storageKey) {
  writeLocalJson(storageKey, {
    version: data.version ?? KPI_SEED_VERSION,
    categories: normalizeKpiRegistry(data.categories),
  });
}
