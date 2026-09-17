import { useEffect, useState } from "react";
import { Play, Trash2 } from "lucide-react";
import SlidePanel from "../../ui/SlidePanel";
import ChartTile, { GuardPill } from "../ChartTile";
import { fetchSemanticTable } from "../../../services/semantic-tables-api";
import { previewDashboardQuery } from "../../../services/dashboards-api";
import { tileDataFromPreview } from "../../../storage/dashboards";

const CHART_OPTIONS = [
 { value: "number", label: "Number" },
 { value: "line", label: "Line" },
 { value: "bar", label: "Bar" },
 { value: "table", label: "Table" },
];

/** One column of the schema pane -- the same description/meaning text the compose agent was
 * shown, so a person editing SQL by hand has the same vocabulary the model had. */
function SchemaTable({ table }) {
 if (!table) return null;
 return (
  <div className="mb-3 overflow-hidden rounded-lg border border-border">
   <div className="flex items-center justify-between bg-surface-tertiary px-2.5 py-1.5">
    <span className="font-mono text-[11px] font-medium text-text-primary">{table.ref?.name}</span>
    {table.ref?.row_count != null && (
     <span className="text-[10px] text-text-muted">{table.ref.row_count.toLocaleString()} rows</span>
    )}
   </div>
   {table.description && (
    <p className="border-b border-border px-2.5 py-1.5 text-[11px] text-text-secondary">{table.description}</p>
   )}
   {(table.columns || []).map((column) => (
    <div
     key={column.name}
     className={`grid grid-cols-[100px_1fr] gap-2 border-t border-border px-2.5 py-1.5 text-[11px] ${
      column.is_personal_data ? "opacity-60" : ""
     }`}
    >
     <code className="text-text-primary">{column.name}</code>
     {column.is_personal_data ? (
      <span className="font-semibold uppercase tracking-wide text-status-critical">personal data · blocked</span>
     ) : (
      <span className="text-text-secondary">{column.meaning || column.description || <em className="text-text-muted">No description</em>}</span>
     )}
    </div>
   ))}
  </div>
 );
}

/**
 * The chart editor: title, chart kind, SQL (editable for a query chart, read-only for a kpi
 * chart with a "detach and edit as query" escape hatch), a Run preview against the real
 * warehouse read guard, a description field, and the schema pane. Opens from the pencil on
 * any chart in the review step or on a saved dashboard.
 *
 * `chart` is the review-step shape (a `DashboardChartSuggestion`-like object, possibly with a
 * `preview` already attached); `onSave(chart)` gets the edited version back, `onDelete` (when
 * given) removes it, `onClose` cancels.
 */
