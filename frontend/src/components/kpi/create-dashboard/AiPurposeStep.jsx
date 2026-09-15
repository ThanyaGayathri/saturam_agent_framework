import { useEffect, useMemo, useState } from "react";
import { ArrowLeft, Sparkles, X } from "lucide-react";
import MetricPicker from "./MetricPicker";
import { fetchSemanticTable, fetchSemanticTables } from "../../../services/semantic-tables-api";

/**
 * Step 2 of the AI path: the metrics this dashboard is *about* (context the composer reads,
 * not the output), what the composer will be shown for them - the tables behind them, how
 * many of their columns carry a description, which columns are withheld as personal data -
 * and the purpose, in the person's own words. Styled like the product's other assistant/
 * composer pairs (`PlaygroundAssistantMessage.jsx`, `ChatComposerTextarea.jsx`) so it reads as
 * native chat, not a bolted-on form.
 */

const BUDGETS = [
 { value: null, label: "Let it decide" },
 { value: 3, label: "About 3 charts" },
 { value: 5, label: "About 5 charts" },
 { value: 8, label: "About 8 charts" },
];

function coverageOf(tables) {
 let described = 0;
 let total = 0;
 const withheld = [];
 for (const table of tables) {
  for (const column of table.columns || []) {
   if (column.is_personal_data) {
    withheld.push(`${table.ref?.name || ""}.${column.name}`);
    continue;
   }
   total += 1;
   if ((column.description || "").trim() || (column.meaning || "").trim()) described += 1;
  }
 }
 return { described, total, withheld };
}

