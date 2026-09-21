import { useEffect, useMemo, useState } from "react";
import { Check, Plus, Sparkles, Trash2, X } from "lucide-react";
import { headerFields, lineFields } from "../../domain/extraction-templates";
import {
 LITERAL, TRANSFORMS, describeSource, describeSources, loadMappingCatalogue, normalizeMapping, normalizeRow, removeMappingFromCatalogue,
 saveMappingToCatalogue, sourceGroups, staleSources, uncoveredRequired,
} from "../../domain/record-mappings";
import { roleFeeds } from "../../domain/use-case-roles";
import { describeTargetObject, fetchTargetObjects, suggestRecordMapping, suggestTargetObjects } from "../../services/use-cases-api";

/**
 * The target objects field (K18): a mapping catalogue the person extends, over the org's
 * live objects. Pick a shipped or saved mapping, copy and edit it, or build a new one: choose
 * the parent object (and a per-line child) from the org, ask the platform to suggest rows from
 * names, then edit each row's source. Required target fields the mapping does not cover are
 * called out from the live description.
 */
const chip = (on) => `rounded-full border px-2.5 py-1 text-xs font-semibold ${on ? "border-primary bg-primary text-white" : "border-border-light bg-surface text-text-secondary hover:border-primary hover:text-primary"}`;
const input = "rounded-md border border-border bg-surface px-1.5 py-0.5 text-[11px] focus:border-primary focus:outline-none";

const KIND_TONE = { document: "bg-primary-light/40 text-primary", line: "bg-primary-light/40 text-primary", resolved: "bg-status-success-light text-status-success", run: "bg-surface-secondary text-text-secondary", literal: "bg-surface-secondary text-text-secondary", unknown: "bg-status-warning-light text-status-warning" };
const KIND_WORD = { document: "document", line: "line", resolved: "resolved", run: "run", literal: "fixed", unknown: "missing" };

/** One source, named for what it is: a field of the document, a value resolved in the target, a run value. */
function SourceTag({ source, template, feeds }) {
 const d = describeSource(source, template, feeds);
 return <span className={`inline-flex items-baseline gap-1 rounded px-1.5 py-0.5 ${KIND_TONE[d.kind]}`} title={d.detail}><span className="font-mono">{d.label}</span><span className="text-[9px] uppercase tracking-wide opacity-80">{KIND_WORD[d.kind]}</span></span>;
}

/**
 * A row's sources as a chain of selects -- the value, then what to use if it is empty -- each
 * option grouped by where it comes from (the document, a resolved role, the run) and a fixed
 * value typed in place. Nothing to type from memory.
 */
function SourceChain({ value, groups, onChange }) {
 const sources = value && value.length ? value : [""];
 const set = (i, next) => { const out = [...sources]; out[i] = next; onChange(out.filter(Boolean)); };
 const known = new Set(groups.flatMap((g) => g.options.map((o) => o.value)));
 return (
  <div className="flex flex-wrap items-center gap-1">
   {sources.map((source, i) => {
    const literal = source.startsWith(LITERAL);
    const selected = literal ? LITERAL : source;
    return (
     <span key={i} className="inline-flex items-center gap-1">
      {i > 0 && <span className="text-[10px] uppercase tracking-wide text-text-muted">else</span>}
      <select value={selected} onChange={(e) => set(i, e.target.value === LITERAL ? LITERAL : e.target.value)} className={`${input} max-w-[15rem] font-mono`} aria-label={i === 0 ? "Source" : `Fallback ${i}`}>
       <option value="">{i === 0 ? "choose a value…" : "(none)"}</option>
       {groups.map((g) => <optgroup key={g.group} label={g.group}>{g.options.map((o) => <option key={o.value} value={o.value} title={o.detail}>{o.label}</option>)}</optgroup>)}
       <option value={LITERAL}>a fixed value…</option>
       {source && !literal && !known.has(source) && <option value={source}>{source} (not in this template)</option>}
      </select>
      {literal && <input value={source.slice(1)} onChange={(e) => set(i, `${LITERAL}${e.target.value}`)} placeholder="type the value" className={`${input} w-28`} aria-label={`Fixed value ${i + 1}`} />}
     </span>
    );
   })}
   {sources[sources.length - 1] && sources.length < 3 && <button type="button" onClick={() => onChange([...sources, ""])} className="rounded border border-dashed border-border px-1.5 py-0.5 text-[10px] text-text-muted hover:border-primary hover:text-primary">+ if empty</button>}
  </div>
 );
}