export default function ChartEditorPanel({ chart, onSave, onDelete, onClose }) {
 const [title, setTitle] = useState(chart.title || "");
 const [kind, setKind] = useState(chart.chart);
 const [sql, setSql] = useState(chart.query?.sql || "");
 const [description, setDescription] = useState(chart.description || "");
 const [detached, setDetached] = useState(chart.source === "query");
 const [preview, setPreview] = useState(chart.preview || null);
 const [guard, setGuard] = useState(chart.guard || null);
 const [running, setRunning] = useState(false);
 const [tables, setTables] = useState({});

 const isQuery = detached;
 const sourceTables = isQuery ? preview?.guard?.tables || chart.query?.source_tables || [] : chart.query?.source_tables || [];
 // What this chart already declared as its tables when the editor opened, before any edit --
 // narrows the preview's own guard check to that set (round-1 review, finding #5: the client
 // never passed a table scope at all, so a preview here checked against the entire semantic
 // layer regardless of what the chart's own schema pane claimed it may read). Left `undefined`
 // for a brand-new or just-detached chart (nothing declared yet to narrow to) rather than an
 // empty array -- the backend reads an *empty* `table_names` as "no table is in scope", which
 // would refuse the very first preview outright rather than leaving it unscoped.
 const [initialSourceTables] = useState(chart.query?.source_tables || []);

 useEffect(() => {
  let cancelled = false;
  const missing = sourceTables.filter((name) => !(name in tables));
  if (!missing.length) return undefined;
  Promise.all(
   missing.map((name) => fetchSemanticTable(name).then((t) => [name, t]).catch(() => [name, null])),
  ).then((entries) => {
   if (!cancelled) setTables((prev) => ({ ...prev, ...Object.fromEntries(entries) }));
  });
  return () => {
   cancelled = true;
  };
  // eslint-disable-next-line react-hooks/exhaustive-deps
 }, [JSON.stringify(sourceTables)]);

 // The SQL this editor opened with -- already live (persisted and previously validated), just
 // composed (guarded and previewed by the run that wrote it), or, for a brand-new/just-detached
 // chart, empty. Trusted as-is unless the current guard verdict is `refused`, so editing an
 // unrelated field (the title, the description) on an otherwise-untouched chart never requires
 // a fresh preview -- while a composed chart the guard refused cannot be saved untouched
 // (the server would 422 it anyway; the pill already says why, so Save says so too rather
 // than letting the person find out from the write). Read live off `guard`, not a second
 // boolean frozen from `chart.guard` at open time: `guard` already starts as `chart.guard`
 // and only moves on a fresh `runPreview()`, so the two could never legitimately disagree --
 // a separate `originalRefused` would just be another place for that fact to go stale.
 // `previewedSql` is the text of the most recent `runPreview()` call
 // this session, or `null` before the first one. Together these are what `canSave` checks
 // (round-1 review, finding #9): editing `sql` away from both leaves neither satisfied, so
 // Save is blocked until a fresh preview backs the new text -- closing the gap where editing
 // SQL after a preview left the old "Guard ok" pill showing, and Save persisted the new text
 // alongside a verdict that was never actually checked against it. The backend re-validates on
 // every write regardless (a stale-but-saved refusal would still 422), so this is a UI-trust
 // fix, not a security one -- but the pill claiming "checked" for unchecked SQL is the bug.
 const originalSql = chart.query?.sql || "";
 const [previewedSql, setPreviewedSql] = useState(null);

 const runPreview = async () => {
  setRunning(true);
  try {
   const result = await previewDashboardQuery(
    { sql },
    initialSourceTables.length ? { tableNames: initialSourceTables } : {},
   );
   setPreview(result);
   setGuard(result.guard);
  } catch (err) {
   setGuard({ status: "refused", detail: err?.detail || err?.message || "The preview could not run." });
   setPreview(null);
  } finally {
   setPreviewedSql(sql);
   setRunning(false);
  }
 };

 const sqlIsOriginal = sql === originalSql && guard?.status !== "refused";
 const sqlMatchesLastPreview = previewedSql !== null && sql === previewedSql;
 const previewOk = sqlMatchesLastPreview && guard?.status !== "refused";
 const canSave =
  !isQuery || (sql.trim().length > 0 && (sqlIsOriginal || previewOk) && !running);

 const save = () => {
  if (!canSave) return;
  const base = {
   ...chart,
   title,
   chart: kind,
   description,
   guard: guard || chart.guard,
   preview,
  };
  if (isQuery) {
   onSave({
    ...base,
    source: "query",
    kpi_identifier: null,
    query: { ...chart.query, sql, source_tables: preview?.guard?.tables || chart.query?.source_tables || [] },
   });
  } else {
   onSave(base);
  }
 };

 return (
  <SlidePanel
   title="Edit chart"
   subtitle={isQuery ? "Composed query" : `From metric · ${chart.kpi_identifier || ""}`}
   onClose={onClose}
   widthClass="max-w-[880px]"
   footer={
    <div className="flex w-full items-center justify-between">
     {onDelete ? (
      <button
       type="button"
       onClick={onDelete}
       className="inline-flex items-center gap-1.5 text-xs font-semibold text-status-critical hover:underline"
      >
       <Trash2 size={13} />
       Delete chart
      </button>
     ) : (
      <span />
     )}
     <div className="flex items-center gap-2">
      <button type="button" onClick={onClose} className="rounded-lg px-3.5 py-2 text-sm font-semibold text-text-secondary hover:text-text-primary">
       Cancel
      </button>
      <button
       type="button"
       onClick={save}
       disabled={!canSave}
       title={
        !canSave && isQuery
         ? sql.trim().length === 0
           ? "Write a query before saving."
           : guard?.status === "refused" && sql === originalSql
             ? "The read guard refused this query. Change it and run the preview."
             : "Run the preview again -- the query changed since it was last checked."
         : undefined
       }
       className="rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-white hover:bg-primary-hover disabled:cursor-not-allowed disabled:opacity-50"
      >
       Save chart
      </button>
     </div>
    </div>
   }
  >
   <div className="grid grid-cols-1 gap-5 md:grid-cols-[1.6fr_1fr]">
    <div className="min-w-0 space-y-4">
     <div>
      <label className="mb-1 block text-xs font-semibold text-text-secondary">Title</label>
      <input
       value={title}
       onChange={(e) => setTitle(e.target.value)}
       className="w-full rounded-md border border-border bg-surface-default px-2.5 py-1.5 text-sm text-text-primary focus:border-primary focus:outline-none"
      />
     </div>

     <div>
      <label className="mb-1 block text-xs font-semibold text-text-secondary">Chart</label>
      <div className="inline-flex overflow-hidden rounded-md border border-border-light text-xs">
       {CHART_OPTIONS.map((o) => (
        <button
         key={o.value}
         type="button"
         onClick={() => setKind(o.value)}
         className={`border-r border-border-light px-2.5 py-1 font-medium last:border-r-0 ${
          kind === o.value ? "bg-primary-light font-semibold text-primary" : "text-text-secondary hover:bg-surface-secondary"
         }`}
        >
         {o.label}
        </button>
       ))}
      </div>
     </div>

     <div>
      <div className="mb-1 flex items-center justify-between">
       <label className="text-xs font-semibold text-text-secondary">Query</label>
       {!isQuery && (
        <button
         type="button"
         onClick={() => setDetached(true)}
         className="text-[11px] font-semibold text-primary hover:underline"
        >
         Detach and edit as query
        </button>
       )}
      </div>
      <textarea
       readOnly={!isQuery}
       value={isQuery ? sql : chart.kpi_identifier ? `-- reused from metric ${chart.kpi_identifier}` : ""}
       onChange={(e) => setSql(e.target.value)}
       rows={10}
       spellCheck={false}
       className={`w-full resize-y rounded-md border border-border-light bg-[#14161f] px-3 py-2.5 font-mono text-xs leading-relaxed text-[#d9dcef] focus:border-primary focus:outline-none ${
        !isQuery ? "opacity-70" : ""
       }`}
      />
      <div className="mt-2 flex flex-wrap items-center gap-2">
       {isQuery && (
        <button
         type="button"
         onClick={runPreview}
         disabled={running || !sql.trim()}
         className="inline-flex items-center gap-1.5 rounded-md border border-border-strong bg-surface-default px-2.5 py-1 text-xs font-semibold text-text-primary hover:bg-surface-secondary disabled:opacity-50"
        >
         <Play size={12} />
         {running ? "Running…" : "Run preview"}
        </button>
       )}
       {guard ? <GuardPill guard={guard} /> : null}
       {preview && !preview.error && (
        <span className="text-[11px] text-text-muted" style={{ fontVariantNumeric: "tabular-nums" }}>
         {preview.rows.length} rows · {preview.duration_ms} ms
        </span>
       )}
      </div>
      {guard?.detail && guard.status !== "ok" && (
       <p className={`mt-1.5 rounded-md border px-2 py-1.5 text-[11px] ${guard.status === "refused" ? "border-status-critical/30 bg-status-critical/5 text-text-secondary" : "border-status-warning/30 bg-status-warning/5 text-text-secondary"}`}>
        {guard.detail}
       </p>
      )}
      {preview?.error && (
       <p className="mt-1.5 rounded-md border border-status-critical/30 bg-status-critical/5 px-2 py-1.5 text-[11px] text-text-secondary">
        The warehouse refused it: {preview.error}
       </p>
      )}
     </div>

     <div>
      <label className="mb-1 block text-xs font-semibold text-text-secondary">Description</label>
      <textarea
       value={description}
       onChange={(e) => setDescription(e.target.value)}
       rows={3}
       className="w-full resize-none rounded-md border border-border bg-surface-default px-2.5 py-1.5 text-sm leading-relaxed text-text-primary focus:border-primary focus:outline-none"
      />
      <p className="mt-1 text-[11px] text-text-muted">Shown under the chart. Not used to run the query.</p>
     </div>

     {preview && !preview.error && (
      <ChartTile
       tile={{ ...chart, chart: kind, title, description, query: { ...chart.query, sql: preview.sql || sql } }}
       data={tileDataFromPreview({ ...chart, chart: kind, query: { ...chart.query, sql } }, preview)}
      />
     )}
    </div>

    <div className="min-w-0">
     <p className="mb-2 text-xs font-semibold text-text-secondary">Tables this chart may read</p>
     {sourceTables.length === 0 && <p className="text-xs text-text-muted">Run the preview to see which tables it reads.</p>}
     {sourceTables.map((name) => (
      <SchemaTable key={name} table={tables[name]} />
     ))}
    </div>
   </div>
  </SlidePanel>
 );
}
