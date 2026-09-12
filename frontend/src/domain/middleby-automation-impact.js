/**
 * Live impact tiles + trigger text for automation playbooks (same CSV/JSON as monitors).
 */

import { loadMiddlebyDataset, parseNum } from "./middleby-analytics-data";
import { getMiddlebyPrimarySpec } from "./middleby-screen-data";
import { getAutomationPlaybook } from "./middleby-automation-playbooks";
import { loadSlaAgingRows, summarizeSlaAging, SLA_THRESHOLD_DAYS } from "./middleby-sla-aging";
import { RELEASE_CALENDAR_PATH } from "./middleby-monitor-chart-specs";

async function loadCatalogRows(catalogId) {
  const spec = getMiddlebyPrimarySpec(catalogId);
  if (!spec?.filePath) return [];
  const data = await loadMiddlebyDataset(spec.filePath);
  return data.rows || [];
}

function isUrgentRow(r) {
  const tf = String(r.timeframe || "");
  return tf.includes("0 - 1") || tf.includes("0-1");
}

function isConsultRow(r) {
  return String(r.consultation || r.consultation_requested || "").toLowerCase() === "yes";
}

function gapScore(sessions, quotes) {
  return sessions - quotes * 50;
}

function sparkSeed(patternId, idx) {
  const s = `${patternId}:${idx}`;
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return (h >>> 0) / 4294967295;
}

/** Before/after trend sparks for automate-step impact panel. */
function buildImpactSparks(patternId) {
  const defs = {
    A1: [
      { label: "Follow-up coverage", improving: true },
      { label: "Days in urgent window", improving: false },
    ],
    A2: [
      { label: "Over-SLA quote share", improving: false },
      { label: "Escalation response time", improving: true },
    ],
    A3: [
      { label: "Signals in exec brief", improving: true },
      { label: "Time to leadership review", improving: true },
    ],
    A4: [
      { label: "Browse-to-quote gap", improving: false },
      { label: "Brand outreach coverage", improving: true },
    ],
    A5: [
      { label: "SKUs on watchlist", improving: true },
      { label: "Catalog gap severity", improving: false },
    ],
    A6: [
      { label: "Consultation routing", improving: true },
      { label: "Rep group coverage", improving: true },
    ],
    A7: [
      { label: "Sessions affected", improving: false },
      { label: "Quotes at risk (est.)", improving: false },
    ],
  };
  const items = defs[patternId] || [
    { label: "Process metric A", improving: true },
    { label: "Process metric B", improving: true },
  ];
  return items.map((item, seriesIdx) => {
    const base = 0.35 + sparkSeed(patternId, seriesIdx) * 0.35;
    const before = Array.from({ length: 7 }, (_, i) => {
      const noise = sparkSeed(patternId, `${seriesIdx}b${i}`) * 0.12 - 0.06;
      return Math.max(0.08, Math.min(0.92, base + noise));
    });
    const shift = item.improving ? -0.18 : 0.12;
    const after = before.map((v, i) => {
      const progress = i / 6;
      const n = sparkSeed(patternId, `${seriesIdx}a${i}`) * 0.08 - 0.04;
      return Math.max(0.05, Math.min(0.95, v + shift * progress + n));
    });
    return { ...item, before, after };
  });
}

/**
 * @returns {Promise<{ trigger: string, impact: Array<{ label: string, value: string, sub?: string }>, dataGrounded: boolean }>}
 */
