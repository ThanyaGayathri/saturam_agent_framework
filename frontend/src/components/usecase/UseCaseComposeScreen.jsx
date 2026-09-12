import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
 AlertTriangle, ArrowRight, BarChart3, Check, ChevronDown, ChevronRight, Gauge, HelpCircle, Info, Lock,
 MessageSquare, Plus, Save, Send, Sparkles, Wand2, Workflow, X,
} from "lucide-react";
import { useKpiRegistry } from "../../hooks/useKpiRegistry";
import { useUseCaseCatalog } from "../../hooks/useUseCaseCatalog";
import { useDemoProfile } from "../../context/DemoProfileContext";
import { loadConfigureWorkspace } from "../../storage/configure-workspace";
import { flattenKpiIndexRows } from "../../domain/kpi-mapping";
import { KPI_DATA_CATALOG_DATASETS } from "../../domain/kpi-data-catalog";
import { getKpiNamesForCatalogId } from "../../domain/kpi-usecase-links";
import {
 CATEGORY_LIST, CHANNELS, COMPARISONS, DIMENSIONS, FIELD_META, LIMIT_OPERATORS, OPERATORS,
 PER_RUN_CAPS, PROPOSED_CONTRACT_FIELDS, ROLLBACKS, SCHEDULES, SCOPES, SEVERITIES, SHAPES,
 TIMEOUT_BEHAVIOURS, WRITEBACK_TARGETS, categoryOf, describeShape, displayName, fieldsFor,
} from "../../domain/use-case-autonomy";
import {
 GAP_KIND, ORIGIN, applyPatch, blockingGaps, draftState, emptySpec, gapsFor, guardrailRule, proposeTitle,
 withCategory, withShape,
} from "../../domain/use-case-spec";
import { extract, optionsForGap } from "../../domain/use-case-extract";
import { loadSpecDraft, saveSpecDraft } from "../../domain/use-case-draft-spec";
import { defaultOperatorFor, suggestFor } from "../../domain/use-case-suggest";

/** Any edit to the spec invalidates an approved plan: the plan is derived from the spec, so an
 *  approval of the old one says nothing about the new one. */
const persist = (s) => saveSpecDraft({ ...s, planApprovedAt: null });

/**
 * Steps 1 and 2 of the builder.
 *
 * Step 1 is the kind of use case: four enterprise categories -- Analytics Automation, Decision
 * Intelligence, Process Automation, Process Optimization -- each mapped onto one of the backend's
 * shapes (contracts/use_case.py). The category is the label a buyer recognises; the shape stays the
 * unit of contract, compilation and governance. It is chosen, not inferred, because it decides
 * the form.
 *
 * Step 2 is THE FORM. It is the primary surface: every field is editable directly, and every empty
 * field carries the platform's suggestion inline -- from the KPI registry, the data catalog and the
 * connected systems -- so someone with clarity never needs the assistant. The assistant is a side
 * panel for the fields a person is unsure about. It works from the form's state: a deterministic
 * gap pass says what is still missing (executors/gaps.py, "consults no model"); the assistant
 * phrases the question, offers the grounded suggestion, and fills the field from whatever the
 * person says. Anything typed on the form wins over anything said.
 */

const CATEGORY_ICON = { analytics_automation: BarChart3, decision_intelligence: Wand2, process_automation: Workflow, process_optimization: Gauge };

/** Templates this screen was designed against: category + backend shape. Others read the title. */
const TEMPLATE_MAP = {
 "gallery-uc-09": ["analytics_automation", "inform"],
 "gallery-uc-01": ["analytics_automation", "alert"],
 "gallery-uc-08": ["decision_intelligence", "recommend"],
 "gallery-uc-41": ["process_optimization", "act"],
};
const categoryForTemplate = (t) =>
 TEMPLATE_MAP[t.id] || (/alert|risk|breach/i.test(t.title || "") ? ["analytics_automation", "alert"] : /optimi|recommend|markdown|reorder/i.test(t.title || "") ? ["decision_intelligence", "recommend"] : ["analytics_automation", "inform"]);

function entityName(technicalName) {
 const leaf = String(technicalName || "").split(".").pop() || "";
 return leaf.replace(/_/g, " ").replace(/\b(sku|po|gr|dc|kpi)\b/g, (m) => m.toUpperCase()).replace(/^\w/, (c) => c.toUpperCase());
}

const ORIGIN_LABEL = {
 [ORIGIN.spec]: { text: "from your description", cls: "text-primary" },
 [ORIGIN.authored]: { text: "entered", cls: "text-text-secondary" },
 [ORIGIN.assumed]: { text: "default", cls: "text-text-muted" },
 proposed: { text: "proposed", cls: "text-text-muted" },
};

const input = "w-full rounded-md border border-border bg-surface px-2.5 py-1.5 text-sm focus:border-primary focus:outline-none";
const select = "rounded-md border border-border bg-surface px-2 py-1.5 text-sm focus:border-primary focus:outline-none";
const chip = (on) => `rounded-full border px-2.5 py-1 text-xs font-semibold ${on ? "border-primary bg-primary text-white" : "border-border-light bg-surface text-text-secondary hover:border-primary hover:text-primary"}`;

function Field({ name, children, gap, origin, hint, suggestion, onAsk, onUse }) {
 const meta = FIELD_META[name] || { label: name, hint: "" };
 const o = origin && ORIGIN_LABEL[origin];
 const pref = suggestion?.options?.find((x) => x.label === suggestion.preferred);
 return (
  <div className={`rounded-lg border px-3 py-2.5 ${gap ? "border-status-warning-border bg-status-warning-light/30" : "border-border bg-surface"}`}>
   <div className="flex items-center justify-between gap-2">
    <span className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-text-muted" title={hint || meta.hint}>
     {meta.label}
     {PROPOSED_CONTRACT_FIELDS.has(name) && <span title="Not in the API contract yet (open item R-15)." className="rounded bg-surface-secondary px-1 py-px text-[9px] normal-case tracking-normal text-text-muted">proposed</span>}
    </span>
    {gap ? <span className="text-[11px] font-semibold text-status-warning">needed</span> : o ? <span className={`text-[11px] ${o.cls}`}>{o.text}</span> : null}
   </div>
   <div className="mt-1.5">{children}</div>
   {/* The platform's suggestion sits on the field itself, so a person with clarity clicks once
       and a person without it sees the reason before deciding to ask. */}
   {gap && (
    <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px]">
     {pref && (
      <span className="inline-flex flex-wrap items-center gap-1.5 text-text-secondary">
       <Sparkles size={11} className="text-primary" />
       <span>Suggested: <strong className="text-text-primary">{pref.label}</strong>{pref.why ? <span className="text-text-muted"> — {pref.why}</span> : null}</span>
       {pref.patch && <button type="button" onClick={() => onUse(pref)} className="rounded-md border border-primary/40 bg-primary-light px-2 py-0.5 text-[11px] font-semibold text-primary hover:bg-primary hover:text-white">Use</button>}
      </span>
     )}
     {onAsk && <button type="button" onClick={onAsk} className="inline-flex items-center gap-1 text-[11px] font-semibold text-text-muted hover:text-primary"><HelpCircle size={11} /> Not sure? Ask the assistant</button>}
    </div>
   )}
  </div>
 );
}

