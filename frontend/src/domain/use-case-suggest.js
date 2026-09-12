import { KPI_DATA_CATALOG_DATASETS } from "./kpi-data-catalog";
import { COMPARISONS, LIMIT_OPERATORS, OPERATORS, ROLLBACKS, SEVERITIES, TIMEOUT_BEHAVIOURS } from "./use-case-autonomy";
import { guardrailRule } from "./use-case-spec";

/**
 * What the assistant brings to each question, before it asks it.
 *
 * The gap pass says WHAT is missing. This says what the platform already knows about it -- and
 * everything it knows comes from three places it can point at: the KPI registry (a metric's
 * description, business area, industry benchmark), the data catalog (which table a metric lives
 * in and which columns that table carries), and the configured sources (which can be written
 * to). A suggestion is a proposal with its reason attached, drawn from those, so the person sees
 * the platform reasoning from their own data rather than reading a form aloud.
 *
 * Deterministic, like the gap pass. A model would phrase these more fluently; it would not know
 * more than this, because this is what the knowledge layers hold.
 *
 * Returns `{ lead, options, preferred }` or null:
 *   lead      -- one sentence of reasoning, said before the question
 *   options   -- [{ label, why, patch }]: what to offer, why, and the spec patch it applies
 *   preferred -- the label to mark as the suggested one
 */

const DIMENSION_FOR_COLUMN = [
 [/categor/, "Category"],
 [/\b(dc_id|store|location)\b/, "Store"],
 [/supplier|vendor/, "Supplier"],
 [/\b(sku|product)/, "Product"],
 [/channel|landing/, "Channel"],
 [/region|territory/, "Region"],
 [/segment/, "Customer segment"],
 [/brand/, "Brand"],
];

const TEAM_FOR_AREA = {
 finance: "Finance team", pricing: "Pricing team", inventory: "Replenishment team", orders: "Fulfilment team",
 support: "Customer care", workforce: "Store operations", pos: "Store operations", supply_chain: "Supply chain team",
 sales: "Sales ops", marketing: "Marketing team", customer: "Customer team",
};

/** Metrics where a rise is the problem. Everything else is bad when it falls. */
const BAD_WHEN_HIGH = /stockout|markdown|abandon|return|shrink|overtime|wait|failure|fraud|lead time|labor cost|cost/i;

/** The direction a breach usually means for a metric: above for things that are bad when high. */
export const defaultOperatorFor = (metricName) => (BAD_WHEN_HIGH.test(metricName || "") ? "gt" : "lt");

const kpiByName = (ctx, name) => (ctx.registry || []).find((k) => k.name === name) || null;
const datasetsFor = (kpi) => KPI_DATA_CATALOG_DATASETS.filter((d) => (kpi?.datasetIds || []).includes(d.id) || (d.linkedKpiNames || []).includes(kpi?.name));
const primaryKpi = (spec, ctx) => kpiByName(ctx, spec.metricIds[0]);

/** "2-5% retail avg" -> { lo: 2, hi: 5, unit: "%" }; "<10% of revenue" -> { hi: 10 }; "95-98%" -> both. */
export function parseBenchmark(text) {
 const t = String(text || "");
 const range = t.match(/(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)\s*(%|x|days?)?/);
 if (range) return { lo: parseFloat(range[1]), hi: parseFloat(range[2]), unit: range[3] === "%" ? "%" : range[3] ? ` ${range[3]}` : "" };
 const lt = t.match(/<\s*(\d+(?:\.\d+)?)\s*(%)?/);
 if (lt) return { hi: parseFloat(lt[1]), unit: lt[2] || "" };
 const gt = t.match(/>\s*(\d+(?:\.\d+)?)\s*(%)?/);
 if (gt) return { lo: parseFloat(gt[1]), unit: gt[2] || "" };
 return null;
}

const STOP = new Set(["want", "know", "when", "what", "which", "where", "with", "that", "this", "than", "them", "they", "then", "there", "about", "into", "from", "have", "been", "were", "will", "should", "could", "every", "each", "some", "more", "most", "just", "like", "tell", "show", "make", "need", "running", "going", "down", "over", "under", "rate"]);
const STEM = (w) => w.replace(/(ings?|ies|es|s|ed)$/, "");
const tokens = (t) => [...new Set(String(t || "").toLowerCase().split(/[^a-z]+/).filter((w) => w.length > 3 && !STOP.has(w)).map(STEM))];

