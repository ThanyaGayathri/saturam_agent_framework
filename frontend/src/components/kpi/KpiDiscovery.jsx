import { useMemo, useState } from "react";
import {
 AlertTriangle,
 Briefcase,
 CalendarClock,
 Check,
 ChevronDown,
 CircleCheck,
 CircleHelp,
 CircleX,
 Clock,
 Database,
 Info,
 LayoutGrid,
 Plus,
 Sparkles,
 Zap,
} from "lucide-react";
import { mappingStatusForKpi } from "../../domain/kpi-mapping";
import { getDatasetById } from "../../domain/kpi-data-catalog";
import { getCatalogEntriesForKpiName } from "../../domain/kpi-usecase-links";
import { Select, Switch } from "../ui";
import KpiFocusPrompt from "./KpiFocusPrompt";

/**
 * Discovery view of the KPI mapping screen: "given the data you connected, what can you measure?"
 *
 * Grouped as a Verdict -> Business Area -> Subject Area -> KPI hierarchy: Verdict is the outermost
 * level (`VERDICT_ORDER`) because "what can I measure right now" is the question this page answers
 * first; Business Area -> Subject Area underneath still mirrors the enterprise KPI registry's own
 * shape. Feasibility gates what can be picked for a dashboard too (`bucketFor`,
 * `notSelectableReasonFor`).
 *
 * A Business Area or Subject Area whose KPIs span more than one verdict repeats under every
 * verdict section it has a matching KPI in (never deduplicated to a single "best" verdict) --
 * a KPI otherwise blocked or partial would silently disappear from a page whose whole point is
 * showing what isn't measurable yet. Each repeat is filtered to just that section's KPIs, and
 * carries a small cross-reference note pointing at the section's other appearances
 * (`subjectVerdictsMap` / `crossRefFor`). Picking a specific verdict from the "Verdict" filter
 * collapses back to a single flat Business Area -> Subject Area -> KPI tree -- with only one
 * verdict in view, the section-level chrome has nothing left to disambiguate.
 *
 * Two inputs, merged into the same tree (the seed "Retail standard" catalogue that used to be a
 * third is not mixed in here -- it has no real identifiers, so nothing on it could be adopted or
 * put on a dashboard):
 *
 *  1. SUGGESTED candidates -- metrics not in the registry yet, proposed live by a run of the
 *     `kpi_suggestion` agent. Every suggestion carries a real `business_area` (validated against
 *     the deployment's vocabulary), so it always finds a real Business Area group; it never
 *     carries a `subject_area`, so it always lands in that area's "Unsorted" group until adopted.
 *  2. REAL registry KPIs -- `GET /kpis`, each with a real `kpi_identifier`, `feasibility`, and
 *     (once adopted) a `subject_area` of its own.
 *
 * A registry row whose `business_area` matches nothing in the deployment's current vocabulary
 * (a stale row from before a rename, say) falls into a top-level "Uncategorized" group instead
 * of silently disappearing from the tree.
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
 * dataset mapping. A suggestion with no verdict is not known to be ready, so it is blocked; a
 * registry KPI whose verdict isn't one of the three canonical ids (backend enum drift) is too,
 * rather than falling through to a bucket id nothing downstream recognizes.
 */
export function bucketFor(row) {
 if (row.suggested) {
  const verdict = row.suggestion?.feasibility?.verdict;
  return BUCKET_IDS.has(verdict) ? verdict : "blocked";
 }
 if (row.kpi.fromRegistry) {
  const verdict = row.kpi.feasibility?.verdict;
  return BUCKET_IDS.has(verdict) ? verdict : "blocked";
 }
 const status = mappingStatusForKpi(row.kpi);
 if (status === "linked") return "ready";
 if (status === "review") return "partial";
 return "blocked";
}

/**
 * Shared with `KpiDetailPanel.jsx`, which renders the same two reasons in its own suggestion
 * section. One pair of constants instead of two hand-typed copies, so a wording change is a
 * one-line edit instead of a two-file, easy-to-miss one.
 */
export const SUGGESTION_RATIONALE_LABEL = "Why the model proposed it";
export const SUGGESTION_VALUE_LABEL = "What value it provides";

/**
 * Why a row cannot be picked for a dashboard. A suggestion can *also* land in the "blocked"
 * bucket (missing data), and for that row "adopt it first" is actively wrong: adopting won't
 * make it selectable, because the real blocker is data, not registry status. Blocked beats
 * suggested, so the reason always agrees with the bucket the row is actually stuck in.
 * Shared with `KpiDetailPanel`, which gives the identical reason in full rather than on hover.
 */
export const NOT_SELECTABLE_REASONS = {
 blocked: { text: "Needs data you haven't connected.", node: "Needs data you haven't connected." },
 adoptFirst: {
  text: "Click \"Add to my metrics\" first. Dashboards can only include KPIs that are in your registry.",
  node: (
   <>
    Click <strong className="font-bold italic text-text-primary">"Add to my metrics"</strong> first.
    Dashboards can only include KPIs that are in your registry.
   </>
  ),
 },
};

export function notSelectableReasonFor(row, bucket) {
 if (bucket === "blocked") return NOT_SELECTABLE_REASONS.blocked;
 if (row.suggested) return NOT_SELECTABLE_REASONS.adoptFirst;
 return NOT_SELECTABLE_REASONS.blocked;
}

const CONFIDENCE = {
 high: { label: "High confidence", cls: "bg-status-success-light text-status-success" },
 medium: { label: "Medium confidence", cls: "bg-status-warning-light text-status-warning" },
 low: { label: "Low confidence", cls: "bg-surface-tertiary text-text-secondary" },
};

const UNCATEGORIZED_BA_ID = "__uncategorized__";
const UNSORTED_SUBJECT = "__unsorted__";

