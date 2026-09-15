import { useEffect, useMemo, useState } from "react";
import {
 Search, ChevronRight, ChevronDown, ChevronLeft,
 ChevronsLeft, ChevronsRight, Folder, FolderOpen,
 CheckCircle2, AlertTriangle,
} from "lucide-react";
import { useKpiRegistry } from "../hooks/useKpiRegistry";
import { useProcesses } from "../hooks/useProcesses";
import { useBusinessAreas } from "../hooks/useBusinessAreas";
import { useConsumeRouterState } from "../hooks/useConsumeRouterState";
import { useDemoProfile } from "../context/DemoProfileContext";
import { getDatasetById } from "../domain/kpi-data-catalog";
import { getCatalogEntriesForKpiName } from "../domain/kpi-usecase-links";
import {
 datasetIdsByLinkedKpiName,
 flattenKpiIndexRows,
 mappingStatusForKpi,
} from "../domain/kpi-mapping";
import Switch from "./ui/Switch";
import AddKpiPanel from "./AddKpiPanel";
import KpiDetailPanel from "./kpi/KpiDetailPanel";

// ── Source badge ──────────────────────────────────────────────────────────────
const SOURCE_STYLE = {
 snowflake:
 "bg-primary-light text-primary border-primary/30 dark:border-border dark:bg-surface-tertiary dark:ring-1 dark:ring-primary/30",
 sap:
 "bg-status-success-muted text-status-success border-status-success-border dark:border-status-success-border/55 dark:bg-status-success/15 dark:text-status-success dark:ring-1 dark:ring-status-success-border/40",
 postgres:
 "bg-primary-light text-primary border-primary/30 dark:border-border dark:bg-surface-tertiary dark:ring-1 dark:ring-primary/30",
 salesforce:
 "bg-primary-light text-primary border-primary/30 dark:border-border dark:bg-surface-tertiary dark:ring-1 dark:ring-primary/30",
};
function SourceBadge({ sourceId, sourceLabel }) {
 const cls = SOURCE_STYLE[sourceId] || "bg-surface-tertiary text-text-secondary border-border";
 return (
 <span className={`inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide ${cls}`}>
 {sourceLabel}
 </span>
 );
}

// ── Status badge ──────────────────────────────────────────────────────────────
function StatusBadge({ status }) {
 if (status === "linked") return (
 <span className="inline-flex items-center gap-1.5 rounded-full bg-status-success-light px-2.5 py-0.5 text-[11px] font-semibold text-status-success dark:bg-status-success/15">
 <CheckCircle2 size={11} /> Auto-mapped
 </span>
 );
 if (status === "review") return (
 <span className="inline-flex items-center gap-1.5 rounded-full bg-status-warning-light px-2.5 py-0.5 text-[11px] font-semibold text-status-warning dark:bg-status-warning/15">
 <AlertTriangle size={11} /> Review
 </span>
 );
 return (
 <span className="inline-flex items-center gap-1.5 rounded-full bg-surface-tertiary px-2.5 py-0.5 text-[11px] font-semibold text-text-muted">
 None
 </span>
 );
}

// ── Pagination helpers ────────────────────────────────────────────────────────
function PaginationBtn({ onClick, disabled, children }) {
 return (
 <button
 type="button"
 disabled={disabled}
 onClick={onClick}
 className="flex h-7 w-7 items-center justify-center rounded-lg border border-border bg-surface-default text-text-muted hover:bg-surface-secondary disabled:opacity-40"
 >
 {children}
 </button>
 );
}

function buildPageNumbers(current, total) {
 if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1);
 const pages = [];
 if (current <= 4) {
 for (let i = 1; i <= 5; i++) pages.push(i);
 pages.push("…"); pages.push(total);
 } else if (current >= total - 3) {
 pages.push(1); pages.push("…");
 for (let i = total - 4; i <= total; i++) pages.push(i);
 } else {
 pages.push(1); pages.push("…");
 for (let i = current - 1; i <= current + 1; i++) pages.push(i);
 pages.push("…"); pages.push(total);
 }
 return pages;
}

const PAGE_SIZE = 10;

