import { useMemo, useState } from "react";
import { Plus, X } from "lucide-react";
import { MASTER_DATA_ROLES } from "../../domain/use-case-autonomy";
import { fieldNames, headerFields, lineFields } from "../../domain/extraction-templates";
import { PARAM_META, RULE_KINDS, kindOf, needsOf, normalizeRule, normalizeRules, ruleCheck, suggestRules } from "../../domain/guardrail-rules";

/**
 * Business guardrails as rules (K17): the checks suggested from the template's own fields, each
 * one a kind applied to a field with its parameters, plus "add a check" for any kind on any field.
 * Nothing here names a purchase order; the six that ship are the purchase-order preset.
 */
const input = "rounded-md border border-border bg-surface px-1.5 py-0.5 text-[11px] focus:border-primary focus:outline-none";

function ParamInputs({ rule, onChange }) {
 const entries = Object.entries(rule.params || {});
 if (!entries.length) return null;
 return (
  <span className="flex flex-wrap items-center gap-2">
   {entries.map(([k, v]) => (
    <label key={k} className="flex items-center gap-1 text-[11px] text-text-muted">{PARAM_META[k]?.label || k} <input type="number" value={v} onChange={(e) => onChange({ ...rule, params: { ...rule.params, [k]: e.target.value === "" ? "" : Number(e.target.value) } })} className={`${input} w-16`} aria-label={`${rule.label} ${k}`} />{PARAM_META[k]?.unit || ""}</label>
   ))}
  </span>
 );
}

export default function GuardrailRulesField({ spec, template, setRules }) {
 const rows = useMemo(() => normalizeRules(spec.recordGuardrails), [spec.recordGuardrails]);
 const md = spec.masterData || {};
 const suggested = useMemo(() => suggestRules(template), [template]);
 const [adding, setAdding] = useState(null); // { kind, field, line_field }
 const on = (id) => rows.find((r) => r.id === id);
 const toggle = (rule) => setRules(on(rule.id) ? rows.filter((r) => r.id !== rule.id) : [...rows, rule]);
 const update = (rule) => setRules(rows.map((r) => (r.id === rule.id ? rule : r)));
 const custom = rows.filter((r) => !suggested.some((s) => s.id === r.id));
 const names = fieldNames(template);
 const header = headerFields(template).map((f) => f.name);
 const lines = lineFields(template).map((f) => f.name);

 const row = (rule, { suggestedRow }) => {
  const active = on(rule.id);
  const needs = needsOf(rule);
  const needsMissing = needs && !md[needs];
  const shown = active || rule;
  return (
   <li key={rule.id} className={`grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-2 rounded-md border px-2.5 py-1.5 ${active ? "border-primary/40 bg-primary-light/30" : "border-border-light"}`}>
    <input type="checkbox" checked={!!active} onChange={() => (suggestedRow ? toggle(rule) : setRules(rows.filter((r) => r.id !== rule.id)))} aria-label={rule.label} />
    <div className="min-w-0">
     <span className="text-xs font-semibold text-text-primary">{shown.label}</span>
     <span className="ml-1.5 text-[11px] text-text-secondary">{ruleCheck(shown)}</span>
     {rule.field && <span className="ml-1.5 font-mono text-[10px] text-text-muted">{rule.field}{rule.line_field && rule.kind === "total_matches_lines" ? ` ↔ ${rule.line_field}` : ""}</span>}
     <span className="ml-1.5 text-[10px] text-text-muted">→ {shown.exception}</span>
     {needsMissing && active && <span className="ml-1.5 text-[11px] text-status-warning">needs {MASTER_DATA_ROLES.find((r) => r.id === needs)?.label.toLowerCase()} under master data, else skipped</span>}
    </div>
    {active ? <ParamInputs rule={active} onChange={update} /> : <span />}
   </li>
  );
 };

 const addable = RULE_KINDS.filter((k) => k.scope !== "none" || !rows.some((r) => r.kind === k.id));
 const fieldsFor = (kind) => (kind?.scope === "header" ? header : kind?.scope === "line" ? lines : names);
 const commitAdd = () => {
  const kind = kindOf(adding.kind);
  if (!kind) return;
  const rule = normalizeRule({ kind: kind.id, field: kind.scope === "none" ? null : adding.field || null, line_field: kind.id === "total_matches_lines" ? adding.line_field || lines.find((n) => /total|amount/.test(n)) || null : undefined, params: { ...kind.params } });
  if (rule && !rows.some((r) => r.id === rule.id)) setRules([...rows, rule]);
  setAdding(null);
 };

 if (!template) return <p className="text-sm text-text-muted">Pick the document template first; the checks are suggested from its fields.</p>;
 return (
  <div>
   <p className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-text-muted">Suggested for {template.label.toLowerCase()}s · from its fields</p>
   <ul className="space-y-1">{suggested.map((r) => row(r, { suggestedRow: true }))}</ul>
   {custom.length > 0 && <><p className="mb-1 mt-2 text-[11px] font-semibold uppercase tracking-wide text-text-muted">Added by you</p><ul className="space-y-1">{custom.map((r) => row(r, { suggestedRow: false }))}</ul></>}
   <div className="mt-2">
    {adding ? (
     <div className="flex flex-wrap items-center gap-2 rounded-md border border-primary/40 bg-primary-light/20 p-2 text-[11px]">
      <select value={adding.kind} onChange={(e) => setAdding({ ...adding, kind: e.target.value, field: fieldsFor(kindOf(e.target.value))[0] || "" })} className={input} aria-label="Check kind">{addable.map((k) => <option key={k.id} value={k.id}>{k.label}</option>)}</select>
      {kindOf(adding.kind)?.scope !== "none" && <select value={adding.field} onChange={(e) => setAdding({ ...adding, field: e.target.value })} className={`${input} font-mono`} aria-label="Field">{fieldsFor(kindOf(adding.kind)).map((n) => <option key={n} value={n}>{n}</option>)}</select>}
      {adding.kind === "total_matches_lines" && <><span className="text-text-muted">sum of</span><select value={adding.line_field || ""} onChange={(e) => setAdding({ ...adding, line_field: e.target.value })} className={`${input} font-mono`} aria-label="Line field">{lines.map((n) => <option key={n} value={n}>{n}</option>)}</select></>}
      <span className="text-text-muted">{kindOf(adding.kind)?.check.replace("{field}", adding.field || "field")}</span>
      <button type="button" onClick={commitAdd} className="rounded-md bg-primary px-2 py-1 font-semibold text-white">Add</button>
      <button type="button" onClick={() => setAdding(null)} className="text-text-muted hover:text-text-primary" aria-label="Cancel"><X size={12} /></button>
     </div>
    ) : (
     <button type="button" onClick={() => setAdding({ kind: "numeric_range", field: names[0] || "", line_field: "" })} className="inline-flex items-center gap-1 rounded-md border border-dashed border-border px-2.5 py-1 text-xs font-semibold text-text-secondary hover:border-primary hover:text-primary"><Plus size={11} /> Add a check</button>
    )}
   </div>
   <p className="mt-1.5 text-[11px] text-text-muted">Every check is a kind the platform evaluates, applied to a field of this template. Any failure holds the record and raises the exception named; a check whose master data is not bound is reported as skipped, never as passed.</p>
  </div>
 );
}
