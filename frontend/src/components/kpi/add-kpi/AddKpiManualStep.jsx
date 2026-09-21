import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, ArrowLeft, Plus, RefreshCw } from "lucide-react";
import { fetchSemanticTables } from "../../../services/semantic-tables-api";
import { FIELD_INPUT } from "../ModeCard";
import { GRAIN_OPTIONS, LEVEL_OPTIONS } from "./grain-options";

/**
 * "Add KPI" -> manual: a `KpiWrite` (contract `POST /kpis`) filled in by hand. The server is
 * the judge of everything that matters -- the formula's tables against `source_tables`, the
 * grain and business area against the deployment's vocabulary, a duplicate identifier -- and
 * refuses with a 422/409 naming the defect, which is shown here verbatim rather than guessed
 * at client-side. The form only insists on what the contract marks required plus `concept`,
 * which the server needs to derive the identifier (`<concept>__<slug of name>`, both halves
 * slugified server-side, so "RFQ quote" is as good as "rfq_quote").
 *
 * Source tables come from `GET /semantic/tables`: the same allow-list the SQL guard enforces,
 * so a formula can only ever name a table the semantic layer exposes. Fetched once per mount
 * -- and the modal keeps this step mounted (hidden) across "Change how", so a look back at the
 * mode cards neither refetches nor loses the draft.
 *
 * `saving` is the modal's, not this step's: the modal needs it to refuse a backdrop / X
 * dismiss while the POST is in flight (see `AddKpiModal.jsx`); this step only reports it.
 */
const INPUT = `mt-1 ${FIELD_INPUT}`;
const LABEL = "block text-xs font-semibold text-text-primary";
const HINT = "mt-1 text-[11px] leading-relaxed text-text-muted";