// ── Main component ────────────────────────────────────────────────────────────
export default function KpiMappings() {
 const { data, setData, hydrated, updateCustomKpi } = useKpiRegistry();
 const { processes } = useProcesses();
 const { profileId, isMiddlebyProfile: isMiddleby } = useDemoProfile();
 const { areas: businessAreas } = useBusinessAreas();
 const categories = data?.categories || [];
 const nameToIdsMap = useMemo(() => datasetIdsByLinkedKpiName(), []);
 const allRows = useMemo(() => flattenKpiIndexRows(categories), [categories]);
 const processLabelById = useMemo(
   () => Object.fromEntries((processes || []).map((p) => [p.id, p.name])),
   [processes]
 );

 // ── UI state ──────────────────────────────────────────────────────────────
 const [search, setSearch] = useState("");
 // Business-area-driven tree: selectedAreaId scopes the list to one area;
 // selectedSubject narrows it further to one subject area inside that area.
 const [selectedAreaId, setSelectedAreaId] = useState(null);
 const [selectedSubject, setSelectedSubject] = useState(null);
 const [expandedAreas, setExpandedAreas] = useState(() => new Set());
 const [unmappedOnly, setUnmappedOnly] = useState(false);
 const [page, setPage] = useState(1);
 const [detailRow, setDetailRow] = useState(null);
 const [editorOpen, setEditorOpen] = useState(false);
 // AddKpiPanel expects editTarget = { ci, si, ki } to edit, or null to create.
 const [editTarget, setEditTarget] = useState(null);

 // Arriving from Discovery's "Edit KPI" (KpiDiscoveryPage.jsx), pre-targeted at the row that
 // sent us here instead of landing on the generic table and making the customer re-find it.
 // `useConsumeRouterState` clears just this key from history state once read, so a later
 // back-navigation or refresh to this same URL does not keep reopening the editor.
 const editTargetFromNav = useConsumeRouterState("editTarget");
 useEffect(() => {
  if (!editTargetFromNav) return;
  setEditTarget(editTargetFromNav);
  setEditorOpen(true);
 }, [editTargetFromNav]);

 // ── Derived values ────────────────────────────────────────────────────────
 const autoMappedCount = useMemo(() => allRows.filter(r => mappingStatusForKpi(r.kpi) === "linked").length, [allRows]);
 const reviewCount = useMemo(() => allRows.filter(r => mappingStatusForKpi(r.kpi) === "review").length, [allRows]);
 const unmappedCount = useMemo(() => allRows.filter(r => (r.kpi.datasetIds || []).length === 0).length, [allRows]);
 const completionPct = allRows.length > 0 ? Math.round((autoMappedCount / allRows.length) * 100) : 0;

 // Rows grouped by real business area (from useBusinessAreas), and inside
 // each area by subject area name (from kpi.subjectArea).
 const rowsByAreaId = useMemo(() => {
 const map = new Map();
 for (const r of allRows) {
 const areaId = r.kpi?.businessAreaId || "";
 if (!areaId) continue;
 if (!map.has(areaId)) map.set(areaId, []);
 map.get(areaId).push(r);
 }
 return map;
 }, [allRows]);

 const subjectsByAreaId = useMemo(() => {
 const map = new Map();
 for (const [areaId, rows] of rowsByAreaId.entries()) {
 const subjects = new Map();
 for (const r of rows) {
 const s = r.kpi?.subjectArea || "";
 if (!s) continue;
 subjects.set(s, (subjects.get(s) || 0) + 1);
 }
 map.set(areaId, subjects);
 }
 return map;
 }, [rowsByAreaId]);

 // ── Filtered rows ─────────────────────────────────────────────────────────
 const filteredRows = useMemo(() => {
 let rows = allRows;

 // Tree selection
 if (selectedAreaId) {
 rows = rows.filter(r => (r.kpi?.businessAreaId || "") === selectedAreaId);
 if (selectedSubject) {
 rows = rows.filter(r => (r.kpi?.subjectArea || "") === selectedSubject);
 }
 }

 // Unmapped pill
 if (unmappedOnly) rows = rows.filter(r => (r.kpi.datasetIds || []).length === 0);

 // Search: KPI name, business area (categoryTitle), subject area
 // (sectionName), and every mapped dataset's technical name.
 if (search.trim()) {
 const q = search.toLowerCase();
 rows = rows.filter(r => {
 const datasetNames = (r.kpi.datasetIds || [])
 .map((id) => getDatasetById(id)?.technicalName || "")
 .join(" ")
 .toLowerCase();
 return (
 r.kpi.name.toLowerCase().includes(q) ||
 datasetNames.includes(q) ||
 r.categoryTitle.toLowerCase().includes(q) ||
 r.sectionName.toLowerCase().includes(q)
 );
 });
 }
 return rows;
 }, [allRows, selectedAreaId, selectedSubject, unmappedOnly, search]);

 const totalPages = Math.max(1, Math.ceil(filteredRows.length / PAGE_SIZE));
 const safePage = Math.min(page, totalPages);
 const pagedRows = filteredRows.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE);

 const resetPage = () => setPage(1);

 // ── Actions ───────────────────────────────────────────────────────────────

 const toggleActive = (ci, si, ki) => {
 const kpi = data.categories?.[ci]?.sections?.[si]?.kpis?.[ki];
 if (!kpi) return;
 const nextActive = kpi.active === false ? true : false;
 // Custom (Supabase-backed) rows must go through updateCustomKpi. If we
 // only touched localData, the next realtime tick would refetch from
 // Supabase and revert the toggle. Seed rows still use setData since they
 // live only in localStorage.
 if (kpi.__supabaseId) {
 void updateCustomKpi(kpi.__supabaseId, { extras: { active: nextActive } });
 return;
 }
 setData(prev => {
 const next = structuredClone(prev);
 const target = next.categories?.[ci]?.sections?.[si]?.kpis?.[ki];
 if (!target) return prev;
 target.active = nextActive;
 return next;
 });
 };

 const toggleArea = (areaId) => setExpandedAreas(prev => {
 const next = new Set(prev);
 next.has(areaId) ? next.delete(areaId) : next.add(areaId);
 return next;
 });

 const selectArea = (areaId) => { setSelectedAreaId(areaId); setSelectedSubject(null); setUnmappedOnly(false); resetPage(); };
 const selectSubject = (areaId, subject) => { setSelectedAreaId(areaId); setSelectedSubject(subject); setUnmappedOnly(false); resetPage(); };
 const selectAll = () => { setSelectedAreaId(null); setSelectedSubject(null); setUnmappedOnly(false); resetPage(); };

 // ── Breadcrumb ────────────────────────────────────────────────────────────
 const selectedAreaLabel = selectedAreaId
 ? (businessAreas.find(a => a.id === selectedAreaId)?.label || selectedAreaId)
 : null;
 const breadcrumbSuffix = selectedSubject
 ? `${selectedAreaLabel} > ${selectedSubject}`
 : selectedAreaLabel;

 // This screen is master data, and the copy should say so -- it is the only place metrics are
 // created, edited, switched off, or assigned to a business area. Discovery consumes the result.
 const introCopy = isMiddleby
 ? "Middleby's metric master list. Add, edit, enable and assign each metric to a department. Enabled metrics are the ones offered under KPIs > What my data supports."
 : "Your metric master list. Add, edit, enable and assign each metric to a business area. Enabled metrics are offered under KPIs > What my data supports; datasets link automatically as sources are modelled.";

 return (
 <div className="flex w-full flex-col gap-4">

 <p className="text-sm text-text-secondary">{introCopy}</p>

 {/* ── Search + stats ─────────────────────────────────────────────────── */}
 <div className="space-y-3">
 <div className="relative">
 <Search size={15} className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-[#9da4ae]" />
 <input
 type="text"
 placeholder="Search KPI name, business area, subject area, or dataset"
 value={search}
 onChange={e => { setSearch(e.target.value); resetPage(); }}
 className="w-full rounded-xl border border-border bg-surface-default py-2.5 pl-10 pr-4 text-sm text-text-primary shadow-sm placeholder:text-[#9da4ae] focus:border-primary focus:outline-none focus:ring-2 focus:ring-[#6941c6]/20"
 />
 </div>

 <div className="flex flex-wrap items-center gap-4">
 <div className="flex flex-1 flex-wrap items-center gap-3">
 <span className="inline-flex items-center gap-1.5 text-sm font-semibold text-[#067647]">
 <CheckCircle2 size={15} className="text-[#17b26a]" />
 {autoMappedCount} assets mapped automatically
 </span>
 <span className="inline-flex items-center gap-1.5 text-sm font-semibold text-[#b54708]">
 <AlertTriangle size={15} className="text-[#f79009]" />
 {reviewCount} need review
 </span>
 {unmappedCount > 0 && (
 <span className="inline-flex items-center gap-1.5 text-sm font-semibold text-[#d92d20]">
 ⊗ {unmappedCount} missing a dataset
 </span>
 )}
 </div>
 <div className="flex shrink-0 items-center gap-3">
 <div className="flex items-center gap-2">
 <span className="text-xs text-text-muted">Mapping completion</span>
 <div className="h-2 w-28 overflow-hidden rounded-full bg-border">
 <div className="h-full rounded-full bg-primary transition-all" style={{ width: `${completionPct}%` }} />
 </div>
 <span className="text-xs font-semibold text-text-primary">{completionPct}%</span>
 </div>
 {/* "Run Auto-Mapper" removed deliberately. Binding a metric to its datasets is not a thing a
     user should have to ask for -- it runs when the semantic layer finishes building, and again
     whenever a metric is added or enabled. Leaving it as a button meant a newly added KPI sat
     unmapped until somebody happened to press it. */}
 <button
 type="button"
 onClick={() => { setEditTarget(null); setEditorOpen(true); }}
 disabled={!hydrated}
 className="inline-flex shrink-0 items-center gap-1.5 rounded-lg bg-primary px-3.5 py-2 text-sm font-semibold text-white shadow-sm transition hover:bg-primary-hover disabled:opacity-50"
 >
 + Add KPI
 </button>
 </div>
 </div>
 </div>

 {/* ── Two-column layout ───────────────────────────────────────────────── */}
 <div className="flex min-h-0 gap-4">

 {/* Left sidebar — business area → subject area tree */}
 <aside className="flex w-60 shrink-0 flex-col overflow-hidden rounded-xl border border-border bg-surface-default shadow-sm">
 <div className="border-b border-border px-4 py-3">
 <h3 className="text-sm font-bold text-text-primary">Business hierarchy</h3>
 <div className="mt-2 flex gap-1.5">
 <button
 type="button"
 onClick={selectAll}
 className={`rounded-full px-2.5 py-0.5 text-xs font-semibold transition ${
 !unmappedOnly && !selectedAreaId
 ? "bg-primary text-white"
 : "bg-[#f4f4f6] text-text-secondary hover:bg-border"
 }`}
 >
 All {allRows.length}
 </button>
 <button
 type="button"
 onClick={() => { setSelectedAreaId(null); setSelectedSubject(null); setUnmappedOnly(true); resetPage(); }}
 className={`rounded-full px-2.5 py-0.5 text-xs font-semibold transition ${
 unmappedOnly ? "bg-[#f79009] text-white" : "bg-[#f4f4f6] text-text-secondary hover:bg-border"
 }`}
 >
 Missing dataset {unmappedCount}
 </button>
 </div>
 </div>

 <div className="flex-1 overflow-y-auto py-1">
 {businessAreas.map((area) => {
 const areaRows = rowsByAreaId.get(area.id) || [];
 if (areaRows.length === 0) return null;
 const isExpanded = expandedAreas.has(area.id);
 const isAreaActive = selectedAreaId === area.id && !selectedSubject;
 const subjectMap = subjectsByAreaId.get(area.id) || new Map();
 return (
 <div key={area.id}>
 <div
 className={`flex cursor-pointer items-center gap-1 px-3 py-1.5 transition hover:bg-[#f9f9fb] ${isAreaActive ? "bg-[#f0eeff]" : ""}`}
 onClick={() => selectArea(area.id)}
 >
 <button
 type="button"
 className="shrink-0 text-[#9da4ae] hover:text-text-secondary"
 onClick={e => { e.stopPropagation(); toggleArea(area.id); }}
 aria-label={isExpanded ? "Collapse" : "Expand"}
 >
 {isExpanded ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
 </button>
 {isExpanded
 ? <FolderOpen size={14} className="shrink-0 text-primary" />
 : <Folder size={14} className="shrink-0 text-primary" />
 }
 <span className={`flex-1 truncate text-[13px] font-medium ${isAreaActive ? "text-primary" : "text-text-primary"}`}>
 {area.label}
 </span>
 <span className="rounded-full bg-[#f4f4f6] px-1.5 py-px text-[10px] font-semibold text-text-muted">
 {areaRows.length}
 </span>
 </div>

 {isExpanded && Array.from(subjectMap.entries()).map(([subject, count]) => {
 const isSubjectActive = selectedAreaId === area.id && selectedSubject === subject;
 return (
 <div
 key={subject}
 className={`flex cursor-pointer items-center gap-1 py-1.5 pl-8 pr-3 transition hover:bg-[#f9f9fb] ${isSubjectActive ? "bg-[#f0eeff]" : ""}`}
 onClick={() => selectSubject(area.id, subject)}
 >
 <Folder size={13} className="shrink-0 text-[#9da4ae]" />
 <span className={`flex-1 truncate text-[12px] ${isSubjectActive ? "font-semibold text-primary" : "text-text-secondary"}`}>
 {subject}
 </span>
 <span className="rounded-full bg-[#f4f4f6] px-1.5 py-px text-[10px] font-semibold text-text-muted">
 {count}
 </span>
 </div>
 );
 })}
 </div>
 );
 })}
 </div>
 </aside>

 {/* Right panel */}
 <div className="flex min-w-0 flex-1 flex-col gap-3">

 {/* Panel header */}
 <div className="flex flex-wrap items-center justify-between gap-2">
 <div className="flex items-center gap-1.5 text-sm">
 <span className="font-semibold text-primary">Mapping Workspace</span>
 {breadcrumbSuffix && (
 <><span className="text-[#d5d7da]">/</span><span className="text-text-secondary">{breadcrumbSuffix}</span></>
 )}
 </div>
 </div>

 {/* Content */}
 {!hydrated ? (
 <p className="text-sm text-text-muted">Loading registry…</p>
 ) : (
 /* ── List view ── */
 <div className="overflow-x-auto rounded-xl border border-border bg-surface-default shadow-sm">
 <table className="w-full min-w-[820px] border-collapse text-left text-sm">
 <thead>
 <tr className="border-b border-border text-[11px] font-semibold tracking-wide text-primary">
 <th className="px-3 py-3">Enable</th>
 <th className="px-3 py-3">KPI</th>
 <th className="px-3 py-3">Datasets mapped</th>
 <th className="px-3 py-3">Used by</th>
 <th className="px-3 py-3">Confidence</th>
 </tr>
 </thead>
 <tbody>
 {pagedRows.map((row) => {
 const { kpi, categoryIndex, sectionIndex, kpiIndex } = row;
 const status = mappingStatusForKpi(kpi);
 const active = kpi.active !== false;
 const rowId = `${categoryIndex}-${sectionIndex}-${kpiIndex}`;
 const dsIds = Array.isArray(kpi.datasetIds) ? kpi.datasetIds : [];
 const mappedDatasets = dsIds.map((id) => getDatasetById(id)).filter(Boolean);
 const useCases = getCatalogEntriesForKpiName(kpi.name, profileId);
 const processIds = Array.isArray(kpi.processMapping) ? kpi.processMapping : [];

 return (
 <tr
 key={rowId}
 className="cursor-pointer border-b border-border transition hover:bg-surface-secondary"
 onClick={() => setDetailRow(row)}
 >
 <td className="align-middle px-3 py-3" onClick={e => e.stopPropagation()}>
 <div className="flex items-center [&_button]:shrink-0">
 <Switch
 checked={active}
 onCheckedChange={() => toggleActive(categoryIndex, sectionIndex, kpiIndex)}
 size="compact"
 aria-label={active ? `Disable ${kpi.name}` : `Enable ${kpi.name}`}
 />
 </div>
 </td>
 <td className="px-3 py-3">
 <p className="truncate font-semibold text-text-primary">{kpi.name}</p>
 <div className="mt-1 flex flex-wrap items-center gap-1.5">
 {/* Relevance ranking on the discovery tab sorts on `businessAreaId`. A metric without one can
     never rank -- it sorts last, forever, with no error. So an unmapped area is called out here
     rather than left as an empty cell nobody reads as a problem. */}
 {businessAreas.find((a) => a.id === kpi.businessAreaId) ? (
 <span className="rounded-md border border-border-light bg-[#f4f4f6] px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-text-secondary">
 {businessAreas.find((a) => a.id === kpi.businessAreaId)?.label}
 </span>
 ) : (
 <span
 title="Without a business area this metric can never be ranked in What my data supports."
 className="inline-flex items-center gap-1 rounded-md border border-status-warning-border bg-status-warning-light px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-status-warning"
 >
 <AlertTriangle size={9} />
 No business area
 </span>
 )}
 <span className="text-[11px] text-text-muted">·</span>
 <span className="text-[11px] text-text-muted">{kpi.subjectArea || row.sectionName}</span>
 </div>
 </td>
 <td className="px-3 py-3">
 {mappedDatasets.length === 0 ? (
 <span className="text-[#9da4ae]">-</span>
 ) : (
 <div className="flex flex-col gap-1">
 {mappedDatasets.map((d) => {
 const parts = d.technicalName.split(".");
 const short = parts.length > 1 ? parts[parts.length - 1] : d.technicalName;
 return (
 <div key={d.id} className="flex items-center gap-2 text-[12px]">
 <SourceBadge sourceId={d.sourceId} sourceLabel={d.sourceLabel} />
 <span className="truncate font-mono text-text-primary" title={d.technicalName}>
 {short}
 </span>
 </div>
 );
 })}
 </div>
 )}
 </td>
 <td className="px-3 py-3">
 {useCases.length === 0 && processIds.length === 0 ? (
 <span className="text-[#9da4ae] text-[12px]">-</span>
 ) : (
 <div className="flex flex-col gap-1 text-[12px] text-text-secondary">
 {useCases.length > 0 ? (
 <span>
 <span className="tabular-nums font-semibold text-text-primary">
 {useCases.length}
 </span>{" "}
 use {useCases.length === 1 ? "case" : "cases"}
 </span>
 ) : null}
 {processIds.length > 0 ? (
 <span>
 <span className="tabular-nums font-semibold text-text-primary">
 {processIds.length}
 </span>{" "}
 {processIds.length === 1 ? "process" : "processes"}
 </span>
 ) : null}
 </div>
 )}
 </td>
 <td className="px-3 py-3"><StatusBadge status={status} /></td>
 </tr>
 );
 })}
 {pagedRows.length === 0 && (
 <tr>
 <td colSpan={5} className="px-4 py-12 text-center text-sm text-text-muted">
 No results found.
 </td>
 </tr>
 )}
 </tbody>
 </table>
 </div>
 )}

 {/* Pagination */}
 {filteredRows.length > 0 && (
 <div className="flex items-center justify-between text-sm text-text-muted">
 <span className="text-xs">
 Showing {Math.min((safePage - 1) * PAGE_SIZE + 1, filteredRows.length)} to{" "}
 {Math.min(safePage * PAGE_SIZE, filteredRows.length)} of {filteredRows.length} results
 </span>
 <div className="flex items-center gap-1">
 <PaginationBtn onClick={() => setPage(1)} disabled={safePage === 1}><ChevronsLeft size={13} /></PaginationBtn>
 <PaginationBtn onClick={() => setPage(p => Math.max(1, p - 1))} disabled={safePage === 1}><ChevronLeft size={13} /></PaginationBtn>
 {buildPageNumbers(safePage, totalPages).map((pn, i) =>
 pn === "…" ? (
 <span key={`e-${i}`} className="flex h-7 w-6 items-center justify-center text-xs text-[#d5d7da]">…</span>
 ) : (
 <button
 key={pn}
 type="button"
 onClick={() => setPage(pn)}
 className={`flex h-7 w-7 items-center justify-center rounded-lg border text-xs font-medium transition ${
 safePage === pn ? "border-primary bg-primary text-white" : "border-border bg-surface-default text-text-muted hover:bg-surface-secondary"
 }`}
 >
 {pn}
 </button>
 )
 )}
 <PaginationBtn onClick={() => setPage(p => Math.min(totalPages, p + 1))} disabled={safePage === totalPages}><ChevronRight size={13} /></PaginationBtn>
 <PaginationBtn onClick={() => setPage(totalPages)} disabled={safePage === totalPages}><ChevronsRight size={13} /></PaginationBtn>
 </div>
 </div>
 )}
 </div>
 </div>

 {/* Mapping detail panel */}
 {detailRow && (
 <KpiDetailPanel
 detailRow={detailRow}
 nameToIdsMap={nameToIdsMap}
 profileId={profileId}
 processLabelById={processLabelById}
 onEdit={() => {
 setEditTarget({
 ci: detailRow.categoryIndex,
 si: detailRow.sectionIndex,
 ki: detailRow.kpiIndex,
 });
 setDetailRow(null);
 setEditorOpen(true);
 }}
 onClose={() => setDetailRow(null)}
 />
 )}

 {/* Add / Edit KPI panel — reuses the KPI Registry AddKpiPanel component
     so the CRUD surface is the same everywhere in the product. */}
 <AddKpiPanel
 open={editorOpen}
 editTarget={editTarget}
 onClose={() => { setEditorOpen(false); setEditTarget(null); }}
 />
 </div>
 );
}
