import { useState } from "react";
import { Check, X } from "lucide-react";
import { headerFields } from "../../domain/extraction-templates";
import { normalizeRules } from "../../domain/guardrail-rules";

/**
 * Type what a golden case should produce (K19): the outcome -- created, or one of the
 * exception classes this use case can raise -- and, for a created record, the header values
 * the extraction must read. Only the fields you fill are checked; the rest are free. The
 * cheaper path stays: run the file and accept its output.
 */
const RESOLUTION_CLASSES = ["UNKNOWN_SENDER", "CUSTOMER_NOT_FOUND", "SHIP_TO_NOT_FOUND", "SHIP_TO_AMBIGUOUS", "PRODUCT_NOT_FOUND", "PRODUCT_AMBIGUOUS", "SCANNED_DOCUMENT", "NO_DOCUMENT", "RECORD_INCOMPLETE"];
const input = "rounded-md border border-border bg-surface px-2 py-1 text-xs focus:border-primary focus:outline-none";

export default function CaseExpectedEditor({ spec, template, caseRow, onSave, onCancel }) {
 const rules = normalizeRules(spec.recordGuardrails);
 const outcomes = ["created", ...new Set([...rules.map((r) => r.exception), ...RESOLUTION_CLASSES])];
 const fields = headerFields(template);
 const initial = caseRow.expected || {};
 const [outcome, setOutcome] = useState(initial.outcome || "created");
 const [values, setValues] = useState(() => Object.fromEntries(fields.map((f) => [f.name, initial.extracted?.[f.name] ?? ""])));
 const [lineCount, setLineCount] = useState(initial.line_count ?? "");
 const save = () => {
  const extracted = Object.fromEntries(Object.entries(values).filter(([, v]) => String(v).trim() !== "").map(([k, v]) => [k, Number.isNaN(Number(v)) || v === "" ? v : /^\d{4}-\d{2}-\d{2}$/.test(v) ? v : Number(v)]));
  onSave({ outcome, extracted: outcome === "created" ? extracted : {}, line_count: outcome === "created" && lineCount !== "" ? Number(lineCount) : null });
 };
 return (
  <div className="mt-2 space-y-2 rounded-lg border border-primary/40 bg-primary-light/20 p-3 text-xs">
   <div className="flex flex-wrap items-center gap-2">
    <label className="font-semibold text-text-muted">Expected outcome <select value={outcome} onChange={(e) => setOutcome(e.target.value)} className={`${input} ml-1 font-mono`}>{outcomes.map((o) => <option key={o} value={o}>{o}</option>)}</select></label>
    {outcome === "created" && <label className="font-semibold text-text-muted">lines <input type="number" min="0" value={lineCount} onChange={(e) => setLineCount(e.target.value)} placeholder="any" className={`${input} ml-1 w-16`} /></label>}
    <span className="text-text-muted">{outcome === "created" ? "Fill only the header values that must match; blank fields are not checked." : "An exception case is checked on its class alone."}</span>
   </div>
   {outcome === "created" && (
    <div className="grid gap-1.5 sm:grid-cols-2">
     {fields.map((f) => (
      <label key={f.name} className="flex items-center gap-2"><span className="w-40 shrink-0 truncate font-mono text-[11px] text-text-secondary">{f.name}{f.required ? "*" : ""}</span><input value={values[f.name]} onChange={(e) => setValues({ ...values, [f.name]: e.target.value })} placeholder="not checked" className={`${input} w-full`} /></label>
     ))}
    </div>
   )}
   <div className="flex items-center gap-2">
    <button type="button" onClick={save} className="inline-flex items-center gap-1 rounded-md bg-primary px-2.5 py-1 font-semibold text-white"><Check size={11} /> Save expected output</button>
    <button type="button" onClick={onCancel} className="inline-flex items-center gap-1 text-text-muted hover:text-text-primary"><X size={11} /> Cancel</button>
   </div>
  </div>
 );
}
