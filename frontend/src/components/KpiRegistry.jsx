// KPI Registry - Enterprise → business area → subject area; HTML table for KPI rows.
import { useEffect, useMemo, useRef, useState } from "react";
import { Briefcase, ChevronDown, LayoutGrid, CircleHelp, Zap } from "lucide-react";
import { useKpiRegistry } from "../hooks/useKpiRegistry";
import { useProcesses } from "../hooks/useProcesses";
import { useLocation, useNavigate } from "react-router-dom";
import { Select, Switch } from "./ui";
import { allBusinessAreaOptions, subjectAreaOptionsForBusinessArea } from "../domain/business-areas";
import { useBusinessAreas } from "../hooks/useBusinessAreas";
import { MIDDLEBY_ANALYTICS_AREAS, middlebySubjectAreaFormOptions } from "../domain/middleby-analytics-areas";
import { useDemoProfile } from "../context/DemoProfileContext";
import { isMiddlebyProfile } from "../domain/profile-capabilities";
import { getCatalogEntriesForKpiName, getKpiNamesForCatalogId } from "../domain/kpi-usecase-links";
import { getUseCasesForRole } from "../domain/featured-use-cases";
import { useUseCaseCatalog } from "../hooks/useUseCaseCatalog";
import { useCaseActivationPath } from "../domain/automation-registry";
import AddKpiPanel from "./AddKpiPanel";

function IconEditRow() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
      <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
    </svg>
  );
}

function IconTrashRow() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <polyline points="3 6 5 6 21 6" />
      <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
    </svg>
  );
}