const STATUS_FILTERS = [
 { value: "all", label: "Show all KPIs" },
 { value: "accepted", label: "Show accepted KPIs" },
 { value: "suggested", label: "Show only suggested KPIs" },
];

/** The outer grouping level. Order here is display order top-to-bottom. */
export const VERDICT_ORDER = ["ready", "partial", "blocked"];

export const VERDICT_FILTERS = [
 { value: "all", label: "All Verdicts" },
 { value: "ready", label: "Ready to measure" },
 { value: "partial", label: "Partially available" },
 { value: "blocked", label: "Not yet possible" },
];

/** Copy, icon and color for each verdict section header. Colors reuse the existing status tokens
 * (`KpiDetailPanel.jsx`'s `FEASIBILITY_STYLE` uses the same three) rather than inventing new ones. */
const VERDICT_META = {
 ready: {
  label: "Ready to measure",
  rule: "Every table the KPI's formula reads is present in your connected data — and, for a multi-table KPI, no join between them has been ruled out by review.",
  Icon: CircleCheck,
  border: "border-status-success/35",
  iconWrap: "border-status-success/35 bg-status-success-light dark:bg-status-success/20",
  iconColor: "text-status-success",
  badge: "bg-status-success-light text-status-success dark:bg-status-success/20",
 },
 partial: {
  label: "Partially available",
  rule: "Some of the source tables are present, not all. The KPI can't be computed yet, but the gap is one table rather than the whole subject area — the card names which.",
  Icon: Clock,
  border: "border-status-warning/35",
  iconWrap: "border-status-warning/35 bg-status-warning-light dark:bg-status-warning/20",
  iconColor: "text-status-warning",
  badge: "bg-status-warning-light text-status-warning dark:bg-status-warning/20",
 },
 blocked: {
  label: "Not yet possible",
  rule: "None of the source tables are present, or the tables are there but review has recorded that they must not be joined. The card names the missing piece.",
  Icon: CircleX,
  border: "border-status-critical/35",
  iconWrap: "border-status-critical/35 bg-status-critical-light dark:bg-status-critical/20",
  iconColor: "text-status-critical",
  badge: "bg-status-critical-light text-status-critical dark:bg-status-critical/20",
 },
};

function PoweredByCell({ kpiName, profileId, onOpen }) {
 const entries = useMemo(() => getCatalogEntriesForKpiName(kpiName, profileId), [kpiName, profileId]);
 const [expanded, setExpanded] = useState(false);
 if (!entries.length) return <span className="text-[13px] text-text-muted">-</span>;
 const MAX_VISIBLE = 2;
 const visible = expanded ? entries : entries.slice(0, MAX_VISIBLE);
 const extra = entries.length - MAX_VISIBLE;
 return (
  <div className="flex min-w-0 flex-wrap items-center gap-1">
   {visible.map((e) => (
    <button
     key={e.id}
     type="button"
     onClick={(ev) => { ev.stopPropagation(); onOpen(e.id); }}
     title={`Open ${e.title}`}
     className="inline-flex max-w-full min-w-0 items-center gap-1 truncate rounded-md border border-[#a9f0d1] bg-[#ecfdf3] px-1.5 py-0.5 text-[12px] font-medium text-[#067647] transition hover:border-[#067647] hover:bg-[#d1fadf] dark:border-[#067647]/35 dark:bg-[#067647]/10 dark:text-[#6ce9a6]"
    >
     <Zap className="h-3 w-3 shrink-0" strokeWidth={2} aria-hidden />
     <span className="truncate">{e.title}</span>
    </button>
   ))}
   {extra > 0 && !expanded ? (
    <button
     type="button"
     onClick={(ev) => { ev.stopPropagation(); setExpanded(true); }}
     title={`Show ${extra} more`}
     className="inline-flex items-center rounded-md border border-border bg-surface-secondary px-1.5 py-0.5 text-[12px] font-medium text-text-secondary transition hover:border-primary/40 hover:bg-primary-light hover:text-primary"
    >
     +{extra} more
    </button>
   ) : null}
   {expanded && entries.length > MAX_VISIBLE ? (
    <button
     type="button"
     onClick={(ev) => { ev.stopPropagation(); setExpanded(false); }}
     className="inline-flex items-center rounded-md border border-border bg-surface-secondary px-1.5 py-0.5 text-[12px] font-medium text-text-secondary transition hover:border-primary/40 hover:bg-primary-light hover:text-primary"
    >
     Show less
    </button>
   ) : null}
  </div>
 );
}

/** Dashboards this KPI has a tile on -- `dashboard_names` from `GET /kpis`. Always empty for a
 * suggestion, since it has no registry identifier for a dashboard tile to reference yet. */
function MapsToCell({ dashboardNames }) {
 const names = Array.isArray(dashboardNames) ? dashboardNames : [];
 if (names.length === 0) return <span className="text-[13px] text-text-muted">-</span>;
 const MAX_VISIBLE = 2;
 const visible = names.slice(0, MAX_VISIBLE);
 const extra = names.length - visible.length;
 return (
  <div className="flex min-w-0 flex-wrap items-center gap-1">
   {visible.map((name) => (
    <span
     key={name}
     className="inline-flex max-w-full min-w-0 items-center gap-1 truncate rounded-md border border-primary/25 bg-primary-light/50 px-1.5 py-0.5 text-[12px] font-medium text-primary dark:border-primary/35 dark:bg-surface-tertiary dark:ring-1 dark:ring-primary/25"
     title={name}
    >
     <LayoutGrid className="h-3 w-3 shrink-0" strokeWidth={2} aria-hidden />
     <span className="truncate">{name}</span>
    </span>
   ))}
   {extra > 0 ? (
    <span
     className="inline-flex items-center rounded-md border border-border bg-surface-secondary px-1.5 py-0.5 text-[12px] font-medium text-text-secondary"
     title={names.slice(MAX_VISIBLE).join(", ")}
    >
     +{extra} more
    </span>
   ) : null}
  </div>
 );
}

