import {
 COMPARISONS, DIMENSIONS, FIELD_META, OPERATORS, PER_RUN_CAPS, ROLLBACKS, SCOPES, SEVERITIES,
 TIMEOUT_BEHAVIOURS, WRITEBACK_TARGETS,
} from "./use-case-autonomy";
import { guardrailRule } from "./use-case-spec";
import { fieldsFor } from "./use-case-autonomy";

/**
 * Turns a message into a spec patch, for the shape already chosen.
 *
 * This is a deterministic stand-in for the extraction model (R9's `spec_extractor`, once it takes
 * the shape as input), and it keeps the property that matters: every value it writes is drawn
 * from a closed set. It never invents a metric name, a dimension, an operator or a concept -- it
 * recognises ones that exist. A value it cannot match to the list goes into `metricTerms`, and
 * the gap pass turns that into a question.
 *
 * Because the shape is chosen before the conversation, an action verb is no longer a question
 * about autonomy. "Propose markdowns" inside a Recommend is just the goal; inside an Inform it is
 * ignored, because an Inform has no field it could fill. The same sentence yields a different
 * patch per shape, and that is the point: the shape decides what a phrase can mean.
 *
 * Returns `{ patch, understood }` -- the fields it set, and short human phrasings of each so the
 * assistant can say what it heard. Saying it back is how a person catches a wrong guess before it
 * becomes a wrong spec.
 */

const METRIC_HINTS = [
 "stockout", "margin", "revenue", "turnover", "conversion", "abandonment", "fill rate",
 "lead time", "nps", "net promoter", "on-time", "days of stock", "sla", "safety stock",
 "closed-won", "closed won", "quote", "rfq", "sell-through", "sell through", "markdown", "traffic", "bounce",
];

/** Concept cues: which semantic-layer entity a phrase points at. The ids are data-catalog dataset
 *  ids; the compose screen resolves them to the entity the semantic layer shows. */
const CONCEPT_CUES = [
 [/\b(skus?|items?|products?|stock|lines?)\b/, /sku_id|product_id/],
 [/\b(categor(?:y|ies))\b/, /category/],
 [/\b(suppliers?|vendors?)\b/, /supplier/],
 [/\b(orders?)\b/, /order/],
 [/\b(stores?|locations?)\b/, /store|dc_id|location/],
];

/** The concept a noun points at: any dataset carrying that grain column, preferring one the
 *  spec's metrics are computed from, so "SKUs" in a markdown use case means the sell-through
 *  table rather than the inventory snapshot. */
function conceptFor(noun, spec, concepts) {
 const cue = CONCEPT_CUES.find(([re]) => re.test(noun));
 if (!cue) return null;
 const fits = concepts.filter((c) => (c.columns || []).some((col) => cue[1].test(col)));
 return fits.find((c) => (c.kpis || []).some((k) => spec.metricIds.includes(k))) || fits[0] || null;
}

/** A captured name, without the full stop that ended the sentence. */
function name(raw) {
 const clean = String(raw || "").replace(/[.,;:!?]+$/, "").trim();
 return clean.replace(/\b\w/g, (c) => c.toUpperCase());
}

function num(re, t) {
 const m = t.match(re);
 return m ? parseFloat(m[1]) : null;
}

/** Registry names and hint terms in a piece of text. One registry match resolves; zero or two
 *  or more becomes a term the gap pass asks about. */
const hasWord = (t, w) => new RegExp(`(^|[^a-z])${w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}(s|es)?($|[^a-z])`).test(t);

function metricsIn(t, registryNames, already = []) {
 const ids = new Set(already);
 const terms = new Set();
 for (const n of registryNames) if (hasWord(t, n.toLowerCase())) ids.add(n);
 for (const hint of METRIC_HINTS) {
  // Whole words only: "slack" is not "sla", "market" is not "margin".
  if (!hasWord(t, hint) || [...ids].some((n) => n.toLowerCase().includes(hint))) continue;
  const matches = registryNames.filter((n) => n.toLowerCase().includes(hint));
  if (matches.length === 1) ids.add(matches[0]);
  else terms.add(hint);
 }
 return { ids: [...ids], terms: [...terms] };
}