/** Registry metrics that share words with the goal, best first. Name words count double. */
export function rankMetrics(goal, ctx, measurableOnly = false) {
 const words = tokens(goal);
 if (!words.length) return [];
 return (ctx.registry || [])
  .map((k) => {
   const name = tokens(k.name), desc = tokens(`${k.desc} ${k.subjectArea}`);
   const score = words.reduce((n, w) => n + (name.some((x) => x.startsWith(w) || w.startsWith(x)) ? 2 : 0) + (desc.some((x) => x === w) ? 1 : 0), 0);
   return { kpi: k, score };
  })
  .filter((r) => r.score > 0 && (!measurableOnly || datasetsFor(r.kpi).length))
  .sort((a, b) => b.score - a.score);
}

const short = (s, n = 90) => (String(s || "").length > n ? `${String(s).slice(0, n - 1).replace(/\s+\S*$/, "")}…` : String(s || ""));

export function suggestFor(gap, spec, ctx) {
 const kpi = primaryKpi(spec, ctx);
 const goal = String(spec.goal || "").toLowerCase();

 switch (gap.field) {
  case "metrics":
  case "metric": {
   // Ambiguous or unknown term: show each candidate with what it measures and where it comes from.
   if (gap.id !== "metrics" && gap.options) {
    const options = gap.options.slice(0, 4).map((name) => {
     const k = kpiByName(ctx, name);
     const ds = datasetsFor(k)[0];
     return { label: name, why: `${short(k?.desc, 80)}${ds ? ` · from ${ds.technicalName}` : " · no connected data yet"}`, patch: null };
    });
    // Prefer one the connected data can compute; among those, the general metric over one that
    // is already cut ("... by Category"), since the cut is a separate question.
    const computable = options.filter((o) => datasetsFor(kpiByName(ctx, o.label)).length);
    const preferred = [...computable].sort((a, b) => /\bby\b/i.test(a.label) - /\bby\b/i.test(b.label))[0] || null;
    let lead = null;
    if (gap.kind === "ambiguous") {
     if (computable.length === options.length) lead = `${options.length} KPIs in your registry answer to that: ${options.map((o) => `${o.label} (${short(kpiByName(ctx, o.label)?.desc, 60).toLowerCase()})`).join("; ")}.${preferred && /\bby\b/i.test(options.find((o) => o !== preferred)?.label || "") ? ` ${preferred.label} keeps the cut as a separate choice.` : ""}`;
     else if (computable.length) lead = `${options.length} KPIs in your registry answer to that. ${computable.map((o) => o.label).join(" and ")} ${computable.length > 1 ? "are the ones" : "is the one"} your connected data can compute.`;
     else lead = `${options.length} KPIs in your registry answer to that, and none of them can be computed from your connected data yet.`;
    }
    return { lead, options, preferred: preferred?.label || null };
   }
   // Nothing named: read the goal against the registry.
   const ranked = rankMetrics(spec.goal, ctx).slice(0, 4);
   if (!ranked.length) return null;
   const options = ranked.map(({ kpi: k }) => {
    const ds = datasetsFor(k)[0];
    return { label: k.name, why: `${short(k.desc, 80)}${ds ? ` · from ${ds.technicalName}` : " · no connected data yet"}`, patch: { metricIds: gap.field === "metric" ? [k.name] : [...new Set([...spec.metricIds, k.name])] } };
   });
   const computable = options.find((o) => datasetsFor(kpiByName(ctx, o.label)).length) || options[0];
   return {
    lead: `Reading that against your KPI registry, ${ranked.length === 1 ? "one KPI fits" : `${ranked.length} KPIs fit`}: ${ranked.map((r) => r.kpi.name).join(", ")}.${computable ? ` ${computable.label} ${datasetsFor(kpiByName(ctx, computable.label))[0] ? `is computed from ${datasetsFor(kpiByName(ctx, computable.label))[0].technicalName} today` : ""}.` : ""}`,
    options,
    preferred: computable.label,
   };
  }

  case "dimensions": {
   if (!kpi) return null;
   const ds = datasetsFor(kpi);
   const dims = [];
   for (const d of ds) for (const col of d.attributeTags || []) {
    const hit = DIMENSION_FOR_COLUMN.find(([re]) => re.test(col));
    if (hit && !dims.includes(hit[1])) dims.push(hit[1]);
   }
   if (!dims.length) return null;
   return {
    lead: `${kpi.name} is computed from ${ds.map((d) => d.technicalName).join(" and ")}, which carr${ds.length > 1 ? "y" : "ies"} ${ds.flatMap((d) => d.attributeTags || []).filter((c) => DIMENSION_FOR_COLUMN.some(([re]) => re.test(c))).join(", ")} — so ${dims.join(" and ")} ${dims.length > 1 ? "are the cuts" : "is the cut"} your data supports without a join.`,
    options: dims.map((d) => ({ label: d, why: "carried by the metric's own table", patch: { dimensions: [...new Set([...spec.dimensions, d])] } })),
    preferred: dims[0],
   };
  }

  case "comparison": {
   const moved = goal.match(/\b(down|drop(?:ped)?|fell|fall(?:en|ing)?|declin\w*|up|rose|ris\w*|increas\w*|grew|growth)\b/);
   const preferred = /\bplan|target|budget\b/.test(goal) ? "Plan or target" : /\blast year|yoy\b/.test(goal) ? "The same period last year" : "The period before";
   return {
    lead: moved
     ? `"${moved[1]}" only means something against a baseline. ${preferred === "The period before" ? "The period before is the usual read for a movement; plan or last year if that is what finance means by it." : `You said ${preferred.toLowerCase()}, so that is the baseline.`}`
     : null,
    options: COMPARISONS.map((c) => ({ label: c.label, why: c.value === "prior_period" ? "what moved since last time" : c.value === "prior_year" ? "removes seasonality" : c.value === "plan" ? "the gap finance is measured on" : "how others did", patch: { comparison: c.value } })),
    preferred,
   };
  }

  case "operator":
  case "threshold": {
   if (!kpi) return null;
   const b = parseBenchmark(kpi.industryBenchmark);
   if (!b) return null;
   const badHigh = BAD_WHEN_HIGH.test(kpi.name);
   const value = badHigh ? b.hi ?? b.lo : b.lo ?? b.hi;
   const operator = badHigh ? "gt" : "lt";
   const opLabel = OPERATORS.find((o) => o.value === operator).label;
   const label = `${opLabel} ${value}${b.unit}`;
   return {
    lead: `Your registry gives the retail benchmark for ${kpi.name} as ${kpi.industryBenchmark}. ${badHigh ? `Above ${value}${b.unit} is outside it — the usual trigger.` : `Below ${value}${b.unit} is outside it — the usual trigger.`}`,
    options: [
     { label, why: "the edge of the benchmark range", patch: { condition: { metric: kpi.name, operator, threshold: value } } },
     ...(b.lo != null && b.hi != null ? [{ label: `moves by more than ${Math.max(1, Math.round((b.hi - b.lo) / 2))}${b.unit}`, why: "a swing, regardless of level", patch: { condition: { metric: kpi.name, operator: "delta_gt", threshold: Math.max(1, Math.round((b.hi - b.lo) / 2)) } } }] : []),
    ],
    preferred: label,
   };
  }

  case "scope": {
   if (!kpi) return null;
   const cols = datasetsFor(kpi).flatMap((d) => d.attributeTags || []);
   const grains = [];
   for (const col of cols) { const hit = DIMENSION_FOR_COLUMN.find(([re]) => re.test(col)); if (hit && !grains.includes(hit[1])) grains.push(hit[1]); }
   const order = ["Store", "Category", "Region", "Brand", "Channel", "Supplier", "Customer segment", "Product"];
   grains.sort((a, b) => order.indexOf(a) - order.indexOf(b));
   const scopes = grains.map((g) => `Any ${g.toLowerCase()}`);
   const preferred = scopes[0] || "Everywhere";
   return {
    lead: scopes.length
     ? `${kpi.name} is available per ${grains.map((g) => g.toLowerCase()).join(" and ")}, so it can watch each ${grains[0].toLowerCase()} separately — a breach in one ${grains[0].toLowerCase()} shows up even when the total looks fine.`
     : `${kpi.name} is only available as a total, so it can only watch everywhere.`,
    options: ["Everywhere", ...scopes].map((s) => ({ label: s, why: s === "Everywhere" ? "one number, one check" : "one check per row of that grain", patch: { scope: s } })),
    preferred,
   };
  }

  case "severity": {
   if (!kpi) return null;
   const preferred = kpi.level === "Strategic" ? "Interrupt someone" : "Flag it";
   return {
    lead: `${kpi.name} is ${kpi.level === "Strategic" ? "a strategic" : "an operational"} KPI in your registry — ${kpi.level === "Strategic" ? "a breach is worth interrupting someone for" : "worth flagging, not paging anyone"}.`,
    options: SEVERITIES.map((s) => ({ label: s.label, why: s.value, patch: { severity: s.value } })),
    preferred,
   };
  }

  case "recipient":
  case "owner": {
   const team = kpi ? TEAM_FOR_AREA[kpi.businessAreaId] : null;
   if (!team) return null;
   const isOwner = gap.field === "owner";
   const other = isOwner ? spec.notify : spec.owner;
   const preferred = isOwner && other ? other : team;
   return {
    lead: isOwner
     ? other ? `Usually the team that receives it — ${other}.` : `${kpi.name} sits under ${kpi.businessAreaId} in your registry — ${team}?`
     : `${kpi.name} sits under ${kpi.businessAreaId} in your registry, so ${team} is who usually acts on it.`,
    options: [...new Set([preferred, team, other].filter(Boolean))].map((t) => ({ label: t, why: t === team ? `owns ${kpi.businessAreaId} metrics` : "already on this use case", patch: isOwner ? { owner: t } : { notify: t } })),
    preferred,
   };
  }

  case "candidates": {
   if (gap.id !== "candidates" || !kpi) return null;
   const ds = datasetsFor(kpi);
   const concept = ds.map((d) => (ctx.concepts || []).find((c) => c.id === d.id)).find(Boolean);
   if (!concept) return null;
   const grainCol = ds[0]?.attributeTags?.find((c) => /_id$/.test(c));
   const filterCols = (ds[0]?.attributeTags || []).filter((c) => !/_id$|_ts$|_date$/.test(c));
   return {
    lead: `${kpi.name} is computed per ${grainCol ? grainCol.replace(/_id$/, "").toUpperCase() : "row"} in ${ds[0].technicalName}, so each run would consider ${concept.name.toLowerCase()} rows${filterCols.length ? ` — filtered on ${filterCols.slice(0, 2).join(" or ")}` : ""}.`,
    options: [{ label: concept.name, why: `the grain of ${kpi.name}`, patch: { candidates: { concept: concept.name, conceptId: concept.id, filter: spec.candidates?.filter || "" } } }],
    preferred: concept.name,
   };
  }

  case "guardrails": {
   if (gap.id !== "guardrails") return null;
   // A limit protects the metric the action could damage, not the one it optimises. Offer
   // registry metrics in the same business area that carry a benchmark, floor at its low edge.
   const area = kpi?.businessAreaId;
   const own = kpi && parseBenchmark(kpi.industryBenchmark) && spec.shape === "act" ? kpi : null;
   const related = (ctx.registry || [])
    .filter((k) => k.industryBenchmark && parseBenchmark(k.industryBenchmark) && (k.businessAreaId === area || /margin/i.test(k.name)) && !spec.metricIds.includes(k.name))
    .sort((a, b) => (a.businessAreaId === area ? 0 : 1) - (b.businessAreaId === area ? 0 : 1))
    .slice(0, 3);
   if (!own && !related.length) return null;
   const limit = (k, operator, value) => {
    const b = parseBenchmark(k.industryBenchmark);
    const g = { metric: k.name, operator, threshold: value, unit: b.unit };
    g.rule = guardrailRule(g);
    return { label: g.rule, why: `benchmark ${k.industryBenchmark}`, patch: { guardrails: [...spec.guardrails, g] } };
   };
   const options = [];
   if (own) {
    const b = parseBenchmark(own.industryBenchmark);
    if (b.lo != null) options.push(limit(own, "gte", b.lo));
    if (b.hi != null) options.push(limit(own, "lte", b.hi));
   }
   for (const k of related) {
    const b = parseBenchmark(k.industryBenchmark);
    const badHigh = BAD_WHEN_HIGH.test(k.name);
    options.push(limit(k, badHigh ? "lte" : "gte", badHigh ? b.hi ?? b.lo : b.lo ?? b.hi));
   }
   return {
    lead: own
     ? `It writes ${own.name}, and your registry benchmarks it at ${own.industryBenchmark} — a floor and a ceiling on it keep every write inside that band.`
     : `A hard limit protects what the action could damage, not what it optimises. ${related[0].name} is the obvious one to fence — your registry benchmarks it at ${related[0].industryBenchmark}.`,
    options: options.slice(0, 4),
    preferred: options[0].label,
   };
  }

  case "objective": {
   if (gap.id !== "objective") return null;
   // The KPI the process is meant to move: the chosen KPI that is bad-when-high (a stockout,
   // a markdown) or the first with a benchmark; target = the good edge of its benchmark.
   const cands = spec.metricIds.map((n) => kpiByName(ctx, n)).filter((k) => k && parseBenchmark(k.industryBenchmark))
    .sort((a, b) => (BAD_WHEN_HIGH.test(a.name) ? 0 : 1) - (BAD_WHEN_HIGH.test(b.name) ? 0 : 1));
   if (!cands.length) return null;
   const options = cands.slice(0, 3).map((k) => {
    const b = parseBenchmark(k.industryBenchmark);
    const badHigh = BAD_WHEN_HIGH.test(k.name);
    const target = badHigh ? b.hi ?? b.lo : b.lo ?? b.hi;
    const direction = badHigh ? "below" : "above";
    return { label: `${k.name} ${direction} ${target}${b.unit}`, why: `benchmark ${k.industryBenchmark}`, patch: { objective: { metric: k.name, direction, target, unit: b.unit } } };
   });
   return {
    lead: `Your registry benchmarks ${cands[0].name} at ${cands[0].industryBenchmark} — the edge of that range is the usual target to tune towards.`,
    options,
    preferred: options[0].label,
   };
  }

  case "writeTarget": {
   const targets = ctx.writeTargets || [];
   if (!targets.length) return null;
   const area = kpi?.businessAreaId || "";
   const preferred = targets.find((t) => (/pric|finance|markdown/.test(area) && /pric/i.test(t)) || (/inventory|supply/.test(area) && /inventory|mrp|stock/i.test(t))) || targets[0];
   return {
    lead: `${targets.length === 1 ? "Only one configured source" : `Only ${targets.length} configured sources`} can be written to. ${preferred ? `For ${area ? `${/^[aeiou]/.test(area) ? "an" : "a"} ${area.replace(/_/g, " ")}` : "this"} action that is ${preferred}.` : ""}`,
    options: targets.map((t) => ({ label: t, why: "connected with write permission", patch: { writeback: t } })),
    preferred,
   };
  }

  case "timeoutBehaviour":
   return {
    lead: "If the approver never answers, the safe default is that nothing happens; escalating keeps it moving without removing the person.",
    options: TIMEOUT_BEHAVIOURS.map((t) => ({ label: t.label, why: t.safe ? "nothing is written" : "writes without a person", patch: { onTimeout: t.value } })),
    preferred: "Escalate to the owner",
   };

  case "perRunCap":
   return {
    lead: "The simulation will show how many records a run would touch. The change limit is what an approver would otherwise be: the most it can get wrong in one go. Start small; raise it once the simulation shows the shape of a run.",
    options: ["10 SKUs", "50 SKUs", "200 SKUs"].map((c) => ({ label: c, why: c === "50 SKUs" ? "a common starting cap" : c === "10 SKUs" ? "tightest" : "for a large catalogue", patch: { blastRadius: c } })),
    preferred: "50 SKUs",
   };

  case "rollback": {
   const target = spec.writeback || "the target";
   return {
    lead: `Every change to ${target} is stamped with the run, so the prior value can be kept per record — restoring it is the cleanest rollback.`,
    options: ROLLBACKS.map((r) => ({ label: r, why: r === ROLLBACKS[0] ? "reversible as a unit" : r === ROLLBACKS[1] ? "undo the write itself" : "a person reverses it", patch: { rollback: r } })),
    preferred: ROLLBACKS[0],
   };
  }

  default:
   return null;
 }
}

export function readingOf() {
 return null;
}

export { LIMIT_OPERATORS };