/** One Subject Area's KPI table: checkbox, name, description, maps to, powered by, status, actions.
 * `xref` (repeat mode only): a short "+ also in <other verdicts>" note when this same Subject Area
 * also has KPIs under a different verdict section. */
function KpiTable({
 title,
 countLabel,
 xref,
 rows,
 isOpen,
 onToggleOpen,
 selected,
 onToggleSelect,
 onOpenDetail,
 onAdopt,
 adoptingIds,
 adoptErrors,
 onToggleActive,
 togglingActiveIds,
 profileId,
 onOpenUseCase,
}) {
 const previewDesc = rows
  .map((r) => r.kpi.desc)
  .filter(Boolean)
  .slice(0, 2)
  .join(" · ");

 return (
  <div className="w-full min-w-0 overflow-hidden rounded-xl border border-border bg-surface-default shadow-[0_1px_3px_rgba(16,24,40,0.06)] dark:shadow-none">
   <button
    type="button"
    onClick={onToggleOpen}
    aria-expanded={isOpen}
    className="flex w-full min-w-0 items-center gap-2.5 border-b border-border bg-surface-secondary px-3 py-3 text-left sm:pl-4 sm:pr-3"
   >
    <ChevronDown
     className={`h-4 w-4 shrink-0 text-primary transition-transform duration-200 ${isOpen ? "rotate-180" : "rotate-0"}`}
     strokeWidth={2}
     aria-hidden
    />
    <LayoutGrid className="h-4 w-4 shrink-0 text-primary" strokeWidth={1.75} aria-hidden />
    <h4 className="shrink-0 text-[15px] font-bold leading-snug tracking-[-0.01em] text-text-primary">{title}</h4>
    <span className="shrink-0 rounded-md bg-surface-default/95 px-2 py-0.5 text-xs font-semibold text-text-tertiary ring-1 ring-border dark:bg-surface/10 dark:text-text-primary">
     {countLabel}
    </span>
    {xref ? (
     <span
      className="shrink-0 rounded-md border border-border bg-surface-tertiary px-1.5 py-0.5 text-[10px] font-bold text-text-tertiary dark:bg-surface/20"
      title={xref}
     >
      {xref}
     </span>
    ) : null}
    {!isOpen && previewDesc ? (
     <span className="min-w-0 flex-1 truncate text-[11px] leading-snug text-text-muted" title={previewDesc}>
      · {previewDesc}
      {rows.length > 2 ? "…" : ""}
     </span>
    ) : null}
   </button>

   {isOpen ? (
    <div className="overflow-x-auto bg-surface-default">
     <table className="w-full min-w-[58rem] table-fixed border-collapse text-left text-[13px]">
      <colgroup>
       <col style={{ width: "2.75rem" }} />
       <col style={{ width: "16%" }} />
       <col style={{ width: "27%" }} />
       <col style={{ width: "18%" }} />
       <col style={{ width: "18%" }} />
       <col style={{ width: "6.5rem" }} />
       <col style={{ width: "4.5rem" }} />
      </colgroup>
      <thead>
       <tr className="border-b border-border bg-surface-secondary">
        <th scope="col" className="px-2 py-2.5 pl-4 sm:pl-5" />
        <th scope="col" className="min-w-0 px-3 py-2.5 text-[10px] font-bold uppercase tracking-[0.08em] text-text-muted">
         <span className="inline-flex items-center gap-1">
          KPI name
          <CircleHelp className="h-3 w-3 text-text-muted" strokeWidth={2} aria-hidden />
         </span>
        </th>
        <th scope="col" className="min-w-0 px-3 py-2.5 text-[10px] font-bold uppercase tracking-[0.08em] text-text-muted">
         <span className="inline-flex items-center gap-1">
          Description
          <CircleHelp className="h-3 w-3 text-text-muted" strokeWidth={2} aria-hidden />
         </span>
        </th>
        <th
         scope="col"
         className="min-w-0 px-3 py-2.5 text-[10px] font-bold uppercase tracking-[0.08em] text-text-muted"
         title="Dashboards that have a tile built from this KPI."
        >
         <span className="inline-flex items-center gap-1">
          Maps to
          <CircleHelp className="h-3 w-3 text-text-muted" strokeWidth={2} aria-hidden />
         </span>
        </th>
        <th
         scope="col"
         className="min-w-0 px-3 py-2.5 text-[10px] font-bold uppercase tracking-[0.08em] text-text-muted"
         title="Prebuilt use cases this KPI can power."
        >
         <span className="inline-flex items-center gap-1">
          Powered by
          <CircleHelp className="h-3 w-3 text-text-muted" strokeWidth={2} aria-hidden />
         </span>
        </th>
        <th scope="col" className="w-[6.5rem] min-w-[6.5rem] max-w-[6.5rem] px-2 py-2.5 text-center text-[10px] font-bold uppercase tracking-[0.08em] text-text-muted">
         Status
        </th>
        <th scope="col" className="w-[4.5rem] min-w-[4.5rem] max-w-[4.5rem] px-2 py-2.5 text-right text-[10px] font-bold uppercase tracking-[0.08em] text-text-muted">
         Actions
        </th>
       </tr>
      </thead>
      <tbody className="divide-y divide-border">
       {rows.map((r, rowIdx) => {
        const key = `${r.categoryIndex}-${r.sectionIndex}-${r.kpiIndex}`;
        const bucket = bucketFor(r);
        const selectable = bucket !== "blocked" && !r.suggested;
        const reason = !selectable ? notSelectableReasonFor(r, bucket) : null;
        const isSelected = selected.has(key);
        const confidence = r.suggested ? CONFIDENCE[r.suggestion?.confidence] : null;
        return (
         <tr
          key={key}
          className={`transition-colors duration-150 hover:bg-surface-secondary ${rowIdx % 2 === 1 ? "bg-surface-secondary/50" : "bg-surface-default"}`}
         >
          <td className="align-middle px-2 py-3 pl-4 sm:pl-5">
           {selectable ? (
            <button
             type="button"
             role="checkbox"
             aria-checked={isSelected}
             aria-label={isSelected ? `Deselect ${r.kpi.name}` : `Select ${r.kpi.name}`}
             onClick={() => onToggleSelect(key)}
             className={`flex h-[18px] w-[18px] shrink-0 items-center justify-center rounded-[5px] border transition-colors ${
              isSelected ? "border-primary bg-primary text-white" : "border-border-light bg-surface hover:border-primary/50"
             }`}
            >
             {isSelected && <Check size={12} />}
            </button>
           ) : (
            <span className="group relative block">
             <span
              tabIndex={0}
              aria-disabled="true"
              aria-label={`Not selectable: ${reason.text}`}
              className="block h-[18px] w-[18px] cursor-not-allowed rounded-[5px] border border-dashed border-border-light focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/40"
             />
             <span
              role="tooltip"
              className="pointer-events-none absolute left-0 top-6 z-20 hidden w-56 rounded-lg border border-border bg-surface px-3 py-2 text-xs font-normal leading-snug text-text-secondary shadow-lg group-hover:block group-focus-within:block"
             >
              {reason.node}
             </span>
            </span>
           )}
          </td>
          <td className="min-w-0 align-middle px-3 py-3">
           <div className="flex min-w-0 max-w-full items-center gap-2.5">
            <span className="shrink-0 rounded border border-primary/30 bg-primary-light px-1.5 py-0.5 text-[10px] font-bold leading-none text-primary dark:border-primary/40 dark:bg-surface-tertiary dark:ring-1 dark:ring-primary/25">
             KPI
            </span>
            <span className="min-w-0 truncate text-[15px] font-semibold leading-snug text-text-primary" title={r.kpi.name}>
             {r.kpi.name}
            </span>
           </div>
          </td>
          <td className="min-w-0 align-top px-3 py-3 text-[13px] leading-[1.45] text-text-secondary">
           <span title={r.kpi.desc || undefined} className="line-clamp-2">
            {r.kpi.desc || "-"}
           </span>
          </td>
          <td className="min-w-0 align-middle px-3 py-3 text-[13px]">
           <MapsToCell dashboardNames={r.kpi.dashboardNames} />
          </td>
          <td className="min-w-0 align-middle px-3 py-3 text-[13px]">
           <PoweredByCell kpiName={r.kpi.name} profileId={profileId} onOpen={onOpenUseCase} />
          </td>
          <td className="w-[6.5rem] min-w-[6.5rem] max-w-[6.5rem] align-middle px-2 py-3">
           <div className="flex w-full flex-col items-center justify-center gap-1 [&_button]:shrink-0">
            {r.suggested ? (
             <span
              title={confidence ? confidence.label : undefined}
              className="inline-flex items-center gap-1 rounded-full bg-status-info-light px-2 py-0.5 text-[11px] font-semibold text-status-info"
             >
              <Sparkles size={10} />
              Suggested
             </span>
            ) : (
             <Switch
              aria-label={r.kpi.active ? `Disable KPI ${r.kpi.name ?? ""}` : `Enable KPI ${r.kpi.name ?? ""}`}
              checked={r.kpi.active}
              disabled={togglingActiveIds.has(r.kpi.identifier)}
              onCheckedChange={() => onToggleActive(r)}
              size="compact"
             />
            )}
           </div>
          </td>
          <td className="w-[4.5rem] min-w-[4.5rem] max-w-[4.5rem] align-middle px-1 py-3 pr-3 text-right">
           <div className="inline-flex items-center justify-end gap-0.5">
            {r.suggested && bucket === "ready" && r.suggestion.proposed_sql ? (
             <button
              type="button"
              disabled={adoptingIds.has(r.suggestion.identifier)}
              onClick={() => onAdopt?.(r)}
              title={adoptErrors?.[r.suggestion.identifier] || "Add to my metrics"}
              className="rounded-md p-1.5 text-primary opacity-80 transition-all hover:bg-primary-light hover:opacity-100 disabled:cursor-not-allowed disabled:opacity-50"
             >
              <Plus size={15} />
             </button>
            ) : null}
            <button
             type="button"
             className="rounded-md p-1.5 text-text-muted opacity-80 transition-all hover:bg-surface-secondary hover:opacity-100 hover:text-primary"
             aria-label={`Details for ${r.kpi.name}`}
             title="Definition, datasets, use cases"
             onClick={() => onOpenDetail?.(r)}
            >
             <Info size={15} />
            </button>
           </div>
          </td>
         </tr>
        );
       })}
      </tbody>
     </table>
    </div>
   ) : null}
  </div>
 );
}

