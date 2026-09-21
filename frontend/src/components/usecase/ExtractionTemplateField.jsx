import { useEffect, useMemo, useRef, useState } from "react";
import { Check, FileText, Plus, Sparkles, Trash2, Upload, X } from "lucide-react";
import {
 fieldOf, headerFields, lineFields, loadTemplateCatalogue, normalizeTemplate, removeTemplateFromCatalogue,
 saveTemplateToCatalogue, slugify,
} from "../../domain/extraction-templates";
import { draftExtractionTemplate } from "../../services/use-cases-api";

/**
 * The extraction template field (K17): a catalogue the person extends, not a list in code.
 *
 * Pick a template from the catalogue (shipped or saved in this workspace), edit one, or start a
 * new one -- by hand, or drafted by the platform from the description and an example document.
 * A custom template is saved to the catalogue so tomorrow's use case can pick it, and embedded on
 * the spec so the platform gets the definition with the use case.
 */
const chip = (on) => `rounded-full border px-2.5 py-1 text-xs font-semibold ${on ? "border-primary bg-primary text-white" : "border-border-light bg-surface text-text-secondary hover:border-primary hover:text-primary"}`;
const input = "rounded-md border border-border bg-surface px-2 py-1 text-xs focus:border-primary focus:outline-none";

function FieldList({ title, fields, onChange }) {
 const [draft, setDraft] = useState("");
 const add = () => { const name = slugify(draft); if (!name || fields.some((f) => f.name === name)) return; onChange([...fields, { name, required: false, hint: "" }]); setDraft(""); };
 return (
  <div>
   <p className="text-[11px] font-semibold uppercase tracking-wide text-text-muted">{title} · {fields.length}</p>
   <ul className="mt-1 flex flex-wrap gap-1.5">
    {fields.map((f) => (
     <li key={f.name} className={`inline-flex items-center gap-1 rounded-md border px-2 py-0.5 font-mono text-[11px] ${f.required ? "border-primary/50 bg-primary-light/40 text-text-primary" : "border-border-light text-text-secondary"}`} title={f.hint || (f.required ? "required" : "optional")}>
      <button type="button" onClick={() => onChange(fields.map((x) => (x.name === f.name ? { ...x, required: !x.required } : x)))} className="hover:text-primary" aria-label={`${f.required ? "Make optional" : "Make required"}: ${f.name}`}>{f.required ? "★" : "☆"}</button>
      {f.name}
      <button type="button" onClick={() => onChange(fields.filter((x) => x.name !== f.name))} className="text-text-muted hover:text-status-critical" aria-label={`Remove ${f.name}`}><X size={10} /></button>
     </li>
    ))}
    <li className="inline-flex items-center gap-1">
     <input value={draft} onChange={(e) => setDraft(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); add(); } }} placeholder="add a field" className={`${input} w-32 font-mono`} aria-label={`Add a ${title.toLowerCase()} field`} />
     <button type="button" onClick={add} className="rounded-md border border-border-light p-1 text-text-muted hover:border-primary hover:text-primary" aria-label={`Add ${title.toLowerCase()} field`}><Plus size={11} /></button>
    </li>
   </ul>
  </div>
 );
}