function PoweredByCell({ kpiName, profileId, onOpen }) {
  const entries = useMemo(
    () => getCatalogEntriesForKpiName(kpiName, profileId),
    [kpiName, profileId]
  );
  const [expanded, setExpanded] = useState(false);
  if (!entries.length) {
    return <span className="text-[13px] text-text-muted">-</span>;
  }
  const MAX_VISIBLE = 2;
  const visible = expanded ? entries : entries.slice(0, MAX_VISIBLE);
  const extra = entries.length - MAX_VISIBLE;
  return (
    <div className="flex min-w-0 flex-wrap items-center gap-1">
      {visible.map((e) => (
        <button
          key={e.id}
          type="button"
          onClick={(ev) => {
            ev.stopPropagation();
            onOpen(e.id);
          }}
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
          onClick={(ev) => {
            ev.stopPropagation();
            setExpanded(true);
          }}
          title={`Show ${extra} more`}
          className="inline-flex items-center rounded-md border border-border bg-surface-secondary px-1.5 py-0.5 text-[12px] font-medium text-text-secondary transition hover:border-primary/40 hover:bg-primary-light hover:text-primary"
        >
          +{extra} more
        </button>
      ) : null}
      {expanded && entries.length > MAX_VISIBLE ? (
        <button
          type="button"
          onClick={(ev) => {
            ev.stopPropagation();
            setExpanded(false);
          }}
          className="inline-flex items-center rounded-md border border-border bg-surface-secondary px-1.5 py-0.5 text-[12px] font-medium text-text-secondary transition hover:border-primary/40 hover:bg-primary-light hover:text-primary"
        >
          Show less
        </button>
      ) : null}
    </div>
  );
}

function MapsToCell({ processIds, processLabelById }) {
  if (!Array.isArray(processIds) || processIds.length === 0) {
    return <span className="text-[13px] text-text-muted">-</span>;
  }
  const labels = processIds
    .map((id) => processLabelById[id])
    .filter(Boolean);
  if (labels.length === 0) {
    return <span className="text-[13px] text-text-muted">-</span>;
  }
  const MAX_VISIBLE = 2;
  const visible = labels.slice(0, MAX_VISIBLE);
  const extra = labels.length - visible.length;
  return (
    <div className="flex min-w-0 flex-wrap items-center gap-1">
      {visible.map((label) => (
        <span
          key={label}
          className="inline-flex max-w-full min-w-0 items-center truncate rounded-md border border-primary/25 bg-primary-light/50 px-1.5 py-0.5 text-[12px] font-medium text-primary dark:border-primary/35 dark:bg-surface-tertiary dark:ring-1 dark:ring-primary/25"
          title={label}
        >
          {label}
        </span>
      ))}
      {extra > 0 ? (
        <span
          className="inline-flex items-center rounded-md border border-border bg-surface-secondary px-1.5 py-0.5 text-[12px] font-medium text-text-secondary"
          title={labels.slice(MAX_VISIBLE).join(", ")}
        >
          +{extra} more
        </span>
      ) : null}
    </div>
  );
}

function KpiTable({
  title,
  countLabel,
  rows,
  isOpen,
  onToggleOpen,
  onToggleKpi,
  onEditKpi,
  onDeleteKpi,
  processLabelById,
  profileId,
  onOpenUseCase,
  hideMapsTo = false,
}) {
  const previewDesc =
    rows.length > 0
      ? rows
          .map((r) => r.kpi.desc)
          .filter(Boolean)
          .slice(0, 2)
          .join(" · ")
      : "";

  return (
    <div className="w-full min-w-0 overflow-hidden rounded-xl border border-border bg-surface-default shadow-[0_1px_3px_rgba(16,24,40,0.06)] dark:shadow-none">
      <div className="flex items-center gap-3 border-b border-border bg-surface-secondary px-3 py-3 sm:pl-4 sm:pr-3">
        <button
          type="button"
          onClick={onToggleOpen}
          aria-expanded={isOpen}
          className="flex min-w-0 flex-1 items-center gap-2.5 text-left"
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
            <span
              className="min-w-0 flex-1 truncate text-[11px] leading-snug text-text-muted"
              title={previewDesc}
            >
              · {previewDesc}
              {rows.length > 2 ? "…" : ""}
            </span>
          ) : null}
        </button>
        <div className="flex shrink-0 items-center gap-0.5">
          <button
            type="button"
            className="rounded-lg p-1.5 text-text-muted transition-colors hover:bg-surface-secondary"
            aria-label={`Edit ${title}`}
            onClick={() => rows[0] && onEditKpi(rows[0])}
            disabled={rows.length === 0}
          >
            <IconEditRow />
          </button>
          <button
            type="button"
            className="rounded-lg p-1.5 text-text-muted transition-colors hover:bg-surface-secondary hover:text-red-500"
            aria-label={`Delete ${title}`}
            onClick={() =>
              rows[0] && onDeleteKpi(rows[0].categoryIndex, rows[0].sectionIndex, rows[0].kpiIndex)
            }
            disabled={rows.length === 0}
          >
            <IconTrashRow />
          </button>
        </div>
      </div>

      {isOpen ? (
        <div className="overflow-x-auto bg-surface-default">
          <table className="w-full min-w-[52rem] table-fixed border-collapse text-left text-[13px]">
            <colgroup>
              <col style={{ width: hideMapsTo ? "20%" : "17%" }} />
              <col style={{ width: hideMapsTo ? "38%" : "30%" }} />
              {!hideMapsTo ? <col style={{ width: "20%" }} /> : null}
              <col style={{ width: hideMapsTo ? "32%" : "22%" }} />
              <col style={{ width: "5.5rem" }} />
              <col style={{ width: "4.75rem" }} />
            </colgroup>
            <thead>
              <tr className="border-b border-border bg-surface-secondary">
                <th
                  scope="col"
                  className="min-w-0 px-3 py-2.5 pl-4 text-[10px] font-bold uppercase tracking-[0.08em] text-text-muted sm:pl-5"
                  title="The KPI's canonical name in the enterprise registry. Used across dashboards, playbooks, and cross-source joins."
                >
                  <span className="inline-flex items-center gap-1">
                    KPI name
                    <CircleHelp className="h-3 w-3 text-text-muted" strokeWidth={2} aria-hidden />
                  </span>
                </th>
                <th
                  scope="col"
                  className="min-w-0 px-3 py-2.5 text-[10px] font-bold uppercase tracking-[0.08em] text-text-muted"
                  title="What this KPI measures and where the value comes from."
                >
                  <span className="inline-flex items-center gap-1">
                    Description
                    <CircleHelp className="h-3 w-3 text-text-muted" strokeWidth={2} aria-hidden />
                  </span>
                </th>
                {!hideMapsTo ? (
                  <th
                    scope="col"
                    className="min-w-0 px-3 py-2.5 text-[10px] font-bold uppercase tracking-[0.08em] text-text-muted"
                    title="Business processes this KPI feeds into. See the Processes registry for definitions."
                  >
                    <span className="inline-flex items-center gap-1">
                      Maps to
                      <CircleHelp className="h-3 w-3 text-text-muted" strokeWidth={2} aria-hidden />
                    </span>
                  </th>
                ) : null}
                <th
                  scope="col"
                  className="min-w-0 px-3 py-2.5 text-[10px] font-bold uppercase tracking-[0.08em] text-text-muted"
                  title="Use cases whose automations move this KPI"
                >
                  <span className="inline-flex items-center gap-1">
                    Powered by
                    <CircleHelp className="h-3 w-3 text-text-muted" strokeWidth={2} aria-hidden />
                  </span>
                </th>
                <th
                  scope="col"
                  className="w-[5.5rem] min-w-[5.5rem] max-w-[5.5rem] align-middle px-2 py-2.5 text-center text-[10px] font-bold uppercase tracking-[0.08em] text-text-muted"
                >
                  Status
                </th>
                <th
                  scope="col"
                  className="w-[4.75rem] min-w-[4.75rem] max-w-[4.75rem] px-2 py-2.5 text-right text-[10px] font-bold uppercase tracking-[0.08em] text-text-muted"
                >
                  Actions
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {rows.map((r, rowIdx) => (
                <tr
                  key={`${r.categoryIndex}-${r.sectionIndex}-${r.kpiIndex}`}
                  className={`transition-colors duration-150 hover:bg-surface-secondary ${
                    rowIdx % 2 === 1 ? "bg-surface-secondary/50" : "bg-surface-default"
                  }`}
                >
                  <td className="min-w-0 align-middle px-3 py-3 pl-4 sm:pl-5">
                    <div className="flex min-w-0 max-w-full items-center gap-2.5">
                      <span className="shrink-0 rounded border border-primary/30 bg-primary-light px-1.5 py-0.5 text-[10px] font-bold leading-none text-primary dark:border-primary/40 dark:bg-surface-tertiary dark:ring-1 dark:ring-primary/25">
                        KPI
                      </span>
                      <span
                        className="min-w-0 truncate text-[15px] font-semibold leading-snug text-text-primary"
                        title={r.kpi.name}
                      >
                        {r.kpi.name}
                      </span>
                    </div>
                  </td>
                  <td className="min-w-0 align-top px-3 py-3 text-[13px] leading-[1.45] text-text-secondary">
                    <span title={r.kpi.desc || undefined} className="line-clamp-2">
                      {r.kpi.desc || "-"}
                    </span>
                  </td>
                  {!hideMapsTo ? (
                    <td className="min-w-0 align-middle px-3 py-3 text-[13px]">
                      <MapsToCell processIds={r.kpi.processMapping} processLabelById={processLabelById} />
                    </td>
                  ) : null}
                  <td className="min-w-0 align-middle px-3 py-3 text-[13px]">
                    <PoweredByCell kpiName={r.kpi.name} profileId={profileId} onOpen={onOpenUseCase} />
                  </td>
                  <td className="w-[5.5rem] min-w-[5.5rem] max-w-[5.5rem] align-middle px-2 py-3">
                    <div className="flex w-full items-center justify-center [&_button]:shrink-0">
                      <Switch
                        aria-label={
                          r.kpi.active ? `Disable KPI ${r.kpi.name ?? ""}` : `Enable KPI ${r.kpi.name ?? ""}`
                        }
                        checked={r.kpi.active}
                        onCheckedChange={() => onToggleKpi(r.categoryIndex, r.sectionIndex, r.kpiIndex)}
                        size="compact"
                      />
                    </div>
                  </td>
                  <td className="w-[4.75rem] min-w-[4.75rem] max-w-[4.75rem] align-middle px-1 py-3 pr-3 text-right">
                    <div className="inline-flex items-center justify-end gap-0.5">
                      <button
                        type="button"
                        className="rounded-md p-1.5 text-text-muted opacity-80 transition-all hover:bg-surface-secondary hover:opacity-100"
                        aria-label="Edit KPI"
                        onClick={() => onEditKpi(r)}
                      >
                        <IconEditRow />
                      </button>
                      <button
                        type="button"
                        className="rounded-md p-1.5 text-text-muted opacity-80 transition-all hover:bg-red-500/10 hover:opacity-100 hover:text-red-500"
                        aria-label="Delete KPI"
                        onClick={() => onDeleteKpi(r.categoryIndex, r.sectionIndex, r.kpiIndex)}
                      >
                        <IconTrashRow />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
}

export default function KpiRegistry() {
  const navigate = useNavigate();
  const { profileId, role } = useDemoProfile();
  const { entries: catalogEntries } = useUseCaseCatalog();
  const { areas: BUSINESS_AREAS } = useBusinessAreas();
  const middlebyKpis = isMiddlebyProfile(profileId);
  // useBusinessAreas already seeds Middleby profile with MIDDLEBY_ANALYTICS_AREAS,
  // so both branches read from the same live hook — no static fallback needed.
  const profileBusinessAreas = BUSINESS_AREAS;
  const businessAreaOptions = [
    { value: "", label: middlebyKpis ? "All areas" : "All Business Area" },
    ...BUSINESS_AREAS.map((b) => ({ value: b.id, label: b.label })),
  ];
  const [search, setSearch] = useState("");
  const {
    data,
    setData,
    hydrated,
    softDeleteCustomKpi,
    updateCustomKpi,
  } = useKpiRegistry();
  const { processes } = useProcesses();
  const kpiData = data.categories;

  const processLabelById = useMemo(
    () => Object.fromEntries((processes || []).map((p) => [p.id, p.name])),
    [processes]
  );

  const location = useLocation();
  const initialAreaFromUrl = useMemo(() => {
    const params = new URLSearchParams(location.search);
    const id = (params.get("area") || "").trim();
    if (!id) return "";
    return profileBusinessAreas.some((b) => b.id === id) ? id : "";
  }, [location.search, profileBusinessAreas]);

  const [subjectOpen, setSubjectOpen] = useState({});
  const [businessGroupOpen, setBusinessGroupOpen] = useState({});
  const [businessAreaId, setBusinessAreaId] = useState(initialAreaFromUrl);
  const [subjectArea, setSubjectArea] = useState("");
  /** Optional soft filter: narrow to KPIs linked to the current role's use cases. */
  const [forMyRole, setForMyRole] = useState(false);
  const [addPanelOpen, setAddPanelOpen] = useState(false);
  const [editTarget, setEditTarget] = useState(null);
  const defaultsAppliedRef = useRef(false);

  const subjectFilterOptions = useMemo(() => {
    // Derive subject areas from the live hook so renames propagate.
    if (!businessAreaId) {
      const all = new Set();
      for (const a of BUSINESS_AREAS) {
        for (const s of a.subjectAreas || []) all.add(s);
      }
      return [
        { value: "", label: "All Subject Area" },
        ...Array.from(all).map((s) => ({ value: s, label: s })),
      ];
    }
    const area = BUSINESS_AREAS.find((a) => a.id === businessAreaId);
    const list = area?.subjectAreas || [];
    return [
      { value: "", label: middlebyKpis ? "All subject areas" : "All Subject Area" },
      ...list.map((s) => ({ value: s, label: s })),
    ];
  }, [BUSINESS_AREAS, businessAreaId, middlebyKpis]);

  const toggleSubject = (key) => {
    setSubjectOpen((prev) => ({ ...prev, [key]: !(prev[key] ?? false) }));
  };

  const toggleBusinessGroup = (baKey) => {
    setBusinessGroupOpen((prev) => ({ ...prev, [baKey]: !(prev[baKey] ?? false) }));
  };

  const subjectToggleKey = (baKey, subject) => `${baKey}\0${subject}`;

  const toggleKpiActive = (categoryIndex, sectionIndex, kpiIndex) => {
    const kpi = data?.categories?.[categoryIndex]?.sections?.[sectionIndex]?.kpis?.[kpiIndex];
    if (!kpi) return;
    const nextActive = !kpi.active;
    // Custom (Supabase-backed) rows must be updated in Supabase, otherwise
    // the next realtime refetch reverts the toggle. Seed rows stay in
    // localStorage via setData.
    if (kpi.__supabaseId) {
      void updateCustomKpi(kpi.__supabaseId, { extras: { active: nextActive } });
      return;
    }
    setData((prev) => ({
      ...prev,
      categories: prev.categories.map((category, ci) => {
        if (ci !== categoryIndex) return category;
        return {
          ...category,
          sections: category.sections.map((section, si) => {
            if (si !== sectionIndex) return section;
            return {
              ...section,
              kpis: section.kpis.map((kpi, ki) => {
                if (ki !== kpiIndex) return kpi;
                return { ...kpi, active: nextActive };
              }),
            };
          }),
        };
      }),
    }));
  };

  const deleteKpi = async (categoryIndex, sectionIndex, kpiIndex) => {
    const kpi = data?.categories?.[categoryIndex]?.sections?.[sectionIndex]?.kpis?.[kpiIndex];
    if (!kpi) return;

    // Seed rows can't be deleted - they live in code, not in a store.
    if (kpi.seed && !kpi.__supabaseId) {
      window.alert("Seed KPIs are read-only. Add a custom KPI instead.");
      return;
    }

    if (!window.confirm("Remove this KPI?")) return;

    // Supabase-owned custom rows: soft-delete via Supabase (persists for
    // everyone, undoable within 24h from the audit trail).
    if (kpi.__supabaseId) {
      await softDeleteCustomKpi(kpi.__supabaseId);
      return;
    }

    // Legacy localStorage-added rows: filter locally.
    setData((prev) => ({
      ...prev,
      categories: prev.categories.map((category, ci) => {
        if (ci !== categoryIndex) return category;
        return {
          ...category,
          sections: category.sections.map((section, si) => {
            if (si !== sectionIndex) return section;
            return {
              ...section,
              kpis: section.kpis.filter((_, ki) => ki !== kpiIndex),
            };
          }),
        };
      }),
    }));
  };

  const editKpi = (r) => {
    setEditTarget({ ci: r.categoryIndex, si: r.sectionIndex, ki: r.kpiIndex });
    setAddPanelOpen(true);
  };

  // Open edit panel via URL param (deep-link from other screens like KPI mappings)
  useEffect(() => {
    if (!hydrated) return;
    const params = new URLSearchParams(location.search);
    const editParam = params.get("edit");
    if (!editParam) return;
    const parts = editParam.split(".").map((n) => Number(n));
    if (parts.length !== 3 || parts.some((n) => !Number.isFinite(n))) return;
    const [ci, si, ki] = parts;
    if (!data?.categories?.[ci]?.sections?.[si]?.kpis?.[ki]) return;
    setEditTarget({ ci, si, ki });
    setAddPanelOpen(true);
    // Strip the query param so reopening via back button doesn't re-trigger
    const nextParams = new URLSearchParams(location.search);
    nextParams.delete("edit");
    navigate(
      { pathname: location.pathname, search: nextParams.toString() ? `?${nextParams}` : "" },
      { replace: true }
    );
  }, [hydrated, location.search, location.pathname, data, navigate]);

  // Open add panel from hub header (?add-kpi=1)
  useEffect(() => {
    if (!hydrated) return;
    const params = new URLSearchParams(location.search);
    if (params.get("add-kpi") !== "1") return;
    setEditTarget(null);
    setAddPanelOpen(true);
    const nextParams = new URLSearchParams(location.search);
    nextParams.delete("add-kpi");
    navigate(
      { pathname: location.pathname, search: nextParams.toString() ? `?${nextParams}` : "" },
      { replace: true }
    );
  }, [hydrated, location.search, location.pathname, navigate]);

  /** KPI names linked to the current role's use cases (for the "For my role" chip). */
  const roleKpiNames = useMemo(() => {
    const set = new Set();
    const roleUseCases = getUseCasesForRole(catalogEntries, profileId, role);
    for (const uc of roleUseCases) {
      for (const n of getKpiNamesForCatalogId(uc.id, 10)) set.add(n);
    }
    return set;
  }, [catalogEntries, profileId, role]);

  const { groupedByBusinessArea, totalFiltered, businessAreaGroupCount, subjectAreaCount } = useMemo(() => {
    const needle = search.trim().toLowerCase();
    /** businessAreaKey -> Map subjectLabel -> rows */
    const baMap = new Map();

    (kpiData || []).forEach((cat, categoryIndex) => {
      (cat.sections || []).forEach((sec, sectionIndex) => {
        (sec.kpis || []).forEach((kpi, kpiIndex) => {
          const kBusiness = kpi.businessAreaId || sec.businessAreaId || "";
          const kSubject = kpi.subjectArea || sec.name || "";

          if (businessAreaId && kBusiness !== businessAreaId) return;
          if (subjectArea && kSubject !== subjectArea) return;
          if (forMyRole && !roleKpiNames.has(kpi.name)) return;

          if (needle) {
            const proc = Array.isArray(kpi.processMapping)
              ? kpi.processMapping.map((id) => processLabelById[id] || "").join(" ")
              : "";
            const baLabel =
              profileBusinessAreas.find((b) => b.id === kBusiness)?.label || kBusiness;
            const match =
              kpi.name.toLowerCase().includes(needle) ||
              (kpi.desc || "").toLowerCase().includes(needle) ||
              kSubject.toLowerCase().includes(needle) ||
              baLabel.toLowerCase().includes(needle) ||
              (kpi.level || "").toLowerCase().includes(needle) ||
              (kpi.unit || "").toLowerCase().includes(needle) ||
              (kpi.industryBenchmark || "").toLowerCase().includes(needle) ||
              proc.toLowerCase().includes(needle);
            if (!match) return;
          }

          const baKey = kBusiness || "__uncategorized__";
          if (!baMap.has(baKey)) baMap.set(baKey, new Map());
          const subjectMap = baMap.get(baKey);
          const subjectKey = kSubject || "Uncategorized";
          if (!subjectMap.has(subjectKey)) subjectMap.set(subjectKey, []);
          subjectMap.get(subjectKey).push({ categoryIndex, sectionIndex, kpiIndex, kpi });
        });
      });
    });

    const orderRank = (id) => {
      if (id === "__uncategorized__") return 9999;
      const i = profileBusinessAreas.findIndex((b) => b.id === id);
      return i >= 0 ? i : 3000;
    };

    const grouped = Array.from(baMap.entries())
      .map(([baId, subjectMap]) => {
        const label =
          baId === "__uncategorized__"
            ? "Uncategorized"
            : profileBusinessAreas.find((b) => b.id === baId)?.label || baId;
        const subjects = Array.from(subjectMap.entries())
          .map(([subject, rows]) => ({ subject, rows }))
          .sort((a, b) => a.subject.localeCompare(b.subject));
        return { id: baId, label, subjects };
      })
      .sort((a, b) => {
        const d = orderRank(a.id) - orderRank(b.id);
        if (d !== 0) return d;
        return a.label.localeCompare(b.label);
      });

    const totals = grouped.reduce(
      (acc, g) => {
        acc.kpis += g.subjects.reduce((n, s) => n + s.rows.length, 0);
        acc.subjects += g.subjects.length;
        return acc;
      },
      { kpis: 0, subjects: 0 }
    );

    return {
      groupedByBusinessArea: grouped,
      totalFiltered: totals.kpis,
      businessAreaGroupCount: grouped.length,
      subjectAreaCount: totals.subjects,
    };
  }, [kpiData, businessAreaId, subjectArea, search, processLabelById, profileBusinessAreas, forMyRole, roleKpiNames]);

  // On first load, expand the first business area AND all of its subject areas so
  // the user immediately sees KPI rows without having to drill into two levels.
  useEffect(() => {
    if (!hydrated || defaultsAppliedRef.current) return;
    if (groupedByBusinessArea.length === 0) return;
    const firstBa = groupedByBusinessArea[0];
    setBusinessGroupOpen((prev) => ({ ...prev, [firstBa.id]: prev[firstBa.id] ?? true }));
    setSubjectOpen((prev) => {
      const next = { ...prev };
      firstBa.subjects.forEach((s) => {
        const key = subjectToggleKey(firstBa.id, s.subject);
        if (next[key] === undefined) next[key] = true;
      });
      return next;
    });
    defaultsAppliedRef.current = true;
  }, [hydrated, groupedByBusinessArea]);

  if (!hydrated) {
    return (
      <div className="w-full min-w-0">
        <p className="text-text-secondary">Loading KPI registry…</p>
      </div>
    );
  }

  return (
    <div className="w-full min-w-0">
      <div className="mb-6 rounded-xl border border-border bg-surface-secondary p-3 sm:p-4" data-tour-kpi-reg="filters">
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
          <button
            type="button"
            onClick={() => setForMyRole((v) => !v)}
            aria-pressed={forMyRole}
            data-tour-kpi-reg="for-my-role"
            title={
              roleKpiNames.size
                ? `Narrow to ${roleKpiNames.size} KPIs linked to your role's use cases`
                : "No KPIs linked to your role yet"
            }
            className={`inline-flex h-9 items-center gap-1.5 rounded-lg border px-3 text-xs font-semibold transition ${
              forMyRole
                ? "border-primary/40 bg-primary-light text-primary dark:border-primary/45 dark:bg-surface-tertiary"
                : "border-border bg-surface-default text-text-secondary hover:border-primary/30 hover:text-text-primary"
            }`}
          >
            <Briefcase size={12} strokeWidth={2.25} aria-hidden />
            For my role
          </button>
          <Select
            label={null}
            options={businessAreaOptions}
            value={businessAreaId}
            onChange={(e) => { setBusinessAreaId(e.target.value); setSubjectArea(""); }}
          />
          {/* Hide subject-area dropdown when there is nothing meaningful to
              filter by (no business area picked, or the picked area has
              at most one subject area). Adds no value otherwise. */}
          {subjectFilterOptions.filter((o) => o.value !== "").length > 1 ? (
            <Select
              label={null}
              options={subjectFilterOptions}
              value={subjectArea}
              onChange={(e) => setSubjectArea(e.target.value)}
            />
          ) : null}
        </div>
        </div>
      </div>


      <div className="w-full space-y-5">
        <div className="flex w-full min-w-0 items-stretch gap-0">
          {/* Tree rail: icon + vertical trunk (connects to nested guides) */}
          <div className="relative flex w-9 shrink-0 flex-col items-center self-stretch sm:w-10">
            <div className="relative z-10 flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-primary/25 bg-primary-light shadow-[0_1px_2px_rgba(105,65,198,0.12)] dark:border-primary/35 dark:bg-surface-tertiary dark:shadow-none dark:ring-1 dark:ring-primary/25">
              <Briefcase className="h-[18px] w-[18px] text-primary" strokeWidth={1.75} aria-hidden />
            </div>
            <div
              className="absolute left-1/2 top-10 z-0 h-[calc(100%-2.5rem)] w-px -translate-x-1/2 bg-gradient-to-b from-border via-border/70 to-transparent"
              aria-hidden
            />
          </div>

          <div className="min-w-0 w-full flex-1 self-start pl-3 sm:pl-4">
            <div className="flex h-fit w-full flex-wrap items-baseline gap-x-3 gap-y-1 pt-2 text-left">
              <h2 className="text-[17px] font-bold tracking-tight text-text-primary">
                {middlebyKpis ? "Middleby Analytics KPIs" : "Enterprise KPIs"}
              </h2>
              <span className="text-sm text-text-muted">
                {businessAreaGroupCount} Business Areas
                <span className="mx-1.5 text-border-light">·</span>
                {subjectAreaCount} Subject Areas
                <span className="mx-1.5 text-border-light">·</span>
                {totalFiltered} KPIs
              </span>
            </div>

            {groupedByBusinessArea.length ? (
              <div className="relative mt-4 w-full min-w-0 space-y-3.5" data-tour-kpi-reg="tree">
                {groupedByBusinessArea.map((ba) => {
                  const baKpiTotal = ba.subjects.reduce((n, s) => n + s.rows.length, 0);
                  const baOpen = businessGroupOpen[ba.id] ?? false;
                  return (
                    <div key={ba.id} className="w-full min-w-0 space-y-2.5">
                      <button
                        type="button"
                        onClick={() => toggleBusinessGroup(ba.id)}
                        aria-expanded={baOpen}
                        className="flex w-full min-w-0 flex-wrap items-center gap-2 rounded-xl border border-border bg-surface-default px-3 py-2.5 text-left shadow-[0_1px_2px_rgba(16,24,40,0.04)] transition-colors hover:border-border-light hover:bg-surface-secondary dark:shadow-none sm:px-3.5"
                      >
                        <ChevronDown
                          className={`h-4 w-4 shrink-0 text-primary transition-transform duration-200 ${baOpen ? "rotate-180" : "rotate-0"}`}
                          strokeWidth={2}
                          aria-hidden
                        />
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

                      {baOpen ? (
                        <div className="ml-6 w-full min-w-0 space-y-3 border-l-2 border-primary/25 pl-3.5 dark:border-primary/35 sm:pl-4">
                          {ba.subjects.map((sec) => {
                            const stKey = subjectToggleKey(ba.id, sec.subject);
                            const open = subjectOpen[stKey] ?? false;
                            return (
                              <KpiTable
                                key={stKey}
                                title={sec.subject}
                                countLabel={`${sec.rows.length} KPIs`}
                                rows={sec.rows}
                                isOpen={open}
                                onToggleOpen={() => toggleSubject(stKey)}
                                onToggleKpi={toggleKpiActive}
                                onEditKpi={editKpi}
                                onDeleteKpi={deleteKpi}
                                processLabelById={processLabelById}
                                profileId={profileId}
                                onOpenUseCase={(cid) => navigate(useCaseActivationPath(cid))}
                                hideMapsTo={middlebyKpis}
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
      </div>

      <AddKpiPanel
        open={addPanelOpen}
        onClose={() => {
          setAddPanelOpen(false);
          setEditTarget(null);
        }}
        editTarget={editTarget}
      />
    </div>
  );
}