function Bubble({ role, children }) {
 return (
  <div className={`flex ${role === "user" ? "justify-end" : "justify-start"}`}>
   <div className={`max-w-[92%] rounded-2xl px-3.5 py-2.5 text-sm leading-relaxed ${role === "user" ? "rounded-br-md bg-primary-light text-text-primary" : "rounded-bl-md border border-border bg-surface text-text-primary"}`}>{children}</div>
  </div>
 );
}

function Reply({ parts }) {
 const { heard, lead, ask, remaining, done, status } = parts;
 return (
  <div className="space-y-2">
   {status && <p className="text-sm text-text-secondary">{status}</p>}
   {heard?.length > 0 && (
    <div>
     <p className="text-[11px] font-semibold uppercase tracking-wide text-text-muted">Filled in</p>
     <ul className="mt-0.5 space-y-0.5">
      {heard.map((h, i) => <li key={i} className="flex gap-1.5 text-sm"><Check size={13} className="mt-1 shrink-0 text-status-success" /><span>{h.replace(/^\w/, (c) => c.toUpperCase())}</span></li>)}
     </ul>
    </div>
   )}
   {heard && heard.length === 0 && !done && !status && <p className="text-sm text-text-secondary">I couldn't match that to anything on the form.</p>}
   {lead && <p className="text-sm text-text-secondary">{lead}</p>}
   {ask && <p className="text-sm font-semibold text-text-primary">{ask}{remaining > 0 && <span className="ml-1.5 font-normal text-text-muted">· {remaining} more after this</span>}</p>}
   {done && (
    <div>
     <p className="text-[11px] font-semibold uppercase tracking-wide text-status-success">The form is complete</p>
     <p className="mt-0.5 text-sm text-text-primary">{done}</p>
    </div>
   )}
  </div>
 );
}

/** Step 1: the kind of use case. Analytics Automation asks its trigger in place. */
function CategoryPicker({ onChoose }) {
 return (
  <div className="mx-auto w-full max-w-5xl space-y-5 px-6 py-6">
   <div>
    <p className="text-xs font-semibold uppercase tracking-wide text-text-muted">Step 1 of 6</p>
    <h1 className="text-2xl font-bold text-text-primary">What kind of use case is this?</h1>
    <p className="mt-1 max-w-2xl text-sm text-text-secondary">The kind decides the form you fill, the workflow that is generated, the readiness checks, and who may switch it on.</p>
   </div>
   <div className="grid gap-3 md:grid-cols-2">
    {CATEGORY_LIST.map((c) => {
     const Icon = CATEGORY_ICON[c.id];
     const fields = [...new Set(c.shapes.flatMap((sh) => fieldsFor({ shape: sh, category: c.id })))];
     return (
      <div key={c.id} className="flex flex-col rounded-2xl border border-border bg-surface p-4 text-left hover:border-primary">
       <button type="button" onClick={() => onChoose(c.id, c.shapes[0])} className="text-left">
        <div className="flex items-center gap-2">
         <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary-light text-primary"><Icon size={16} /></span>
         <span className="text-base font-bold text-text-primary">{c.label}</span>
         <span className="text-sm text-text-secondary">· {c.plain}</span>
        </div>
        <p className="mt-2 text-sm text-text-secondary">{c.blurb}</p>
        <p className="mt-2 text-xs text-text-secondary">
         <span className="font-semibold text-text-primary">For example</span> · {c.examples.map((e) => e.title).join(" · ")}
        </p>
        <div className="mt-3 flex flex-wrap gap-1">
         {fields.filter((f) => f !== "schedule").map((f) => <span key={f} className="rounded-md bg-surface-secondary px-1.5 py-0.5 text-[11px] font-medium text-text-secondary">{FIELD_META[f].label}</span>)}
        </div>
        <p className="mt-3 text-[11px] text-text-muted">Side effect: {c.sideEffect}.{SHAPES[c.shapes[0]].level >= 3 ? ` Switched on by someone other than the author${SHAPES[c.shapes[0]].level >= 4 ? " — an admin" : ""}.` : ""}</p>
       </button>
       <span className="mt-3 inline-flex items-center gap-1 text-xs font-semibold text-primary">Choose <ArrowRight size={12} /></span>
      </div>
     );
    })}
   </div>
  </div>
 );
}

export default function UseCaseComposeScreen() {
 const navigate = useNavigate();
 const [params] = useSearchParams();
 const { data } = useKpiRegistry();
 const { entries } = useUseCaseCatalog();
 const { profileId, storageKeys } = useDemoProfile();
 const bottomRef = useRef(null);
 const lastAsked = useRef(null);

 const registry = useMemo(() => flattenKpiIndexRows(data?.categories || []).map((r) => r.kpi), [data?.categories]);
 const registryNames = useMemo(() => registry.map((k) => k.name), [registry]);
 const measurable = useMemo(() => { const set = new Set(); for (const ds of KPI_DATA_CATALOG_DATASETS) for (const n of ds.linkedKpiNames || []) set.add(n); return set; }, []);
 const concepts = useMemo(() => KPI_DATA_CATALOG_DATASETS.map((d) => ({ id: d.id, name: entityName(d.technicalName), technicalName: d.technicalName, sourceLabel: d.sourceLabel, columns: d.attributeTags || [], kpis: d.linkedKpiNames || [] })), []);
 const workspace = useMemo(() => loadConfigureWorkspace(storageKeys.CONFIGURE_WORKSPACE, profileId), [storageKeys.CONFIGURE_WORKSPACE, profileId]);
 const writeTargets = useMemo(() => (workspace.internalSources || []).filter((s) => s.permissionLevel === "read_write").map((s) => s.name), [workspace]);
 const channels = useMemo(() => CHANNELS.map((c) => {
  const integration = c.providerId ? (workspace.externalIntegrations || []).find((e) => e.providerId === c.providerId && e.status === "active") : null;
  return { ...c, configured: !c.providerId || !!integration, target: integration?.name || null };
 }), [workspace]);
 const ctx = useMemo(() => ({ registry, registryNames, concepts, writeTargets }), [registry, registryNames, concepts, writeTargets]);

 const templateId = params.get("template");
 const [spec, setSpecState] = useState(() => {
  const draft = loadSpecDraft();
  if (!templateId) return draft;
  const t = (entries || []).find((e) => e.id === templateId);
  if (!t) return draft;
  const [category, shape] = categoryForTemplate(t);
  const metrics = getKpiNamesForCatalogId(templateId, 99);
  const base = emptySpec(t.problemSignal || t.title || "", shape, category);
  return persist(applyPatch({ ...base, title: t.title || "" }, { metricIds: shape === "alert" ? metrics.slice(0, 1) : metrics }, ORIGIN.spec));
 });
 const setSpec = (updater) => setSpecState((prev) => persist(typeof updater === "function" ? updater(prev) : updater));

 const category = categoryOf(spec);
 const shape = SHAPES[spec.shape];
 const fields = useMemo(() => (shape ? fieldsFor(spec) : []), [shape, spec.shape, spec.category]);
 const gaps = useMemo(() => gapsFor(spec, registryNames, measurable), [spec, registryNames, measurable]);
 const blocking = blockingGaps(gaps);
 const dataGaps = gaps.filter((g) => g.kind === GAP_KIND.data);
 const state = draftState(gaps);
 const filled = fields.filter((f) => !blocking.some((g) => g.field === f)).length;

 // ── Assistant state ───────────────────────────────────────────────────────────────────────
 const [assistantOpen, setAssistantOpen] = useState(false);
 const [messages, setMessages] = useState([]);
 const [draftText, setDraftText] = useState("");
 const [oneGo, setOneGo] = useState(!templateId);
 const [oneGoText, setOneGoText] = useState("");
 useEffect(() => { if (assistantOpen) bottomRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages, assistantOpen]);

 const unitOf = (name) => registry.find((k) => k.name === name)?.unit || "";

 /** Persist a change, deriving what the contract needs but nobody should type: the title, the
  *  business area from the primary KPI, the unit the threshold is in. */
 const commit = (next) => {
  if (!next.title.trim() || next.origin.title === ORIGIN.spec) next = { ...next, title: proposeTitle(next), origin: { ...next.origin, title: ORIGIN.spec } };
  const primary = registry.find((k) => k.name === next.metricIds[0]);
  if (primary && (!next.businessArea || next.origin.businessArea !== ORIGIN.authored)) next = { ...next, businessArea: primary.businessAreaId || "", origin: { ...next.origin, businessArea: ORIGIN.spec } };
  if (next.condition && primary && next.condition.unit !== (primary.unit || "")) next = { ...next, condition: { ...next.condition, unit: primary.unit || "" } };
  next = persist(next);
  setSpecState(next);
  return next;
 };

 // ── Step 1 ────────────────────────────────────────────────────────────────────────────────
 const choose = (categoryId, shapeId) => {
  const next = persist(withCategory(spec, categoryId, shapeId));
  setSpecState(next);
  setMessages([]);
  setAssistantOpen(false);
  lastAsked.current = null;
 };
 const setMode = (shapeId) => {
  if (shapeId === spec.shape) return;
  const next = persist(withShape(spec, shapeId, spec.category));
  setSpecState(next);
  lastAsked.current = null;
 };
 const changeCategory = () => setSpecState(persist({ ...spec, category: null, shape: null, shapeChosen: false, autonomy: "inform", autonomyChosen: false }));

 // ── The assistant: works from the form's state ────────────────────────────────────────────
 // The reply is assembled from deterministic parts: what was filled, what the platform knows
 // about the next open field, and the one question. It reports; it does not decide.
 const respond = (understood, nextSpec, nextGaps, { status = null, field = null } = {}) => {
  const parts = { heard: understood, lead: null, ask: null, remaining: 0, done: null, status };
  const all = blockingGaps(nextGaps);
  const gap = (field && all.find((g) => g.field === field)) || all[0];
  let suggest = null;
  if (gap) {
   suggest = suggestFor(gap, nextSpec, ctx);
   const again = lastAsked.current === gap.id && !field;
   if (suggest?.lead && !again) parts.lead = suggest.lead;
   parts.ask = again ? `Still open: ${gap.ask.replace(/^\w/, (c) => c.toLowerCase())}` : gap.ask;
   parts.remaining = all.length - 1;
   lastAsked.current = gap.id;
  } else if (nextGaps.length === 0) {
   parts.done = `${describeShape(nextSpec)} Runs ${SCHEDULES.find((x) => x.value === nextSpec.schedule)?.label.toLowerCase()}${nextSpec.assumed?.includes("schedule") ? " — the default; change Frequency on the form if that's too often" : ""}. Save & validate when you're ready.`;
  } else {
   parts.done = "The form is complete, but a KPI cannot be computed from your connected data — see the form.";
  }
  setMessages((m) => [...m, { role: "assistant", parts, gaps: gap ? [gap] : [], suggest }]);
 };

 const openAssistant = (field = null) => {
  setAssistantOpen(true);
  const all = blockingGaps(gaps);
  const status = all.length
   ? `${filled} of ${fields.length} fields filled. Still needed: ${all.map((g) => FIELD_META[g.field]?.label.toLowerCase() || g.field).filter((v, i, a) => a.indexOf(v) === i).join(", ")}.`
   : "The form is complete.";
  respond([], spec, gaps, { status, field });
 };

 const directAnswer = (text, gap) => {
  if (!gap) return null;
  switch (gap.id) {
   case "operator": case "threshold": {
    const n = parseFloat((text.match(/\d+(?:\.\d+)?/) || [])[0]);
    if (Number.isNaN(n)) return null;
    const operator = spec.condition?.operator || defaultOperatorFor(spec.metricIds[0]);
    return { patch: { condition: { metric: spec.metricIds[0] || null, ...(spec.condition || {}), operator, threshold: n } }, said: `triggers when it ${OPERATORS.find((o) => o.value === operator)?.label} ${n}` };
   }
   case "objective-target": { const n = parseFloat((text.match(/\d+(?:\.\d+)?/) || [])[0]); return Number.isNaN(n) ? null : { patch: { objective: { ...spec.objective, target: n } }, said: `target: ${n}` }; }
   case "recipient": return { notify: text };
   case "approver": case "approver-sod": return { approver: text };
   case "owner": return { owner: text };
   case "candidates-filter": return { candidates: { ...spec.candidates, filter: text } };
   case "perRunCap": { const n = (text.match(/\d+(?:\.\d+)?/) || [])[0]; return n ? { patch: { blastRadius: /%/.test(text) ? `${n}% of catalogue` : `${n} SKUs` }, said: `change limit: ${/%/.test(text) ? `${n}% of catalogue` : `${n} SKUs`} per run` } : { blastRadius: text }; }
   case "goal": return { goal: text };
   default: return null;
  }
 };

 /** Free text -> form. Used by the assistant and by "describe it in one go". */
 const modeFromText = (msg) => {
  if (spec.category !== "analytics_automation") return spec.shape;
  const t = msg.toLowerCase();
  const alertCue = /\b(alert|notify|tell (me|the|us)|let me know|when [^.]* (goes above|falls below|crosses|exceeds|drops below|is above|is below)|threshold|breach)\b/.test(t);
  const reportCue = /\b(explain|why|compare|report|breakdown|analy[sz]e|drivers?)\b/.test(t);
  if (alertCue && !reportCue) return "alert";
  if (reportCue && !alertCue) return "inform";
  return spec.shape;
 };

 const ingest = (msg, { viaAssistant = true } = {}) => {
  const mode = modeFromText(msg);
  const base = mode === spec.shape ? spec : withShape(spec, mode, spec.category);
  let { patch, understood } = extract(msg, base, ctx);
  if (mode !== spec.shape) understood = [`output: ${category.trigger.find((t) => t.shape === mode)?.label.toLowerCase()}`, ...understood];
  if (!Object.keys(patch).length && viaAssistant) {
   const direct = directAnswer(msg, blocking[0]);
   if (direct) { patch = direct.patch || direct; understood = [direct.said || `${FIELD_META[blocking[0].field]?.label || blocking[0].field}: ${msg}`]; }
  }
  const goal = base.goal ? base.goal : msg;
  const next = commit(applyPatch({ ...base, goal }, patch, ORIGIN.spec));
  return { next, understood };
 };

 const send = (text) => {
  const msg = String(text || draftText).trim();
  if (!msg) return;
  setDraftText("");
  setMessages((m) => [...m, { role: "user", text: msg }]);
  const { next, understood } = ingest(msg);
  respond(understood, next, gapsFor(next, registryNames, measurable));
 };

 const [filledNote, setFilledNote] = useState(null);
 const fillFromDescription = () => {
  const msg = oneGoText.trim();
  if (!msg) return;
  const { next, understood } = ingest(msg, { viaAssistant: false });
  const open = blockingGaps(gapsFor(next, registryNames, measurable));
  setOneGo(false);
  // The form is the surface: say what was filled here, and let the empty fields' own
  // suggestions (or the assistant, if the person opens it) take care of the rest.
  setFilledNote({
   filled: understood,
   open: open.map((g) => FIELD_META[g.field]?.label || g.field).filter((v, i, a) => a.indexOf(v) === i),
  });
  if (assistantOpen) { setMessages((m) => [...m, { role: "user", text: msg }]); respond(understood, next, gapsFor(next, registryNames, measurable)); }
 };

 const answer = (gap, value, suggested = null) => {
  setMessages((m) => (assistantOpen ? [...m, { role: "user", text: value }] : m));
  let patch = {};
  if (suggested?.patch) patch = suggested.patch;
  else switch (gap.field) {
   case "metrics": case "metric":
    patch = { metricIds: gap.field === "metric" ? [value] : [...new Set([...spec.metricIds, value])], metricTerms: spec.metricTerms.filter((t) => !gap.id.endsWith(t)) };
    break;
   case "dimensions": patch = { dimensions: spec.dimensions.includes(value) ? spec.dimensions : [...spec.dimensions, value] }; break;
   case "comparison": patch = { comparison: COMPARISONS.find((c) => c.label === value)?.value || spec.comparison }; break;
   case "operator": patch = { condition: { ...(spec.condition || { metric: spec.metricIds[0] || null, threshold: null }), operator: OPERATORS.find((o) => o.label === value)?.value || "gt" } }; break;
   case "severity": patch = { severity: SEVERITIES.find((s) => s.label === value)?.value || spec.severity }; break;
   case "scope": patch = { scope: value }; break;
   case "candidates": { const c = concepts.find((x) => x.name === value); patch = { candidates: { concept: c?.name || value, conceptId: c?.id || null, filter: spec.candidates?.filter || "" } }; break; }
   case "objective": patch = { objective: { metric: value, direction: defaultOperatorFor(value) === "gt" ? "below" : "above", target: null, unit: unitOf(value) } }; break;
   case "guardrails": patch = { guardrails: spec.guardrails.map((g) => (g === gap.guardrail ? { ...g, metric: value, rule: guardrailRule({ ...g, metric: value }) } : g)) }; break;
   case "writeTarget": patch = { writeback: value }; break;
   case "timeoutBehaviour": patch = { onTimeout: TIMEOUT_BEHAVIOURS.find((t) => t.label === value)?.value || spec.onTimeout }; break;
   case "perRunCap": patch = { blastRadius: value }; break;
   case "rollback": patch = { rollback: value }; break;
   case "recipient": patch = { notify: value }; break;
   default: patch = { [FIELD_META[gap.field]?.key || gap.field]: value };
  }
  const next = commit(applyPatch(spec, patch, ORIGIN.spec));
  if (assistantOpen) respond([`${FIELD_META[gap.field]?.label || gap.field}: ${value}`], next, gapsFor(next, registryNames, measurable));
 };

 // ── Typing on the form ────────────────────────────────────────────────────────────────────
 const setField = (field, value) => setSpec((s) => {
  const next = applyPatch(s, { [FIELD_META[field]?.key || field]: value }, ORIGIN.authored);
  const primary = registry.find((k) => k.name === next.metricIds[0]);
  return primary && next.origin.businessArea !== ORIGIN.authored ? { ...next, businessArea: primary.businessAreaId || "", origin: { ...next.origin, businessArea: ORIGIN.spec } } : next;
 });
 const setCondition = (part, value) => setSpec((s) => applyPatch(s, { condition: { ...(s.condition || { metric: s.metricIds[0] || null, operator: "gt", threshold: null }), unit: unitOf(s.metricIds[0]), [part]: value } }, ORIGIN.authored));
 const setObjective = (part, value) => setSpec((s) => applyPatch(s, { objective: { ...(s.objective || { metric: s.metricIds[0] || null, direction: "below", target: null, unit: "" }), ...(part === "metric" ? { unit: unitOf(value) } : {}), [part]: value } }, ORIGIN.authored));
 const setGuardrail = (i, part, value) => setSpec((s) => applyPatch(s, { guardrails: s.guardrails.map((g, j) => (j === i ? { ...g, [part]: value } : g)).map((g) => ({ ...g, rule: guardrailRule(g) })) }, ORIGIN.authored));
 const addGuardrail = () => setSpec((s) => { const g = { metric: s.metricIds[0] || "", operator: "gte", threshold: null, unit: "" }; return applyPatch(s, { guardrails: [...s.guardrails, { ...g, rule: guardrailRule(g) }] }, ORIGIN.authored); });
 const removeGuardrail = (i) => setSpec((s) => applyPatch(s, { guardrails: s.guardrails.filter((_, j) => j !== i) }, ORIGIN.authored));
 const gapFor = (field) => blocking.find((g) => g.field === field);
 const originOf = (field) => {
  if (spec.origin?.[field]) return spec.origin[field];
  if (["schedule", "severity", "timeoutBehaviour"].includes(field) && spec.assumed?.includes(field)) return ORIGIN.assumed;
  return null;
 };
 const suggestionFor = (field) => { const g = gapFor(field); return g ? suggestFor(g, spec, ctx) : null; };
 const useSuggestion = (field, opt) => { const g = gapFor(field); if (g) answer(g, opt.label, opt); };

 if (!category || !shape) return <CategoryPicker onChoose={choose} />;
 const CategoryIcon = CATEGORY_ICON[category.id];
 const targets = writeTargets.length ? writeTargets : WRITEBACK_TARGETS;
 const example = category.examples.find((e) => e.shape === spec.shape) || category.examples[0];

 const fieldProps = (f) => ({ gap: gapFor(f), origin: originOf(f), suggestion: suggestionFor(f), onAsk: () => openAssistant(f), onUse: (opt) => useSuggestion(f, opt) });

 const renderField = (f) => {
  switch (f) {
   case "metrics": case "metric":
    return (
     <Field key={f} name={f} {...fieldProps(f)}>
      <div className="flex flex-wrap items-center gap-1.5">
       {spec.metricIds.map((m) => (
        <span key={m} className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-semibold ${measurable.has(m) ? "bg-primary-light text-primary" : "bg-status-critical-light text-status-critical"}`}>
         {m}{!measurable.has(m) && <AlertTriangle size={11} />}
         <button type="button" aria-label={`Remove ${m}`} onClick={() => setField(f, spec.metricIds.filter((x) => x !== m))}><X size={11} /></button>
        </span>
       ))}
       {spec.metricTerms.map((t) => <span key={t} className="inline-flex items-center gap-1 rounded-full border border-dashed border-status-warning-border px-2.5 py-1 text-xs font-semibold text-status-warning"><HelpCircle size={11} /> "{t}" — which?</span>)}
       {(f === "metrics" || spec.metricIds.length === 0) && (
        <select value="" onChange={(e) => e.target.value && setField(f, f === "metric" ? [e.target.value] : [...new Set([...spec.metricIds, e.target.value])])} className={`${select} max-w-[14rem]`}>
         <option value="">{f === "metric" ? "Choose a KPI…" : "Add a KPI…"}</option>
         {registryNames.filter((n) => !spec.metricIds.includes(n)).map((n) => <option key={n} value={n}>{n}</option>)}
        </select>
       )}
      </div>
     </Field>
    );
   case "dimensions":
    return <Field key={f} name={f} {...fieldProps(f)}><div className="flex flex-wrap gap-1.5">{DIMENSIONS.map((d) => { const on = spec.dimensions.includes(d); return <button key={d} type="button" aria-pressed={on} onClick={() => setField(f, on ? spec.dimensions.filter((x) => x !== d) : [...spec.dimensions, d])} className={chip(on)}>{d}</button>; })}</div></Field>;
   case "comparison":
    return <Field key={f} name={f} {...fieldProps(f)}><select value={spec.comparison || ""} onChange={(e) => setField(f, e.target.value || null)} className={`${select} w-full`}><option value="">Choose…</option>{COMPARISONS.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}</select></Field>;
   case "operator":
    return <Field key={f} name={f} {...fieldProps(f)}><div className="flex flex-wrap gap-1.5">{OPERATORS.map((o) => <button key={o.value} type="button" aria-pressed={spec.condition?.operator === o.value} onClick={() => setCondition("operator", o.value)} className={chip(spec.condition?.operator === o.value)}>{o.label}</button>)}</div></Field>;
   case "threshold":
    return (
     <Field key={f} name={f} {...fieldProps(f)}>
      <div className="flex items-center gap-2">
       <input type="number" value={spec.condition?.threshold ?? ""} onChange={(e) => setCondition("threshold", e.target.value === "" ? null : Number(e.target.value))} placeholder="5" className={`${input} w-28`} />
       <span className="text-xs text-text-muted">{spec.condition?.unit || unitOf(spec.metricIds[0]) ? <strong className="text-text-secondary">{spec.condition?.unit || unitOf(spec.metricIds[0])}</strong> : null} {spec.metricIds[0] ? `for ${spec.metricIds[0]}` : "for the KPI above"}</span>
      </div>
     </Field>
    );
   case "scope":
    return <Field key={f} name={f} {...fieldProps(f)}><div className="flex flex-wrap gap-1.5">{SCOPES.map((s) => <button key={s} type="button" aria-pressed={spec.scope === s} onClick={() => setField(f, s)} className={chip(spec.scope === s)}>{s}</button>)}</div></Field>;
   case "severity":
    return <Field key={f} name={f} {...fieldProps(f)}><div className="flex flex-wrap gap-1.5">{SEVERITIES.map((s) => <button key={s.value} type="button" aria-pressed={spec.severity === s.value} onClick={() => setField(f, s.value)} className={chip(spec.severity === s.value)}>{s.label}</button>)}</div></Field>;
   case "recipient":
    return (
     <Field key={f} name={f} {...fieldProps(f)}>
      <input value={spec.notify} onChange={(e) => setField(f, e.target.value)} placeholder="A person or a team" className={input} />
      <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
       {channels.map((c) => {
        const on = (spec.notifyChannels || ["in_app"]).includes(c.value);
        return (
         <button key={c.value} type="button" aria-pressed={on} disabled={c.always || !c.configured} title={c.always ? "Always. The result lives here; other channels link to it." : c.configured ? (c.target || c.label) : `${c.label} is not connected — add it under Configure → External`} onClick={() => setSpec((s) => applyPatch(s, { notifyChannels: on ? (s.notifyChannels || []).filter((x) => x !== c.value) : [...(s.notifyChannels || ["in_app"]), c.value] }, ORIGIN.authored))} className={`${chip(on)} ${c.always ? "cursor-default opacity-90" : ""} ${!c.configured ? "cursor-not-allowed border-dashed opacity-50" : ""}`}>
          {c.label}{c.target && on ? <span className="ml-1 font-normal opacity-80">· {c.target.split(/\s*[-–]\s*/).pop()}</span> : null}{!c.configured && <span className="ml-1 font-normal">· not connected</span>}
         </button>
        );
       })}
      </div>
      <p className="mt-1 text-[11px] text-text-muted">Lands in the app. Other channels get a link to it, so the numbers stay with their provenance.</p>
     </Field>
    );
   case "candidates":
    return (
     <Field key={f} name={f} {...fieldProps(f)}>
      <div className="grid gap-1.5 sm:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
       <select value={spec.candidates?.conceptId || ""} onChange={(e) => { const c = concepts.find((x) => x.id === e.target.value); setField(f, c ? { concept: c.name, conceptId: c.id, filter: spec.candidates?.filter || "" } : null); }} className={select}>
        <option value="">Business object…</option>
        {concepts.map((c) => <option key={c.id} value={c.id}>{c.name} · {c.sourceLabel}</option>)}
       </select>
       <input value={spec.candidates?.filter || ""} disabled={!spec.candidates?.concept} onChange={(e) => setField(f, { ...spec.candidates, filter: e.target.value })} placeholder="where… e.g. more than 8 weeks of cover" className={`${input} disabled:opacity-50`} />
      </div>
      {spec.candidates?.conceptId && <p className="mt-1.5 font-mono text-[11px] text-text-muted">{concepts.find((c) => c.id === spec.candidates.conceptId)?.technicalName}{spec.candidates.filter ? ` WHERE ${spec.candidates.filter}` : ""} — reviewed before it becomes a filter</p>}
     </Field>
    );
   case "objective":
    return (
     <Field key={f} name={f} {...fieldProps(f)}>
      <div className="grid gap-1.5 sm:grid-cols-[minmax(0,2fr)_auto_6rem]">
       <select value={spec.objective?.metric || ""} onChange={(e) => setObjective("metric", e.target.value)} className={select}>
        <option value="">KPI to move…</option>
        {[...new Set([...spec.metricIds, ...registryNames])].map((n) => <option key={n} value={n}>{n}</option>)}
       </select>
       <div className="flex gap-1">{["below", "above", "to"].map((d) => <button key={d} type="button" aria-pressed={spec.objective?.direction === d} onClick={() => setObjective("direction", d)} className={chip(spec.objective?.direction === d)}>{d}</button>)}</div>
       <div className="flex items-center gap-1"><input type="number" value={spec.objective?.target ?? ""} onChange={(e) => setObjective("target", e.target.value === "" ? null : Number(e.target.value))} placeholder="5" className={input} /><span className="text-xs text-text-muted">{spec.objective?.unit || unitOf(spec.objective?.metric)}</span></div>
      </div>
      <p className="mt-1 text-[11px] text-text-muted">Goes into the AI step's brief: every recommendation is chosen to move this KPI. The guardrails below say what it may not damage doing so.</p>
     </Field>
    );
   case "guardrails":
    return (
     <Field key={f} name={f} {...fieldProps(f)}>
      <ul className="space-y-1.5">
       {spec.guardrails.map((g, i) => (
        <li key={i} className="grid grid-cols-[minmax(0,2fr)_minmax(0,2fr)_5rem_3rem_auto] items-center gap-1.5">
         <select value={g.metric || ""} onChange={(e) => setGuardrail(i, "metric", e.target.value)} className={`${select} ${!g.metric || !registryNames.includes(g.metric) ? "border-status-warning-border" : ""}`}>
          <option value="">KPI…</option>
          {g.metric && !registryNames.includes(g.metric) && <option value={g.metric}>"{g.metric}" — not in registry</option>}
          {[...new Set([...spec.metricIds, ...registryNames])].map((n) => <option key={n} value={n}>{n}</option>)}
         </select>
         <select value={g.operator} onChange={(e) => setGuardrail(i, "operator", e.target.value)} className={select}>{LIMIT_OPERATORS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}</select>
         <input type="number" value={g.threshold ?? ""} onChange={(e) => setGuardrail(i, "threshold", e.target.value === "" ? null : Number(e.target.value))} className={input} />
         <input value={g.unit || ""} onChange={(e) => setGuardrail(i, "unit", e.target.value)} placeholder="%" className={input} />
         <button type="button" aria-label="Remove guardrail" onClick={() => removeGuardrail(i)} className="text-text-muted hover:text-status-critical"><X size={13} /></button>
        </li>
       ))}
      </ul>
      <button type="button" onClick={addGuardrail} className="mt-1.5 inline-flex items-center gap-1 text-xs font-semibold text-primary hover:underline"><Plus size={12} /> Add a guardrail</button>
     </Field>
    );
   case "approver":
    return <Field key={f} name={f} {...fieldProps(f)}><input value={spec.approver} onChange={(e) => setField(f, e.target.value)} placeholder={`Someone other than ${spec.author}`} className={input} /></Field>;
   case "writeTarget":
    return (
     <Field key={f} name={f} {...fieldProps(f)}>
      <select value={spec.writeback} onChange={(e) => setField(f, e.target.value)} className={`${select} w-full`}>
       <option value="">Choose a connected system with write permission…</option>
       {targets.map((w) => <option key={w} value={w}>{w}</option>)}
      </select>
      {!writeTargets.length && <p className="mt-1 text-[11px] text-status-warning">No connected system has write permission. Add one under Configure → Data sources.</p>}
     </Field>
    );
   case "timeoutBehaviour":
    return <Field key={f} name={f} {...fieldProps(f)}><div className="flex flex-wrap gap-1.5">{TIMEOUT_BEHAVIOURS.map((t) => <button key={t.value} type="button" aria-pressed={spec.onTimeout === t.value} onClick={() => setField(f, t.value)} className={`${chip(spec.onTimeout === t.value)} ${!t.safe && spec.onTimeout === t.value ? "!border-status-critical !bg-status-critical" : ""}`}>{t.label}</button>)}</div></Field>;
   case "perRunCap":
    return (
     <Field key={f} name={f} {...fieldProps(f)}>
      <div className="flex flex-wrap items-center gap-1.5">
       {PER_RUN_CAPS.map((c) => <button key={c} type="button" aria-pressed={spec.blastRadius === c} onClick={() => setField(f, c)} className={chip(spec.blastRadius === c)}>{c}</button>)}
       <input value={PER_RUN_CAPS.includes(spec.blastRadius) ? "" : spec.blastRadius || ""} onChange={(e) => setField(f, e.target.value || null)} placeholder="or type one" className={`${input} w-32`} />
      </div>
     </Field>
    );
   case "rollback":
    return <Field key={f} name={f} {...fieldProps(f)}><div className="flex flex-wrap gap-1.5">{ROLLBACKS.map((r) => <button key={r} type="button" aria-pressed={spec.rollback === r} onClick={() => setField(f, r)} className={chip(spec.rollback === r)}>{r}</button>)}</div></Field>;
   case "schedule":
    return <Field key={f} name={f} origin={originOf(f)}><select value={spec.schedule} onChange={(e) => setField(f, e.target.value)} className={`${select} w-full`}>{SCHEDULES.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}</select></Field>;
   default:
    return null;
  }
 };

 const stillNeeded = blocking.length;

 return (
  <div className={`mx-auto w-full px-6 py-6 ${assistantOpen ? "grid max-w-[1400px] gap-5 lg:grid-cols-[minmax(0,1fr)_400px]" : "max-w-4xl"}`}>
   {/* ── The form ─────────────────────────────────────────────────────── */}
   <section className="space-y-3">
    <div className="flex flex-wrap items-start justify-between gap-3">
     <div>
      <p className="text-xs font-semibold uppercase tracking-wide text-text-muted">Step 2 of 6</p>
      <h1 className="text-2xl font-bold text-text-primary">Define it</h1>
      <p className="mt-1 flex flex-wrap items-center gap-2 text-sm text-text-secondary">
       <span className="inline-flex items-center gap-1.5 rounded-full border border-border bg-surface px-2.5 py-0.5 text-xs font-semibold text-text-primary"><CategoryIcon size={12} className="text-primary" /> {displayName(spec)}</span>
       <button type="button" onClick={changeCategory} className="text-xs font-semibold text-text-muted hover:text-primary">Change</button>
      </p>
     </div>
     <div className="flex items-center gap-2">
      <span className={`rounded-full px-3 py-1 text-xs font-semibold ${state === "ready" ? "bg-status-success-light text-status-success" : state === "blocked_on_data" ? "bg-status-critical-light text-status-critical" : "bg-status-warning-light text-status-warning"}`}>
       {state === "ready" ? "Complete" : state === "blocked_on_data" ? "Blocked on data" : `${stillNeeded} still needed`}
      </span>
      {!assistantOpen && (
       <button type="button" onClick={() => openAssistant()} className="inline-flex items-center gap-1.5 rounded-lg border border-primary/40 bg-primary-light px-3 py-1.5 text-xs font-semibold text-primary hover:bg-primary hover:text-white">
        <MessageSquare size={13} /> Ask the assistant
       </button>
      )}
     </div>
    </div>

    {/* Optional: say it in one go and let the form fill; otherwise fill the form directly. */}
    <div className="rounded-xl border border-dashed border-border bg-surface-secondary/60">
     <button type="button" onClick={() => setOneGo((v) => !v)} className="flex w-full items-center justify-between px-3.5 py-2 text-left">
      <span className="flex items-center gap-1.5 text-xs font-semibold text-text-secondary"><Sparkles size={12} className="text-primary" /> Describe it in one go — optional</span>
      {oneGo ? <ChevronDown size={14} className="text-text-muted" /> : <ChevronRight size={14} className="text-text-muted" />}
     </button>
     {oneGo && (
      <div className="space-y-2 px-3.5 pb-3">
       <textarea rows={2} value={oneGoText} onChange={(e) => setOneGoText(e.target.value)} placeholder={example ? `e.g. ${example.prompt}` : "Say what you want, as you would to a colleague."} className="w-full resize-none rounded-lg border border-border bg-surface px-3 py-2 text-sm focus:border-primary focus:outline-none" />
       <div className="flex flex-wrap items-center gap-2">
        <button type="button" disabled={!oneGoText.trim()} onClick={fillFromDescription} className="rounded-lg bg-primary px-3 py-1.5 text-xs font-semibold text-white hover:bg-primary-hover disabled:opacity-40">Fill the form from this</button>
        {example && <button type="button" onClick={() => setOneGoText(example.prompt)} className="text-xs font-semibold text-text-muted hover:text-primary">Use the {example.title} example</button>}
        <span className="text-[11px] text-text-muted">Whatever it recognises is filled in; the rest stays for you or the assistant.</span>
       </div>
      </div>
     )}
     {filledNote && !oneGo && (
      <div className="flex flex-wrap items-start justify-between gap-2 border-t border-dashed border-border px-3.5 py-2 text-xs">
       <div className="min-w-0">
        <span className="font-semibold text-status-success"><Check size={12} className="mr-1 inline" />Filled {filledNote.filled.length} field{filledNote.filled.length === 1 ? "" : "s"}</span>
        <span className="text-text-secondary"> — {filledNote.filled.map((h) => h.replace(/^\w/, (c) => c.toUpperCase())).join(" · ")}</span>
        {filledNote.open.length > 0 && <span className="text-text-secondary">. Still needed below: <strong className="text-text-primary">{filledNote.open.join(", ")}</strong>.</span>}
       </div>
       <button type="button" onClick={() => setFilledNote(null)} aria-label="Dismiss" className="text-text-muted hover:text-text-primary"><X size={13} /></button>
      </div>
     )}
    </div>

    <div className="rounded-xl border border-border bg-surface p-3">
     <p className="text-[11px] font-semibold uppercase tracking-wide text-text-muted">What it will do</p>
     <p className="mt-1 text-sm text-text-primary">{describeShape(spec)}</p>
     {shape.level >= 3 && <p className="mt-1.5 flex items-start gap-1.5 text-xs text-text-secondary"><Info size={13} className="mt-0.5 shrink-0 text-text-muted" />Because it makes changes, someone other than you switches it on{shape.level >= 4 ? " — an admin, since nobody approves each change" : ""}.</p>}
    </div>

    <div className="grid gap-3 sm:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
     <Field name="goal" gap={gapFor("goal")} origin={originOf("goal")} hint="What this exists to do, in your words. Free text; it drives nothing." onAsk={() => openAssistant("goal")}>
      <textarea rows={2} value={spec.goal} onChange={(e) => setField("goal", e.target.value)} placeholder="e.g. Why is margin down, and where?" className={`${input} resize-none`} />
     </Field>
     <Field name="title" origin={spec.origin?.title === ORIGIN.authored ? ORIGIN.authored : spec.title ? "proposed" : null} hint="How it appears in the catalogue. Proposed from the KPIs; overwrite it any time.">
      <input value={spec.title} onChange={(e) => setField("title", e.target.value)} placeholder="Proposed once there is a KPI" className={input} />
     </Field>
    </div>

    {category.trigger && (
     <Field name="mode" origin={null}>
      <div className="grid gap-2 sm:grid-cols-2">
       {category.trigger.map((t) => (
        <button key={t.shape} type="button" aria-pressed={spec.shape === t.shape} onClick={() => setMode(t.shape)} className={`rounded-lg border px-3 py-2 text-left ${spec.shape === t.shape ? "border-primary bg-primary-light/50" : "border-border bg-surface hover:border-primary"}`}>
         <span className="block text-sm font-semibold text-text-primary">{t.label}</span>
         <span className="block text-xs text-text-muted">{t.hint}</span>
        </button>
       ))}
      </div>
     </Field>
    )}

    {fields.filter((f) => f !== "schedule").map(renderField)}

    <div className="grid gap-3 sm:grid-cols-2">
     <Field name="owner" gap={gapFor("owner")} origin={originOf("owner")} hint="Accountable once it is live." suggestion={suggestionFor("owner")} onAsk={() => openAssistant("owner")} onUse={(opt) => useSuggestion("owner", opt)}>
      <input value={spec.owner} onChange={(e) => setField("owner", e.target.value)} placeholder="Name or team" className={input} />
     </Field>
     {renderField("schedule")}
    </div>

    {dataGaps.map((g) => (
     <div key={g.id} className="flex flex-wrap items-start gap-2 rounded-xl border border-status-critical-border bg-status-critical-light px-3.5 py-2.5">
      <AlertTriangle size={15} className="mt-0.5 text-status-critical" />
      <div className="min-w-0 flex-1">
       <p className="text-sm font-semibold text-text-primary">{g.ask}</p>
       <div className="mt-1.5 flex gap-2">
        <button type="button" className="rounded-md bg-status-critical px-2.5 py-1 text-xs font-semibold text-white">Add to data request</button>
        <button type="button" onClick={() => setField("metrics", spec.metricIds.filter((m) => m !== g.metric))} className="rounded-md border border-border-light bg-surface px-2.5 py-1 text-xs font-semibold text-text-secondary">Drop this KPI</button>
       </div>
      </div>
     </div>
    ))}

    <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border pt-3">
     <button type="button" onClick={() => navigate("/use-cases/gallery")} className="inline-flex items-center gap-1.5 rounded-lg border border-border bg-surface px-3 py-2 text-sm font-semibold text-text-secondary hover:bg-surface-secondary">
      <Save size={14} /> Save as draft
      {dataGaps.length > 0 && <span className="rounded-full bg-status-critical-light px-1.5 py-0.5 text-[10px] font-semibold text-status-critical">blocked on data</span>}
     </button>
     <button type="button" disabled={blocking.length > 0} onClick={() => navigate("/use-cases/builder/preflight")} className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-white hover:bg-primary-hover disabled:cursor-not-allowed disabled:opacity-40">
      <Lock size={14} /> Save & validate <ArrowRight size={14} />
     </button>
    </div>
   </section>

   {/* ── The assistant ────────────────────────────────────────────────── */}
   {assistantOpen && (
    <aside className="flex min-h-[60vh] flex-col rounded-2xl border border-border bg-surface-secondary lg:sticky lg:top-4 lg:h-[calc(100vh-6.5rem)] lg:min-h-0 lg:self-start">
     <div className="flex items-center justify-between gap-2 border-b border-border px-4 py-3">
      <div>
       <p className="flex items-center gap-1.5 text-sm font-bold text-text-primary"><MessageSquare size={14} className="text-primary" /> Assistant</p>
       <p className="text-[11px] text-text-muted">Fills the form from what you say. Anything you enter on the form wins.</p>
      </div>
      <button type="button" onClick={() => setAssistantOpen(false)} aria-label="Close the assistant" className="text-text-muted hover:text-text-primary"><X size={16} /></button>
     </div>
     <div className="flex-1 space-y-3 overflow-y-auto px-4 py-4">
      {messages.map((m, i) => (
       <div key={i} className="space-y-2">
        <Bubble role={m.role}>{m.parts ? <Reply parts={m.parts} /> : m.text}</Bubble>
        {m.gaps?.length > 0 && i === messages.length - 1 && m.gaps.map((g) => {
         const sug = m.suggest?.options?.length ? m.suggest : null;
         const opts = sug ? sug.options.map((o) => o.label) : optionsForGap(g, ctx);
         if (!opts?.length) return null;
         const shown = opts.slice(0, 6);
         const rest = sug ? (optionsForGap(g, ctx) || []).filter((o) => !opts.includes(o)).slice(0, 6 - shown.length) : [];
         return (
          <div key={g.id} className="space-y-1 pl-1">
           <div className="flex flex-wrap items-center gap-1.5">
            {shown.map((o) => { const so = sug?.options.find((x) => x.label === o); const pref = sug && sug.preferred === o; return <button key={o} type="button" title={so?.why} onClick={() => answer(g, o, so)} className={`rounded-full border px-2.5 py-1 text-xs font-semibold ${pref ? "border-primary bg-primary-light text-primary" : "border-border-light bg-surface text-text-secondary hover:border-primary hover:text-primary"}`}>{pref && <Sparkles size={10} className="mr-1 inline" />}{o}</button>; })}
            {rest.map((o) => <button key={o} type="button" onClick={() => answer(g, o)} className="rounded-full border border-dashed border-border-light bg-surface px-2.5 py-1 text-xs font-semibold text-text-muted hover:border-primary hover:text-primary">{o}</button>)}
           </div>
           {sug?.preferred && sug.options.find((x) => x.label === sug.preferred)?.why && <p className="text-[11px] text-text-muted"><Sparkles size={10} className="mr-1 inline" />suggested: {sug.preferred} — {sug.options.find((x) => x.label === sug.preferred).why}</p>}
          </div>
         );
        })}
       </div>
      ))}
      <div ref={bottomRef} />
     </div>
     <form onSubmit={(e) => { e.preventDefault(); send(); }} className="flex items-end gap-2 border-t border-border bg-surface p-3">
      <textarea rows={2} value={draftText} onChange={(e) => setDraftText(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }} placeholder="Answer, or say it in your words…" className="min-h-[44px] flex-1 resize-none rounded-lg border border-border bg-surface px-3 py-2 text-sm focus:border-primary focus:outline-none" />
      <button type="submit" disabled={!draftText.trim()} className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary text-white hover:bg-primary-hover disabled:opacity-40" aria-label="Send"><Send size={16} /></button>
     </form>
    </aside>
   )}
  </div>
 );
}
