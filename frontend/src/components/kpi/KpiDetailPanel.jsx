import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { AlertTriangle, Check, Copy, Pencil, Plus, Trash2, X, ExternalLink } from "lucide-react";
import { mappingConfidenceForKpi } from "../../domain/kpi-mapping";
import { getDatasetById } from "../../domain/kpi-data-catalog";
import { getCatalogEntriesForKpiName } from "../../domain/kpi-usecase-links";
import KpiTrend from "./KpiTrend";
import { bucketFor, NOT_SELECTABLE_REASONS, SUGGESTION_RATIONALE_LABEL, SUGGESTION_VALUE_LABEL } from "./KpiDiscovery";

/**
 * The definition surface for one KPI: description, unit, level, mapping confidence, the datasets
 * that feed it, and what uses it. For a registry KPI, also its value over the last 30 nights
 * (`KpiTrend`, from `GET /kpis/{id}/series`) -- the one place on the discovery page a number is
 * shown, and every one of them a real nightly materialisation of the formula.
 *
 * Shared deliberately. Discovery ("what can my data measure?") and the master-data table ("what
 * backs this metric?") both need it, and a second, view-local panel is exactly how the two would
 * start disagreeing about what a KPI means. The trend is optional (`trend` prop): a caller that
 * has no series state simply gets the definition surface it always had.
 *
 * A real registry KPI (`fromRegistry`) gets a second tab, "Where this number comes from" -- its
 * business definition shape, the tables its formula reads and their load freshness, the formula
 * itself, and a trust/audit summary -- and its edit/remove actions move to a third, "Edit
 * definition". A suggestion or seed/template row has none of that server-side data to show, so
 * it keeps the single scrolling layout this panel always had; nothing about that path changed.
 */
const FEASIBILITY_STYLE = {
 ready: { cls: "border-status-success/35 bg-status-success-light dark:bg-status-success/15", label: "text-status-success" },
 partial: { cls: "border-status-warning/35 bg-status-warning-light dark:bg-status-warning/15", label: "text-status-warning" },
 blocked: { cls: "border-status-critical/35 bg-status-critical-light dark:bg-status-critical/15", label: "text-status-critical" },
};

/** `{length: 30, unit: "day"}` -> "Trailing 30 days". Plural-safe; `unit` is one of the
 * contract's `TimeWindowUnit` values (day/week/month/quarter/year), already singular. */
function formatTimeWindow(tw) {
 if (!tw?.length || !tw?.unit) return null;
 return `Trailing ${tw.length} ${tw.unit}${tw.length === 1 ? "" : "s"}`;
}

/** "2026-09-17T01:08:00Z" -> "17 Sept 2026, 01:08" in the viewer's own locale reading order,
 * short enough for an inline label. `null`/invalid input renders as `null`, never "Invalid Date". */
