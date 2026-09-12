import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity,
  AlertTriangle,
  BarChart3,
  ChartLine,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  ClipboardCheck,
  ClipboardList,
  Headphones,
  LayoutGrid,
  Layers,
  Monitor,
  Package,
  PackageCheck,
  Pencil,
  Repeat,
  Search,
  ShoppingCart,
  Sparkles,
  Store,
  Tag,
  Trash2,
  TrendingUp,
  Users,
  X,
} from "lucide-react";
import { useLocation, useNavigate } from "react-router-dom";
import { useProcesses } from "../hooks/useProcesses";
import { useBusinessAreas } from "../hooks/useBusinessAreas";
import { useUseCaseCatalog } from "../hooks/useUseCaseCatalog";
import { useCasesForProcessId } from "../domain/use-case-process-links";
import { useCaseActivationPath } from "../domain/automation-registry";
import {
  getProcessAutomationStatus,
  getProcessesAggregateStats,
  getStatusMeta,
} from "../domain/process-demo-metadata";
import AddProcessPanel from "./AddProcessPanel";
import TourLaunchButton from "../tours/TourLaunchButton";

const PROCESS_ICONS_MAP = {
  PackageCheck,
  ClipboardCheck,
  Repeat,
  Tag,
  Store,
};

const BUSINESS_AREA_ICONS = {
  inventory: Package,
  orders: ShoppingCart,
  merchandise: Tag,
  "store-ops": Store,
  "e-com": ChartLine,
  pos: Monitor,
  support: Headphones,
  pricing: TrendingUp,
  finance: BarChart3,
  workforce: Users,
};

const USE_CASE_PILL_LIMIT = 3;

function groupProcessesBySubjectArea(areaProcesses, businessArea) {
  const order = businessArea?.subjectAreas ?? [];
  const buckets = new Map();
  for (const p of areaProcesses) {
    let key = p.subjectArea;
    if (!key || !order.includes(key)) key = order[0] || "Other";
    if (!buckets.has(key)) buckets.set(key, []);
    buckets.get(key).push(p);
  }
  const out = [];
  for (const s of order) {
    const list = buckets.get(s);
    if (list?.length) out.push({ subject: s, list });
  }
  for (const [s, list] of buckets) {
    if (!order.includes(s) && list.length) out.push({ subject: s, list });
  }
  return out;
}

/**
 * Filter pill with optional count badge, aligned to shared Badge look (border, type scale).
 * @param {boolean} active
 * @param {() => void} onClick
 * @param {import('react').ReactNode} [icon]
 * @param {number} [count] optional tabular count from store
 * @param {import('react').ReactNode} children
 */