export default function ExtractionTemplateField({ spec, setTemplate, description, setExampleDocument }) {
 const [catalogue, setCatalogue] = useState(() => loadTemplateCatalogue());
 const [editing, setEditing] = useState(null); // { id?, label, record, header: [{name,required,hint}], lines: [...] , source }
 const [drafting, setDrafting] = useState(false);
 const [draftNote, setDraftNote] = useState("");
 const [example, setExampleState] = useState(() => (spec.exampleDocument?.documentB64 ? spec.exampleDocument : null)); // { name, documentB64 }
 const fileRef = useRef(null);
 const topFileRef = useRef(null);
 const setExample = (value) => { setExampleState(value); setExampleDocument?.(value); };
 const chosen = useMemo(() => {
  if (spec.extractionTemplateDef && spec.extractionTemplateDef.id === spec.extractionTemplate) return normalizeTemplate(spec.extractionTemplateDef);
  return catalogue.find((t) => t.id === spec.extractionTemplate) || null;
 }, [spec.extractionTemplate, spec.extractionTemplateDef, catalogue]);

 // A document kind the reader found in the description and nothing covers: open the editor on it.
 useEffect(() => {
  if (spec.extractionTemplateDraft && !spec.extractionTemplate && !editing) startNew(spec.extractionTemplateDraft.label);
  // eslint-disable-next-line react-hooks/exhaustive-deps
 }, [spec.extractionTemplateDraft, spec.extractionTemplate]);

 const choose = (t) => { setEditing(null); setTemplate(t.id, t.source === "shipped" ? null : t); };
 const startNew = (label = "") => setEditing({ label, record: "", header: [], lines: [{ name: "line_no", required: true, hint: "" }], source: "authored" });
 const startEdit = (t) => setEditing({ id: t.id, label: t.label, record: t.record, header: headerFields(t), lines: lineFields(t), source: t.source === "shipped" ? "authored" : t.source, basedOn: t.source === "shipped" ? t.id : null });
 const keep = () => {
  const raw = { ...editing, id: editing.basedOn ? `${slugify(editing.label)}${slugify(editing.label) === editing.basedOn ? "_custom" : ""}` : editing.id || slugify(editing.label) };
  const template = saveTemplateToCatalogue({ ...raw, header: editing.header, lines: editing.lines });
  setCatalogue(loadTemplateCatalogue());
  setEditing(null);
  setTemplate(template.id, template);
 };
 const remove = (t) => { removeTemplateFromCatalogue(t.id); setCatalogue(loadTemplateCatalogue()); if (spec.extractionTemplate === t.id) setTemplate("", null); };
 const onFile = (file, { thenDraft = false } = {}) => {
  if (!file) return;
  if (file.size > 4 * 1024 * 1024) { setDraftNote("That PDF is over 4 MB; a one- or two-page sample of the format is enough."); return; }
  const reader = new FileReader();
  reader.onload = () => {
   const doc = { name: file.name, documentB64: String(reader.result).split(",")[1] || "" };
   setExample(doc);
   // A sample first (K24): the platform reads the document and proposes the fields; the
   // person edits and keeps. The editor opens on the draft, the sample stays attached.
   if (thenDraft) draftFrom(doc, file.name);
  };
  reader.readAsDataURL(file);
 };
 const draftFrom = async (doc, name) => {
  setDrafting(true); setDraftNote("");
  const label = editing?.label || chosen?.label || "";
  setEditing((e) => e || { label, record: "", header: [], lines: [], source: "drafted" });
  try {
   const out = await draftExtractionTemplate({ description: description || spec.goal || "", label: label || undefined, example: { documentB64: doc.documentB64 } });
   const t = out.template;
   setEditing((e) => ({ ...(e || {}), label: e?.label || t.label, record: t.record || e?.record || "", header: (t.header || []).map(fieldOf), lines: (t.lines || []).map(fieldOf), source: "drafted" }));
   setDraftNote(`Suggested from ${name}${out.notes ? `, the platform noted: ${out.notes}` : ""}. Edit the fields and keep.`);
  } catch (err) {
   setDraftNote(err?.status === 503 ? "The platform has no extraction model configured, so it cannot read the sample; start from a shipped kind and edit." : `Could not read the sample: ${err?.detail || err?.message || "the platform did not answer"}.`);
  } finally { setDrafting(false); }
 };
 const draft = async () => {
  setDrafting(true); setDraftNote("");
  try {
   const out = await draftExtractionTemplate({ description: description || spec.goal || "", label: editing?.label || undefined, example: example ? { documentB64: example.documentB64 } : undefined });
   const t = out.template;
   setEditing((e) => ({ ...(e || {}), label: e?.label || t.label, record: t.record || e?.record || "", header: (t.header || []).map(fieldOf), lines: (t.lines || []).map(fieldOf), source: "drafted" }));
   setDraftNote(out.notes ? `The platform noted: ${out.notes}` : "Drafted by the platform from your description" + (example ? " and the example." : "."));
  } catch (err) {
   setDraftNote(err?.status === 503 ? "The platform has no extraction model configured, so it cannot draft; add the fields by hand." : `Could not draft: ${err?.detail || err?.message || "the platform did not answer"}.`);
  } finally { setDrafting(false); }
 };
 const valid = editing && editing.label.trim() && (editing.header.length + editing.lines.length) > 0;

 return (
  <div>
   <div className="mb-2 flex flex-wrap items-center gap-2 rounded-md border border-dashed border-primary/40 bg-primary-light/20 px-2.5 py-2">
    <input ref={topFileRef} type="file" accept="application/pdf" className="hidden" onChange={(e) => { onFile(e.target.files?.[0], { thenDraft: true }); e.target.value = ""; }} />
    {example ? (
     <span className="inline-flex items-center gap-1.5 text-xs text-text-secondary"><FileText size={12} className="text-primary" /> Sample: <strong className="text-text-primary">{example.name}</strong>
      <button type="button" onClick={() => draftFrom(example, example.name)} disabled={drafting} className="ml-1 font-semibold text-primary hover:underline disabled:opacity-60">{drafting ? "Reading…" : "Propose the fields again"}</button>
      <button type="button" onClick={() => setExample(null)} className="text-text-muted hover:text-status-critical" aria-label="Remove sample"><X size={11} /></button>
     </span>
    ) : (
     <>
      <button type="button" onClick={() => topFileRef.current?.click()} disabled={drafting} className="inline-flex items-center gap-1.5 rounded-md bg-primary px-2.5 py-1.5 text-xs font-semibold text-white disabled:opacity-60"><Upload size={12} /> {drafting ? "Reading the sample…" : "Upload a sample document"}</button>
      <span className="text-[11px] text-text-muted">The platform reads it and proposes the fields; you edit and keep. It also becomes the AI step's example and the first golden case.</span>
     </>
    )}
   </div>
   <p className="mb-1 text-[11px] text-text-muted">{example ? "Or start from a shipped kind:" : "No sample yet? Start from a shipped kind and edit it:"}</p>
   <div className="flex flex-wrap items-center gap-1.5">
    {catalogue.map((t) => (
     <span key={t.id} className="inline-flex items-center">
      <button type="button" aria-pressed={spec.extractionTemplate === t.id} onClick={() => choose(t)} className={chip(spec.extractionTemplate === t.id)} title={t.source === "shipped" ? "Ships with the platform" : "Saved in this workspace"}>{t.label}{t.source !== "shipped" && <span className="ml-1 opacity-70">·</span>}</button>
     </span>
    ))}
    <button type="button" onClick={() => startNew(spec.extractionTemplateDraft?.label || "")} className="inline-flex items-center gap-1 rounded-full border border-dashed border-border px-2.5 py-1 text-xs font-semibold text-text-secondary hover:border-primary hover:text-primary"><Plus size={11} /> New document kind</button>
   </div>

   {chosen && !editing && (
    <div className="mt-2 rounded-md border border-border-light bg-surface-secondary/40 p-2.5">
     <div className="grid gap-2 text-[11px] sm:grid-cols-2">
      <div><p className="font-semibold uppercase tracking-wide text-text-muted">Header · {headerFields(chosen).length}</p><p className="mt-0.5 font-mono text-text-secondary">{headerFields(chosen).map((h) => `${h.name}${h.required ? "*" : ""}`).join(" · ")}</p></div>
      <div><p className="font-semibold uppercase tracking-wide text-text-muted">Per line · {lineFields(chosen).length}</p><p className="mt-0.5 font-mono text-text-secondary">{lineFields(chosen).map((h) => `${h.name}${h.required ? "*" : ""}`).join(" · ")}</p></div>
     </div>
     <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px] text-text-muted">
      <span>Creates {chosen.record === "order" ? "an order" : `a ${chosen.record}`}. * required. The AI step fills exactly these fields; a field not on the document is null.</span>
      <button type="button" onClick={() => startEdit(chosen)} className="ml-auto font-semibold text-primary hover:underline">{chosen.source === "shipped" ? "Copy and edit" : "Edit"}</button>
      {chosen.source !== "shipped" && <button type="button" onClick={() => remove(chosen)} className="inline-flex items-center gap-1 text-text-muted hover:text-status-critical"><Trash2 size={11} /> Remove from catalogue</button>}
     </div>
    </div>
   )}

   {editing && (
    <div className="mt-2 space-y-2.5 rounded-md border border-primary/40 bg-primary-light/20 p-3">
     <div className="flex flex-wrap items-center gap-2">
      <label className="text-[11px] font-semibold text-text-muted">Document kind <input value={editing.label} onChange={(e) => setEditing({ ...editing, label: e.target.value })} placeholder="Delivery note" className={`${input} ml-1 w-44`} /></label>
      <label className="text-[11px] font-semibold text-text-muted">Creates <input value={editing.record} onChange={(e) => setEditing({ ...editing, record: e.target.value })} placeholder="receipt" className={`${input} ml-1 w-28`} /></label>
      {editing.basedOn && <span className="text-[11px] text-text-muted">based on the shipped {editing.basedOn.replace(/_/g, " ")}</span>}
     </div>
     <div className="flex flex-wrap items-center gap-2">
      <button type="button" onClick={draft} disabled={drafting} className="inline-flex items-center gap-1.5 rounded-md bg-primary px-2.5 py-1.5 text-xs font-semibold text-white disabled:opacity-60"><Sparkles size={12} /> {drafting ? "Drafting…" : editing.header.length + editing.lines.length > 1 ? "Redraft the fields" : "Draft the fields"}</button>
      <input ref={fileRef} type="file" accept="application/pdf" className="hidden" onChange={(e) => onFile(e.target.files?.[0])} />
      {example ? (
       <span className="inline-flex items-center gap-1.5 text-xs text-text-secondary"><FileText size={12} className="text-primary" /> {example.name} <button type="button" onClick={() => setExample(null)} className="text-text-muted hover:text-status-critical" aria-label="Remove example"><X size={11} /></button></span>
      ) : (
       <button type="button" onClick={() => fileRef.current?.click()} className="inline-flex items-center gap-1.5 rounded-md border border-dashed border-border px-2.5 py-1.5 text-xs font-semibold text-text-secondary hover:border-primary hover:text-primary"><Upload size={12} /> Add an example PDF</button>
      )}
      <span className="text-[11px] text-text-muted">from your description{example ? " and the example" : ", better with an example"}</span>
     </div>
     {draftNote && <p className="text-[11px] text-text-secondary">{draftNote}</p>}
     <FieldList title="Header" fields={editing.header} onChange={(header) => setEditing({ ...editing, header })} />
     <FieldList title="Per line" fields={editing.lines} onChange={(lines) => setEditing({ ...editing, lines })} />
     <div className="flex items-center gap-2">
      <button type="button" onClick={keep} disabled={!valid} className="inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-50"><Check size={12} /> Keep and use this template</button>
      <button type="button" onClick={() => setEditing(null)} className="text-xs font-semibold text-text-muted hover:text-text-primary">Cancel</button>
      <span className="ml-auto text-[11px] text-text-muted">★ required · saved to this workspace's catalogue</span>
     </div>
    </div>
   )}
  </div>
 );
}