function formatTimestamp(value) {
 if (!value) return null;
 const d = new Date(value);
 if (Number.isNaN(d.getTime())) return null;
 return d.toLocaleString(undefined, { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

/** Today, `YYYY-MM-DD`, for the "Re-run for another date" picker's default/max. */
function todayIso() {
 return new Date().toISOString().slice(0, 10);
}

/** The shared description+SQL editor for both an unpersisted suggestion draft and a real
 * registry row -- one caller passes `saving`/`error` for its async PATCH, the other leaves
 * them at their defaults because its "save" never leaves the browser. */
function EditKpiForm({ description, sql, onDescriptionChange, onSqlChange, onSave, onCancel, saving = false, error = null, note }) {
 return (
  <div className="space-y-3 rounded-xl border border-border bg-surface-secondary p-4">
   <div>
    <label className="text-[10px] font-semibold uppercase tracking-wide text-text-muted">Description</label>
    <textarea
     value={description}
     onChange={(e) => onDescriptionChange(e.target.value)}
     rows={2}
     disabled={saving}
     className="mt-1 w-full rounded-lg border border-border bg-surface-default px-3 py-2 text-sm text-text-primary focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20 disabled:opacity-60"
    />
   </div>
   <div>
    <label className="text-[10px] font-semibold uppercase tracking-wide text-text-muted">SQL</label>
    <textarea
     value={sql}
     onChange={(e) => onSqlChange(e.target.value)}
     rows={5}
     spellCheck={false}
     disabled={saving}
     className="mt-1 w-full rounded-lg border border-border bg-surface-default px-3 py-2 font-mono text-xs text-text-primary focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20 disabled:opacity-60"
    />
   </div>
   {note && <p className="text-xs text-text-muted">{note}</p>}
   {error && (
    <p role="alert" className="flex items-start gap-2 text-xs text-status-critical">
     <AlertTriangle size={13} className="mt-0.5 shrink-0" />
     <span>Couldn't save: {error}</span>
    </p>
   )}
   <div className="flex items-center gap-3">
    <button
     type="button"
     onClick={onSave}
     disabled={saving}
     className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-xs font-semibold text-white transition-colors hover:bg-primary-hover disabled:cursor-not-allowed disabled:opacity-60"
    >
     {saving ? "Saving…" : "Save"}
    </button>
    <button
     type="button"
     onClick={onCancel}
     disabled={saving}
     className="text-xs font-semibold text-text-secondary hover:text-text-primary disabled:opacity-60"
    >
     Cancel
    </button>
   </div>
  </div>
 );
}

const TABS = [
 { id: "overview", label: "Overview" },
 { id: "definition", label: "Where this number comes from" },
 { id: "edit", label: "Edit definition" },
];

function TabBar({ active, onChange }) {
 return (
  <div className="flex shrink-0 items-center gap-1 border-b border-border px-5" role="tablist">
   {TABS.map((t) => (
    <button
     key={t.id}
     type="button"
     role="tab"
     aria-selected={active === t.id}
     onClick={() => onChange(t.id)}
     className={`border-b-2 px-1 py-2.5 text-sm font-semibold transition-colors ${
      active === t.id
       ? "border-primary text-primary"
       : "border-transparent text-text-secondary hover:text-text-primary"
     } ${t.id !== "overview" ? "ml-4" : ""}`}
    >
     {t.label}
    </button>
   ))}
  </div>
 );
}

/** A short uppercase-label card, the same shape every section in this panel already uses --
 * `Pill` is the small chips row a couple of the new sections below want on top of it. */
function DetailSection({ label, children, className = "" }) {
 return (
  <section className={className}>
   <p className="text-[10px] font-semibold uppercase tracking-wide text-text-muted">{label}</p>
   {children}
  </section>
 );
}

function Pill({ children }) {
 return (
  <span className="inline-flex items-center rounded-full border border-border-light bg-surface-secondary px-2.5 py-1 text-xs font-medium text-text-secondary">
   {children}
  </span>
 );
}

/** "Where this number comes from": the business-definition shape, the tables the formula reads
 * (and their load freshness, when the warehouse could answer), the formula itself, and a
 * trust/audit summary -- everything here is a real field off `GET /kpis/{id}` or its `series`,
 * never computed or guessed client-side. Registry-only (`kpi.fromRegistry`); the caller never
 * mounts this tab for a suggestion or seed row. */
function WhereItComesFrom({ kpi, feasibility, feasibilityStyle, trend, onComputeForDate }) {
 const [copied, setCopied] = useState(false);
 const [redate, setRedate] = useState(() => todayIso());
 const windowLabel = formatTimeWindow(kpi.timeWindow);
 const trendData = trend?.status === "ready" ? trend.data : null;
 const computing = trend?.status === "computing";
 const parameters = Array.isArray(kpi.parameters) ? kpi.parameters : [];
 const sourceTables = Array.isArray(kpi.sourceTables) ? kpi.sourceTables : [];
 const statusByTable = Object.fromEntries(
  (Array.isArray(kpi.sourceTableStatus) ? kpi.sourceTableStatus : []).map((s) => [s.table, s])
 );

 const copySql = () => {
  if (!kpi.sql || !navigator.clipboard) return;
  navigator.clipboard.writeText(kpi.sql).then(() => {
   setCopied(true);
   window.setTimeout(() => setCopied(false), 1500);
  });
 };

 return (
  <div className="space-y-6">
   <DetailSection label="Business definition">
    <div className="mt-2 flex flex-wrap gap-1.5">
     <Pill>{kpi.resultShape === "rowset" ? "Table of values" : "One number, no breakdown"}</Pill>
     {windowLabel && <Pill>{windowLabel}</Pill>}
     {kpi.dialect && <Pill>{kpi.dialect}</Pill>}
     {kpi.concept && <Pill>{kpi.concept}</Pill>}
    </div>
    {parameters.length > 0 && (
     <ul className="mt-3 space-y-1.5">
      {parameters.map((p) => (
       <li key={p.name} className="text-sm text-text-secondary">
        <span className="font-mono text-xs text-text-primary">{p.name}</span>
        <span className="text-text-muted"> · {p.kind}</span>
        {p.description ? <span> — {p.description}</span> : null}
       </li>
      ))}
     </ul>
    )}
   </DetailSection>

   <DetailSection label="The data it reads">
    {sourceTables.length > 0 ? (
     <ul className="mt-2 space-y-2">
      {sourceTables.map((table) => {
       const status = statusByTable[table];
       return (
        <li key={table} className="rounded-lg border border-border bg-surface-secondary px-3 py-2">
         <p className="font-mono text-xs text-text-primary">{table}</p>
         {status ? (
          <p className="mt-1 text-xs text-text-muted">
           {status.row_count != null ? `${status.row_count.toLocaleString()} rows` : "Row count unknown"}
           {formatTimestamp(status.loaded_at) ? ` · loaded ${formatTimestamp(status.loaded_at)}` : ""}
           {status.load_verdict ? ` · ${status.load_verdict}` : ""}
          </p>
         ) : (
          <p className="mt-1 text-xs text-text-muted">No load recorded yet.</p>
         )}
        </li>
       );
      })}
     </ul>
    ) : (
     <p className="mt-2 text-sm text-text-muted">No source tables recorded.</p>
    )}
   </DetailSection>

   <DetailSection label="Technical">
    {kpi.sql ? (
     <div className="mt-2 space-y-2">
      <pre className="overflow-x-auto rounded-lg bg-surface-secondary p-3 text-xs text-text-primary whitespace-pre-wrap break-words">
       {kpi.sql}
      </pre>
      <button
       type="button"
       onClick={copySql}
       className="inline-flex items-center gap-1.5 text-xs font-semibold text-primary hover:underline"
      >
       {copied ? <Check size={13} /> : <Copy size={13} />}
       {copied ? "Copied" : "Copy SQL"}
      </button>
     </div>
    ) : (
     <p className="mt-2 text-sm text-text-muted">
      The formula is only shown to someone who can edit KPIs.
     </p>
    )}
   </DetailSection>

   <DetailSection label="Trust">
    <dl className="mt-2 space-y-2 text-sm">
     <div className="flex justify-between gap-3">
      <dt className="text-text-muted">Validated against</dt>
      <dd className="text-right text-text-primary">{kpi.validatedAgainstSnapshot || "Not yet validated"}</dd>
     </div>
     {feasibility && (
      <div className="flex justify-between gap-3">
       <dt className="text-text-muted">Sources present</dt>
       <dd className={`text-right font-semibold capitalize ${feasibilityStyle?.label || ""}`}>{feasibility.verdict}</dd>
      </div>
     )}
     {trendData && (
      <>
       <div className="flex justify-between gap-3">
        <dt className="text-text-muted">Last computed</dt>
        <dd className="text-right text-text-primary">{formatTimestamp(trendData.computed_at) || "—"}</dd>
       </div>
       <div className="flex justify-between gap-3">
        <dt className="text-text-muted">As of</dt>
        <dd className="text-right text-text-primary">{formatTimestamp(trendData.as_of) || "—"}</dd>
       </div>
       {trendData.provenance_ref && (
        <div className="flex justify-between gap-3">
         <dt className="text-text-muted">Run</dt>
         <dd className="text-right font-mono text-xs text-text-primary">{trendData.provenance_ref}</dd>
        </div>
       )}
      </>
     )}
    </dl>

    {onComputeForDate && (
     <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-border pt-3">
      <label className="text-xs font-semibold text-text-secondary" htmlFor="kpi-redate">
       Re-run for another date
      </label>
      <input
       id="kpi-redate"
       type="date"
       value={redate}
       max={todayIso()}
       onChange={(e) => setRedate(e.target.value)}
       disabled={computing}
       className="rounded-lg border border-border bg-surface-default px-2 py-1 text-xs text-text-primary disabled:opacity-60"
      />
      <button
       type="button"
       disabled={computing || !redate}
       onClick={() => onComputeForDate(redate)}
       className="inline-flex items-center rounded-lg bg-primary px-3 py-1.5 text-xs font-semibold text-white transition-colors hover:bg-primary-hover disabled:cursor-not-allowed disabled:opacity-60"
      >
       {computing ? "Running…" : "Re-run"}
      </button>
      {trend?.status === "error" && trend.error && (
       <p className="w-full text-xs text-status-critical">{trend.error}</p>
      )}
     </div>
    )}
   </DetailSection>
  </div>
 );
}

export default function KpiDetailPanel({
 detailRow,
 nameToIdsMap,
 profileId,
 processLabelById,
 onEdit,
 onClose,
 trend = null,
 onTrendRetry,
 onTrendCompute,
 onTrendComputeForDate,
 onTrendPickDimension,
 onRemove,
 removing = false,
 removeError = null,
 onAdopt,
 adopting = false,
 adoptError = null,
 onSaveEdit,
 savingEdit = false,
 editError = null,
}) {
 // "Remove from my metrics" asks once before it acts: the row is gone from the registry for
 // good, and there is no undo beyond adopting it again. Confirmed inline, not in a modal --
 // the panel is already the focused surface.
 const [confirmingRemove, setConfirmingRemove] = useState(false);
 // A suggestion's description/sql and a registry KPI's are both editable in place here (a
 // suggestion is never persisted until adopted, so its edit just rewrites the local draft; a
 // registry row's edit is a real `PATCH /kpis/{id}` -- see `onSaveEdit` below). One draft, not
 // two: the two cases share the same fields and the same form, only what "save" does differs.
 const [editingDraft, setEditingDraft] = useState(false);
 const [draftDescription, setDraftDescription] = useState("");
 const [draftSql, setDraftSql] = useState("");
 // Which of the three tabs a registry row shows; meaningless (and never rendered) for a
 // suggestion or seed row, which keep the single scrolling layout below.
 const [activeTab, setActiveTab] = useState("overview");
 // Closes the form -- and returns to Overview -- whenever the caller hands this panel a
 // genuinely different row state, which includes the row the caller itself produces right
 // after a successful save (both `KpiDiscoveryPage.jsx`'s suggestion-draft patch and its
 // post-PATCH registry refresh rebuild `detailRow`). A save that *fails* never touches
 // `detailRow`, so the form -- and whatever the customer typed -- stays exactly as they left it.
 useEffect(() => {
  setEditingDraft(false);
  setActiveTab("overview");
 }, [detailRow]);
 // A real registry KPI (C1, #232) and a live suggestion (F1, #234) both carry the server's
 // own join-aware `feasibility` -- a set difference over the tables the semantic graph
 // actually holds -- and that is what is shown below. `mappingConfidenceForKpi`'s score is
 // the prototype's heuristic over the seed catalogue's local dataset links, which a real row
 // never has: rendered for one, it always says "18 / 100, no enterprise datasets linked", a
 // statement about the demo simulation, not about the KPI. Seed cards keep it.
 const fromRegistry = !!detailRow.kpi.fromRegistry;
 const suggested = !!(detailRow.suggested && detailRow.suggestion);
 const conf =
  fromRegistry || suggested ? null : mappingConfidenceForKpi(detailRow.kpi, nameToIdsMap);
 // A suggestion's `feasibility` is optional in the contract -- the one live producer always
 // stamps it, but nothing enforces that. Fall back the same way the card view does (bucketFor,
 // SuggestionReasons in KpiDiscovery.jsx) rather than silently dropping the whole section.
 const feasibility = fromRegistry
  ? detailRow.kpi.feasibility
  : suggested
   ? detailRow.suggestion.feasibility || {
      verdict: "blocked",
      rationale: "The feasibility check did not run for this suggestion.",
     }
   : null;
 // Fall back to the "blocked" look for an unmapped verdict, matching bucketFor's own
 // "no verdict is not known ready" rule.
 const feasibilityStyle = feasibility ? FEASIBILITY_STYLE[feasibility.verdict] || FEASIBILITY_STYLE.blocked : null;
 const useCases = getCatalogEntriesForKpiName(detailRow.kpi.name, profileId);
 const processIds = Array.isArray(detailRow.kpi.processMapping) ? detailRow.kpi.processMapping : [];
 const datasetIds = Array.isArray(detailRow.kpi.datasetIds) ? detailRow.kpi.datasetIds : [];
 // Real and always present on a registry KPI's formula (unlike `datasetIds`, which nothing
 // in the adopt/create/edit flow currently sets) -- see the "Linked datasets" section below.
 const sourceTables = Array.isArray(detailRow.kpi.sourceTables) ? detailRow.kpi.sourceTables : [];
 const startEdit = () => {
  setDraftDescription((suggested ? detailRow.suggestion.description : detailRow.kpi.desc) || "");
  setDraftSql((suggested ? detailRow.suggestion.proposed_sql : detailRow.kpi.sql) || "");
  setEditingDraft(true);
 };
 const saveEdit = () => {
  onSaveEdit?.({ description: draftDescription, sql: draftSql });
  // A suggestion edit is a synchronous local patch (always "succeeds"), so nothing will change
  // `detailRow` for the effect above to react to -- close the form here instead. A registry
  // edit is the async PATCH case that effect already handles: `detailRow` only changes once
  // the save actually lands, so closing early here would hide a failure's error text.
  if (suggested) setEditingDraft(false);
 };

 // The remove/edit block a registry row shows on "Edit definition" -- unchanged content, just
 // relocated off the always-visible scroll it used to share with Overview. Read as a value, not
 // a component, so it closes over this render's locals the same way the inline JSX below always did.
 const editDefinitionTab = (
  <div className="space-y-6">
   {editingDraft ? (
    <EditKpiForm
     description={draftDescription}
     sql={draftSql}
     onDescriptionChange={setDraftDescription}
     onSqlChange={setDraftSql}
     onSave={saveEdit}
     onCancel={() => setEditingDraft(false)}
     saving={savingEdit}
     error={editError}
     note="Editing the formula moves this KPI back to review until it's revalidated against the semantic layer."
    />
   ) : (
    <button
     type="button"
     onClick={startEdit}
     className="inline-flex items-center gap-1.5 text-sm font-semibold text-primary hover:underline"
    >
     <Pencil size={14} />
     Edit description or SQL
    </button>
   )}

   {onRemove && (
    <section className="rounded-xl border border-border bg-surface-secondary p-4">
     {!confirmingRemove ? (
      <button
       type="button"
       onClick={() => setConfirmingRemove(true)}
       disabled={removing}
       className="inline-flex items-center gap-1.5 text-sm font-semibold text-status-critical hover:underline disabled:cursor-not-allowed disabled:opacity-60"
      >
       <Trash2 size={14} />
       Remove from my metrics
      </button>
     ) : (
      <div className="space-y-3">
       <p className="text-sm text-text-secondary">
        Remove <strong className="font-semibold text-text-primary">{detailRow.kpi.name}</strong> from your
        registry? Dashboards using it must be changed first; you can adopt it again later.
       </p>
       <div className="flex items-center gap-3">
        <button
         type="button"
         onClick={() => onRemove(detailRow)}
         disabled={removing}
         className="inline-flex items-center gap-1.5 rounded-lg bg-status-critical px-3 py-1.5 text-xs font-semibold text-white transition-colors hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-60"
        >
         <Trash2 size={13} />
         {removing ? "Removing…" : "Yes, remove"}
        </button>
        <button
         type="button"
         onClick={() => setConfirmingRemove(false)}
         disabled={removing}
         className="text-xs font-semibold text-text-secondary hover:text-text-primary"
        >
         Cancel
        </button>
       </div>
      </div>
     )}
     {removeError && (
      <p role="alert" className="mt-3 flex items-start gap-2 text-xs text-status-critical">
       <AlertTriangle size={13} className="mt-0.5 shrink-0" />
       <span>Couldn't remove it: {removeError}</span>
      </p>
     )}
    </section>
   )}
  </div>
 );

 const showOverview = !fromRegistry || activeTab === "overview";

 return (
 <>
 <button
 type="button"
 className="fixed inset-x-0 bottom-0 top-[72px] z-30 bg-[#0a0d12]/40 backdrop-blur-[1px]"
 aria-label="Close panel"
 onClick={onClose}
 />
 <aside className="fixed bottom-0 right-0 top-[72px] z-30 flex w-full max-w-md flex-col border-l border-border bg-surface-default shadow-2xl">
 <div className="flex items-center justify-between border-b border-border px-5 py-4">
 <h3 className="text-lg font-semibold text-text-primary">Mapping detail</h3>
 <button type="button" className="rounded-lg p-2 text-text-muted hover:bg-surface-secondary" onClick={onClose}>
 <X size={20} />
 </button>
 </div>
 {fromRegistry && <TabBar active={activeTab} onChange={setActiveTab} />}
 <div className="flex-1 space-y-6 overflow-y-auto px-5 py-4">
 {showOverview && (
 <>
 <section>
 <p className="text-[10px] font-semibold uppercase tracking-wide text-text-muted">KPI</p>
 <p className="mt-1 text-base font-semibold text-text-primary">{detailRow.kpi.name}</p>
 <p className="mt-2 text-sm text-text-secondary">{detailRow.kpi.desc || "-"}</p>
 {/* The same reason the card's disabled box gives on hover, said here in full: a proposal
     is not a governed KPI yet, so it cannot be picked for a dashboard until adopted. Skipped
     when the suggestion is itself blocked (missing data) -- adopting it wouldn't help, and the
     Feasibility section below already names the real reason, so this would just contradict it. */}
 {detailRow.suggested && bucketFor(detailRow) !== "blocked" && (
 <p className="mt-3 rounded-lg border border-dashed border-border-light bg-surface-secondary px-3 py-2 text-xs leading-snug text-text-secondary">
 {NOT_SELECTABLE_REASONS.adoptFirst.node}
 </p>
 )}
 </section>

 {/* The value, first: for a registry KPI it is the reason to open this panel after "Add to my
     metrics". Every point is gold's own nightly row for this formula (or today's, computed on
     adopt); a KPI with none yet says so and offers the compute, never a placeholder line. */}
 {fromRegistry && trend && (
 <KpiTrend
 state={trend}
 unit={detailRow.kpi.unit}
 resultShape={detailRow.kpi.resultShape}
 onRetry={onTrendRetry}
 onCompute={onTrendCompute}
 onPickDimension={onTrendPickDimension}
 />
 )}
 <section className="grid grid-cols-2 gap-3 text-sm">
 {/* Unit is registry-only: a suggestion has no unit until it is adopted into the registry, so
     showing a dash here would read as broken rather than as "not yet governed". */}
 {!detailRow.suggested ? (
 <div>
 <p className="text-[10px] font-semibold uppercase tracking-wide text-text-muted">Unit</p>
 <p className="mt-1 text-text-primary">{detailRow.kpi.unit || "-"}</p>
 </div>
 ) : null}
 {detailRow.kpi.level ? (
 <div>
 <p className="text-[10px] font-semibold uppercase tracking-wide text-text-muted">Level</p>
 <p className="mt-1 text-text-primary">{detailRow.kpi.level}</p>
 </div>
 ) : null}
 <div className="col-span-2">
 <p className="text-[10px] font-semibold uppercase tracking-wide text-text-muted">Function</p>
 <p className="mt-1 text-text-primary">{detailRow.sectionName}</p>
 </div>
 <div className="col-span-2">
 <p className="text-[10px] font-semibold uppercase tracking-wide text-text-muted">Business Area</p>
 <p className="mt-1 text-text-primary">{detailRow.categoryTitle}</p>
 </div>
 </section>

 {conf && (
 <section className="rounded-xl border border-primary/35 bg-primary-light dark:bg-primary/15 p-4">
 <p className="text-[10px] font-semibold uppercase tracking-wide text-primary">Mapping confidence</p>
 <div className="mt-2 flex items-baseline gap-2">
 <span className="text-2xl font-bold text-text-primary">{conf.score}</span>
 <span className="text-sm font-medium text-primary">/ 100</span>
 <span className="rounded-full bg-surface-default px-2 py-0.5 text-[11px] font-semibold text-[#5925a6]">{conf.label}</span>
 </div>
 <p className="mt-2 text-sm text-text-secondary">{conf.detail}</p>
 </section>
 )}

 {feasibility && (
 <section className={`rounded-xl border p-4 ${feasibilityStyle.cls}`}>
 <p className={`text-[10px] font-semibold uppercase tracking-wide ${feasibilityStyle.label}`}>Feasibility</p>
 <p className={`mt-2 text-lg font-bold capitalize ${feasibilityStyle.label}`}>{feasibility.verdict}</p>
 <p className="mt-2 text-sm text-text-secondary">{feasibility.rationale}</p>
 {feasibility.missing_tables?.length > 0 && (
 <p className="mt-2 text-xs text-text-muted">Missing: {feasibility.missing_tables.join(", ")}</p>
 )}
 </section>
 )}

 <section>
 <p className="text-[10px] font-semibold uppercase tracking-wide text-text-muted">Linked datasets</p>
 {datasetIds.length > 0 ? (
 <ul className="mt-2 space-y-2">
 {datasetIds.map(id => {
 const d = getDatasetById(id);
 return (
 <li key={id}>
 <Link
 to={`/kpis/gallery?ds=${encodeURIComponent(id)}`}
 className="flex items-start gap-2 rounded-lg border border-border bg-surface-secondary px-3 py-2 text-sm text-primary hover:bg-primary-light dark:bg-primary/20"
 onClick={onClose}
 >
 <ExternalLink size={16} className="mt-0.5 shrink-0" />
 <span className="min-w-0">
 <span className="block font-mono text-xs text-text-primary">{d?.technicalName || id}</span>
 {d && (
 <span className="text-xs text-text-muted">
 {d.sourceLabel} · {d.status === "stale" ? "Stale" : "Healthy"}
 </span>
 )}
 </span>
 </Link>
 </li>
 );
 })}
 </ul>
 ) : sourceTables.length > 0 ? (
 /* Not yet linked to a catalog dataset (nothing in the adopt/create/edit flow sets
     `dataset_ids` today -- tracked separately). `source_tables` is real and always
     present on a registry KPI's formula, so show what the SQL actually reads rather
     than a dead end. Plain tags, not the linked-dataset pills above: a raw table name
     has no gallery page to open and no health status to report. */
 <>
 <p className="mt-1 text-[11px] text-text-muted">
 Not yet linked to a catalog dataset -- tables this KPI&apos;s formula reads.
 </p>
 <ul className="mt-2 flex flex-wrap gap-1.5">
 {sourceTables.map((table) => (
 <li
 key={table}
 className="rounded-md bg-surface-secondary px-2 py-1 font-mono text-xs text-text-secondary"
 >
 {table}
 </li>
 ))}
 </ul>
 </>
 ) : (
 <p className="mt-2 text-sm text-text-muted">No datasets linked.</p>
 )}
 </section>

 <section>
 <p className="text-[10px] font-semibold uppercase tracking-wide text-text-muted">Use cases</p>
 <p className="mt-1 text-[11px] text-text-muted">Use cases in the library that move this KPI.</p>
 <ul className="mt-2 space-y-2">
 {useCases.length === 0 ? (
 <li className="text-sm text-text-muted">No use cases reference this KPI yet.</li>
 ) : (
 useCases.map((uc) => (
 <li key={uc.id}>
 <Link
 to={`/use-cases/gallery?highlight=${encodeURIComponent(uc.id)}`}
 className="flex items-center justify-between gap-2 rounded-lg border border-border bg-surface-secondary px-3 py-2 text-sm text-primary hover:bg-primary-light dark:bg-primary/20"
 onClick={onClose}
 >
 <span className="min-w-0 truncate text-text-primary">{uc.title}</span>
 <ExternalLink size={14} className="shrink-0 text-primary" />
 </Link>
 </li>
 ))
 )}
 </ul>
 </section>

 <section>
 <p className="text-[10px] font-semibold uppercase tracking-wide text-text-muted">Processes</p>
 <p className="mt-1 text-[11px] text-text-muted">Business processes this KPI measures.</p>
 <ul className="mt-2 space-y-2">
 {processIds.length === 0 ? (
 <li className="text-sm text-text-muted">No processes linked.</li>
 ) : (
 processIds.map((pid) => {
 const name = processLabelById?.[pid] || pid;
 return (
 <li key={pid}>
 <Link
 to={`/processes/${encodeURIComponent(pid)}`}
 className="flex items-center justify-between gap-2 rounded-lg border border-border bg-surface-secondary px-3 py-2 text-sm text-primary hover:bg-primary-light dark:bg-primary/20"
 onClick={onClose}
 >
 <span className="min-w-0 truncate text-text-primary">{name}</span>
 <ExternalLink size={14} className="shrink-0 text-primary" />
 </Link>
 </li>
 );
 })
 )}
 </ul>
 </section>

 {/* The same two sentences after adopt: a registry row carries the suggestion's rationale and
     business_value (contract 1.2.2), so the panel keeps answering "why is this here?" once the
     proposal is governed. Both are null on a hand-authored KPI -- then there is no section. */}
 {fromRegistry && (detailRow.kpi.rationale || detailRow.kpi.businessValue) && (
 <section className="rounded-xl border border-status-info/40 bg-status-info-light/40 p-4 dark:bg-status-info/10">
 {detailRow.kpi.rationale && (
 <>
 <p className="text-[10px] font-semibold uppercase tracking-wide text-status-info">{SUGGESTION_RATIONALE_LABEL}</p>
 <p className="mt-2 text-sm text-text-secondary">{detailRow.kpi.rationale}</p>
 </>
 )}
 {detailRow.kpi.businessValue && (
 <>
 <p className={`text-[10px] font-semibold uppercase tracking-wide text-primary ${detailRow.kpi.rationale ? "mt-3" : ""}`}>{SUGGESTION_VALUE_LABEL}</p>
 <p className="mt-2 text-sm text-text-secondary">{detailRow.kpi.businessValue}</p>
 </>
 )}
 </section>
 )}

 {suggested && (
 <section className="rounded-xl border border-status-info/40 bg-status-info-light/40 p-4 dark:bg-status-info/10">
 <p className="text-[10px] font-semibold uppercase tracking-wide text-status-info">{SUGGESTION_RATIONALE_LABEL}</p>
 <p className="mt-2 text-sm text-text-secondary">{detailRow.suggestion.rationale}</p>
 {detailRow.suggestion.business_value && (
 <>
 <p className="mt-3 text-[10px] font-semibold uppercase tracking-wide text-primary">{SUGGESTION_VALUE_LABEL}</p>
 <p className="mt-2 text-sm text-text-secondary">{detailRow.suggestion.business_value}</p>
 </>
 )}
 {detailRow.suggestion.derived_from_tables?.length > 0 && (
 <p className="mt-2 text-xs text-text-muted">
 Derived from: {detailRow.suggestion.derived_from_tables.join(", ")}
 {detailRow.suggestion.confidence ? ` · ${detailRow.suggestion.confidence} confidence` : ""}
 </p>
 )}
 {detailRow.suggestion.proposed_sql && (
 <>
 <p className="mt-3 text-[10px] font-semibold uppercase tracking-wide text-status-info">Proposed formula</p>
 <pre className="mt-1 overflow-x-auto rounded-lg bg-surface-default p-3 text-xs text-text-primary whitespace-pre-wrap break-words">
 {detailRow.suggestion.proposed_sql}
 </pre>
 </>
 )}
 </section>
 )}

 {suggested && (
 <div className="space-y-3">
 {editingDraft ? (
 <EditKpiForm
 description={draftDescription}
 sql={draftSql}
 onDescriptionChange={setDraftDescription}
 onSqlChange={setDraftSql}
 onSave={saveEdit}
 onCancel={() => setEditingDraft(false)}
 note="This edits the draft only -- feasibility and evidence above still describe the original suggestion until it's adopted."
 />
 ) : (
 <button
 type="button"
 onClick={startEdit}
 className="inline-flex items-center gap-1.5 text-sm font-semibold text-primary hover:underline"
 >
 <Pencil size={14} />
 Edit description or SQL
 </button>
 )}
 {/* Gated the same way the card's own adopt button is: only a `ready` suggestion with a
     formula can become a real KPI -- which an edit above can supply where the agent left
     none, or fix where feasibility (still the original suggestion's) says otherwise. */}
 {!editingDraft && bucketFor(detailRow) === "ready" && detailRow.suggestion.proposed_sql && (
 <div className="space-y-1.5">
 <button
 type="button"
 disabled={adopting}
 onClick={() => onAdopt?.(detailRow)}
 className="inline-flex items-center gap-1.5 text-sm font-semibold text-primary hover:underline disabled:cursor-not-allowed disabled:opacity-60"
 >
 <Plus size={14} />
 {adopting ? "Adding…" : "Add to my metrics"}
 </button>
 {adoptError && <p className="text-xs text-status-critical">{adoptError}</p>}
 </div>
 )}
 </div>
 )}
 {!suggested && !fromRegistry && (
 /* A tree/template candidate has a real position in Configure's mapping tree (unlike a
     registry row or a live suggestion, neither of which this local edit form can save to
     directly), so its edit still goes to the master-data screen that owns it. Unchanged from
     before tabs existed -- a registry row's equivalent now lives on "Edit definition". */
 <button
 type="button"
 onClick={onEdit}
 className="inline-flex text-sm font-semibold text-primary hover:underline"
 >
 Edit KPI →
 </button>
 )}
 </>
 )}

 {fromRegistry && activeTab === "definition" && (
  <WhereItComesFrom
   kpi={detailRow.kpi}
   feasibility={feasibility}
   feasibilityStyle={feasibilityStyle}
   trend={trend}
   onComputeForDate={onTrendComputeForDate}
  />
 )}

 {fromRegistry && activeTab === "edit" && editDefinitionTab}
 </div>
 </aside>
 </>
 );
}

// The relevance answer and the chosen view outlive a navigation, so they are persisted rather
// than held in component state -- localStorage only, the same layer every other preference in
// this app uses. Nothing here talks to a backend.
const VIEW_KEY = "retailgpt:kpi-mapping-view";
const FOCUS_KEY = "retailgpt:kpi-focus-areas";

/** Segmented control for the two readings of the same rows. */
function ViewToggle({ view, onChange }) {
 return (
  <div className="flex shrink-0 items-center rounded-lg border border-border bg-surface-secondary p-0.5">
   {[
    { id: "discovery", label: "What my data supports" },
    { id: "table", label: "All metrics" },
   ].map((v) => (
    <button
     key={v.id}
     type="button"
     onClick={() => onChange(v.id)}
     aria-pressed={view === v.id}
     className={`rounded-md px-3 py-1.5 text-sm font-semibold transition-colors ${
      view === v.id ? "bg-surface shadow-sm text-text-primary" : "text-text-secondary hover:text-text-primary"
     }`}
    >
     {v.label}
    </button>
   ))}
  </div>
 );
}
