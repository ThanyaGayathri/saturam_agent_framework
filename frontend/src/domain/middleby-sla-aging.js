import { loadMiddlebyDataset } from "./middleby-analytics-data";

const SLA_AGING_PATH = "/middleby/data/from_real_hubspot/sla_aging.json";
const UC04_PATH = "/middleby/data/from_real_hubspot/uc04_followup_priority.json";

/** Deterministic hash for stable demo days_open from deal id. */
function hashDealId(dealId) {
  const s = String(dealId || "");
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return (h >>> 0) % 1000;
}

/** Estimate days open from HubSpot timeframe when export lacks created_date. */
export function estimateDaysOpen(dealId, timeframe) {
  const h = hashDealId(dealId);
  const tf = String(timeframe || "").toLowerCase();
  if (tf.includes("0 - 1")) return 1 + (h % 28);
  if (tf.includes("1 - 3")) return 30 + (h % 31);
  if (tf.includes("4 - 6")) return 120 + (h % 30);
  if (tf.includes("6 - 9")) return 180 + (h % 30);
  if (tf.includes("1 year")) return 365 + (h % 60);
  return 7 + (h % 21);
}

export function deriveSlaAgingRows(uc04Rows) {
  return (uc04Rows || [])
    .filter((r) => r.deal_id && String(r.stage || "").toLowerCase() !== "closed lost")
    .map((r) => ({
      deal_id_hash: r.deal_id,
      brand: r.brand || "",
      days_open: estimateDaysOpen(r.deal_id, r.timeframe),
      timeframe: r.timeframe || "",
      consultation: r.consultation || "",
      rep_group: r.rep_group || "",
      data_source: r.data_source || "real (anonymised)",
      example_data: "mixed",
    }));
}

let slaCache = null;

export async function loadSlaAgingRows() {
  if (slaCache) return slaCache;

  const dedicated = await loadMiddlebyDataset(SLA_AGING_PATH);
  if (dedicated.rows?.length) {
    slaCache = dedicated.rows;
    return slaCache;
  }

  const uc04 = await loadMiddlebyDataset(UC04_PATH);
  slaCache = deriveSlaAgingRows(uc04.rows);
  return slaCache;
}

export const SLA_THRESHOLD_DAYS = 14;

export function summarizeSlaAging(rows) {
  const list = rows || [];
  const urgent = list.filter((r) => String(r.timeframe || "").includes("0 - 1"));
  const consult = urgent.filter((r) => String(r.consultation || "").toLowerCase() === "yes");
  const overSla = list.filter((r) => Number(r.days_open) > SLA_THRESHOLD_DAYS);
  const sorted = [...list].sort((a, b) => Number(b.days_open) - Number(a.days_open));
  const oldest = sorted[0];

  const buckets = [
    { label: "0-7 days", min: 0, max: 7, count: 0 },
    { label: "8-14 days", min: 8, max: 14, count: 0 },
    { label: "15+ days", min: 15, max: 999, count: 0 },
  ];
  for (const r of list) {
    const d = Number(r.days_open) || 0;
    const bucket = buckets.find((b) => d >= b.min && d <= b.max);
    if (bucket) bucket.count += 1;
  }

  return {
    total: list.length,
    urgentCount: urgent.length,
    consultationCount: consult.length,
    overSlaCount: overSla.length,
    oldest,
    buckets,
    thresholdDays: SLA_THRESHOLD_DAYS,
  };
}