/** Expand-all / Collapse-all button pair -- identical markup and behavior in both places a tree
 * of Business Areas gets rendered (`VerdictSection`'s header and the flat-tree header below),
 * each just wiring it to its own `setAllOpen(scope, groups, open)` call. */
function ExpandCollapseControls({ onExpandAll, onCollapseAll, className = "" }) {
 return (
  <div className={`flex shrink-0 items-center gap-1.5 text-xs font-semibold ${className}`}>
   <button type="button" onClick={onExpandAll} className="text-primary hover:underline">
    Expand all
   </button>
   <span className="text-border-light">·</span>
   <button type="button" onClick={onCollapseAll} className="text-primary hover:underline">
    Collapse all
   </button>
  </div>
 );
}

/** Verdict section wrapper: icon + label + rule + KPI/Subject-Area count + a per-section
 * expand/collapse-all, then whatever Business-Area tree is passed as children. Used only when the
 * Verdict filter is "all" -- a single chosen verdict renders as a plain flat tree instead, since
 * there's nothing left to disambiguate with one verdict in view. */
function VerdictSection({ bucket, result, onExpandAll, onCollapseAll, children }) {
 const meta = VERDICT_META[bucket];
 const Icon = meta.Icon;
 return (
  <div className={`mb-[18px] overflow-hidden rounded-xl border bg-surface-default ${meta.border}`}>
   <div className="flex flex-wrap items-center gap-3 px-3.5 py-4">
    <div className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border ${meta.iconWrap}`}>
     <Icon className={`h-5 w-5 ${meta.iconColor}`} strokeWidth={1.75} aria-hidden />
    </div>
    <h2 className={`text-[17px] font-bold tracking-tight ${meta.iconColor}`}>{meta.label}</h2>
    <span className={`rounded-md px-2.5 py-0.5 text-[12.5px] font-bold ${meta.badge}`}>
     {result.kpiCount} KPI{result.kpiCount === 1 ? "" : "s"}
     <span className="mx-1.5">·</span>
     {result.subjectAreaCount} Subject {result.subjectAreaCount === 1 ? "Area" : "Areas"}
    </span>
    <span className="shrink-0 text-text-muted" title={meta.rule}>
     <CircleHelp className="h-[13px] w-[13px]" strokeWidth={2} aria-hidden />
    </span>
    {result.groups.length > 0 && <ExpandCollapseControls onExpandAll={onExpandAll} onCollapseAll={onCollapseAll} className="ml-auto" />}
   </div>
   <p className="m-0 px-3.5 pb-4 pl-[70px] text-xs leading-relaxed text-text-secondary">{meta.rule}</p>
   <div className="flex flex-col gap-3 px-3.5 pb-5">
    {result.groups.length ? children : <p className="text-sm text-text-muted">No KPIs in this bucket match the current filters.</p>}
   </div>
  </div>
 );
}

export default function KpiDiscovery({
 rows,
 areas,
 areasStatus = "ready",
 areasError = null,
 onRetryAreas,
 focusAreaIds,
 onFocusChange,
 onCreateDashboard,
 onOpenDetail,
 onAdopt,
 adoptingIds,
 adoptErrors,
 onToggleActive,
 togglingActiveIds,
 profileId,
 onOpenUseCase,
}) {
 const [selected, setSelected] = useState(() => new Set());
 const [search, setSearch] = useState("");
 const [businessAreaFilter, setBusinessAreaFilter] = useState("");
 const [subjectAreaFilter, setSubjectAreaFilter] = useState("");
 const [statusFilter, setStatusFilter] = useState("all");
 const [verdictFilter, setVerdictFilter] = useState("all");
 // Source is a facet, not a gate: null/empty means "every source" -- the default. Muting one
 // means "show me what I could measure WITHOUT it", so a metric survives only if EVERY source
 // it needs is still active; a metric with no source at all is never filtered out.
 const [mutedSources, setMutedSources] = useState(() => new Set());
 // Keyed by a scope prefix ("flat" for the single-tree view, or a verdict id for a repeat-mode
 // section) so the same Business Area/Subject Area can be open in one verdict section and
 // collapsed in another -- they're independent trees, not the same node rendered twice.
 const [openBa, setOpenBa] = useState({});
 const [openSubject, setOpenSubject] = useState({});

 const allSources = useMemo(() => {
  const set = new Set();
  for (const row of rows) for (const label of sourcesFor(row.kpi)) set.add(label);
  return [...set].sort();
 }, [rows]);

 const areaLabelById = useMemo(() => Object.fromEntries((areas || []).map((a) => [a.id, a.label])), [areas]);
 // The one place a business area id becomes its displayed heading -- shared by the group
 // header and the search match below, so "Uncategorized" (the synthetic group's real label,
 // not the `__uncategorized__` sentinel) is what a search for it actually matches.
 const labelForBa = (baId) => (baId === UNCATEGORIZED_BA_ID ? "Uncategorized" : areaLabelById[baId] || baId);
 // Same reasoning as `labelForBa`: "Unsorted" (not the `__unsorted__` sentinel) is what the
 // group heading actually shows, and what a search for it should match.
 const labelForSubject = (key) => (key === UNSORTED_SUBJECT ? "Unsorted" : key);
 const businessAreaOptions = useMemo(
  () => [{ value: "", label: "All Business Area" }, ...(areas || []).map((a) => ({ value: a.id, label: a.label }))],
  [areas]
 );
 // Scoped to the chosen Business Area's own declared vocabulary (`GET /business-areas`'s
 // `subject_areas`) when one is picked; the union across every area otherwise. "Unsorted" is
 // always offered -- it is a real, populated group (every suggestion lands there) even though
 // it names no vocabulary term.
 const subjectAreaOptions = useMemo(() => {
  const list = businessAreaFilter
   ? (areas || []).find((a) => a.id === businessAreaFilter)?.subject_areas || []
   : Array.from(new Set((areas || []).flatMap((a) => a.subject_areas || [])));
  return [
   { value: "", label: "All Subject Area" },
   ...list.map((s) => ({ value: s, label: s })),
   { value: UNSORTED_SUBJECT, label: "Unsorted" },
  ];
 }, [areas, businessAreaFilter]);

 const baToggleKey = (scope, baId) => `${scope}\0${baId}`;
 const subjectToggleKey = (scope, baId, subject) => `${scope}\0${baId}\0${subject}`;

 const toggleSelect = (key) =>
  setSelected((prev) => {
   const next = new Set(prev);
   next.has(key) ? next.delete(key) : next.add(key);
   return next;
  });

 // Every row that survives search / Business Area / Subject Area / status / source / verdict
 // filtering, each carrying the resolved (possibly synthetic) `baId`/`subjectKey` it was grouped
 // by, so both the single-tree and the per-verdict groupings below can be built from one pass.
 const filteredRows = useMemo(() => {
  const needle = search.trim().toLowerCase();
  const known = new Set((areas || []).map((a) => a.id));
  const out = [];

  for (const row of rows) {
   const { kpi } = row;
   if (mutedSources.size > 0) {
    const src = sourcesFor(kpi);
    if (src.length > 0 && src.some((label) => mutedSources.has(label))) continue;
   }
   if (statusFilter === "accepted" && row.suggested) continue;
   if (statusFilter === "suggested" && !row.suggested) continue;
   if (verdictFilter !== "all" && bucketFor(row) !== verdictFilter) continue;

   const baId = kpi.businessAreaId && known.has(kpi.businessAreaId) ? kpi.businessAreaId : UNCATEGORIZED_BA_ID;
   if (businessAreaFilter && baId !== businessAreaFilter) continue;

   const subjectKey = kpi.subjectArea || UNSORTED_SUBJECT;
   if (subjectAreaFilter && subjectKey !== subjectAreaFilter) continue;

   if (needle) {
    const baLabel = labelForBa(baId);
    const subjectLabel = labelForSubject(subjectKey);
    const match =
     (kpi.name || "").toLowerCase().includes(needle) ||
     (kpi.desc || "").toLowerCase().includes(needle) ||
     subjectLabel.toLowerCase().includes(needle) ||
     baLabel.toLowerCase().includes(needle);
    if (!match) continue;
   }

   out.push({ row, baId, subjectKey });
  }
  return out;
  // eslint-disable-next-line react-hooks/exhaustive-deps
 }, [rows, search, businessAreaFilter, subjectAreaFilter, statusFilter, verdictFilter, mutedSources, areas, areaLabelById]);

 // Builds one Business Area -> Subject Area tree from a set of (already filtered) entries --
 // shared by the single flat tree and by each of the three per-verdict trees in repeat mode, so
 // sorting/labelling/"Uncategorized" handling stays in exactly one place.
 const buildGroups = (entries) => {
  /** baId -> Map subjectKey -> rows */
  const baMap = new Map();
  for (const { row, baId, subjectKey } of entries) {
   if (!baMap.has(baId)) baMap.set(baId, new Map());
   const subjectMap = baMap.get(baId);
   if (!subjectMap.has(subjectKey)) subjectMap.set(subjectKey, []);
   subjectMap.get(subjectKey).push(row);
  }

  const focus = new Set(focusAreaIds);
  const orderRank = (id) => {
   if (id === UNCATEGORIZED_BA_ID) return 9999;
   if (focus.has(id)) return -1;
   const i = (areas || []).findIndex((a) => a.id === id);
   return i >= 0 ? i : 3000;
  };

  const grouped = Array.from(baMap.entries())
   .map(([baId, subjectMap]) => {
    const label = labelForBa(baId);
    const subjects = Array.from(subjectMap.entries())
     .map(([subject, subjectRows]) => ({
      subject: subject === UNSORTED_SUBJECT ? "Unsorted" : subject,
      sortKey: subject === UNSORTED_SUBJECT ? "￿" : subject,
      rawKey: subject,
      rows: subjectRows,
     }))
     .sort((a, b) => a.sortKey.localeCompare(b.sortKey));
    return { id: baId, label, subjects };
   })
   .sort((a, b) => orderRank(a.id) - orderRank(b.id) || a.label.localeCompare(b.label));

  const totals = grouped.reduce(
   (acc, g) => {
    acc.kpis += g.subjects.reduce((n, s) => n + s.rows.length, 0);
    acc.subjects += g.subjects.length;
    return acc;
   },
   { kpis: 0, subjects: 0 }
  );

  return {
   groups: grouped,
   businessAreaCount: grouped.length,
   subjectAreaCount: totals.subjects,
   kpiCount: totals.kpis,
  };
 };

 // Single flat tree -- used when a specific verdict is picked from the filter, and it's the only
 // thing rendered then, so section-level chrome would be redundant. Left null when the Verdict
 // filter is "all" (`verdictResults` renders instead), the same guard `verdictResults` itself uses
 // below, so a search keystroke/filter change in the default view doesn't pay for a buildGroups
 // pass (sort included) whose result would just be thrown away unread.
 const flatResult = useMemo(() => {
  if (verdictFilter !== "all") return buildGroups(filteredRows);
  return null;
  // eslint-disable-next-line react-hooks/exhaustive-deps
 }, [filteredRows, verdictFilter, areas, focusAreaIds]);

 // Repeat mode -- one tree per verdict, each built from only that verdict's rows. A Business
 // Area/Subject Area with KPIs in more than one bucket appears once per bucket it has a match
 // in, each time showing only that bucket's KPIs. Built in the same pass as `subjectVerdictsMap`
 // below (one `bucketFor` call per row, not two) since both are keyed off the same per-row bucket.
 const { verdictResults, subjectVerdictsMap } = useMemo(() => {
  if (verdictFilter !== "all") return { verdictResults: null, subjectVerdictsMap: null };
  const byBucket = { ready: [], partial: [], blocked: [] };
  // (baId, rawSubjectKey) -> which verdicts that Subject Area has a KPI in, across ALL of
  // `filteredRows` (not just one bucket) -- lets each repeated section point at where its other
  // appearances are, instead of just silently repeating.
  const subjectVerdicts = new Map();
  for (const entry of filteredRows) {
   const { row, baId, subjectKey } = entry;
   const bucket = bucketFor(row);
   byBucket[bucket]?.push(entry);
   const key = `${baId}\0${subjectKey}`;
   if (!subjectVerdicts.has(key)) subjectVerdicts.set(key, new Set());
   subjectVerdicts.get(key).add(bucket);
  }
  return {
   verdictResults: {
    ready: buildGroups(byBucket.ready),
    partial: buildGroups(byBucket.partial),
    blocked: buildGroups(byBucket.blocked),
   },
   subjectVerdictsMap: subjectVerdicts,
  };
  // eslint-disable-next-line react-hooks/exhaustive-deps
 }, [filteredRows, verdictFilter, areas, focusAreaIds]);

 const crossRefFor = (bucket, baId, rawSubjectKey) => {
  const others = subjectVerdictsMap?.get(`${baId}\0${rawSubjectKey}`);
  if (!others) return null;
  const labels = VERDICT_ORDER.filter((b) => b !== bucket && others.has(b)).map((b) => VERDICT_META[b].label);
  return labels.length ? `+ also in ${labels.join(", ")}` : null;
 };

 // Counts `selectedRows`, not the raw `selected` key set: a search/Business Area/status/verdict
 // filter can hide a selected row without deselecting it, and the count shown here must match
 // what `onCreateDashboard` below actually receives. A KPI belongs to exactly one verdict
 // bucket, so it is never double-counted across repeat-mode sections.
 //
 // Ordered to match whichever tree is actually on screen (the sorted Business-Area/Subject-Area
 // `groups`, not raw `filteredRows`/API order) -- `CreateDashboardModal` lays out dashboard tiles
 // in this same order, so it should match what the user saw while selecting.
 const selectedRows = useMemo(() => {
  const isSelected = (r) => selected.has(`${r.categoryIndex}-${r.sectionIndex}-${r.kpiIndex}`);
  const flattenGroups = (groupsList) => groupsList.flatMap((g) => g.subjects.flatMap((s) => s.rows));
  const ordered = verdictFilter === "all" ? VERDICT_ORDER.flatMap((bucket) => flattenGroups(verdictResults[bucket].groups)) : flattenGroups(flatResult.groups);
  return ordered.filter(isSelected);
 }, [verdictFilter, verdictResults, flatResult, selected]);

 const setAllOpen = (scope, groupsList, open) => {
  const ba = {};
  const subj = {};
  for (const g of groupsList) {
   ba[baToggleKey(scope, g.id)] = open;
   for (const s of g.subjects) subj[subjectToggleKey(scope, g.id, s.subject)] = open;
  }
  setOpenBa((prev) => ({ ...prev, ...ba }));
  setOpenSubject((prev) => ({ ...prev, ...subj }));
 };

 // Per-Business-Area: just that group's Subject Area tables, leaving the Business Area row
 // itself (and every other group, in every scope) untouched.
 const setBaChildren = (scope, ba, open) =>
  setOpenSubject((prev) => {
   const next = { ...prev };
   for (const s of ba.subjects) next[subjectToggleKey(scope, ba.id, s.subject)] = open;
   return next;
  });

 // Shared Business-Area-row + Subject-Area-table renderer -- used for the single flat tree
 // (`scope: "flat"`) and for each of the three repeat-mode sections (`scope: <verdict id>`, which
 // doubles as the bucket id `crossRefFor` needs to exclude from its own "also in" label).
 const renderGroups = (groupsList, scope) =>
  groupsList.map((ba) => {
   const baKpiTotal = ba.subjects.reduce((n, s) => n + s.rows.length, 0);
   const baKey = baToggleKey(scope, ba.id);
   const baOpen = openBa[baKey] ?? true;
   return (
    <div key={baKey} className="w-full min-w-0 space-y-2.5">
     <div className="flex w-full min-w-0 items-center gap-2 rounded-xl border border-border bg-surface-default px-3 py-2.5 shadow-[0_1px_2px_rgba(16,24,40,0.04)] transition-colors hover:border-border-light hover:bg-surface-secondary dark:shadow-none sm:px-3.5">
      <button
       type="button"
       onClick={() => setOpenBa((prev) => ({ ...prev, [baKey]: !baOpen }))}
       aria-expanded={baOpen}
       className="flex min-w-0 flex-1 flex-wrap items-center gap-2 text-left"
      >
       <ChevronDown className={`h-4 w-4 shrink-0 text-primary transition-transform duration-200 ${baOpen ? "rotate-180" : "rotate-0"}`} strokeWidth={2} aria-hidden />
       <Briefcase className="h-4 w-4 shrink-0 text-primary" strokeWidth={1.75} aria-hidden />
       <span className="shrink-0 rounded-full bg-primary-light px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wide text-primary dark:bg-surface-tertiary dark:ring-1 dark:ring-primary/30">
        Business area
       </span>
       <h3 className="text-[17px] font-bold leading-tight text-primary">{ba.label}</h3>
       <span className="rounded-md bg-surface-secondary px-2.5 py-0.5 text-sm font-medium text-text-secondary">
        {ba.subjects.length} Subject {ba.subjects.length === 1 ? "Area" : "Areas"}
        {scope !== "flat" ? " here" : ""}
        <span className="mx-1.5 text-border-light">·</span>
        {baKpiTotal} KPI{baKpiTotal === 1 ? "" : "s"}
        {scope !== "flat" ? " here" : ""}
       </span>
      </button>
      {/* Per-group children control: just this Business Area's Subject Area tables,
          not the row itself and not any other group. Only worth showing once there is
          more than one child to fold at once. */}
      {baOpen && ba.subjects.length > 1 ? (
       <div className="flex shrink-0 items-center gap-1.5 text-xs font-semibold">
        <button type="button" onClick={() => setBaChildren(scope, ba, true)} className="text-primary hover:underline">
         Expand all
        </button>
        <span className="text-border-light">·</span>
        <button type="button" onClick={() => setBaChildren(scope, ba, false)} className="text-primary hover:underline">
         Collapse all
        </button>
       </div>
      ) : null}
     </div>

     {baOpen ? (
      <div className="ml-6 w-full min-w-0 space-y-3 border-l-2 border-primary/25 pl-3.5 dark:border-primary/35 sm:pl-4">
       {ba.subjects.map((sec) => {
        const stKey = subjectToggleKey(scope, ba.id, sec.subject);
        const open = openSubject[stKey] ?? true;
        return (
         <KpiTable
          key={stKey}
          title={sec.subject}
          countLabel={`${sec.rows.length} KPIs`}
          xref={scope !== "flat" ? crossRefFor(scope, ba.id, sec.rawKey) : null}
          rows={sec.rows}
          isOpen={open}
          onToggleOpen={() => setOpenSubject((prev) => ({ ...prev, [stKey]: !open }))}
          selected={selected}
          onToggleSelect={toggleSelect}
          onOpenDetail={onOpenDetail}
          onAdopt={onAdopt}
          adoptingIds={adoptingIds}
          adoptErrors={adoptErrors}
          onToggleActive={onToggleActive}
          togglingActiveIds={togglingActiveIds}
          profileId={profileId}
          onOpenUseCase={onOpenUseCase}
         />
        );
       })}
      </div>
     ) : null}
    </div>
   );
  });

 return (
  <div className="space-y-5">
   <KpiFocusPrompt
    areas={areas}
    selectedIds={focusAreaIds}
    onChange={onFocusChange}
    status={areasStatus}
    error={areasError}
    onRetry={onRetryAreas}
   />

   {areasStatus === "error" && (
    <div className="flex items-center gap-2 rounded-xl border border-status-warning/30 bg-status-warning/10 px-4 py-2.5 text-sm text-text-secondary">
     <AlertTriangle size={15} className="shrink-0 text-status-warning" />
     Couldn't load your business areas. Names may show as raw ids until this is retried.
    </div>
   )}

   <div className="rounded-xl border border-border bg-surface-secondary p-3 sm:p-4">
    <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
     <div className="relative min-w-0 flex-1">
      <svg className="absolute left-3.5 top-1/2 h-[15px] w-[15px] -translate-y-1/2 text-text-muted" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
       <circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" />
      </svg>
      <input
       type="text"
       placeholder="Search for KPIs"
       value={search}
       onChange={(e) => setSearch(e.target.value)}
       className="w-full rounded-lg border border-border bg-surface-default py-2.5 pl-10 pr-3 text-sm text-text-primary shadow-sm placeholder:text-text-muted outline-none transition-shadow focus:border-primary focus:ring-2 focus:ring-primary/20"
      />
     </div>
     <div className="flex shrink-0 flex-wrap items-center gap-2 sm:gap-2.5">
      <Select
       label={null}
       options={businessAreaOptions}
       value={businessAreaFilter}
       onChange={(e) => { setBusinessAreaFilter(e.target.value); setSubjectAreaFilter(""); }}
      />
      <Select label={null} options={subjectAreaOptions} value={subjectAreaFilter} onChange={(e) => setSubjectAreaFilter(e.target.value)} />
      <Select label={null} options={STATUS_FILTERS} value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)} />
      <Select label={null} options={VERDICT_FILTERS} value={verdictFilter} onChange={(e) => setVerdictFilter(e.target.value)} />
     </div>
    </div>
   </div>

   {allSources.length > 1 && (
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
         on ? "border-border-light bg-surface text-text-primary" : "border-dashed border-border-light bg-transparent text-text-muted line-through"
        }`}
       >
        <Database size={11} />
        {label}
       </button>
      );
     })}
     {mutedSources.size > 0 && (
      <button type="button" onClick={() => setMutedSources(new Set())} className="text-xs font-semibold text-primary hover:underline">
       Show all
      </button>
     )}
    </div>
   )}

   <div className="min-w-0 w-full">
    {verdictFilter === "all" ? (
     VERDICT_ORDER.map((bucket) => (
      <VerdictSection
       key={bucket}
       bucket={bucket}
       result={verdictResults[bucket]}
       onExpandAll={() => setAllOpen(bucket, verdictResults[bucket].groups, true)}
       onCollapseAll={() => setAllOpen(bucket, verdictResults[bucket].groups, false)}
      >
       {renderGroups(verdictResults[bucket].groups, bucket)}
      </VerdictSection>
     ))
    ) : (
     <>
      <div className="flex flex-wrap items-center justify-between gap-3 pb-3.5">
       <span className="text-sm text-text-muted">
        {flatResult.businessAreaCount} Business Areas
        <span className="mx-1.5 text-border-light">·</span>
        {flatResult.subjectAreaCount} Subject Areas
        <span className="mx-1.5 text-border-light">·</span>
        {flatResult.kpiCount} KPIs
       </span>
       {flatResult.groups.length > 0 && (
        <ExpandCollapseControls
         onExpandAll={() => setAllOpen("flat", flatResult.groups, true)}
         onCollapseAll={() => setAllOpen("flat", flatResult.groups, false)}
        />
       )}
      </div>
      {flatResult.groups.length ? (
       <div className="w-full min-w-0 space-y-3.5">{renderGroups(flatResult.groups, "flat")}</div>
      ) : (
       <p className="text-sm text-text-muted">No KPIs match the current filters.</p>
      )}
     </>
    )}
   </div>

   {/* Selection bar. Sticks to the bottom so the action stays reachable while scrolling a long
       tree -- the alternative is scrolling back to a toolbar after every pick. */}
   {selectedRows.length > 0 && (
    <div className="sticky bottom-4 z-20 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border-light bg-surface px-4 py-3 shadow-lg">
     <p className="text-sm">
      <span className="font-bold text-text-primary">{selectedRows.length} selected</span>
      <span className="text-text-secondary"> · all buildable from connected data</span>
     </p>
     <div className="flex flex-wrap items-center gap-2">
      <button type="button" onClick={() => setSelected(new Set())} className="rounded-lg px-3 py-2 text-sm font-semibold text-text-secondary hover:text-text-primary">
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
  </div>
 );
}