export default function AiPurposeStep({
 initialPurpose,
 initialBudget,
 metrics,
 setMetrics,
 readyKpis,
 extraTables,
 setExtraTables,
 onBack,
 onGenerate,
}) {
 const [purpose, setPurpose] = useState(initialPurpose || "");
 const [budget, setBudget] = useState(initialBudget ?? null);
 const [tables, setTables] = useState({});
 const [catalogue, setCatalogue] = useState(null);
 const [addingTable, setAddingTable] = useState(false);

 const metricTables = useMemo(
  () => Array.from(new Set(metrics.flatMap((m) => m.sourceTables || []))).sort(),
  [metrics],
 );
 const allTables = useMemo(
  () => Array.from(new Set([...metricTables, ...extraTables])).sort(),
  [metricTables, extraTables],
 );

 // Column descriptions for the coverage line, one fetch per table, cached for the step's
 // life. A semantic layer that is down just leaves the line out - the run itself will say
 // so if it matters.
 useEffect(() => {
  let cancelled = false;
  const missing = allTables.filter((name) => !(name in tables));
  if (!missing.length) return undefined;
  Promise.all(
   missing.map((name) =>
    fetchSemanticTable(name)
     .then((table) => [name, table])
     .catch(() => [name, null]),
   ),
  ).then((entries) => {
   if (cancelled) return;
   setTables((prev) => ({ ...prev, ...Object.fromEntries(entries) }));
  });
  return () => {
   cancelled = true;
  };
 }, [allTables, tables]);

 const loaded = allTables.map((name) => tables[name]).filter(Boolean);
 const coverage = loaded.length ? coverageOf(loaded) : null;
 const canGenerate = purpose.trim().length > 0 && allTables.length > 0;

 const openCatalogue = () => {
  setAddingTable(true);
  if (catalogue === null) {
   fetchSemanticTables()
    .then((list) => setCatalogue((list?.tables || []).map((t) => t.name)))
    .catch(() => setCatalogue([]));
  }
 };

 return (
  <>
   <div className="mb-4 flex items-center justify-between">
    <button
     type="button"
     onClick={onBack}
     className="inline-flex items-center gap-1 text-xs font-semibold text-primary hover:text-primary-hover"
    >
     <ArrowLeft size={13} />
     Change mode
    </button>
    <span className="inline-flex items-center gap-1.5 rounded-full bg-primary-light px-2.5 py-1 text-xs font-semibold text-primary">
     <Sparkles size={11} />
     AI generated
    </span>
   </div>

   <div className="mb-4 flex gap-3">
    <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary text-white">
     <Sparkles size={13} />
    </div>
    <p className="pt-1 text-[15px] leading-relaxed text-text-primary">
     Pick the metrics this dashboard is about, then tell me what you need to see. I'll read
     those metrics and the tables behind them, and compose the charts — not just one per
     metric.
    </p>
   </div>

   <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-text-muted">Metrics</p>
   <div className="mb-3 flex flex-wrap items-center gap-2">
    {metrics.map((kpi) => (
     <span
      key={kpi.identifier}
      className="inline-flex items-center gap-1.5 rounded-full bg-surface-tertiary py-1 pl-3 pr-1.5 text-xs font-medium text-text-secondary"
     >
      {kpi.name}
      <button
       type="button"
       onClick={() => setMetrics(metrics.filter((m) => m.identifier !== kpi.identifier))}
       aria-label={`Remove ${kpi.name}`}
       className="rounded-full p-0.5 text-text-muted hover:bg-surface-secondary hover:text-text-primary"
      >
       <X size={11} />
      </button>
     </span>
    ))}
    <MetricPicker
     readyKpis={readyKpis}
     selectedIdentifiers={new Set(metrics.map((m) => m.identifier))}
     onAdd={(kpi) => setMetrics([...metrics, kpi])}
    />
   </div>

   <div className="mb-4 rounded-lg border border-border bg-surface-secondary px-3.5 py-3 text-xs">
    <div className="flex items-start gap-3">
     <span className="w-24 shrink-0 pt-1 text-text-muted">Tables read</span>
     <div className="flex min-w-0 flex-1 flex-wrap items-center gap-1.5">
      {allTables.map((name) => (
       <span
        key={name}
        className="inline-flex items-center gap-1 rounded-full border border-border bg-surface-default px-2 py-0.5 font-mono text-[11px] text-text-primary"
       >
        {name}
        {tables[name]?.columns ? (
         <span className="font-sans text-[10px] text-text-muted">{tables[name].columns.length} cols</span>
        ) : null}
        {extraTables.includes(name) && (
         <button
          type="button"
          aria-label={`Remove ${name}`}
          onClick={() => setExtraTables(extraTables.filter((t) => t !== name))}
          className="text-text-muted hover:text-text-primary"
         >
          <X size={10} />
         </button>
        )}
       </span>
      ))}
      {addingTable ? (
       <select
        autoFocus
        className="rounded-md border border-border bg-surface-default px-2 py-0.5 text-[11px] text-text-primary focus:outline-none"
        value=""
        onChange={(e) => {
         if (e.target.value) setExtraTables([...extraTables, e.target.value]);
         setAddingTable(false);
        }}
        onBlur={() => setAddingTable(false)}
       >
        <option value="">{catalogue === null ? "Loading…" : "Choose a table"}</option>
        {(catalogue || [])
         .filter((name) => !allTables.includes(name))
         .map((name) => (
          <option key={name} value={name}>{name}</option>
         ))}
       </select>
      ) : (
       <button
        type="button"
        onClick={openCatalogue}
        className="rounded-full border border-dashed border-border-light px-2 py-0.5 text-[11px] font-semibold text-primary hover:border-primary/40"
       >
        + Add table
       </button>
      )}
      {allTables.length === 0 && (
       <span className="text-text-muted">Pick a metric, or add a table, so there is something to compose from.</span>
      )}
     </div>
    </div>
    {coverage && (
     <div className="mt-2.5 flex items-start gap-3 border-t border-dashed border-border pt-2.5">
      <span className="w-24 shrink-0 text-text-muted">Descriptions</span>
      <div className="min-w-0 flex-1">
       <p className="text-text-secondary">
        <b className="font-semibold text-text-primary">{coverage.described} of {coverage.total}</b> columns have a
        description or a reviewed meaning
        {coverage.total > coverage.described ? "; the rest will be used by name only." : "."}
       </p>
       <div className="mt-1.5 h-1 max-w-[220px] overflow-hidden rounded-full bg-surface-tertiary">
        <span
         className="block h-full bg-status-success"
         style={{ width: `${coverage.total ? Math.round((100 * coverage.described) / coverage.total) : 0}%` }}
        />
       </div>
      </div>
     </div>
    )}
    {coverage && coverage.withheld.length > 0 && (
     <div className="mt-2.5 flex items-start gap-3 border-t border-dashed border-border pt-2.5">
      <span className="w-24 shrink-0 text-text-muted">Withheld</span>
      <p className="min-w-0 flex-1 text-text-secondary">
       {coverage.withheld.map((c) => (
        <code key={c} className="mr-1 rounded bg-surface-tertiary px-1 text-[10px]">{c}</code>
       ))}
       {coverage.withheld.length === 1 ? "is" : "are"} personal data — never sent to the assistant, never
       selectable in a chart.
      </p>
     </div>
    )}
   </div>

   <div className="mb-2.5 rounded-lg border border-border px-3.5 py-3">
    <textarea
     autoFocus
     rows={3}
     value={purpose}
     onChange={(e) => setPurpose(e.target.value)}
     placeholder="e.g. Show me where stock-outs are costing us margin and whether late suppliers are the cause. Weekly, last 12 weeks."
     className="w-full resize-none bg-transparent text-sm leading-relaxed text-text-primary placeholder:text-text-muted focus:outline-none"
    />
    <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
     <div className="inline-flex overflow-hidden rounded-md border border-border-light text-[11px]">
      {BUDGETS.map((b) => (
       <button
        key={String(b.value)}
        type="button"
        onClick={() => setBudget(b.value)}
        className={`px-2.5 py-1 font-medium ${
         budget === b.value ? "bg-primary-light font-semibold text-primary" : "text-text-secondary hover:bg-surface-secondary"
        } border-r border-border-light last:border-r-0`}
       >
        {b.label}
       </button>
      ))}
     </div>
     <button
      type="button"
      disabled={!canGenerate}
      onClick={() => onGenerate({ purpose: purpose.trim(), chartBudget: budget })}
      className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-primary-hover disabled:cursor-not-allowed disabled:opacity-50"
     >
      <Sparkles size={14} />
      Compose dashboard
     </button>
    </div>
   </div>
   <p className="text-xs leading-relaxed text-text-muted">
    Tip: name the audience, the cadence and the comparison you care about — "by store", "vs last
    quarter", "top ten".
   </p>
  </>
 );
}