/**
 * @param text     the message
 * @param spec     the current spec (its shape decides which fields may be filled)
 * @param ctx      { registryNames, concepts: [{id, name}], writeTargets: [string] }
 */
export function extract(text, spec, ctx) {
 const registryNames = ctx.registryNames || [];
 const concepts = ctx.concepts || [];
 const writeTargets = ctx.writeTargets?.length ? ctx.writeTargets : WRITEBACK_TARGETS;
 let t = String(text || "").toLowerCase();
 const patch = {};
 const understood = [];
 const need = new Set(fieldsFor(spec));
 if (!need.size) return { patch, understood };

 // The objective: "to bring stockout rate below 5%", "improve sell-through to 85%". Cut out before
 // the limit and metric passes so its number is not read as a guardrail or a threshold.
 if (need.has("objective") && !spec.objective?.metric) {
  const m = t.match(/\b(?:to |so that |aiming to |in order to )?(bring|get|reduce|lower|cut|raise|lift|improve|increase|push|drive|move)\s+([a-z][a-z -]{2,40}?)\s+(to|below|under|above|over|down to|up to)\s+(\d+(?:\.\d+)?)\s*(%|days?|x)?/);
  if (m) {
   const { ids } = metricsIn(m[2], registryNames);
   if (ids[0]) {
    const dir = /below|under|down/.test(m[3]) ? "below" : /above|over|up/.test(m[3]) ? "above" : "to";
    patch.objective = { metric: ids[0], direction: dir, target: parseFloat(m[4]), unit: m[5] === "%" ? "%" : m[5] ? ` ${m[5]}` : "" };
    understood.push(`objective: ${ids[0]} ${dir} ${m[4]}${patch.objective.unit}`);
    t = t.replace(m[0], ` ${m[2]} `);
   }
  }
 }

 // Guardrail clauses are cut out first, so "never below 15% margin" is not also read as a metric
 // to compute or as an alert threshold.
 const limitClauses = need.has("guardrails")
  ? [...t.matchAll(/\b(?:never|not|don't|do not|no more than|at least|at most|keep)\b[^.;]*?\b(below|under|above|over|beyond|more than|less than)\s+(\d+(?:\.\d+)?)\s*(%|weeks?|days?|units?)?[^.;]*/g)]
  : [];
 let rest = t;
 for (const m of limitClauses) rest = rest.replace(m[0], " ");

 // Candidates: a concept plus its filter. The concept comes from the semantic layer; the filter is
 // the clause after it, kept as text for the person to review before it becomes SQL.
 if (need.has("candidates") && !spec.candidates?.concept) {
  const all = [...rest.matchAll(/\b(?:for|on|across|review|consider|reorder|re-?price|adjust|tune|update|reassign|re-?plan)\s+(?:the\s+|all\s+|every\s+)?((?:[a-z-]+\s+){0,3}?(?:skus?|items?|products?|stock|categor(?:y|ies)|suppliers?|vendors?|orders?|stores?))\b\s*((?:with|where|whose|that|having|below|above|over|under)\b[^.;,]*)?/g)];
  const m = all.find((x) => x[2]) || all[0];
  if (m) {
   const concept = conceptFor(m[1], { ...spec, metricIds: [...spec.metricIds, ...metricsIn(rest, registryNames).ids] }, concepts);
   if (concept) {
    const filter = (m[2] || "").replace(/^(with|where|whose|that|having)\s+/, "").replace(/,?\s+using\b.*$/, "").trim();
    patch.candidates = { concept: concept.name, conceptId: concept.id, filter };
    understood.push(`scope: ${concept.name}${filter ? ` where ${filter}` : ""}`);
    if (filter) rest = rest.replace(filter, " ");
   }
  }
 }

 // Metrics.
 if (need.has("metrics") || need.has("metric")) {
  const { ids, terms } = metricsIn(rest, registryNames, spec.metricIds);
  const chosen = need.has("metric") ? ids.slice(0, 1) : ids;
  if (chosen.length !== spec.metricIds.length) { patch.metricIds = chosen; understood.push(`${need.has("metric") ? "KPI" : "KPIs"}: ${chosen.join(", ")}`); }
  const allTerms = [...new Set([...(spec.metricTerms || []), ...terms])];
  if (allTerms.length !== (spec.metricTerms || []).length) {
   patch.metricTerms = allTerms;
   understood.push(`you mean a KPI by "${terms.join('", "')}" — I'll check which`);
  }
 }
 const metricIds = patch.metricIds || spec.metricIds;

 // Dimensions.
 if (need.has("dimensions")) {
  const dims = new Set(spec.dimensions);
  for (const d of DIMENSIONS) if (new RegExp(`\\b${d.toLowerCase()}s?\\b`).test(rest)) dims.add(d);
  if (dims.size !== spec.dimensions.length) { patch.dimensions = [...dims]; understood.push(`analysed by ${[...dims].join(", ")}`); }
 }

 // Comparison.
 // Not guarded on the field being empty: "actually, compare it to plan instead" is a correction.
 if (need.has("comparison")) {
  if (/\b(last year|year over year|yoy|same period last year)\b/.test(rest)) { patch.comparison = "prior_year"; understood.push("compared with last year"); }
  else if (/\b(last (month|week|quarter|period)|prior period|previous|month over month|vs\.? last)\b/.test(rest)) { patch.comparison = "prior_period"; understood.push("compared with the period before"); }
  else if (/\b(plan|target|budget)\b/.test(rest)) { patch.comparison = "plan"; understood.push("compared with plan"); }
  else if (/\b(peers?|peer average)\b/.test(rest)) { patch.comparison = "peer"; understood.push("compared with the peer average"); }
 }

 // Condition: direction + number. Only an alert has one; for anything else "below 15%" is a limit.
 if (need.has("operator") || need.has("threshold")) {
  const above = num(/\b(?:above|over|exceeds?|more than)\s+(\d+(?:\.\d+)?)\s*%?/, rest);
  const below = num(/\b(?:below|under|less than|falls? to)\s+(\d+(?:\.\d+)?)\s*%?/, rest);
  const delta = num(/\b(?:moves?|changes?|shifts?) by (?:more than )?(\d+(?:\.\d+)?)\s*%?/, rest);
  if (above != null || below != null || delta != null) {
   const operator = delta != null ? "delta_gt" : above != null ? "gt" : "lt";
   const threshold = delta ?? above ?? below;
   patch.condition = { metric: metricIds[0] || null, operator, threshold };
   understood.push(`triggers when it ${OPERATORS.find((o) => o.value === operator).label} ${threshold}`);
  }
 }

 // Scope.
 if (need.has("scope")) {
  const m = rest.match(/\b(?:any|every|each|per|by)\s+(store|categor(?:y|ies)|brand|channel|region)s?\b/);
  if (m) {
   const noun = m[1].startsWith("categor") ? "category" : m[1];
   const scope = SCOPES.find((s) => s.toLowerCase() === `any ${noun}`);
   if (scope) { patch.scope = scope; understood.push(`scope: ${scope.toLowerCase()}`); }
  } else if (/\b(everywhere|overall|across the board)\b/.test(rest)) { patch.scope = "Everywhere"; understood.push("watching everywhere"); }
 }

 // Severity.
 if (need.has("severity")) {
  if (/\b(critical|urgent|immediately|page)\b/.test(rest)) { patch.severity = "critical"; understood.push("severity: interrupt someone"); }
  else if (/\b(just log|log it|low priority|for the record)\b/.test(rest)) { patch.severity = "info"; understood.push("severity: log it"); }
 }

 // Schedule -- the trigger.
 if (/\b(every hour|hourly)\b/.test(rest)) patch.schedule = "hourly";
 else if (/\b(every (morning|day)|daily|each morning)\b/.test(rest)) patch.schedule = "daily";
 else if (/\b(every (monday|week)|weekly|mondays?)\b/.test(rest)) patch.schedule = "weekly";
 else if (/\b(monthly|every month|month end|first of the month)\b/.test(rest)) patch.schedule = "monthly";
 else if (/\b(on demand|when (i|someone) asks?|manually)\b/.test(rest)) patch.schedule = "manual";
 if (patch.schedule) understood.push(`runs ${patch.schedule}`);

 // Guardrails: each limit clause becomes { metric, operator, threshold }. The metric is the
 // registry name in the clause; a clause naming none is kept with the metric blank and asked about.
 if (need.has("guardrails") && limitClauses.length) {
  const rails = [...spec.guardrails];
  for (const m of limitClauses) {
   const clause = m[0];
   const { ids, terms } = metricsIn(clause, registryNames);
   // A limit that names no metric outright ("never set coverage below 14 days") is usually about
   // one of the metrics already chosen; a word of that metric's name in the clause is enough.
   const byWord = metricIds.find((id) => id.toLowerCase().split(/\W+/).some((w) => w.length > 3 && new RegExp(`\\b${w}\\b`).test(clause)));
   const metric = ids[0] || byWord || terms[0] || null;
   // "never above N" is a ceiling; "keep it above N" / "at least N" is a floor. The prefix decides.
   const negated = /^(never|not|don't|do not|no more than|at most)\b/.test(clause);
   const upward = /^(above|over|beyond|more than)$/.test(m[1]);
   const ceiling = negated ? upward : !upward;
   const g = { metric, operator: ceiling ? "lte" : "gte", threshold: parseFloat(m[2]), unit: m[3] === "%" ? "%" : m[3] ? ` ${m[3]}` : "" };
   g.rule = guardrailRule(g);
   // "never below 14 or above 60" is two limits on the same metric.
   const second = clause.match(/\b(?:or|and)\s+(?:above|over|more than)\s+(\d+(?:\.\d+)?)\s*(%|weeks?|days?|units?)?/);
   rails.push(g);
   if (second && !ceiling && negated) {
    const g2 = { metric, operator: "lte", threshold: parseFloat(second[1]), unit: second[2] === "%" ? "%" : second[2] ? ` ${second[2]}` : g.unit };
    g2.rule = guardrailRule(g2);
    rails.push(g2);
   }
  }
  patch.guardrails = rails;
  understood.push(`guardrail${rails.length - spec.guardrails.length > 1 ? "s" : ""}: ${rails.slice(spec.guardrails.length).map((g) => g.rule).join("; ")}`);
 }

 // Write target: only a configured source with write permission.
 if (need.has("writeTarget") && !spec.writeback) {
  for (const w of writeTargets) {
   const [system, object] = w.toLowerCase().split(/\s*[-–]\s*/);
   const names = [system, object, object?.replace(/_/g, " ")].filter(Boolean);
   if (names.some((n) => n.length > 3 && rest.includes(n))) { patch.writeback = w; understood.push(`target system: ${w}`); break; }
  }
 }

 // Timeout behaviour.
 if (need.has("timeoutBehaviour")) {
  if (/\b(escalate)\b/.test(rest)) patch.onTimeout = "escalate";
  else if (/\b(remind)\b/.test(rest)) patch.onTimeout = "remind";
  else if (/\b(go ahead|proceed anyway)\b/.test(rest)) patch.onTimeout = "proceed";
  if (patch.onTimeout) understood.push(`if no response: ${TIMEOUT_BEHAVIOURS.find((x) => x.value === patch.onTimeout).label.toLowerCase()}`);
 }

 // Per-run cap.
 if (need.has("perRunCap") && !spec.blastRadius) {
  const m = rest.match(/\b(?:at most|no more than|up to|max(?:imum)?(?: of)?|cap(?:ped)?(?: it| this| the run| each run)? (?:at|to))\s+(\d+(?:\.\d+)?)\s*(%|skus?|items?|products?|lines?|rows?)?/);
  if (m) {
   const n = m[1];
   const cap = m[2] === "%" ? `${n}% of catalogue` : `${n} SKUs`;
   patch.blastRadius = PER_RUN_CAPS.includes(cap) ? cap : cap;
   understood.push(`change limit: ${patch.blastRadius} per run`);
  }
 }

 // Rollback.
 if (need.has("rollback") && !spec.rollback) {
  if (/\b(keep|store|remember) the (previous|old|prior) value|restore/.test(rest)) patch.rollback = ROLLBACKS[0];
  else if (/\b(revert|undo|roll ?back)\b/.test(rest)) patch.rollback = ROLLBACKS[1];
  else if (/\bticket\b/.test(rest)) patch.rollback = ROLLBACKS[2];
  if (patch.rollback) understood.push(`rollback: ${patch.rollback.toLowerCase()}`);
 }

 // Owner / approver / recipient -- "owned by X", "approved by X", "tell X", "send it to X".
 const owner = rest.match(/\b(?:owned by|owner is|owner:|owner will be)\s+([a-z][a-z .'-]{1,40})/) || rest.match(/(?:^|[.,;]\s*)(?:and\s+)?([a-z][a-z .'-]{1,30}?)\s+(?:owns (?:it|this)|is the owner|will own (?:it|this))/);
 if (owner) { patch.owner = name(owner[1]); understood.push(`owner: ${patch.owner}`); }
 if (need.has("approver")) {
  const appr = rest.match(/\b(?:approved by|approver is|approver:|approver will be)\s+([a-z][a-z .'-]{1,40})/)
   || rest.match(/(?:^|[.,;]\s*)(?:and\s+|let\s+|with\s+)?([a-z][a-z .'-]{1,30}?)\s+(?:approves|signs? (?:it |them |each one )?off|to approve|has to approve|must approve)/);
  if (appr) { patch.approver = name(appr[1]); understood.push(`approved by ${patch.approver}`); }
 }
 if (need.has("recipient")) {
  const chans = new Set(spec.notifyChannels || ["in_app"]);
  if (/\bslack\b/.test(rest)) chans.add("slack");
  if (/\b(ms )?teams\b/.test(rest)) chans.add("teams");
  if (/\b(e-?mail)\b/.test(rest)) chans.add("email");
  if (chans.size !== (spec.notifyChannels || ["in_app"]).length) { patch.notifyChannels = [...chans]; understood.push(`via ${[...chans].filter((c) => c !== "in_app").join(" and ")}`); }
  const rec = rest.match(/\b(?:notify|tell|email|send (?:it|them|this) to|deliver (?:it )?to|report to|alert)\s+(the\s+)?([a-z][a-z .'-]{1,30}?)(?:\s+(?:when|if|every|each|on|by|via|in)\b|[.,;]|$)/);
  if (rec && !/^(me|us|myself)\b/.test(rec[2].trim())) { patch.notify = name(rec[2]); understood.push(`recipients: ${patch.notify}`); }
 }

 return { patch, understood };
}

/** Quick-reply options for a gap, so answering is a click rather than typing a name that might
 *  not exist. */
export function optionsForGap(gap, ctx = {}) {
 if (gap.options) return gap.options;
 switch (gap.field) {
  case "dimensions": return DIMENSIONS;
  case "comparison": return COMPARISONS.map((c) => c.label);
  case "operator": return OPERATORS.map((o) => o.label);
  case "severity": return SEVERITIES.map((s) => s.label);
  case "scope": return SCOPES;
  case "candidates": return gap.id === "candidates" ? (ctx.concepts || []).map((c) => c.name) : null;
  case "writeTarget": return ctx.writeTargets?.length ? ctx.writeTargets : WRITEBACK_TARGETS;
  case "timeoutBehaviour": return TIMEOUT_BEHAVIOURS.map((x) => x.label);
  case "perRunCap": return PER_RUN_CAPS;
  case "rollback": return ROLLBACKS;
  default: return null;
 }
}

export { FIELD_META };