/** One mapped field in the summary: the target, then the value in words with where it comes from. */
function MappedRow({ object, row, template, feeds, stale, perLine }) {
 return (
  <tr className={`border-t border-border-light align-baseline ${stale ? "text-status-warning" : ""}`} title={describeSources(row.source, template, feeds)}>
   <td className="py-1 pr-3 font-mono text-text-primary">{object}.{row.target}{row.required ? <span className="text-status-critical">*</span> : null}</td>
   <td className="py-1 pr-3"><span className="inline-flex flex-wrap items-baseline gap-1">{row.source.map((src, i) => <span key={i} className="inline-flex items-baseline gap-1">{i > 0 && <span className="text-[9px] uppercase text-text-muted">else</span>}<SourceTag source={src} template={template} feeds={feeds} /></span>)}</span></td>
   <td className="py-1 text-[9px] uppercase tracking-wide text-text-muted">{perLine ? "per line" : ""}</td>
  </tr>
 );
}

function RowTable({ title, rows, described, groups, onChange, parentField }) {
 const byName = Object.fromEntries((described?.fields || []).map((f) => [f.name, f]));
 const uncovered = uncoveredRequired(described, rows).filter((n) => n !== parentField);
 const [adding, setAdding] = useState("");
 const add = () => { const t = adding.trim(); if (!t || rows.some((r) => r.target === t)) return; onChange([...rows, normalizeRow({ target: t, source: [], required: !!byName[t]?.required })]); setAdding(""); };
 return (
  <div>
   <p className="text-[11px] font-semibold uppercase tracking-wide text-text-muted">{title} · {rows.length} rows</p>
   <table className="mt-1 w-full text-[11px]">
    <thead><tr className="text-left text-text-muted"><th className="py-0.5 pr-2 font-semibold">Target field</th><th className="py-0.5 pr-2 font-semibold">← value</th><th className="py-0.5 pr-2 font-semibold">as</th><th /></tr></thead>
    <tbody>
     {rows.map((r) => (
      <tr key={r.target} className="border-t border-border-light align-middle">
       <td className="py-1 pr-2 font-mono text-text-primary" title={byName[r.target]?.label}>{r.target}{(r.required || byName[r.target]?.required) && <span className="text-status-critical">*</span>}{byName[r.target] && <span className="ml-1 text-[10px] text-text-muted">{byName[r.target].type}</span>}</td>
       <td className="py-1 pr-2"><SourceChain value={r.source} groups={groups} onChange={(source) => onChange(rows.map((x) => (x.target === r.target ? { ...x, source } : x)))} /></td>
       <td className="py-1 pr-2"><select value={r.transform || ""} onChange={(e) => onChange(rows.map((x) => (x.target === r.target ? { ...x, transform: e.target.value || null } : x)))} className={input} aria-label={`${r.target} transform`}>{[...TRANSFORMS, ...(r.transform && !TRANSFORMS.some((t) => t.value === r.transform) ? [{ value: r.transform, label: r.transform.replace("text:", "text, ") + (r.transform.startsWith("text:") ? " chars" : "") }] : [])].map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}</select></td>
       <td className="py-1"><button type="button" onClick={() => onChange(rows.filter((x) => x.target !== r.target))} className="text-text-muted hover:text-status-critical" aria-label={`Remove ${r.target}`}><X size={11} /></button></td>
      </tr>
     ))}
    </tbody>
   </table>
   <div className="mt-1 flex flex-wrap items-center gap-2">
    {described ? (
     <select value={adding} onChange={(e) => setAdding(e.target.value)} className={`${input} font-mono`} aria-label={`Add a ${title} row`}><option value="">add a field…</option>{(described.fields || []).filter((f) => !rows.some((r) => r.target === f.name) && f.name !== parentField).map((f) => <option key={f.name} value={f.name}>{f.name}{f.required ? " *" : ""} - {f.label}</option>)}</select>
    ) : (
     <input value={adding} onChange={(e) => setAdding(e.target.value)} placeholder="target field name" className={`${input} w-40 font-mono`} aria-label={`Add a ${title} row`} />
    )}
    <button type="button" onClick={add} className="rounded-md border border-border-light p-1 text-text-muted hover:border-primary hover:text-primary" aria-label={`Add ${title} row`}><Plus size={11} /></button>
    {uncovered.length > 0 && <span className="text-[11px] text-status-warning">Required by the org, not mapped: {uncovered.join(", ")}</span>}
   </div>
  </div>
 );
}

