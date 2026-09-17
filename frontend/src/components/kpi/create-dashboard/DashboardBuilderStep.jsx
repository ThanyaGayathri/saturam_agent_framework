import { useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, ArrowLeft, CalendarClock, Check, Pencil, Plus, RefreshCw, Sparkles, Trash2 } from "lucide-react";
import MetricPicker from "./MetricPicker";
import ChartEditorPanel from "./ChartEditorPanel";
import ChartTile from "../ChartTile";
import { spanForChart, tileDataFromPreview } from "../../../storage/dashboards";
import { fetchSemanticTable } from "../../../services/semantic-tables-api";

const AI_BADGE = (
 <span className="inline-flex items-center gap-1 rounded-full bg-primary-light px-2 py-0.5 text-[11px] font-semibold text-primary">
  <Sparkles size={9} />
  suggested
 </span>
);

let _chartSeq = 0;
function newChartId() {
 _chartSeq += 1;
 return `draft_${Date.now()}_${_chartSeq}`;
}

// Mirrors `dashboards/queries.py`'s `_FILTERABLE_TEXT_TYPES`/`_is_filterable_type` -- a
// dropdown-of-values filter suits text/categorical columns, not numeric or date ones. Checked
// here too (not just server-side on submit) so a column the write would reject with a 422 is
// never offered as pickable in the first place.
const FILTERABLE_TEXT_TYPES = [
 "text",
 "varchar",
 "character varying",
 "char",
 "character",
 "citext",
 "string",
 "enum",
];

function isFilterableType(dataType) {
 const normalized = String(dataType || "")
  .trim()
  .toLowerCase();
 return FILTERABLE_TEXT_TYPES.some(
  (kind) => normalized === kind || normalized.startsWith(`${kind}(`),
 );
}

/**
 * The review step for both paths: for the AI path this *is* the composed dashboard --
 * every chart the agent wrote, with its SQL/metric, guard verdict, preview rows and
 * description, each editable or removable, plus a slot to add one more. For the manual path
 * it is the KPI picker, one chart per metric, with the same edit surface. Name, filter and
 * refresh live in the header/foot, folded in rather than shown on an earlier screen.
 */
