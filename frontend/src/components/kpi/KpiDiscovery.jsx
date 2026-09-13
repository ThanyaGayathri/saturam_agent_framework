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

/** Feasibility bucket for a KPI. A real registry KPI (C1, #232) carries the server's own
 * join-aware `feasibility.verdict` -- `ready`/`partial`/`blocked`, the same three ids this
 * bucketing already uses, so it maps straight across. Everything else (seed, suggested) keeps
 * `mappingStatusForKpi`'s local heuristic, unchanged. */
function bucketFor(kpi) {
 if (kpi.fromRegistry) return kpi.feasibility?.verdict || "blocked";
 const status = mappingStatusForKpi(kpi);
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
  blurb: "Computable, but something is stale or only partly mapped. Worth a look before you rely on them.",
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

function KpiCard({ row, bucket, selected, onToggle, onOpenDetail, onAdopt }) {
 const { kpi } = row;
 const datasets = (kpi.datasetIds || []).map((id) => getDatasetById(id)).filter(Boolean);
 const sources = sourcesFor(kpi);
 // A candidate is not master data yet, so it cannot be put on a dashboard until it is adopted.
 // Blocked metrics stay inspectable -- "why can't I have this?" is answered in the panel.
 const selectable = bucket !== "blocked" && !row.suggested;

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
     <span className="mt-0.5 h-[18px] w-[18px] shrink-0 rounded-[5px] border border-dashed border-border-light" />
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

   {/* Why we proposed it. Without this a suggestion is an assertion, and the user has no way to
       judge whether to trust it. */}
   {row.suggested && kpi.suggestedReason && (
    <p className="border-l-2 border-status-info pl-2.5 text-xs text-text-secondary">{kpi.suggestedReason}</p>
   )}

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

   <div className="mt-auto flex flex-wrap items-center gap-1.5 pt-1">
    <ProvenanceBadge inferred={!!kpi.inferred} suggested={!!row.suggested} fromRegistry={!!kpi.fromRegistry} />
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
    {row.suggested && (
     <button
      type="button"
      onClick={(e) => { e.stopPropagation(); onAdopt?.(row); }}
      className="ml-auto inline-flex items-center gap-1 rounded-md border border-primary/40 px-2 py-0.5 text-[11px] font-semibold text-primary hover:bg-primary-light"
     >
      <Plus size={11} />
      Add to my metrics
     </button>
    )}
   </div>
  </div>
 );
}

export default function KpiDiscovery({ rows, areas, focusAreaIds, onFocusChange, onCreateDashboard, onOpenDetail, onAdopt }) {
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
   out[bucketFor(row.kpi)].push(row);
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