export default function RecordMappingField({ spec, template, target, setMapping, description }) {
 const [catalogue, setCatalogue] = useState(() => loadMappingCatalogue());
 const [editing, setEditing] = useState(null);
 const [objects, setObjects] = useState(null); // live list or null when the API is unreachable
 const [described, setDescribed] = useState({}); // name -> description
 const [note, setNote] = useState("");
 const [busy, setBusy] = useState(false);
 const targetKey = /salesforce/i.test(target || "") ? "salesforce" : (target || "").toLowerCase();
 const chosen = useMemo(() => {
  if (spec.recordMappingDef && spec.recordMappingDef.id === spec.targetObjects) return normalizeMapping(spec.recordMappingDef);
  return catalogue.find((m) => m.id === spec.targetObjects) || null;
 }, [spec.targetObjects, spec.recordMappingDef, catalogue]);
 const offered = catalogue.filter((m) => m.target === targetKey);
 // Rows of the chosen mapping whose source is not a field of the current template: the
 // shipped purchase-order mapping under a template drafted from a customer's sample, or a
 // template edited after the mapping was chosen. Named here, and re-suggested on request.
 const stale = useMemo(() => staleSources(chosen, template), [chosen, template]);
 // Which document fields feed each resolved role (K21's regexes over this template), so a
 // resolved value is shown with the extracted field it came from.
 const feeds = useMemo(() => roleFeeds(template), [template]);
 const staleKey = new Set(stale.map((s) => `${s.object}.${s.target}`));

 // The objects the target holds for this document kind (K24): asked once the target and
 // the template are known, shown as a suggestion the person uses or ignores.
 const [objectSuggestions, setObjectSuggestions] = useState(null);
 useEffect(() => {
  if (!target || !template) { setObjectSuggestions(null); return undefined; }
  const ac = new AbortController();
  suggestTargetObjects({ template: { id: template.id, label: template.label, record: template.record, header: headerFields(template), lines: lineFields(template) }, description, target: targetKey }, { signal: ac.signal })
   .then((rows) => setObjectSuggestions(rows || []))
   .catch(() => setObjectSuggestions([]));
  return () => ac.abort();
  // eslint-disable-next-line react-hooks/exhaustive-deps
 }, [target, template?.id, JSON.stringify(headerFields(template || {}).map((f) => f.name))]);
 const useObjectSuggestion = async (sug) => {
  const shipped = sug.shipped_mapping && catalogue.find((m) => m.id === sug.shipped_mapping);
  if (shipped) { choose(shipped); return; }
  setEditing({ label: `${sug.object}${sug.child ? ` + ${sug.child}` : ""}`, object: sug.object, fields: [], child: sug.child ? { object: sug.child, parent_field: `${sug.object}Id`, fields: [] } : null, status_field: null, source: "authored" });
  setTimeout(() => suggestFor(sug.object, sug.child), 0);
 };

 useEffect(() => {
  if (!editing || objects !== null) return;
  fetchTargetObjects(targetKey).then(setObjects).catch((err) => { setObjects([]); setNote(err?.status === 503 ? "The platform cannot reach the target right now, so objects are typed rather than listed." : "Objects could not be listed; type the object names."); });
 }, [editing, objects, targetKey]);
 const describe = async (name) => {
  if (!name || described[name] !== undefined) return described[name];
  try { const d = await describeTargetObject(targetKey, name); setDescribed((x) => ({ ...x, [name]: d })); return d; } catch { setDescribed((x) => ({ ...x, [name]: null })); return null; }
 };
 useEffect(() => { if (editing?.object) describe(editing.object); if (editing?.child?.object) describe(editing.child.object); // eslint-disable-next-line react-hooks/exhaustive-deps
 }, [editing?.object, editing?.child?.object]);

 const choose = (m) => { setEditing(null); setMapping(m.id, m.source === "shipped" ? null : m); };
 const startNew = () => setEditing({ label: "", object: "", fields: [], child: null, status_field: null, source: "authored" });
 const startEdit = (m) => setEditing({ ...m, id: m.source === "shipped" ? undefined : m.id, basedOn: m.source === "shipped" ? m.id : null, source: m.source === "shipped" ? "authored" : m.source, label: m.source === "shipped" ? `${m.label.replace(/ \(standard\)$/, "")} (custom)` : m.label });
 const keep = () => { const m = saveMappingToCatalogue({ ...editing, target: targetKey }); setCatalogue(loadMappingCatalogue()); setEditing(null); setMapping(m.id, m); };
 const remove = (m) => { removeMappingFromCatalogue(m.id); setCatalogue(loadMappingCatalogue()); if (spec.targetObjects === m.id) setMapping("", null); };
 const resuggest = () => {
  if (!chosen) return;
  startEdit(chosen);
  setTimeout(() => suggestFor(chosen.object, chosen.child?.object || null), 0);
 };
 const suggestFor = async (object, child) => {
  if (!object || !template) return;
  setBusy(true); setNote("");
  try {
   const out = await suggestRecordMapping({ template: { id: template.id, label: template.label, record: template.record, header: headerFields(template), lines: lineFields(template) }, parent: object, child: child || null, label: undefined });
   setEditing((e) => ({ ...(e || {}), object, label: e?.label || out.label, fields: out.fields, child: out.child ? { ...out.child } : (child ? { object: child, parent_field: `${object}Id`, fields: [] } : null), status_field: out.status_field, status_values: out.status_values, record: out.record, source: "authored" }));
   setNote(`Suggested from names over the org's ${object}${child ? ` and ${child}` : ""} fields; rows with no source need one. Edit and keep.`);
  } catch (err) { setNote(`Could not suggest: ${err?.detail || err?.message || "the platform did not answer"}.`); } finally { setBusy(false); }
 };
 const suggest = async () => {
  if (!editing?.object || !template) return;
  setBusy(true); setNote("");
  try {
   const out = await suggestRecordMapping({ template: { id: template.id, label: template.label, record: template.record, header: headerFields(template), lines: lineFields(template) }, parent: editing.object, child: editing.child?.object || null, label: editing.label || undefined });
   setEditing((e) => ({ ...e, label: e.label || out.label, fields: out.fields, child: out.child ? { ...out.child } : e.child, status_field: out.status_field, status_values: out.status_values, record: out.record }));
   setNote(`Suggested from names over the org's ${editing.object}${editing.child?.object ? ` and ${editing.child.object}` : ""} fields; rows with no source need one.`);
  } catch (err) { setNote(`Could not suggest: ${err?.detail || err?.message || "the platform did not answer"}.`); } finally { setBusy(false); }
 };
 const valid = editing && editing.label.trim() && editing.object.trim() && editing.fields.some((r) => r.source?.length);
 // The org's objects in three groups so the long tail does not bury the likely ones: what the
 // platform suggested for this template, the org's own (custom) objects, then every standard one.
 const objectGroups = useMemo(() => {
  if (!objects || !objects.length) return [];
  const suggested = new Set((objectSuggestions || []).flatMap((s) => [s.object, s.child].filter(Boolean)));
  const pick = (test) => objects.filter(test);
  return [
   { group: "Suggested for this template", options: pick((o) => suggested.has(o.name)) },
   { group: "Your org's own objects", options: pick((o) => o.custom && !suggested.has(o.name)) },
   { group: "Standard objects", options: pick((o) => !o.custom && !suggested.has(o.name)) },
  ].filter((g) => g.options.length);
 }, [objects, objectSuggestions]);
 const objectSelect = (value, onPick, label) => (
  objectGroups.length ? (
   <select value={value} onChange={(e) => onPick(e.target.value)} className={`${input} max-w-[18rem] font-mono`} aria-label={label}>
    <option value="">{label}…</option>
    {objectGroups.map((g) => <optgroup key={g.group} label={g.group}>{g.options.map((o) => <option key={o.name} value={o.name}>{o.name}{o.label !== o.name ? ` - ${o.label}` : ""}</option>)}</optgroup>)}
    {value && !objects.some((o) => o.name === value) && <option value={value}>{value}</option>}
   </select>
  ) : <input value={value} onChange={(e) => onPick(e.target.value)} placeholder={label} className={`${input} w-40 font-mono`} aria-label={label} />
 );

 if (!target) return <p className="text-sm text-text-muted">Choose the target system first.</p>;
 return (
  <div>
   {objectSuggestions && objectSuggestions.length > 0 && !editing && (
    <div className="mb-2 rounded-md border border-dashed border-primary/40 bg-primary-light/20 px-2.5 py-2 text-xs">
     <p className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-text-muted">Suggested objects in {target.split(/\s*[-–]\s*/)[0]}</p>
     <div className="flex flex-wrap items-center gap-1.5">
      {objectSuggestions.slice(0, 3).map((sug) => (
       <button key={sug.object} type="button" onClick={() => useObjectSuggestion(sug)} title={sug.why} className="inline-flex items-center gap-1.5 rounded-md border border-primary/40 bg-surface px-2.5 py-1 font-semibold text-primary hover:bg-primary hover:text-white">
        {sug.object}{sug.child ? ` + ${sug.child} per line` : ""}<span className="font-normal opacity-80"> · {sug.why.split(";")[0]}</span>{sug.shipped_mapping ? <span className="rounded bg-status-success-light px-1 text-[10px] text-status-success">mapping ready</span> : <span className="rounded bg-surface-secondary px-1 text-[10px] text-text-muted">map the fields</span>}
       </button>
      ))}
     </div>
    </div>
   )}
   <div className="flex flex-wrap items-center gap-1.5">
    {offered.map((m) => <button key={m.id} type="button" aria-pressed={spec.targetObjects === m.id} onClick={() => choose(m)} className={chip(spec.targetObjects === m.id)} title={m.source === "shipped" ? "Ships with the platform" : "Saved in this workspace"}>{m.label}</button>)}
    <button type="button" onClick={startNew} className="inline-flex items-center gap-1 rounded-full border border-dashed border-border px-2.5 py-1 text-xs font-semibold text-text-secondary hover:border-primary hover:text-primary"><Plus size={11} /> New mapping</button>
   </div>
   {chosen && !editing && (
    <div className="mt-2 rounded-md border border-border-light bg-surface-secondary/40 p-2.5 text-[11px]">
     <p className="font-semibold uppercase tracking-wide text-text-muted">{chosen.object}{chosen.child ? ` + ${chosen.child.object} per line` : ""} · {chosen.fields.length + (chosen.child?.fields.length || 0)} mapped fields</p>
     <div className="overflow-x-auto"><table className="mt-1 text-text-secondary">
      <thead><tr className="text-left text-[10px] uppercase tracking-wide text-text-muted"><th className="py-0.5 pr-3 font-semibold">Target field</th><th className="py-0.5 pr-3 font-semibold">← value</th><th /></tr></thead>
      <tbody>
       {chosen.fields.map((r) => <MappedRow key={r.target} object={chosen.object} row={r} template={template} feeds={feeds} stale={staleKey.has(`${chosen.object}.${r.target}`)} />)}
       {(chosen.child?.fields || []).map((r) => <MappedRow key={`c-${r.target}`} object={chosen.child.object} row={r} template={template} feeds={feeds} stale={staleKey.has(`${chosen.child.object}.${r.target}`)} perLine />)}
      </tbody>
     </table></div>
     <p className="mt-1.5 flex flex-wrap gap-x-3 text-[10px] text-text-muted"><span><span className="rounded bg-primary-light/40 px-1 text-primary">document</span> extracted by the AI step</span><span><span className="rounded bg-status-success-light px-1 text-status-success">resolved</span> the document field on the left, looked up in {target.split(/\s*[-–]\s*/)[0]} by the Resolve step, and the matched record's field on the right</span><span><span className="rounded bg-surface-secondary px-1">run</span> set by the run</span></p>
     {stale.length > 0 && (
      <p className="mt-1.5 text-status-warning">
       {stale.length} source{stale.length === 1 ? "" : "s"} the template does not extract: {[...new Set(stale.map((s) => s.source))].join(", ")}. Those fields would be blank on every record.{" "}
       <button type="button" onClick={resuggest} className="font-semibold underline hover:text-primary">Re-suggest from this template</button>
      </p>
     )}
     <div className="mt-2 flex flex-wrap items-center gap-2 text-text-muted">
      <span>{chosen.status_field ? `"Created as" sets ${chosen.object}.${chosen.status_field}.` : "The object has no status to set."} Rolled back by deleting the {chosen.object}.</span>
      <button type="button" onClick={() => startEdit(chosen)} className="ml-auto font-semibold text-primary hover:underline">{chosen.source === "shipped" ? "Copy and edit" : "Edit"}</button>
      {chosen.source !== "shipped" && <button type="button" onClick={() => remove(chosen)} className="inline-flex items-center gap-1 hover:text-status-critical"><Trash2 size={11} /> Remove</button>}
     </div>
    </div>
   )}
   {editing && (
    <div className="mt-2 space-y-2.5 rounded-md border border-primary/40 bg-primary-light/20 p-3">
     <div className="flex flex-wrap items-center gap-2 text-[11px]">
      <label className="font-semibold text-text-muted">Name <input value={editing.label} onChange={(e) => setEditing({ ...editing, label: e.target.value })} placeholder="Case per return" className={`${input} ml-1 w-44`} /></label>
      <label className="font-semibold text-text-muted">Creates {objectSelect(editing.object, (v) => setEditing({ ...editing, object: v, fields: editing.object === v ? editing.fields : [] }), "parent object")}</label>
      <label className="font-semibold text-text-muted">per line {objectSelect(editing.child?.object || "", (v) => setEditing({ ...editing, child: v ? { object: v, parent_field: editing.child?.parent_field || `${editing.object}Id`, fields: editing.child?.object === v ? editing.child.fields : [] } : null }), "child object")}</label>
      <button type="button" onClick={suggest} disabled={busy || !editing.object || !template} className="inline-flex items-center gap-1.5 rounded-md bg-primary px-2.5 py-1.5 text-xs font-semibold text-white disabled:opacity-60"><Sparkles size={12} /> {busy ? "Suggesting…" : "Suggest from names"}</button>
     </div>
     {note && <p className="text-[11px] text-text-secondary">{note}</p>}
     {editing.object && <RowTable title={editing.object} rows={editing.fields} described={described[editing.object]} groups={sourceGroups(template, { feeds })} onChange={(fields) => setEditing({ ...editing, fields })} />}
     {editing.child?.object && <RowTable title={`${editing.child.object} per line`} rows={editing.child.fields} described={described[editing.child.object]} groups={sourceGroups(template, { lineScope: true, feeds })} parentField={editing.child.parent_field} onChange={(fields) => setEditing({ ...editing, child: { ...editing.child, fields } })} />}
     <div className="flex flex-wrap items-center gap-2 text-[11px]">
      <label className="text-text-muted">Status field <input value={editing.status_field || ""} onChange={(e) => setEditing({ ...editing, status_field: e.target.value || null })} placeholder="Status, or none" className={`${input} ml-1 w-28 font-mono`} /></label>
      {editing.status_field && <span className="text-text-muted">draft → <input value={editing.status_values?.draft ?? "Draft"} onChange={(e) => setEditing({ ...editing, status_values: { ...(editing.status_values || {}), draft: e.target.value } })} className={`${input} w-20`} aria-label="Draft status value" /> active → <input value={editing.status_values?.active ?? "Activated"} onChange={(e) => setEditing({ ...editing, status_values: { ...(editing.status_values || {}), active: e.target.value } })} className={`${input} w-20`} aria-label="Active status value" /></span>}
     </div>
     <div className="flex items-center gap-2">
      <button type="button" onClick={keep} disabled={!valid} className="inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-50"><Check size={12} /> Keep and use this mapping</button>
      <button type="button" onClick={() => setEditing(null)} className="text-xs font-semibold text-text-muted hover:text-text-primary">Cancel</button>
      <span className="ml-auto text-[11px] text-text-muted">* required by the org · saved to this workspace's catalogue</span>
     </div>
    </div>
   )}
   <p className="mt-1.5 text-[11px] text-text-muted">The platform creates the parent, then each line's child with the parent's id, then sets the status "created as" asks for. A required field the mapping cannot fill is an exception on the document, never a blank on the record.</p>
  </div>
 );
}