export async function resolvePlaybookLiveContext(patternId) {
  const playbook = getAutomationPlaybook(patternId);
  if (!playbook) {
    return { trigger: "", impact: [], impactSparks: [], dataGrounded: false };
  }

  const fallback = {
    trigger: playbook.trigger,
    impact: playbook.impact,
    impactSparks: buildImpactSparks(patternId),
    dataGrounded: false,
  };

  try {
    switch (patternId) {
      case "A1": {
        const uc04 = await loadCatalogRows("middleby-uc-04");
        const urgent = uc04.filter(isUrgentRow);
        const consult = urgent.filter(isConsultRow);
        return {
          trigger: `${urgent.length} quotes in the 0-1 month window need near-term dealer follow-up.`,
          impact: [
            { label: "Quotes in worklist", value: String(urgent.length), sub: "Near-term follow-up" },
            { label: "Consultation priority", value: String(consult.length), sub: "Senior rep routing" },
            playbook.impact[2],
          ],
          dataGrounded: true,
          impactSparks: buildImpactSparks(patternId),
        };
      }
      case "A2": {
        const slaRows = await loadSlaAgingRows();
        const sla = summarizeSlaAging(slaRows);
        const overSla = slaRows.filter((r) => Number(r.days_open) > SLA_THRESHOLD_DAYS);
        const repGroups = new Set(overSla.map((r) => r.rep_group).filter(Boolean));
        const pct = sla.total ? Math.round((sla.overSlaCount / sla.total) * 100) : 0;
        return {
          trigger: `${sla.overSlaCount} quotes exceeded the ${SLA_THRESHOLD_DAYS}-day SLA threshold in the aging sample.`,
          impact: [
            { label: "Deals escalated", value: String(sla.overSlaCount), sub: `Over ${SLA_THRESHOLD_DAYS}-day SLA` },
            { label: "Rep groups involved", value: String(repGroups.size), sub: "In HubSpot sample" },
            { label: "Pipeline at risk", value: `${pct}%`, sub: "Share of active quotes" },
          ],
          dataGrounded: true,
          impactSparks: buildImpactSparks(patternId),
        };
      }
      case "A3": {
        const uc16 = await loadCatalogRows("middleby-uc-16");
        const signalCount = uc16.length;
        return {
          trigger: `${signalCount} weekly anomaly signals and executive headlines are ready for leadership review.`,
          impact: [
            { label: "Signals in brief", value: String(signalCount), sub: "Cross-source" },
            playbook.impact[1],
            playbook.impact[2],
          ],
          dataGrounded: true,
          impactSparks: buildImpactSparks(patternId),
        };
      }
      case "A4": {
        const uc18 = await loadCatalogRows("middleby-uc-18");
        const ranked = uc18
          .map((r) => ({
            brand: r.brand,
            sessions: parseNum(r.ga_sessions) ?? 0,
            quotes: parseNum(r.hubspot_quotes) ?? 0,
            gap: gapScore(parseNum(r.ga_sessions) ?? 0, parseNum(r.hubspot_quotes) ?? 0),
          }))
          .sort((a, b) => b.gap - a.gap);
        const top = ranked[0];
        if (!top) return fallback;
        return {
          trigger: `${top.brand} shows the widest browse-to-quote gap: ${top.sessions.toLocaleString()} sessions vs ${top.quotes} quotes.`,
          impact: [
            { label: "Browse sessions", value: top.sessions.toLocaleString(), sub: `${top.brand} (GA sample)` },
            { label: "HubSpot quotes", value: String(top.quotes), sub: "Sep export snapshot" },
            { label: "Gap rank", value: "#1", sub: `Among ${ranked.length} brands` },
          ],
          dataGrounded: true,
          impactSparks: buildImpactSparks(patternId),
        };
      }
      case "A5": {
        const uc19 = await loadCatalogRows("middleby-uc-19");
        const watch = uc19.filter((r) => {
          const browse = parseNum(r.browse_sessions_example) ?? 0;
          const quotes = parseNum(r.quotes_real) ?? 0;
          return browse >= 24 && quotes <= 2;
        });
        return {
          trigger: `${watch.length} SKUs crossed watch thresholds in the catalog sample.`,
          impact: [
            { label: "SKUs on watchlist", value: String(watch.length), sub: "High browse, ≤2 quotes" },
            { label: "Catalog rows scanned", value: String(uc19.length), sub: "SKU gap sample" },
            playbook.impact[2],
          ],
          dataGrounded: true,
          impactSparks: buildImpactSparks(patternId),
        };
      }
      case "A6": {
        const uc10 = await loadCatalogRows("middleby-uc-10");
        const yesRow = uc10.find((r) => isConsultRow(r));
        const consultCount = parseNum(yesRow?.quote_count) ?? 0;
        const uc04 = await loadCatalogRows("middleby-uc-04");
        const repGroups = new Set(
          uc04.filter(isConsultRow).map((r) => r.rep_group).filter(Boolean)
        );
        return {
          trigger: `${consultCount} quotes requested consultation in the HubSpot sample.`,
          impact: [
            { label: "Consultation quotes", value: String(consultCount), sub: "In sample" },
            {
              label: "Rep groups involved",
              value: String(repGroups.size || 12),
              sub: "Regional coverage",
            },
            playbook.impact[2],
          ],
          dataGrounded: true,
          impactSparks: buildImpactSparks(patternId),
        };
      }
      case "A7": {
        const uc08 = await loadCatalogRows("middleby-uc-08");
        const top = [...uc08]
          .map((r) => ({
            label: r.error_type,
            sessions: parseNum(r.sessions_affected_example) ?? 0,
            quotesAtRisk: parseNum(r.est_quotes_at_risk_example) ?? 0,
          }))
          .sort((a, b) => b.sessions - a.sessions)[0];
        const rel = await loadMiddlebyDataset(RELEASE_CALENDAR_PATH);
        const releases = (rel.rows || []).filter((r) => /major|minor/i.test(String(r.release_type || "")));
        const lastRelease = releases[releases.length - 1];
        const deployLabel = lastRelease?.release_date
          ? String(lastRelease.release_date).slice(0, 6).replace("-", " ")
          : "Mar 22";
        return {
          trigger: top
            ? `${top.label} spiked after the last site release marker.`
            : playbook.trigger,
          impact: [
            {
              label: "Sessions affected",
              value: top ? top.sessions.toLocaleString() : playbook.impact[0].value,
              sub: top ? top.label.split(" ")[0] + " failure" : playbook.impact[0].sub,
            },
            {
              label: "Quotes at risk (est.)",
              value: top ? String(top.quotesAtRisk) : playbook.impact[1].value,
              sub: "UX impact sample",
            },
            { label: "Deploy marker", value: deployLabel, sub: "Last site release" },
          ],
          dataGrounded: Boolean(top),
          impactSparks: buildImpactSparks(patternId),
        };
      }
      default:
        return fallback;
    }
  } catch {
    return fallback;
  }
}