export default function DashboardBuilderStep({
 name,
 setName,
 refresh,
 setRefresh,
 refreshOptions,
 charts,
 setCharts,
 filterColumns,
 setFilterColumns,
 readyKpis,
 aiSuggested,
 contextSummary,
 onBack,
 onChangeMode,
 onCancel,
 onSubmit,
 submitting,
 error,
 notReadyCount = 0,
}) {
 const [editingId, setEditingId] = useState(null);
 // A not-yet-added chart's draft, or `null`. Distinct from `editingId`/`editingChart` below,
 // which only ever point at a chart already in `charts` -- see `addBlankQueryChart`'s comment.
 const [addingChart, setAddingChart] = useState(null);
 const canSubmit = charts.length > 0 && !submitting;
 const editingChart = editingId ? charts.find((c) => c.id === editingId) : null;

 const removeChart = (id) => setCharts(charts.filter((c) => c.id !== id));
 const updateChart = (id, patch) => setCharts(charts.map((c) => (c.id === id ? { ...c, ...patch } : c)));

 const addMetricAsChart = (kpi) => {
  setCharts([
   ...charts,
   {
    id: newChartId(),
    title: kpi.name,
    chart: "number",
    source: "kpi",
    kpi_identifier: kpi.identifier,
    description: kpi.description || "",
    guard: { status: "ok", detail: "" },
   },
  ]);
 };

 // A brand-new chart is never added to `charts` until its editor is actually saved (round-1
 // review, finding #10): pushing it in immediately, as this used to, left an orphaned
 // blank-SQL draft behind on Cancel -- invisible on this screen (nothing renders `addingChart`
 // as a tile), but still present in `charts` and still sent on "Create dashboard", where its
 // empty `sql` 422s the *entire* request with no visible connection to the chart the person
 // cancelled. `addingChart` mirrors `DashboardView.jsx`'s own `addingChart` draft-before-save
 // pattern for the same reason.
 const addBlankQueryChart = () => {
  setAddingChart({
   id: newChartId(),
   title: "New chart",
   chart: "table",
   source: "query",
   query: { sql: "", source_tables: [] },
   description: "",
  });
 };

 const selectedIdentifiers = new Set(
  charts.filter((c) => c.source !== "query").map((c) => c.kpi_identifier),
 );

 // Every table a `query` chart's own guarded SQL reads (`ChartEditorPanel`'s save populates
 // `query.source_tables` from the preview's guard verdict) - the only tables a filter column
 // can come from in this phase (R13.1: a `kpi` tile reads a per-KPI gold view, not a raw
 // table, so it has no columns to offer here).
 const queryTables = useMemo(
  () =>
   Array.from(
    new Set(
     charts.filter((c) => c.source === "query").flatMap((c) => c.query?.source_tables || []),
    ),
   ).sort(),
  [charts],
 );

 // Candidate `{table, column}` pairs, fetched once per table from the semantic layer's own
 // read surface (the same one the chart editor's schema pane uses) and never re-fetched --
 // `fetchedTablesRef` tracks what has been asked for, independent of render, so a table
 // dropped and re-added mid-session does not re-fetch. Personal-data and non-text columns are
 // excluded client-side (mirroring the server's `available_filter_columns`) so a numeric/date
 // column is never offered here in the first place; the write is still re-checked
 // server-side (`_validate_filters`), which stays the source of truth.
 const fetchedTablesRef = useRef(new Set());
 const [candidateColumns, setCandidateColumns] = useState([]);
 useEffect(() => {
  const toFetch = queryTables.filter((t) => !fetchedTablesRef.current.has(t));
  if (toFetch.length === 0) return;
  toFetch.forEach((t) => fetchedTablesRef.current.add(t));
  let cancelled = false;
  Promise.all(
   toFetch.map((table) =>
    fetchSemanticTable(table)
     .then((info) => ({
      table,
      columns: (info?.columns || [])
       .filter((c) => !c.is_personal_data && isFilterableType(c.data_type))
       .map((c) => c.name),
     }))
     .catch(() => ({ table, columns: [] })),
   ),
  ).then((results) => {
   if (cancelled) {
    // This effect was superseded before the fetch resolved - undo the "already fetched"
    // mark for these tables so a later run (e.g. the same table reappearing after the user
    // swaps which query chart is present) retries them instead of being permanently blocked
    // from ever fetching their columns again this session.
    toFetch.forEach((t) => fetchedTablesRef.current.delete(t));
    return;
   }
   setCandidateColumns((prev) => [
    ...prev,
    ...results.flatMap(({ table, columns }) => columns.map((column) => ({ table, column }))),
   ]);
  });
  return () => {
   cancelled = true;
  };
 }, [queryTables]);

 // A chart removed after its columns were offered takes its candidates (and any selection
 // made against them) with it.
 const visibleCandidates = useMemo(
  () => candidateColumns.filter((c) => queryTables.includes(c.table)),
  [candidateColumns, queryTables],
 );
 useEffect(() => {
  const valid = new Set(queryTables);
  setFilterColumns((prev) => prev.filter((f) => valid.has(f.table)));
 }, [queryTables, setFilterColumns]);

 const isFilterSelected = (table, column) =>
  filterColumns.some((f) => f.table === table && f.column === column);
 const toggleFilterColumn = (table, column) => {
  if (isFilterSelected(table, column)) {
   setFilterColumns(filterColumns.filter((f) => !(f.table === table && f.column === column)));
  } else {
   setFilterColumns([...filterColumns, { table, column }]);
  }
 };

 return (
  <form
   onSubmit={(e) => {
    e.preventDefault();
    if (canSubmit) onSubmit();
   }}
  >
   <div className="mb-4 flex items-start justify-between gap-3">
    {aiSuggested ? (
     <button
      type="button"
      onClick={onBack}
      className="inline-flex items-center gap-1 text-xs font-semibold text-primary hover:text-primary-hover"
     >
      <ArrowLeft size={13} />
      Back to purpose (recompose)
     </button>
    ) : onChangeMode ? (
     <button
      type="button"
      onClick={onChangeMode}
      className="inline-flex items-center gap-1 text-xs font-semibold text-primary hover:text-primary-hover"
     >
      <ArrowLeft size={13} />
      Change mode
     </button>
    ) : (
     <span />
    )}
    {aiSuggested && (
     <span className="inline-flex items-center gap-1.5 rounded-full bg-primary-light px-2.5 py-1 text-xs font-semibold text-primary">
      <Sparkles size={11} />
      AI generated — edit anything
     </span>
    )}
   </div>

   <div className="mb-4">
    <label htmlFor="db-name" className="block text-sm font-semibold text-text-primary">
     Name
    </label>
    <input
     id="db-name"
     value={name}
     onChange={(e) => setName(e.target.value)}
     placeholder="e.g. Stock-outs vs margin"
     className="mt-1.5 w-full rounded-lg border border-border bg-surface-default px-3 py-2 text-sm text-text-primary placeholder:text-text-muted focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
    />
   </div>

   {contextSummary && (
    <p className="mb-4 text-xs text-text-muted">
     Read {contextSummary.kpi_identifiers?.length || 0} metric(s) and{" "}
     {contextSummary.table_names?.length || 0} table(s) ·{" "}
     {contextSummary.described_columns ?? 0} of {contextSummary.total_columns ?? 0} columns described
     {contextSummary.withheld_columns?.length ? ` · ${contextSummary.withheld_columns.length} withheld as personal data` : ""}
    </p>
   )}

   <div className="mb-5">
    <p className="mb-2 flex items-center gap-2 text-sm font-semibold text-text-primary">
     {charts.length} chart{charts.length === 1 ? "" : "s"}
     {aiSuggested && charts.length > 0 && AI_BADGE}
    </p>

    {notReadyCount > 0 && (
     <div className="mb-3 flex items-start gap-2 rounded-lg border border-status-warning/30 bg-status-warning/10 px-3 py-2.5 text-xs text-text-secondary">
      <AlertTriangle size={15} className="mt-0.5 shrink-0 text-status-warning" />
      <p>
       {notReadyCount} metric{notReadyCount === 1 ? " isn't" : "s aren't"} linked to a registered
       KPI yet, so {notReadyCount === 1 ? "it" : "they"} couldn't be added.
      </p>
     </div>
    )}

    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
     {charts.map((chart) => (
      <ChartTile
       key={chart.id}
       className={spanForChart(chart.chart) === 2 ? "sm:col-span-2" : ""}
       tile={chart}
       data={chart.source === "query" ? tileDataFromPreview(chart, chart.preview) : null}
       guard={chart.guard}
       actions={
        <>
         <button
          type="button"
          onClick={() => setEditingId(chart.id)}
          aria-label={`Edit ${chart.title}`}
          className="rounded-md p-1.5 text-text-muted hover:bg-surface-tertiary hover:text-text-primary"
         >
          <Pencil size={13} />
         </button>
         <button
          type="button"
          onClick={() => removeChart(chart.id)}
          aria-label={`Remove ${chart.title}`}
          className="rounded-md p-1.5 text-text-muted hover:bg-status-critical/10 hover:text-status-critical"
         >
          <Trash2 size={13} />
         </button>
        </>
       }
      />
     ))}
     <button
      type="button"
      onClick={addBlankQueryChart}
      className="flex min-h-[140px] flex-col items-center justify-center gap-1.5 rounded-xl border border-dashed border-border-light text-sm font-semibold text-primary hover:border-primary/40 hover:bg-primary-light/20"
     >
      <Plus size={16} />
      Add a chart
     </button>
    </div>

    {!aiSuggested && (
     <div className="mt-3">
      <MetricPicker readyKpis={readyKpis} selectedIdentifiers={selectedIdentifiers} onAdd={addMetricAsChart} />
     </div>
    )}
   </div>

   <div className="mb-5">
    <div className="mb-1 flex items-center justify-between">
     <p className="text-sm font-semibold text-text-primary">Dashboard filters</p>
     {filterColumns.length > 0 && (
      <span className="inline-flex items-center rounded-full bg-primary-light px-2 py-0.5 text-[11px] font-semibold text-primary">
       {filterColumns.length} selected
      </span>
     )}
    </div>
    <p className="mb-2 text-xs text-text-muted">
     Pick columns viewers can filter this dashboard by. Each checked column becomes a dropdown
     on the published dashboard, populated with that column&rsquo;s values.
    </p>

    {visibleCandidates.length === 0 ? (
     <p className="rounded-lg border border-dashed border-border-light px-3 py-2.5 text-xs text-text-muted">
      Add a chart with a custom SQL query to make some of its columns available as filters.
     </p>
    ) : (
     <div className="overflow-hidden rounded-lg border border-border">
      {visibleCandidates.map(({ table, column }, index) => {
       const selected = isFilterSelected(table, column);
       return (
        <button
         key={`${table}.${column}`}
         type="button"
         onClick={() => toggleFilterColumn(table, column)}
         className={`flex w-full items-center gap-2.5 px-3 py-2 text-left ${
          index > 0 ? "border-t border-border" : ""
         } ${selected ? "bg-primary-light" : "hover:bg-surface-secondary"}`}
        >
         <span
          className={`flex h-4 w-4 shrink-0 items-center justify-center rounded ${
           selected ? "bg-primary" : "border border-border-light"
          }`}
         >
          {selected && <Check size={11} className="text-white" strokeWidth={3} />}
         </span>
         <span
          className={`flex-1 text-sm ${selected ? "font-semibold text-primary" : "font-medium text-text-tertiary"}`}
         >
          {column}
         </span>
         <span className="font-mono text-[11px] text-text-muted">
          {table}.{column}
         </span>
        </button>
       );
      })}
     </div>
    )}
   </div>

   {charts.length === 0 && (
    <div className="mb-5 flex items-start gap-2 rounded-lg border border-status-warning/30 bg-status-warning/10 px-3 py-2.5 text-xs text-text-secondary">
     <AlertTriangle size={15} className="mt-0.5 shrink-0 text-status-warning" />
     <p>Add at least one chart to create the dashboard.</p>
    </div>
   )}

   <div className="mb-0">
    <label htmlFor="db-refresh" className="flex items-center gap-1.5 text-sm font-semibold text-text-primary">
     <RefreshCw size={13} />
     Refresh the numbers
    </label>
    <select
     id="db-refresh"
     value={refresh}
     onChange={(e) => setRefresh(e.target.value)}
     className="mt-1.5 w-full rounded-lg border border-border bg-surface-default px-3 py-2 text-sm text-text-primary focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
    >
     {refreshOptions.map((o) => (
      <option key={o.value} value={o.value}>
       {o.label}
      </option>
     ))}
    </select>
    <p className="mt-1 text-xs text-text-muted">
     Charts read from the warehouse on this schedule, never on open.
    </p>
   </div>

   {error && (
    <p className="mt-5 rounded-lg border border-status-critical/30 bg-status-critical/10 px-3 py-2 text-xs font-medium text-status-critical">
     {error}
    </p>
   )}

   <div className="mt-5 flex items-center justify-end gap-2 border-t border-border pt-4">
    <button
     type="button"
     onClick={onCancel}
     className="rounded-lg px-3.5 py-2 text-sm font-semibold text-text-secondary hover:text-text-primary"
    >
     Cancel
    </button>
    <button
     type="submit"
     disabled={!canSubmit}
     className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-primary-hover disabled:cursor-not-allowed disabled:opacity-50"
    >
     <CalendarClock size={15} />
     {submitting ? "Creating…" : "Create dashboard"}
    </button>
   </div>

   {addingChart && !editingChart && (
    <ChartEditorPanel
     chart={addingChart}
     onClose={() => setAddingChart(null)}
     onSave={(saved) => {
      setCharts([...charts, saved]);
      setAddingChart(null);
     }}
    />
   )}

   {editingChart && (
    <ChartEditorPanel
     chart={editingChart}
     onClose={() => setEditingId(null)}
     onSave={(updated) => {
      updateChart(editingChart.id, updated);
      setEditingId(null);
     }}
     onDelete={() => {
      removeChart(editingChart.id);
      setEditingId(null);
     }}
    />
   )}
  </form>
 );
}
