import { useMemo, useState } from "react";
import { AlertTriangle, CalendarClock, Check, Database, GitMerge, Info, Plus, Sparkles, XCircle } from "lucide-react";
import { mappingStatusForKpi } from "../../domain/kpi-mapping";
import { getDatasetById } from "../../domain/kpi-data-catalog";
import KpiFocusPrompt from "./KpiFocusPrompt";

/**
 * Discovery view of the KPI mapping screen: "given the data you connected, what can you measure?"
 *
 * This is the same relationship the admin table shows, read in the other direction. The table
 * starts from a metric and asks which data backs it; this starts from the connected data and asks
 * which metrics that supports. One underlying binding, two readings -- which is why they are two
 * views of one screen rather than two screens that would drift apart.
 *
 * The page is split ONCE, by feasibility, and sorted by relevance inside each bucket. Splitting by
 * both at the top level produces "available" and "available but less relevant", which nobody can
 * act on.
 *
 * Blocked metrics are shown, never hidden: the gap list is the most commercially useful output
 * here, because it tells the customer exactly what to fix to unlock the rest.
 */

/** Distinct source systems behind a KPI, in stable order. Empty when nothing is mapped. */
function sourcesFor(kpi) {
 const labels = (kpi.datasetIds || [])
  .map((id) => getDatasetById(id)?.sourceLabel)
  .filter(Boolean);
 return [...new Set(labels)].sort();
}

const BUCKET_IDS = new Set(["ready", "partial", "blocked"]);

/**
 * Feasibility bucket for a row. A suggestion carries the server's verdict -- a set difference over
 * source tables that already uses these three words. A real registry KPI (C1, #232) carries the
 * server's own join-aware `feasibility.verdict` on the KPI itself, the same three ids, so it maps
 * straight across. Everything else (seed KPIs) is still judged from `mappingStatusForKpi`'s local
 * dataset mapping. A suggestion with no verdict is not known to be ready, so it is blocked.
 */
function bucketFor(row) {
 if (row.suggested) {
  const verdict = row.suggestion?.feasibility?.verdict;
  return BUCKET_IDS.has(verdict) ? verdict : "blocked";
 }
 if (row.kpi.fromRegistry) return row.kpi.feasibility?.verdict || "blocked";
 const status = mappingStatusForKpi(row.kpi);
 if (status === "linked") return "ready";
 if (status === "review") return "partial";
 return "blocked";
}

const BUCKETS = [
 {
  id: "ready",
  title: "Ready to measure",
  blurb: "Every field these need is present in your connected data.",
  Icon: Check,
  cls: "bg-status-success-light text-status-success border-status-success-border",
  dot: "bg-status-success",
 },
 {
  id: "partial",
  title: "Partially available",
  blurb: "Close, but not yet reliable: a piece is missing, stale or unresolved. Each card names it.",
  Icon: AlertTriangle,
  cls: "bg-status-warning-light text-status-warning border-status-warning-border",
  dot: "bg-status-warning",
 },
 {
  id: "blocked",
  title: "Not yet possible",
  blurb: "Nothing in your connected data backs these. The missing piece is named on each card.",
  Icon: XCircle,
  cls: "bg-status-critical-light text-status-critical border-status-critical-border",
  dot: "bg-status-critical",
 },
];

/**
 * Provenance badge. Where a definition came from changes how far you should trust it: a curated
 * metric has a vetted formula behind it, an inferred one is a guess from column names. Shown as a
 * badge rather than as a section, because the user cares whether a metric is useful and works --
 * not where we got it.
 */
function ProvenanceBadge({ inferred, suggested, fromRegistry }) {
 if (suggested) {
  return (
   <span className="inline-flex items-center gap-1 rounded-full bg-status-info-light px-2 py-0.5 text-[11px] font-semibold text-status-info">
    <Sparkles size={10} />
    Suggested — not in your registry
   </span>
  );
 }
 if (fromRegistry) {
  return (
   <span className="inline-flex items-center gap-1 rounded-full bg-status-success-light px-2 py-0.5 text-[11px] font-semibold text-status-success">
    <Check size={10} />
    In your KPI registry
   </span>
  );
 }
 if (!inferred) {
  return (
   <span className="inline-flex items-center gap-1 rounded-full bg-surface-tertiary px-2 py-0.5 text-[11px] font-semibold text-text-secondary">
    Retail standard
   </span>
  );
 }
 return (
  <span className="inline-flex items-center gap-1 rounded-full bg-status-info-light px-2 py-0.5 text-[11px] font-semibold text-status-info">
   <Sparkles size={10} />
   Inferred from your data
  </span>
 );
}

