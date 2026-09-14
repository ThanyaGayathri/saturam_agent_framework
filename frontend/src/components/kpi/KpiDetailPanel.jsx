import { Link } from "react-router-dom";
import { X, ExternalLink } from "lucide-react";
import { mappingConfidenceForKpi } from "../../domain/kpi-mapping";
import { getDatasetById } from "../../domain/kpi-data-catalog";
import { getCatalogEntriesForKpiName } from "../../domain/kpi-usecase-links";
import { bucketFor, NOT_SELECTABLE_REASONS } from "./KpiDiscovery";

/**
 * The definition surface for one KPI: description, unit, level, mapping confidence, the datasets
 * that feed it, and what uses it.
 *
 * Shared deliberately. Discovery ("what can my data measure?") and the master-data table ("what
 * backs this metric?") both need it, and a second, view-local panel is exactly how the two would
 * start disagreeing about what a KPI means.
 */
const FEASIBILITY_STYLE = {
 ready: { cls: "border-status-success/35 bg-status-success-light dark:bg-status-success/15", label: "text-status-success" },
 partial: { cls: "border-status-warning/35 bg-status-warning-light dark:bg-status-warning/15", label: "text-status-warning" },
 blocked: { cls: "border-status-critical/35 bg-status-critical-light dark:bg-status-critical/15", label: "text-status-critical" },
};

export default function KpiDetailPanel({ detailRow, nameToIdsMap, profileId, processLabelById, onEdit, onClose }) {
 // A real registry KPI (C1, #232) carries the server's own join-aware `feasibility` -- shown
 // below instead of `mappingConfidenceForKpi`'s local heuristic score, which has nothing real
 // to compute from for a row the seed/dataset simulation has never heard of.
 const fromRegistry = !!detailRow.kpi.fromRegistry;
 const conf = fromRegistry ? null : mappingConfidenceForKpi(detailRow.kpi, nameToIdsMap);
 // A suggestion carries its own server-checked `feasibility` (the same one `bucketFor` reads in
 // KpiDiscovery.jsx) -- shown here too, not just for registry KPIs, so a blocked suggestion says
 // why instead of leaving this section empty. Falls back to "blocked, check didn't run" rather
 // than an absent object, matching `bucketFor`'s own "no verdict is not known ready" rule.
 const feasibility = fromRegistry
  ? detailRow.kpi.feasibility
  : detailRow.suggested
  ? detailRow.suggestion?.feasibility || {
     verdict: "blocked",
     rationale: "The feasibility check did not run for this suggestion.",
    }
  : null;
 const feasibilityStyle = feasibility ? FEASIBILITY_STYLE[feasibility.verdict] || FEASIBILITY_STYLE.blocked : null;
 const useCases = getCatalogEntriesForKpiName(detailRow.kpi.name, profileId);
 const processIds = Array.isArray(detailRow.kpi.processMapping) ? detailRow.kpi.processMapping : [];
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
 <div className="flex-1 space-y-6 overflow-y-auto px-5 py-4">
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
 <ul className="mt-2 space-y-2">
 {(Array.isArray(detailRow.kpi.datasetIds) ? detailRow.kpi.datasetIds : []).length === 0 ? (
 <li className="text-sm text-text-muted">No datasets linked.</li>
 ) : (
 detailRow.kpi.datasetIds.map(id => {
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
 })
 )}
 </ul>
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

 {/* No local edit screen has ever heard of a real registry KPI -- routing there would be
 misleading, not merely inert, so the link is hidden rather than left to silently do
 nothing. */}
 {!fromRegistry && (
 <button
 type="button"
 onClick={onEdit}
 className="inline-flex text-sm font-semibold text-primary hover:underline"
 >
 Edit KPI →
 </button>
 )}
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