export default function AddKpiManualStep({
 areaState = { status: "ready", areas: [], error: null },
 onRetryAreas,
 saving = false,
 onSavingChange,
 onBack,
 onCreate,
}) {
 const [name, setName] = useState("");
 const [description, setDescription] = useState("");
 const [businessArea, setBusinessArea] = useState("");
 const [concept, setConcept] = useState("");
 const [grain, setGrain] = useState("overall");
 const [resultShape, setResultShape] = useState("scalar");
 const [unit, setUnit] = useState("");
 const [level, setLevel] = useState("");
 const [sql, setSql] = useState("");
 const [sourceTables, setSourceTables] = useState(() => new Set());
 const [tableState, setTableState] = useState({ status: "loading", tables: [], error: null });
 const [error, setError] = useState(null);

 useEffect(() => {
  const controller = new AbortController();
  fetchSemanticTables({ signal: controller.signal })
   .then((res) => {
    if (controller.signal.aborted) return;
    setTableState({ status: "ready", tables: res?.tables || [], error: null });
   })
   .catch((err) => {
    if (controller.signal.aborted || err?.name === "AbortError") return;
    setTableState({ status: "error", tables: [], error: err?.detail || err?.message || "Couldn't load the table list." });
   });
  return () => controller.abort();
 }, []);

 // Supported areas first (the ones the semantic graph can back), the rest still offered but
 // marked -- a KPI in an unsupported area is allowed, it just cannot be ready yet.
 const areas = areaState.areas || [];
 const areaOptions = useMemo(
  () => [...areas].sort((a, b) => Number(!!b.supported) - Number(!!a.supported)),
  [areas]
 );
 useEffect(() => {
  if (!businessArea && areaOptions.length) setBusinessArea(areaOptions[0].id);
 }, [areaOptions, businessArea]);

 const toggleTable = (tableName) =>
  setSourceTables((prev) => {
   const next = new Set(prev);
   if (next.has(tableName)) next.delete(tableName);
   else next.add(tableName);
   return next;
  });

 const missing = [];
 if (!name.trim()) missing.push("a name");
 if (!description.trim()) missing.push("a description");
 if (!businessArea) missing.push("a business area");
 if (!concept.trim()) missing.push("a concept");
 if (!sql.trim()) missing.push("the SQL");
 if (sourceTables.size === 0) missing.push("at least one source table");
 const canSave = missing.length === 0 && !saving;

 const submit = async () => {
  if (!canSave) return;
  onSavingChange?.(true);
  setError(null);
  try {
   await onCreate({
    name: name.trim(),
    description: description.trim(),
    business_area: businessArea,
    concept: concept.trim(),
    grain,
    result_shape: resultShape,
    sql: sql.trim(),
    source_tables: [...sourceTables],
    ...(unit.trim() ? { unit: unit.trim() } : {}),
    ...(level ? { level } : {}),
   });
  } catch (err) {
   // Aborted means the page is gone (it aborts its in-flight create on unmount); there is
   // nobody left to show the message to.
   if (err?.name === "AbortError") return;
   setError(err?.detail || err?.message || "Couldn't add the KPI.");
   onSavingChange?.(false);
  }
 };

 // The three states the area list can be in, modelled the same way the source-tables block
 // below already does: a failed `GET /business-areas` used to leave the select on "Loading
 // areas..." for good, with the form unsubmittable and nothing saying why.
 const areasLoading = areaState.status === "loading";
 const areasFailed = areaState.status === "error";
 const areasEmpty = areaState.status === "ready" && areaOptions.length === 0;

 return (
  <>
   <button
    type="button"
    onClick={onBack}
    disabled={saving}
    className="mb-4 inline-flex items-center gap-1 text-xs font-semibold text-text-secondary hover:text-text-primary disabled:opacity-60"
   >
    <ArrowLeft size={13} />
    Change how
   </button>

   <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
    <div className="sm:col-span-2">
     <label htmlFor="kpi-name" className={LABEL}>Name</label>
     <input
      id="kpi-name"
      autoFocus
      value={name}
      onChange={(e) => setName(e.target.value)}
      disabled={saving}
      placeholder="e.g. Quote-to-deal conversion rate"
      className={INPUT}
     />
    </div>
    <div className="sm:col-span-2">
     <label htmlFor="kpi-description" className={LABEL}>Description</label>
     <textarea
      id="kpi-description"
      rows={2}
      value={description}
      onChange={(e) => setDescription(e.target.value)}
      disabled={saving}
      placeholder="What the number means and what it is for, in a sentence or two."
      className={INPUT}
     />
    </div>
    <div>
     <label htmlFor="kpi-area" className={LABEL}>Business area</label>
     <select
      id="kpi-area"
      value={businessArea}
      onChange={(e) => setBusinessArea(e.target.value)}
      disabled={saving || areaOptions.length === 0}
      className={INPUT}
     >
      {areasLoading && <option value="">Loading areas…</option>}
      {areasFailed && <option value="">Couldn't load the areas</option>}
      {areasEmpty && <option value="">No business areas defined</option>}
      {areaOptions.map((a) => (
       <option key={a.id} value={a.id}>
        {a.label || a.id}{a.supported ? "" : " (no data mapped yet)"}
       </option>
      ))}
     </select>
     {areasFailed && (
      <p className="mt-1 flex flex-wrap items-center gap-2 text-xs text-status-critical">
       <span>
        Couldn't load the business areas
        {areaState.error?.detail ? `: ${areaState.error.detail}` : "."}
       </span>
       {onRetryAreas && (
        <button
         type="button"
         onClick={onRetryAreas}
         disabled={saving}
         className="inline-flex items-center gap-1 rounded-md border border-border-light bg-surface px-2 py-0.5 text-[11px] font-semibold text-text-secondary transition-colors hover:bg-surface-secondary"
        >
         <RefreshCw size={11} />
         Retry
        </button>
       )}
      </p>
     )}
     {areasEmpty && (
      <p className={HINT}>This deployment has no business areas yet, a KPI needs one before it can be added.</p>
     )}
    </div>
    <div>
     <label htmlFor="kpi-concept" className={LABEL}>Concept</label>
     <input
      id="kpi-concept"
      value={concept}
      onChange={(e) => setConcept(e.target.value)}
      disabled={saving}
      placeholder="e.g. rfq, contact, websession"
      className={INPUT}
     />
     <p className={HINT}>
      The ontology concept the metric hangs off. Sets the identifier <code className="font-mono">concept__name</code>{" "}
      (lower-cased, spaces and punctuation become <code className="font-mono">_</code>), which cannot be changed once added.
     </p>
    </div>
    <div>
     <label htmlFor="kpi-grain" className={LABEL}>Grain</label>
     <select id="kpi-grain" value={grain} onChange={(e) => setGrain(e.target.value)} disabled={saving} className={INPUT}>
      {GRAIN_OPTIONS.map((g) => (
       <option key={g.value} value={g.value}>{g.label}</option>
      ))}
     </select>
     <p className={HINT}>{GRAIN_OPTIONS.find((g) => g.value === grain)?.description}</p>
    </div>
    <div>
     <label htmlFor="kpi-shape" className={LABEL}>Result shape</label>
     <select id="kpi-shape" value={resultShape} onChange={(e) => setResultShape(e.target.value)} disabled={saving} className={INPUT}>
      <option value="scalar">Scalar: one number (can be trended and alerted on)</option>
      <option value="rowset">Rowset: a table of rows</option>
     </select>
    </div>
    <div>
     <label htmlFor="kpi-unit" className={LABEL}>Unit <span className="font-normal text-text-muted">(optional)</span></label>
     <input id="kpi-unit" value={unit} onChange={(e) => setUnit(e.target.value)} disabled={saving} placeholder="e.g. %, count, days" className={INPUT} />
    </div>
    <div>
     <label htmlFor="kpi-level" className={LABEL}>Level <span className="font-normal text-text-muted">(optional)</span></label>
     <select id="kpi-level" value={level} onChange={(e) => setLevel(e.target.value)} disabled={saving} className={INPUT}>
      {LEVEL_OPTIONS.map((l) => (
       <option key={l.value} value={l.value}>{l.label}</option>
      ))}
     </select>
    </div>
    <div className="sm:col-span-2">
     <label htmlFor="kpi-sql" className={LABEL}>SQL</label>
     <textarea
      id="kpi-sql"
      rows={5}
      spellCheck={false}
      value={sql}
      onChange={(e) => setSql(e.target.value)}
      disabled={saving}
      placeholder="SELECT count(*) FROM silver.rfq_quote WHERE stage = 'won'"
      className={`${INPUT} font-mono text-xs`}
     />
     <p className={HINT}>Reads only the tables ticked below; the server refuses a formula that names any other.</p>
    </div>
    <div className="sm:col-span-2">
     <p className={LABEL}>Source tables</p>
     {tableState.status === "loading" && <p className={HINT}>Loading the semantic layer's tables…</p>}
     {tableState.status === "error" && (
      <p className="mt-1 text-xs text-status-critical">{tableState.error}</p>
     )}
     {tableState.status === "ready" && tableState.tables.length === 0 && (
      <p className={HINT}>The semantic layer exposes no tables yet, connect a source first.</p>
     )}
     {tableState.status === "ready" && tableState.tables.length > 0 && (
      <div className="mt-1.5 grid max-h-44 grid-cols-1 gap-1 overflow-y-auto rounded-lg border border-border bg-surface-secondary p-2 sm:grid-cols-2">
       {tableState.tables.map((t) => {
        const checked = sourceTables.has(t.name);
        return (
         <label
          key={t.name}
          className={`flex cursor-pointer items-center gap-2 rounded-md px-2 py-1 text-xs ${checked ? "bg-primary-light text-text-primary" : "text-text-secondary hover:bg-surface-default"}`}
         >
          <input type="checkbox" checked={checked} onChange={() => toggleTable(t.name)} disabled={saving} className="accent-primary" />
          <span className="min-w-0 truncate font-mono">{t.name}</span>
          {Number.isFinite(t.row_count) && (
           <span className="ml-auto shrink-0 text-[10px] text-text-muted">{t.row_count.toLocaleString()} rows</span>
          )}
         </label>
        );
       })}
      </div>
     )}
    </div>
   </div>

   {error && (
    <p role="alert" className="mt-4 flex items-start gap-2 rounded-lg border border-status-critical-border bg-status-critical-light px-3 py-2 text-xs text-status-critical">
     <AlertTriangle size={13} className="mt-0.5 shrink-0" />
     <span>Couldn't add it: {error}</span>
    </p>
   )}

   <div className="mt-5 flex flex-wrap items-center justify-between gap-2 border-t border-border pt-4">
    <span className="text-[11px] text-text-muted">
     {missing.length > 0 ? `Still needs ${missing.join(", ")}.` : "Saved to your KPI registry and computed tonight, or now from its panel."}
    </span>
    <button
     type="button"
     disabled={!canSave}
     onClick={submit}
     className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-primary-hover disabled:cursor-not-allowed disabled:opacity-50"
    >
     <Plus size={14} />
     {saving ? "Adding…" : "Add to my metrics"}
    </button>
   </div>
  </>
 );
}