function FilterChip({ active, onClick, icon, count, children }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={`inline-flex h-8 shrink-0 items-center gap-1.5 rounded-md border px-2 py-0.5 text-left text-xs font-medium leading-none transition sm:px-2.5 sm:py-1 sm:text-[13px] ${
        active
          ? "border-primary/35 bg-primary-light text-primary shadow-sm hover:bg-primary-light-hover/90 dark:border-primary/40 dark:bg-surface-tertiary dark:text-primary dark:shadow-none dark:ring-1 dark:ring-primary/30 dark:hover:bg-surface-tertiary/90"
          : "border-border bg-surface-secondary text-text-tertiary shadow-sm hover:border-primary/20 hover:bg-surface-tertiary hover:text-text-secondary dark:bg-surface-secondary dark:hover:bg-surface-tertiary"
      } focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/25 focus-visible:ring-offset-1 focus-visible:ring-offset-surface dark:focus-visible:ring-offset-[var(--app-surface-page)]`}
    >
      {icon ? (
        <span className={active ? "shrink-0 text-primary" : "shrink-0 text-text-muted"}>{icon}</span>
      ) : null}
      <span className="min-w-0 max-w-[10rem] truncate sm:max-w-[12rem]">{children}</span>
      {count != null && count >= 0 ? (
        <span
          className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold tabular-nums sm:text-[11px] ${
            active
              ? "border border-primary/35 bg-primary-light text-primary shadow-sm dark:border-primary/40 dark:bg-surface-tertiary dark:text-primary dark:ring-1 dark:ring-primary/25"
              : "border border-border bg-surface-secondary text-text-tertiary shadow-sm dark:bg-surface-tertiary"
          }`}
          aria-hidden
        >
          {count}
        </span>
      ) : null}
    </button>
  );
}

export default function Processes() {
  const navigate = useNavigate();
  const location = useLocation();
  const { processes, hydrated, softDeleteCustomProcess } = useProcesses();
  const { entries: useCases } = useUseCaseCatalog();
  const { areas: BUSINESS_AREAS } = useBusinessAreas();

  const [search, setSearch] = useState("");
  const [areaFilter, setAreaFilter] = useState("");
  const [subjectFilter, setSubjectFilter] = useState("");
  const [collapsedAreaIds, setCollapsedAreaIds] = useState(() => new Set());
  const [addOpen, setAddOpen] = useState(false);
  const [editTarget, setEditTarget] = useState(null);

  const processCountByArea = useMemo(() => {
    const m = new Map();
    for (const p of processes) {
      m.set(p.businessAreaId, (m.get(p.businessAreaId) || 0) + 1);
    }
    return m;
  }, [processes]);

  const monitoredProcessCount = useMemo(() => {
    if (!processes.length) return 0;
    let n = 0;
    for (const p of processes) {
      if (useCasesForProcessId(useCases || [], p.id).length > 0) n++;
    }
    return n;
  }, [processes, useCases]);

  const processCountBySubjectInArea = useMemo(() => {
    if (!areaFilter) return new Map();
    const m = new Map();
    for (const p of processes) {
      if (p.businessAreaId !== areaFilter) continue;
      const s = p.subjectArea || "";
      m.set(s, (m.get(s) || 0) + 1);
    }
    return m;
  }, [processes, areaFilter]);

  const processCountInSelectedArea = useMemo(
    () => (areaFilter ? processCountByArea.get(areaFilter) ?? 0 : 0),
    [processCountByArea, areaFilter]
  );

  const areasWithData = useMemo(() => {
    const ids = new Set(processes.map((p) => p.businessAreaId));
    return BUSINESS_AREAS.filter((ba) => ids.has(ba.id));
  }, [processes, BUSINESS_AREAS]);

  const scopedProcesses = useMemo(() => {
    const q = search.trim().toLowerCase();
    return processes.filter((p) => {
      if (areaFilter && p.businessAreaId !== areaFilter) return false;
      if (subjectFilter && p.subjectArea !== subjectFilter) return false;
      if (!q) return true;
      return (
        p.name.toLowerCase().includes(q) ||
        (p.description || "").toLowerCase().includes(q) ||
        (p.subjectArea || "").toLowerCase().includes(q) ||
        (BUSINESS_AREAS.find((b) => b.id === p.businessAreaId)?.label || "")
          .toLowerCase()
          .includes(q)
      );
    });
  }, [processes, areaFilter, subjectFilter, search, BUSINESS_AREAS]);

  const businessAreasToRender = useMemo(() => {
    if (areaFilter) {
      return BUSINESS_AREAS.filter((ba) => ba.id === areaFilter);
    }
    const ids = new Set(scopedProcesses.map((p) => p.businessAreaId));
    return BUSINESS_AREAS.filter((ba) => ids.has(ba.id));
  }, [areaFilter, scopedProcesses, BUSINESS_AREAS]);

  const subjectFilterChips = useMemo(() => {
    if (!areaFilter) return [];
    const ba = BUSINESS_AREAS.find((b) => b.id === areaFilter);
    if (!ba?.subjectAreas?.length) return [];
    const present = new Set(
      processes.filter((p) => p.businessAreaId === areaFilter).map((p) => p.subjectArea)
    );
    return ba.subjectAreas.filter((s) => present.has(s));
  }, [areaFilter, processes, BUSINESS_AREAS]);

  const hasActiveFilters = Boolean(search.trim() || areaFilter || subjectFilter);

  const clearFilters = useCallback(() => {
    setSearch("");
    setAreaFilter("");
    setSubjectFilter("");
  }, []);

  const handleAdd = useCallback(() => {
    setEditTarget(null);
    setAddOpen(true);
  }, []);

  const handleEditProcess = useCallback((process) => {
    setEditTarget(process);
    setAddOpen(true);
  }, []);

  const handleDeleteProcess = useCallback(
    async (process) => {
      if (!process?.__supabaseId) return;
      if (!window.confirm(`Delete "${process.name}"?`)) return;
      await softDeleteCustomProcess(process.__supabaseId);
    },
    [softDeleteCustomProcess]
  );

  const toggleArea = useCallback((id) => {
    setCollapsedAreaIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  useEffect(() => {
    if (location.state?.openAddProcess) {
      setAddOpen(true);
    }
  }, [location.state?.openAddProcess]);

  useEffect(() => {
    if (!location.state?.openAddProcess) return;
    navigate("/processes", { replace: true, state: {} });
  }, [location.state?.openAddProcess, navigate]);

  useEffect(() => {
    if (!hydrated) return;
    const hash = location.hash?.replace(/^#/, "");
    if (!hash) return;
    if (hash.startsWith("area-")) {
      const areaId = hash.slice("area-".length);
      setCollapsedAreaIds((prev) => {
        if (!prev.has(areaId)) return prev;
        const next = new Set(prev);
        next.delete(areaId);
        return next;
      });
    }
    requestAnimationFrame(() => {
      const el = document.getElementById(hash);
      if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }, [hydrated, location.hash]);

  if (!hydrated) {
    return (
      <div className="min-h-full w-full bg-transparent">
        <div className="mx-auto min-h-[calc(100vh-5rem)] w-full max-w-[1440px] px-5 py-6 sm:px-8">
          <p className="text-base leading-6 text-text-secondary">Loading processes…</p>
        </div>
      </div>
    );
  }

  const totalShown = scopedProcesses.length;
  const nAreas = businessAreasToRender.length;

  return (
    <div className="min-h-full w-full bg-transparent">
      <div className="mx-auto min-h-[calc(100vh-5rem)] w-full max-w-[1440px] px-5 py-6 sm:px-8">
        <header className="flex flex-col gap-3 border-b border-border pb-6 lg:flex-row lg:items-end lg:justify-between" data-tour-processes="header">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-3">
              <h1 className="text-2xl font-semibold tracking-tight text-text-primary">Processes</h1>
              <TourLaunchButton tourId="processes" label="Take a quick Processes tour" />
            </div>
            <p className="mt-1.5 max-w-2xl text-sm leading-6 text-text-secondary sm:text-[15px] sm:leading-7">
              Filter by area, then browse by subject. Each card is one process.
            </p>
            <p className="mt-2 text-sm leading-5 text-text-muted" aria-live="polite">
              {totalShown} {totalShown === 1 ? "process" : "processes"}
              {nAreas > 0 ? (
                <>
                  {" "}
                  in {nAreas} business {nAreas === 1 ? "area" : "areas"}
                </>
              ) : null}
            </p>
          </div>
          <div className="flex shrink-0">
            <button
              type="button"
              onClick={handleAdd}
              className="inline-flex w-full items-center justify-center gap-2 rounded-lg bg-primary px-4 py-2.5 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-primary-hover focus:outline-none focus:ring-2 focus:ring-primary/25 focus:ring-offset-2 focus:ring-offset-surface-page sm:w-auto"
            >
              Add process
            </button>
          </div>
        </header>

        <ProcessesAggregateStats totalProcesses={processes.length} monitoredProcesses={monitoredProcessCount} />

        <div className="mt-4 flex w-full min-w-0 max-w-full flex-nowrap items-stretch gap-2 overflow-x-auto rounded-xl border border-border bg-surface p-1.5 shadow-sm dark:bg-surface-default sm:gap-2.5 sm:p-2" data-tour-processes="filters">
          <label htmlFor="process-search" className="sr-only">
            Search
          </label>
          <div className="relative w-[11rem] shrink-0 sm:w-52 md:w-60">
            <Search
              size={16}
              strokeWidth={2}
              className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-text-muted"
              aria-hidden
            />
            <input
              id="process-search"
              type="search"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search"
              autoComplete="off"
              className="h-9 w-full rounded-lg border border-border bg-surface py-0 pl-8 pr-7 text-sm text-text-primary shadow-sm placeholder:text-text-muted focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
            />
            {search ? (
              <button
                type="button"
                onClick={() => setSearch("")}
                className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded p-0.5 text-text-muted transition hover:bg-surface-secondary hover:text-text-secondary"
                aria-label="Clear search"
              >
                <X size={15} strokeWidth={2} />
              </button>
            ) : null}
          </div>
          <div
            className="hidden w-px shrink-0 self-stretch bg-border sm:block"
            aria-hidden
          />
          <div className="min-w-0 flex-1 overflow-x-auto overscroll-x-contain [-ms-overflow-style:none] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
            <div className="flex min-h-8 w-max items-center gap-1.5 pl-[2px] pr-0.5 sm:min-h-9 sm:gap-2">
              <FilterChip
                active={!areaFilter}
                count={processes.length}
                onClick={() => {
                  setAreaFilter("");
                  setSubjectFilter("");
                }}
                icon={<LayoutGrid size={14} strokeWidth={2} aria-hidden />}
              >
                All areas
              </FilterChip>
              {areasWithData.map((ba) => {
                const AIcon = BUSINESS_AREA_ICONS[ba.id] || Package;
                return (
                  <FilterChip
                    key={ba.id}
                    active={areaFilter === ba.id}
                    count={processCountByArea.get(ba.id) ?? 0}
                    onClick={() => {
                      setAreaFilter(ba.id);
                      setSubjectFilter("");
                    }}
                    icon={<AIcon size={14} strokeWidth={2} aria-hidden />}
                  >
                    {ba.label}
                  </FilterChip>
                );
              })}
              {areaFilter && subjectFilterChips.length > 0 ? (
                <>
                  <div
                    className="mx-0.5 hidden h-5 w-px shrink-0 self-center bg-border sm:block"
                    aria-hidden
                  />
                  <FilterChip
                    active={!subjectFilter}
                    count={processCountInSelectedArea}
                    onClick={() => setSubjectFilter("")}
                    icon={<Layers size={14} strokeWidth={2} aria-hidden />}
                  >
                    All subjects
                  </FilterChip>
                  {subjectFilterChips.map((s) => (
                    <FilterChip
                      key={s}
                      active={subjectFilter === s}
                      count={processCountBySubjectInArea.get(s) ?? 0}
                      onClick={() => setSubjectFilter(s)}
                    >
                      {s}
                    </FilterChip>
                  ))}
                </>
              ) : null}
            </div>
          </div>
          {hasActiveFilters ? (
            <button
              type="button"
              onClick={clearFilters}
              className="shrink-0 self-center rounded-lg px-2.5 py-1.5 text-sm font-semibold text-primary transition hover:bg-primary-light/50"
            >
              Clear
            </button>
          ) : null}
        </div>

        {nAreas === 0 ? (
          <div className="mt-6 rounded-2xl border border-dashed border-border bg-surface p-8 text-center shadow-sm">
            <p className="text-sm font-medium text-text-primary">No processes match the current filters</p>
            <p className="mt-1 text-sm text-text-secondary">Adjust the filters above or clear them to see more.</p>
            {hasActiveFilters ? (
              <button
                type="button"
                onClick={clearFilters}
                className="mt-4 rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-white transition hover:bg-primary-hover"
              >
                Clear filters
              </button>
            ) : null}
          </div>
        ) : (
          <div className="mt-5 flex flex-col gap-4" data-tour-processes="areas">
            {businessAreasToRender.map((ba) => {
              const AreaIcon = BUSINESS_AREA_ICONS[ba.id] || Package;
              const areaProcesses = scopedProcesses.filter((p) => p.businessAreaId === ba.id);
              const subjectGroups = groupProcessesBySubjectArea(areaProcesses, ba);
              const collapsed = collapsedAreaIds.has(ba.id);
              return (
                <section
                  key={ba.id}
                  id={`area-${ba.id}`}
                  className="scroll-mt-24 overflow-hidden rounded-2xl border border-border bg-surface shadow-sm transition-shadow hover:shadow-md dark:bg-surface-default"
                >
                  <div className="border-b border-border bg-surface-page-top/60 dark:border-border dark:bg-surface-secondary/90">
                    <button
                      type="button"
                      onClick={() => toggleArea(ba.id)}
                      className="flex w-full items-center gap-4 bg-primary-light/40 px-4 py-4 text-left ring-1 ring-inset ring-primary/15 dark:bg-primary/10 dark:ring-primary/25 sm:px-6 sm:py-5"
                      aria-expanded={!collapsed}
                      aria-controls={`area-body-${ba.id}`}
                      id={`area-head-${ba.id}`}
                    >
                      <span className="inline-flex h-12 w-12 shrink-0 items-center justify-center rounded-xl bg-primary text-white shadow-sm dark:bg-primary/90">
                        <AreaIcon size={22} strokeWidth={1.75} aria-hidden />
                      </span>
                      <div className="min-w-0 flex-1 flex flex-col gap-1">
                        <span className="text-[10px] font-bold uppercase tracking-[0.14em] text-primary/90 dark:text-primary/85">
                          Business area
                        </span>
                        <div className="flex flex-wrap items-baseline gap-2.5">
                          <h2 className="text-xl font-bold leading-tight text-text-primary sm:text-2xl">
                            {ba.label}
                          </h2>
                          <span
                            className="inline-flex items-center gap-1 rounded-full border border-primary/25 bg-surface px-2.5 py-0.5 text-xs font-semibold tabular-nums text-primary dark:border-primary/40 dark:bg-surface-default"
                            title="Number of processes in this business area"
                          >
                            <span className="tabular-nums">{areaProcesses.length}</span>
                            <span className="text-[10px] font-medium uppercase tracking-wide text-primary/80">
                              process{areaProcesses.length === 1 ? "" : "es"}
                            </span>
                          </span>
                        </div>
                      </div>
                      <ChevronDown
                        size={22}
                        strokeWidth={2}
                        className={`shrink-0 text-primary/70 transition-transform duration-200 ${collapsed ? "-rotate-90" : ""}`}
                        aria-hidden
                      />
                    </button>
                  </div>

                  <div
                    id={`area-body-${ba.id}`}
                    role="region"
                    aria-labelledby={`area-head-${ba.id}`}
                    className={collapsed ? "hidden" : undefined}
                  >
                    {areaProcesses.length === 0 ? (
                      <div className="px-3 py-8 text-center sm:px-4">
                        <p className="text-sm text-text-secondary">No processes here with the current filters.</p>
                      </div>
                    ) : (
                      <div className="space-y-4 p-3 sm:space-y-4 sm:p-5 dark:bg-surface-page/30">
                        {subjectGroups.map(({ subject, list }) => (
                          <div
                            key={subject}
                            className="overflow-hidden rounded-xl border border-border bg-surface-secondary/80 shadow-sm dark:border-border dark:bg-surface-secondary/60"
                          >
                            <div className="border-b border-border/80 bg-surface/90 px-3 py-2.5 dark:border-border dark:bg-surface-default/80 sm:px-4 sm:py-3">
                              <div className="flex flex-wrap items-end justify-between gap-2 sm:items-center">
                                <div className="min-w-0">
                                  <p className="text-[11px] font-semibold uppercase tracking-wide text-text-muted">
                                    Subject area
                                  </p>
                                  <h3 className="mt-0.5 text-base font-semibold leading-tight text-text-primary sm:text-[17px] sm:leading-snug">
                                    {subject}
                                  </h3>
                                </div>
                                <span className="inline-flex items-center gap-1.5 rounded-md border border-border/90 bg-surface px-2 py-1 text-xs font-medium tabular-nums text-text-tertiary dark:border-border dark:bg-surface-default/90">
                                  <Layers size={12} strokeWidth={2} className="shrink-0 text-text-muted" aria-hidden />
                                  {list.length}
                                </span>
                              </div>
                            </div>
                            <ul className="grid list-none grid-cols-1 gap-3 p-3 sm:grid-cols-2 sm:gap-4 sm:p-4 lg:grid-cols-2 xl:grid-cols-3">
                              {list.map((p) => {
                                const Icon = PROCESS_ICONS_MAP[p.iconKey] || PackageCheck;
                                return (
                                  <li key={p.id} className="min-w-0">
                                    <ProcessCard
                                      process={p}
                                      Icon={Icon}
                                      useCases={useCases || []}
                                      onOpenProcess={() => navigate(`/processes/${encodeURIComponent(p.id)}`)}
                                      onEditProcess={p.__supabaseId ? handleEditProcess : undefined}
                                      onDeleteProcess={p.__supabaseId ? handleDeleteProcess : undefined}
                                      onUseCaseClick={(uc) => {
                                        if (uc.source === "user") {
                                          const path = uc.status === "draft"
                                            ? (uc.resumePath || "/use-cases/builder/step-1").split("?")[0]
                                            : "/use-cases/builder/step-8";
                                          navigate(`${path}?reopenCatalogId=${encodeURIComponent(uc.id)}`);
                                        } else {
                                          navigate(useCaseActivationPath(uc.id));
                                        }
                                      }}
                                    />
                                  </li>
                                );
                              })}
                            </ul>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </section>
              );
            })}
          </div>
        )}

        <AddProcessPanel
          open={addOpen}
          onClose={() => {
            setAddOpen(false);
            setEditTarget(null);
          }}
          editTarget={editTarget}
        />
      </div>
    </div>
  );
}

function ProcessCard({ process: p, Icon, useCases, onUseCaseClick, onOpenProcess, onEditProcess, onDeleteProcess }) {
  const matched = useCasesForProcessId(useCases, p.id);
  const status = getProcessAutomationStatus(matched.length);
  const statusMeta = getStatusMeta(status);
  const showRowActions = Boolean(onEditProcess || onDeleteProcess);
  return (
    <article className="group/card relative flex h-full min-h-[140px] flex-col rounded-xl border border-border bg-surface p-4 shadow-sm ring-0 transition-all duration-200 hover:border-primary/20 hover:shadow-md hover:ring-1 hover:ring-primary/10 sm:min-h-[152px] sm:p-5">
      {showRowActions && (
        <div
          className="absolute right-2 top-2 z-10 flex items-center gap-1 opacity-0 transition group-hover/card:opacity-100 focus-within:opacity-100"
          onClick={(e) => e.stopPropagation()}
        >
          {onEditProcess && (
            <button
              type="button"
              onClick={(e) => { e.stopPropagation(); onEditProcess(p); }}
              className="rounded-md p-1.5 text-text-muted hover:bg-surface-secondary hover:text-primary"
              title="Edit process"
              aria-label="Edit process"
            >
              <Pencil size={14} strokeWidth={2} />
            </button>
          )}
          {onDeleteProcess && (
            <button
              type="button"
              onClick={(e) => { e.stopPropagation(); onDeleteProcess(p); }}
              className="rounded-md p-1.5 text-text-muted hover:bg-status-danger-light hover:text-status-danger"
              title="Delete process"
              aria-label="Delete process"
            >
              <Trash2 size={14} strokeWidth={2} />
            </button>
          )}
        </div>
      )}
      <button
        type="button"
        onClick={onOpenProcess}
        className="group/header flex w-full flex-1 items-start gap-3 text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/30 focus-visible:ring-offset-2 focus-visible:ring-offset-surface sm:gap-3.5"
        aria-label={`Open ${p.name} details`}
      >
        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-primary-light transition-colors group-hover/card:bg-primary-light-hover sm:h-11 sm:w-11">
          <Icon size={19} className="text-primary" strokeWidth={1.85} aria-hidden />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-start justify-between gap-2">
            <h3 className="text-[15px] font-semibold leading-tight text-text-primary group-hover/header:text-primary sm:text-base sm:leading-snug">
              {p.name}
            </h3>
            <ChevronRight size={14} className="mt-0.5 shrink-0 text-text-muted opacity-0 transition group-hover/card:opacity-100" aria-hidden />
          </div>
          <div className="mt-1.5">
            <span
              className="inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10.5px] font-semibold tabular-nums sm:text-[11px]"
              style={{ background: statusMeta.bg, color: statusMeta.text, borderColor: statusMeta.border }}
            >
              <span className="h-1.5 w-1.5 rounded-full" style={{ background: statusMeta.dot }} />
              {statusMeta.label}
            </span>
          </div>
          <p className="mt-2 line-clamp-2 text-sm leading-relaxed text-text-secondary sm:leading-6">
            {p.description}
          </p>
        </div>
      </button>
      <ProcessUseCaseLinks matched={matched} onUseCaseClick={onUseCaseClick} />
    </article>
  );
}

function ProcessesAggregateStats({ totalProcesses, monitoredProcesses }) {
  const stats = useMemo(
    () => getProcessesAggregateStats(totalProcesses, monitoredProcesses),
    [totalProcesses, monitoredProcesses]
  );
  const items = [
    { label: "Active processes", value: stats.active, icon: Activity, tone: "primary" },
    { label: "Monitored via use cases", value: stats.monitored, icon: Sparkles, tone: "success" },
    { label: "Analyses today", value: stats.analysesToday.toLocaleString(), icon: CheckCircle2, tone: "primary" },
    { label: "Exceptions open", value: stats.exceptions, icon: AlertTriangle, tone: stats.exceptions > 0 ? "warning" : "neutral" },
  ];
  const toneClasses = {
    primary: {
      wrap: "border-border bg-surface dark:border-border dark:bg-surface-default",
      value: "text-text-primary",
      icon: "text-primary",
      iconBg: "bg-primary-light dark:bg-surface-tertiary dark:ring-1 dark:ring-primary/25",
    },
    success: {
      wrap: "border-status-success-border/60 bg-status-success-light/30 dark:border-status-success-border/40 dark:bg-status-success/10",
      value: "text-status-success",
      icon: "text-status-success",
      iconBg: "bg-status-success-light dark:bg-surface-tertiary dark:text-status-success dark:ring-1 dark:ring-status-success-border/45",
    },
    warning: {
      wrap: "border-status-warning-border bg-status-warning-light dark:border-status-warning-border/45 dark:bg-status-warning/12",
      value: "text-status-warning",
      icon: "text-status-warning",
      iconBg: "bg-status-warning-light dark:bg-surface-tertiary dark:text-status-warning dark:ring-1 dark:ring-status-warning-border/45",
    },
    neutral: {
      wrap: "border-border bg-surface-secondary dark:border-border dark:bg-surface-default",
      value: "text-text-tertiary",
      icon: "text-text-muted",
      iconBg: "bg-surface-secondary dark:bg-surface-tertiary",
    },
  };
  return (
    <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4 sm:gap-4">
      {items.map((it) => {
        const ItIcon = it.icon;
        const t = toneClasses[it.tone] || toneClasses.primary;
        return (
          <div key={it.label} className={`flex items-center gap-3 rounded-xl border px-4 py-3 shadow-sm ${t.wrap}`}>
            <div className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ${t.iconBg}`}>
              <ItIcon size={16} className={t.icon} strokeWidth={1.85} aria-hidden />
            </div>
            <div className="min-w-0">
              <p className="text-[11px] font-semibold uppercase tracking-wide text-text-muted">{it.label}</p>
              <p className={`text-lg font-semibold tabular-nums ${t.value}`}>{it.value}</p>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function ProcessUseCaseLinks({ matched, onUseCaseClick }) {
  const [expanded, setExpanded] = useState(false);
  const canCollapse = matched.length > USE_CASE_PILL_LIMIT;
  const visible = expanded || !canCollapse ? matched : matched.slice(0, USE_CASE_PILL_LIMIT);
  const extra = matched.length - visible.length;

  return (
    <div className="mt-3 border-t border-border/90 pt-3 dark:border-border">
      <div className="flex items-center justify-between gap-2">
        <p className="flex min-w-0 items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-status-success sm:text-xs">
          <ClipboardList size={13} strokeWidth={2.25} className="shrink-0 text-status-success" aria-hidden />
          <span>Involved in use cases</span>
          <span
            className="rounded-full border border-status-success-border/80 bg-status-success-light px-2 py-0.5 text-[11px] font-semibold tabular-nums text-status-success sm:text-xs"
            title="In catalog"
          >
            {matched.length}
          </span>
        </p>
        {canCollapse ? (
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            aria-expanded={expanded}
            className="inline-flex shrink-0 items-center gap-0.5 rounded text-xs font-semibold text-primary transition hover:opacity-90"
          >
            {expanded ? "Less" : "All"}
            <ChevronDown
              size={12}
              strokeWidth={2.5}
              aria-hidden
              className={`transition-transform duration-200 ${expanded ? "rotate-180" : ""}`}
            />
          </button>
        ) : null}
      </div>
      {matched.length === 0 ? (
        <p className="mt-2 text-sm text-text-muted">None in catalog yet.</p>
      ) : (
        <div className="mt-2 flex flex-wrap gap-2">
          {visible.map((uc) => (
            <button
              key={uc.id}
              type="button"
              onClick={() => onUseCaseClick(uc)}
              title={uc.problemSignal || uc.title}
              className="max-w-full truncate rounded-lg border border-border bg-surface-secondary px-2.5 py-1 text-left text-xs font-medium leading-snug text-text-tertiary transition hover:border-primary/35 hover:bg-surface hover:text-primary focus:outline-none focus:ring-2 focus:ring-primary/20 sm:text-[13px]"
            >
              {uc.title}
            </button>
          ))}
          {!expanded && extra > 0 ? (
            <button
              type="button"
              onClick={() => setExpanded(true)}
              className="rounded-lg border border-dashed border-border px-2.5 py-1 text-xs font-semibold text-primary transition hover:border-primary/50 hover:bg-primary-light/50"
            >
              +{extra} more
            </button>
          ) : null}
        </div>
      )}
    </div>
  );
}