const CONFIDENCE = {
 high: { label: "High confidence", cls: "bg-status-success-light text-status-success" },
 medium: { label: "Medium confidence", cls: "bg-status-warning-light text-status-warning" },
 low: { label: "Low confidence", cls: "bg-surface-tertiary text-text-secondary" },
};

/** The agent's own confidence. Not a probability: a hint about which suggestions to read first. */
function ConfidenceBadge({ level }) {
 const c = CONFIDENCE[level];
 if (!c) return null;
 return (
  <span
   title="How strongly the evidence supports this suggestion. Not a probability; read it as which cards to look at first."
   className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-semibold ${c.cls}`}
  >
   {c.label}
  </span>
 );
}

/**
 * The tables the formula reads, each marked present or missing from the semantic layer. This is
 * the evidence behind both reason lines: the agent cites these tables, the executor subtracts them.
 */
function EvidenceChips({ suggestion }) {
 const tables = suggestion.derived_from_tables || [];
 const missing = new Set(suggestion.feasibility?.missing_tables || []);
 return (
  <>
   {tables.map((table) =>
    missing.has(table) ? (
     <span
      key={table}
      title="Not in the semantic layer yet"
      className="inline-flex items-center gap-1 rounded-full border border-status-critical-border bg-status-critical-light px-2 py-0.5 font-mono text-[11px] font-medium text-status-critical"
     >
      <XCircle size={10} />
      {table}
     </span>
    ) : (
     <span
      key={table}
      title="Present in the semantic layer"
      className="inline-flex items-center gap-1 rounded-full bg-surface-tertiary px-2 py-0.5 font-mono text-[11px] font-medium text-text-secondary"
     >
      <Database size={10} />
      {table}
     </span>
    )
   )}
   {tables.length > 1 && (
    <span
     title="Reads more than one table. Whether they join is checked by the executor, not assumed by the agent."
     className="inline-flex items-center gap-1 rounded-full border border-status-warning-border bg-status-warning-light px-2 py-0.5 text-[11px] font-semibold text-status-warning"
    >
     <GitMerge size={10} />
     Joins {tables.length}
    </span>
   )}
  </>
 );
}

const VERDICT_LEAD = {
 partial: { text: "One piece short.", bar: "border-status-warning", lead: "text-status-warning" },
 blocked: { text: "Needs data you haven't connected.", bar: "border-status-critical", lead: "text-status-critical" },
};

/**
 * The two reasons a suggestion arrives with, kept apart because they come from different places.
 * `rationale` is the agent's: why this metric follows from these tables. `feasibility.rationale`
 * is the executor's: whether those tables are actually here. When the verdict is negative the
 * executor's line leads, because "why you can't have it yet" is the one a person can act on; the
 * agent's line stays underneath so the proposal itself is still judgeable.
 */
function SuggestionReasons({ suggestion, bucket }) {
 if (bucket === "ready" && !suggestion.proposed_sql) {
  return (
   <p className="border-l-2 border-status-warning pl-2.5 text-xs text-text-secondary">
    <span className="font-semibold text-status-warning">No formula was proposed for this one.</span>{" "}
    The data is there, but the agent didn&apos;t write a query it was confident in.
   </p>
  );
 }
 if (bucket === "ready") {
  return (
   <p className="border-l-2 border-status-info pl-2.5 text-xs text-text-secondary">{suggestion.rationale}</p>
  );
 }
 const v = VERDICT_LEAD[bucket];
 return (
  <div className="space-y-1.5">
   <p className={`border-l-2 pl-2.5 text-xs text-text-secondary ${v.bar}`}>
    <span className={`font-semibold ${v.lead}`}>{v.text}</span>{" "}
    {suggestion.feasibility?.rationale || "The feasibility check did not run for this suggestion."}
   </p>
   <p className="pl-3 text-xs text-text-muted">
    <span className="font-semibold">Why it was proposed:</span> {suggestion.rationale}
   </p>
  </div>
 );
}

function KpiCard({ row, bucket, selected, onToggle, onOpenDetail, onAdopt, adopting, adoptError }) {
 const { kpi } = row;
 const datasets = (kpi.datasetIds || []).map((id) => getDatasetById(id)).filter(Boolean);
 const sources = sourcesFor(kpi);
 // A candidate is not master data yet, so it cannot be put on a dashboard until it is adopted.
 // Blocked metrics stay inspectable -- "why can't I have this?" is answered in the panel.
 const selectable = bucket !== "blocked" && !row.suggested;
 // Why a card cannot be picked, said on the disabled box itself: a suggestion is not a
 // governed KPI yet (a dashboard tile needs a registered identifier, so "Add to my metrics"
 // comes first), and a blocked metric has nothing to compute from. A dashed box that just
 // sits there reads as broken.
 // `text` is the plain form (for assistive tech); `node` is what the hover tooltip renders,
 // which is a real element rather than a `title` so the action can be set in bold italics.
 const notSelectableReason = row.suggested
  ? {
     text: "\"Add to my metrics first\" — a dashboard can only be built on a registered KPI.",
     node: (
      <>
       <strong className="font-bold italic text-text-primary">"Add to my metrics first"</strong> — a dashboard
       can only be built on a registered KPI.
      </>
     ),
    }
  : { text: "Needs data you haven't connected.", node: "Needs data you haven't connected." };

 return (
  <div
   role={selectable ? "button" : undefined}
   tabIndex={selectable ? 0 : undefined}
   onClick={selectable ? onToggle : undefined}
   onKeyDown={selectable ? (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onToggle(); } } : undefined}
   className={`flex flex-col gap-2.5 rounded-xl border bg-surface p-4 text-left transition-shadow ${
    selected ? "border-primary ring-2 ring-primary/20" : "border-border hover:shadow-md"
   } ${selectable ? "cursor-pointer" : "opacity-75"}`}
  >
   <div className="flex items-start gap-2.5">
    {selectable ? (
     <span
      className={`mt-0.5 flex h-[18px] w-[18px] shrink-0 items-center justify-center rounded-[5px] border ${
       selected ? "border-primary bg-primary text-white" : "border-border-light bg-surface"
      }`}
     >
      {selected && <Check size={12} />}
     </span>
    ) : (
     <span className="group relative mt-0.5 shrink-0">
      <span
       role="img"
       aria-label={`Not selectable: ${notSelectableReason.text}`}
       className="block h-[18px] w-[18px] cursor-not-allowed rounded-[5px] border border-dashed border-border-light"
      />
      <span
       role="tooltip"
       className="pointer-events-none absolute left-0 top-6 z-20 hidden w-64 rounded-lg border border-border bg-surface px-3 py-2 text-xs font-normal leading-snug text-text-secondary shadow-lg group-hover:block"
      >
       {notSelectableReason.node}
      </span>
     </span>
    )}
    <div className="min-w-0 flex-1">
     <h4 className="text-sm font-bold text-text-primary">{kpi.name}</h4>
     {kpi.subjectArea && <p className="text-xs text-text-muted">{kpi.subjectArea}</p>}
    </div>
    {/* Separate affordance, because the card body already means "select". Clicking a card to
        both pick it and open a panel would be one gesture doing two jobs. */}
    <button
     type="button"
     title="Definition, datasets, use cases"
     aria-label={`Details for ${kpi.name}`}
     onClick={(e) => { e.stopPropagation(); onOpenDetail?.(row); }}
     className="-mr-1 -mt-1 shrink-0 rounded-md p-1 text-text-muted hover:bg-surface-tertiary hover:text-primary"
    >
     <Info size={15} />
    </button>
   </div>

   {(kpi.desc || kpi.description) && (
    <p className="text-xs text-text-secondary line-clamp-2">{kpi.desc || kpi.description}</p>
   )}

   {/* Why we proposed it, and whether it can be computed. Without this a suggestion is an
       assertion, and the user has no way to judge whether to trust it. */}
   {row.suggested ? (
    <>
     <SuggestionReasons suggestion={row.suggestion} bucket={bucket} />
     {adoptError && (
      <p className="border-l-2 border-status-critical pl-2.5 text-xs text-status-critical">{adoptError}</p>
     )}
    </>
   ) : (
    <>
     {/* Blocked: name the gap. This line is the reason blocked cards are shown at all. */}
     {bucket === "blocked" && (
      <p className="border-l-2 border-status-critical pl-2.5 text-xs text-text-secondary">
       <span className="font-semibold text-status-critical">Needs data you haven&apos;t connected.</span>{" "}
       No dataset in your workspace carries the fields this metric requires.
      </p>
     )}

     {bucket === "partial" && (
      <p className="border-l-2 border-status-warning pl-2.5 text-xs text-text-secondary">
       <span className="font-semibold text-status-warning">Check before relying on this.</span>{" "}
       A mapped dataset is stale or unresolved.
      </p>
     )}
    </>
   )}

   <div className="mt-auto flex flex-wrap items-center gap-1.5 pt-1">
    <ProvenanceBadge inferred={!!kpi.inferred} suggested={!!row.suggested} fromRegistry={!!kpi.fromRegistry} />
    {row.suggested && <ConfidenceBadge level={row.suggestion.confidence} />}
    {row.suggested && <EvidenceChips suggestion={row.suggestion} />}
    {/* One source is a read; two is a JOIN, and a join we inferred is where a plausible-but-wrong
        number comes from. So the two cases get different treatment rather than N flat pills. */}
    {sources.length > 1 ? (
     <span
      className="inline-flex items-center gap-1 rounded-full border border-status-warning-border bg-status-warning-light px-2 py-0.5 text-[11px] font-semibold text-status-warning"
      title={`Joins ${sources.join(" and ")}. The join path is inferred — confirm it in the semantic layer before relying on this.`}
     >
      <GitMerge size={10} />
      Joins {sources.join(" + ")}
     </span>
    ) : (
     sources.map((label) => (
      <span
       key={label}
       className="inline-flex items-center gap-1 rounded-full bg-surface-tertiary px-2 py-0.5 text-[11px] font-medium text-text-secondary"
       title={datasets.map((d) => d.technicalName).join("\n")}
      >
       <Database size={10} />
       {label}
      </span>
     ))
    )}
    {/* A suggestion can only become a real, governed KPI when it is actually ready AND has a
        formula: `POST /kpis` validates the formula at construction and requires non-null `sql`,
        and a `ready` verdict does not guarantee `proposed_sql` -- feasibility is judged purely
        on table presence, independent of whether the agent wrote a query. "Why can't I adopt
        this" is answered by the reason lines above instead. */}
    {row.suggested && bucket === "ready" && row.suggestion.proposed_sql && (
     <button
      type="button"
      disabled={adopting}
      onClick={(e) => { e.stopPropagation(); onAdopt?.(row); }}
      className="ml-auto inline-flex items-center gap-1 rounded-md border border-primary/40 px-2 py-0.5 text-[11px] font-semibold text-primary hover:bg-primary-light disabled:cursor-not-allowed disabled:opacity-60"
     >
      <Plus size={11} />
      {adopting ? "Adding…" : "Add to my metrics"}
     </button>
    )}
   </div>
  </div>
 );
}

export default function KpiDiscovery({
 rows,
 areas,
 focusAreaIds,
 onFocusChange,
 onCreateDashboard,
 onOpenDetail,
 onAdopt,
 adoptingIds,
 adoptErrors,
}) {
 const [selected, setSelected] = useState(() => new Set());
 // null means "every source" -- the default. Source is a facet here, not a gate: this screen's
 // object is a metric, and the product's value is correlation ACROSS systems, so making people
 // choose systems before seeing anything would work against the thing being sold.
 const [mutedSources, setMutedSources] = useState(() => new Set());

 const allSources = useMemo(() => {
  const set = new Set();
  for (const row of rows) for (const label of sourcesFor(row.kpi)) set.add(label);
  return [...set].sort();
 }, [rows]);

 // Muting a source means "show me what I could measure WITHOUT it", so a metric survives only if
 // EVERY source it needs is still active. `some` would be wrong and quietly misleading: a metric
 // that joins SAP and Snowflake would keep showing as measurable after Snowflake was excluded,
 // which is the opposite of what the question is asking.
 //
 // Metrics with no source at all are never filtered out. "Not yet possible" is not a property of
 // any connected system, and that gap list is the most useful output on the page.
 const visibleRows = useMemo(() => {
  if (mutedSources.size === 0) return rows;
  return rows.filter((row) => {
   const src = sourcesFor(row.kpi);
   if (src.length === 0) return true;
   return src.every((label) => !mutedSources.has(label));
  });
 }, [rows, mutedSources]);

 // Feasibility first, relevance second. A focused area sorts ahead of everything else; within a
 // relevance tier, order is stable by name so the list does not reshuffle between renders.
 const buckets = useMemo(() => {
  const focus = new Set(focusAreaIds);
  const out = { ready: [], partial: [], blocked: [] };
  for (const row of visibleRows) {
   out[bucketFor(row)].push(row);
  }
  for (const key of Object.keys(out)) {
   out[key].sort((a, b) => {
    const aF = focus.has(a.kpi?.businessAreaId) ? 0 : 1;
    const bF = focus.has(b.kpi?.businessAreaId) ? 0 : 1;
    if (aF !== bF) return aF - bF;
    return String(a.kpi?.name || "").localeCompare(String(b.kpi?.name || ""));
   });
  }
  return out;
 }, [visibleRows, focusAreaIds]);

 // The bar reports a count, but the caller needs the rows themselves -- resolved here so the
 // selection stays a set of stable keys rather than a set of objects that re-render into new
 // identities.
 const selectedRows = useMemo(
  () => visibleRows.filter((r) => selected.has(`${r.categoryIndex}-${r.sectionIndex}-${r.kpiIndex}`)),
  [visibleRows, selected]
 );

 const answered = focusAreaIds.length > 0;

 const toggle = (key) =>
  setSelected((prev) => {
   const next = new Set(prev);
   next.has(key) ? next.delete(key) : next.add(key);
   return next;
  });

 const rowKey = (r) => `${r.categoryIndex}-${r.sectionIndex}-${r.kpiIndex}`;

 return (
  <div className="space-y-6">
   <KpiFocusPrompt areas={areas} selectedIds={focusAreaIds} onChange={onFocusChange} />

   {answered && allSources.length > 1 && (
    <div className="flex flex-wrap items-center gap-2 text-sm">
     <span className="text-text-muted">Sources</span>
     {allSources.map((label) => {
      const on = !mutedSources.has(label);
      return (
       <button
        key={label}
        type="button"
        aria-pressed={on}
        onClick={() =>
         setMutedSources((prev) => {
          const next = new Set(prev);
          next.has(label) ? next.delete(label) : next.add(label);
          return next;
         })
        }
        className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-semibold transition-colors ${
         on
          ? "border-border-light bg-surface text-text-primary"
          : "border-dashed border-border-light bg-transparent text-text-muted line-through"
        }`}
       >
        <Database size={11} />
        {label}
       </button>
      );
     })}
     {mutedSources.size > 0 && (
      <button
       type="button"
       onClick={() => setMutedSources(new Set())}
       className="text-xs font-semibold text-primary hover:underline"
      >
       Show all
      </button>
     )}
    </div>
   )}

   {/* Nothing is ranked until the question is answered -- showing an arbitrary order first and
       re-sorting after would make the ranking look like it did nothing. */}
   {!answered && (
    <p className="rounded-xl border border-dashed border-border-light bg-surface-secondary px-4 py-8 text-center text-sm text-text-muted">
     Pick at least one area above to see what your connected data can measure.
    </p>
   )}

   {answered && (
    <>
     {BUCKETS.map(({ id, title, blurb, Icon, cls, dot }) => {
      const list = buckets[id];
      if (list.length === 0) return null;
      return (
       <section key={id}>
        <div className="mb-3 flex flex-wrap items-baseline gap-x-3 gap-y-1">
         <h3 className="flex items-center gap-2 text-base font-bold text-text-primary">
          <span className={`flex h-6 w-6 items-center justify-center rounded-full border ${cls}`}>
           <Icon size={13} />
          </span>
          {title}
          <span className={`ml-1 rounded-full px-2 py-0.5 text-xs font-semibold ${cls}`}>{list.length}</span>
         </h3>
         <p className="text-sm text-text-muted">{blurb}</p>
        </div>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
         {list.map((row) => (
          <KpiCard
           key={rowKey(row)}
           row={row}
           bucket={id}
           selected={selected.has(rowKey(row))}
           onToggle={() => toggle(rowKey(row))}
           onOpenDetail={onOpenDetail}
           onAdopt={onAdopt}
           adopting={!!row.suggestion && adoptingIds.has(row.suggestion.identifier)}
           adoptError={row.suggestion && adoptErrors?.[row.suggestion.identifier]}
          />
         ))}
        </div>
       </section>
      );
     })}

     {/* Selection bar. Sticks to the bottom so the action stays reachable while scrolling a long
         list -- the alternative is scrolling back to a toolbar after every pick. */}
     {selected.size > 0 && (
      <div className="sticky bottom-4 z-20 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border-light bg-surface px-4 py-3 shadow-lg">
       <p className="text-sm">
        <span className="font-bold text-text-primary">{selected.size} selected</span>
        <span className="text-text-secondary"> · all buildable from connected data</span>
       </p>
       <div className="flex flex-wrap items-center gap-2">
        <button
         type="button"
         onClick={() => setSelected(new Set())}
         className="rounded-lg px-3 py-2 text-sm font-semibold text-text-secondary hover:text-text-primary"
        >
         Clear
        </button>
        <button
         type="button"
         onClick={() => onCreateDashboard?.(selectedRows)}
         className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-primary-hover"
        >
         <CalendarClock size={15} />
         Create dashboard
        </button>
       </div>
      </div>
     )}
    </>
   )}
  </div>
 );
}
