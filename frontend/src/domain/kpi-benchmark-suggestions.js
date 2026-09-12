import { BUSINESS_AREAS } from "./business-areas";
import { getBusinessAreasSync } from "../storage/business-areas";
import { storageKeysForProfile } from "../storage/keys";
import { readActiveDemoProfileId } from "../storage/demo-profile-preference";

/** Demo "AI" copy aligned loosely with common retail seed patterns - editable in the UI. */
const SUBJECT_HINTS = {
  Revenue: "AI suggestion: revenue growth and base levels vary by channel; align with your finance target range.",
  Profitability: "AI suggestion: 50-60% gross margin is often cited for apparel; tune to your sub-category and channel mix.",
  "Customer Satisfaction": "AI suggestion: ~30-50 NPS is a broad retail band; calibrate against your historical baseline.",
  "Stock Health": "AI suggestion: 2-5% is a common retail range for stockout-type metrics; adjust for category and service level.",
  Replenishment: "AI suggestion: 14-30 days of cover is a typical safety-stock starting point; refine with supplier lead times.",
  "Supplier Management": "AI suggestion: 7-14 days lead time is a common planning assumption; replace with your supplier data.",
  "Order Fulfillment": "AI suggestion: 95-98% fill rate is a frequent operational target; set what your network commits to.",
  "Delivery Management": "AI suggestion: 92-96% on-time delivery is a typical benchmark band for many retailers.",
  "Fraud Detection": "AI suggestion: 0.1-0.5% of orders is a rough fraud band; your channel fraud profile will differ.",
  "Returns Management": "AI suggestion: 8-12% e-com / 5-8% in-store return rates are often used as context; verify for your category.",
  Conversion: "AI suggestion: 2-4% conversion is a rough e-commerce average; your traffic mix changes the story.",
  "Markdown Optimisation": "AI suggestion: sell-through and markdown intensity vary; use targets your planning team already tracks.",
  Productivity: "AI suggestion: sales per labor hour varies widely by format; set an internal target vs. last year.",
  "Labor Cost": "AI suggestion: 15-25% labor as % of revenue is a common planning band; align with finance.",
  "Foot Traffic": "AI suggestion: traffic norms vary by location type; compare to your door’s historical peaks and troughs.",
  "Checkout Efficiency": "AI suggestion: under ~3 minutes wait is a common store goal at peak; tune to your SLA.",
  "Transaction Health": "AI suggestion: under ~1% failure/read errors is a typical stabilisation target for POS health.",
  "Shrinkage & Loss Prevention": "AI suggestion: ~1.5-2% of revenue shrink is often discussed as a context range; legal and methods vary.",
  "Gross Margin": "AI suggestion: category margin varies; use the range your merchandising team already publishes.",
  "Contact Centre": "AI suggestion: >85% of calls answered quickly is a frequent service benchmark; match your policy.",
};

export function suggestIndustryBenchmark({ level, businessAreaId, subjectArea }) {
  const subj = (subjectArea || "").trim();
  if (subj && SUBJECT_HINTS[subj]) return SUBJECT_HINTS[subj];
  // Prefer the persisted store so renames on the Business hierarchy tab
  // flow into the AI benchmark hint. Falls back to the seed const on error.
  let areaPool = BUSINESS_AREAS;
  try {
    const profileId = readActiveDemoProfileId();
    const key = storageKeysForProfile(profileId).BUSINESS_AREAS;
    const persisted = getBusinessAreasSync(key, profileId);
    if (persisted?.length) areaPool = persisted;
  } catch {
    /* fall through to seed */
  }
  const ba = areaPool.find((b) => b.id === businessAreaId);
  const baLabel = ba?.label || "this business area";
  const lev = (level || "Operational").toLowerCase();
  return `AI suggestion: typical ${lev} benchmarks for ${baLabel} depend on format, region, and seasonality - enter the range your team aligns on.`;
}
