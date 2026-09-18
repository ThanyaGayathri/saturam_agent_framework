import { useEffect, useMemo, useState } from "react";
import {
 AlertTriangle,
 Briefcase,
 CalendarClock,
 Check,
 ChevronDown,
 CircleHelp,
 Database,
 Info,
 LayoutGrid,
 Plus,
 Sparkles,
 Trash2,
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
 * Grouped as a Business Area -> Subject Area -> KPI hierarchy (mirroring the enterprise KPI
 * registry's own shape) rather than by feasibility bucket -- the registry is what a customer
 * browses and governs, and a metric belongs to exactly one place in it regardless of whether
 * it happens to be measurable yet. Feasibility still gates what can be picked for a dashboard
 * (`bucketFor`, `notSelectableReasonFor`) -- it stopped being how the page is organized.
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
 * dataset mapping. A suggestion with no verdict is not known to be ready, so it is blocked.
 */
export function bucketFor(row) {
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

/** One Subject Area's KPI table: checkbox, name, description, maps to, powered by, status, actions. */
function KpiTable({
 title,
 countLabel,
 rows,
 isOpen,
 onToggleOpen,
 selected,
 onToggleSelect,
 onOpenDetail,
 onAdopt,
 onRemove,
 removeState,
 adoptingIds,
 adoptErrors,
 onToggleActive,
 togglingActiveIds,
 profileId,
 onOpenUseCase,
}) {
 // Which row's "remove from my metrics" is armed, by row key -- local to this Subject Area's
 // own table, the same scope `KpiDetailPanel.jsx`'s own `confirmingRemove` uses for the panel.
 // At most one row's confirm shows at a time within this table.
 const [confirmDeleteKey, setConfirmDeleteKey] = useState(null);
 // A click on the trash button itself is handled by its own onClick (toggling the armed row,
 // including switching straight to a different row's confirm without this effect's help); this
 // is only for dismissing an open confirmation by clicking anywhere else on the page.
 useEffect(() => {
  if (!confirmDeleteKey) return;
  const close = (e) => {
   if (!e.target.closest?.("[data-remove-popover-root]")) setConfirmDeleteKey(null);
  };
  document.addEventListener("mousedown", close);
  return () => document.removeEventListener("mousedown", close);
 }, [confirmDeleteKey]);
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
            {!r.suggested && r.kpi.fromRegistry && onRemove ? (
             <span className="relative" data-remove-popover-root>
              <button
               type="button"
               className="rounded-md p-1.5 text-text-muted opacity-80 transition-all hover:bg-status-critical-light hover:text-status-critical hover:opacity-100"
               aria-label={`Remove ${r.kpi.name} from my metrics`}
               title="Remove from my metrics"
               onClick={() => setConfirmDeleteKey((prev) => (prev === key ? null : key))}
              >
               <Trash2 size={15} />
              </button>
              {confirmDeleteKey === key && (
               <div
                role="dialog"
                aria-label={`Remove ${r.kpi.name}?`}
                className="absolute right-0 top-9 z-30 w-56 rounded-lg border border-border bg-surface p-3 text-left shadow-lg"
               >
                <p className="text-xs leading-snug text-text-secondary">
                 Remove <strong className="font-semibold text-text-primary">{r.kpi.name}</strong> from
                 your registry? Dashboards using it must be changed first.
                </p>
                {removeState?.identifier === r.kpi.identifier && removeState.error && (
                 <p className="mt-1.5 text-xs text-status-critical">{removeState.error}</p>
                )}
                <div className="mt-2.5 flex items-center gap-3">
                 <button
                  type="button"
                  disabled={removeState?.identifier === r.kpi.identifier && !removeState.error}
                  onClick={() => onRemove(r)}
                  className="inline-flex items-center gap-1.5 rounded-md bg-status-critical px-2.5 py-1 text-xs font-semibold text-white transition-colors hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-60"
                 >
                  <Trash2 size={12} />
                  {removeState?.identifier === r.kpi.identifier && !removeState.error
                   ? "Removing…"
                   : "Yes, remove"}
                 </button>
                 <button
                  type="button"
                  onClick={() => setConfirmDeleteKey(null)}
                  className="text-xs font-semibold text-text-secondary hover:text-text-primary"
                 >
                  Cancel
                 </button>
                </div>
               </div>
              )}
             </span>
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
 onRemove,
 removeState,
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
 // Source is a facet, not a gate: null/empty means "every source" -- the default. Muting one
 // means "show me what I could measure WITHOUT it", so a metric survives only if EVERY source
 // it needs is still active; a metric with no source at all is never filtered out.
 const [mutedSources, setMutedSources] = useState(() => new Set());
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

 const subjectToggleKey = (baId, subject) => `${baId}\0${subject}`;

 const toggleSelect = (key) =>
  setSelected((prev) => {
   const next = new Set(prev);
   next.has(key) ? next.delete(key) : next.add(key);
   return next;
  });

 const { groups, businessAreaCount, subjectAreaCount, kpiCount } = useMemo(() => {
  const needle = search.trim().toLowerCase();
  const known = new Set((areas || []).map((a) => a.id));
  /** baId -> Map subjectKey -> rows */
  const baMap = new Map();

  for (const row of rows) {
   const { kpi } = row;
   if (mutedSources.size > 0) {
    const src = sourcesFor(kpi);
    if (src.length > 0 && src.some((label) => mutedSources.has(label))) continue;
   }
   if (statusFilter === "accepted" && row.suggested) continue;
   if (statusFilter === "suggested" && !row.suggested) continue;

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
 }, [rows, search, businessAreaFilter, subjectAreaFilter, statusFilter, mutedSources, areas, areaLabelById, focusAreaIds]);

 const selectedRows = useMemo(() => {
  const flat = groups.flatMap((g) => g.subjects.flatMap((s) => s.rows));
  return flat.filter((r) => selected.has(`${r.categoryIndex}-${r.sectionIndex}-${r.kpiIndex}`));
 }, [groups, selected]);

 // Global expand/collapse -- every Business Area group and every Subject Area table under it,
 // computed from the currently filtered `groups` so a search/filter narrows what "all" means.
 const expandAll = () => {
  const ba = {};
  const subj = {};
  for (const g of groups) {
   ba[g.id] = true;
   for (const s of g.subjects) subj[subjectToggleKey(g.id, s.subject)] = true;
  }
  setOpenBa(ba);
  setOpenSubject(subj);
 };
 const collapseAll = () => {
  const ba = {};
  const subj = {};
  for (const g of groups) {
   ba[g.id] = false;
   for (const s of g.subjects) subj[subjectToggleKey(g.id, s.subject)] = false;
  }
  setOpenBa(ba);
  setOpenSubject(subj);
 };
 // Per-Business-Area: just that group's Subject Area tables, leaving the Business Area row
 // itself (and every other group) untouched.
 const setBaChildren = (ba, open) =>
  setOpenSubject((prev) => {
   const next = { ...prev };
   for (const s of ba.subjects) next[subjectToggleKey(ba.id, s.subject)] = open;
   return next;
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

   <div className="flex w-full min-w-0 items-stretch gap-0">
    <div className="relative flex w-9 shrink-0 flex-col items-center self-stretch sm:w-10">
     <div className="relative z-10 flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-primary/25 bg-primary-light shadow-[0_1px_2px_rgba(105,65,198,0.12)] dark:border-primary/35 dark:bg-surface-tertiary dark:shadow-none dark:ring-1 dark:ring-primary/25">
      <Briefcase className="h-[18px] w-[18px] text-primary" strokeWidth={1.75} aria-hidden />
     </div>
     <div className="absolute left-1/2 top-10 z-0 h-[calc(100%-2.5rem)] w-px -translate-x-1/2 bg-gradient-to-b from-border via-border/70 to-transparent" aria-hidden />
    </div>

    <div className="min-w-0 w-full flex-1 self-start pl-3 sm:pl-4">
     <div className="flex h-fit w-full flex-wrap items-baseline justify-between gap-x-3 gap-y-1 pt-2 text-left">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
       <h2 className="text-[17px] font-bold tracking-tight text-text-primary">Enterprise KPIs</h2>
       <span className="text-sm text-text-muted">
        {businessAreaCount} Business Areas
        <span className="mx-1.5 text-border-light">·</span>
        {subjectAreaCount} Subject Areas
        <span className="mx-1.5 text-border-light">·</span>
        {kpiCount} KPIs
       </span>
      </div>
      {groups.length > 0 && (
       <div className="flex shrink-0 items-center gap-2 text-xs font-semibold">
        <button type="button" onClick={expandAll} className="text-primary hover:underline">
         Expand all
        </button>
        <span className="text-border-light">·</span>
        <button type="button" onClick={collapseAll} className="text-primary hover:underline">
         Collapse all
        </button>
       </div>
      )}
     </div>

     {groups.length ? (
      <div className="relative mt-4 w-full min-w-0 space-y-3.5">
       {groups.map((ba) => {
        const baKpiTotal = ba.subjects.reduce((n, s) => n + s.rows.length, 0);
        const baOpen = openBa[ba.id] ?? true;
        return (
         <div key={ba.id} className="w-full min-w-0 space-y-2.5">
          <div className="flex w-full min-w-0 items-center gap-2 rounded-xl border border-border bg-surface-default px-3 py-2.5 shadow-[0_1px_2px_rgba(16,24,40,0.04)] transition-colors hover:border-border-light hover:bg-surface-secondary dark:shadow-none sm:px-3.5">
           <button
            type="button"
            onClick={() => setOpenBa((prev) => ({ ...prev, [ba.id]: !baOpen }))}
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
             <span className="mx-1.5 text-border-light">·</span>
             {baKpiTotal} Total KPI{baKpiTotal === 1 ? "" : "s"}
            </span>
           </button>
           {/* Per-group children control: just this Business Area's Subject Area tables,
               not the row itself and not any other group. Only worth showing once there is
               more than one child to fold at once. */}
           {baOpen && ba.subjects.length > 1 ? (
            <div className="flex shrink-0 items-center gap-1.5 text-xs font-semibold">
             <button type="button" onClick={() => setBaChildren(ba, true)} className="text-primary hover:underline">
              Expand all
             </button>
             <span className="text-border-light">·</span>
             <button type="button" onClick={() => setBaChildren(ba, false)} className="text-primary hover:underline">
              Collapse all
             </button>
            </div>
           ) : null}
          </div>

          {baOpen ? (
           <div className="ml-6 w-full min-w-0 space-y-3 border-l-2 border-primary/25 pl-3.5 dark:border-primary/35 sm:pl-4">
            {ba.subjects.map((sec) => {
             const stKey = subjectToggleKey(ba.id, sec.subject);
             const open = openSubject[stKey] ?? true;
             return (
              <KpiTable
               key={stKey}
               title={sec.subject}
               countLabel={`${sec.rows.length} KPIs`}
               rows={sec.rows}
               isOpen={open}
               onToggleOpen={() => setOpenSubject((prev) => ({ ...prev, [stKey]: !open }))}
               selected={selected}
               onToggleSelect={toggleSelect}
               onOpenDetail={onOpenDetail}
               onAdopt={onAdopt}
               onRemove={onRemove}
               removeState={removeState}
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
       })}
      </div>
     ) : (
      <p className="mt-8 text-sm text-text-muted">No KPIs match the current filters.</p>
     )}
    </div>
   </div>

   {/* Selection bar. Sticks to the bottom so the action stays reachable while scrolling a long
       tree -- the alternative is scrolling back to a toolbar after every pick. */}
   {selectedRows.length > 0 && (
    <div className="sticky bottom-4 z-20 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border-light bg-surface px-4 py-3 shadow-lg">
     <p className="text-sm">
      {/* Counts `selectedRows`, not the raw `selected` key set: a search/Business Area/status
          filter can hide a selected row without deselecting it, and the count shown here must
          match what `onCreateDashboard` below actually receives. */}
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
