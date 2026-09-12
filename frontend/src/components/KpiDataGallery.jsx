import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  Database,
  MoreVertical,
  Pencil,
  Search,
  Trash2,
  AlertTriangle,
} from "lucide-react";
import { Select } from "./ui";
import {
  KPI_DATA_CATALOG_DATASETS,
  getDatasetById,
  groupDatasetsBySource,
} from "../domain/kpi-data-catalog";
import { useKpiSources } from "../hooks/useKpiSources";
import sapSvg from "../assets/sap.svg";
import snowflakeSvg from "../assets/snowflake.svg";
import postgresSvg from "../assets/postgres.svg";
import salesforceSvg from "../assets/salesforce.svg";

const SOURCE_ICON = {
  sap: sapSvg,
  snowflake: snowflakeSvg,
  postgres: postgresSvg,
  salesforce: salesforceSvg,
};

const SOURCE_OPTIONS = [
  { value: "", label: "Source: All" },
  { value: "sap", label: "SAP" },
  { value: "snowflake", label: "Snowflake" },
  { value: "postgres", label: "PostgreSQL" },
  { value: "salesforce", label: "Salesforce" },
];

const AREA_OPTIONS = [
  { value: "", label: "Business area: All" },
  { value: "Inventory", label: "Inventory" },
  { value: "Orders", label: "Orders" },
  { value: "E-Commerce", label: "E-Commerce" },
  { value: "Support", label: "Support" },
  { value: "Finance", label: "Finance" },
  { value: "Pricing", label: "Pricing" },
  { value: "Supply Chain", label: "Supply Chain" },
  { value: "Retail Performance", label: "Retail Performance" },
  { value: "POS", label: "POS" },
  { value: "Merchandise", label: "Merchandise" },
];

const STATUS_OPTIONS = [
  { value: "", label: "Status: All" },
  { value: "healthy", label: "Healthy" },
  { value: "stale", label: "Stale" },
];

const SORT_OPTIONS = [
  { value: "sync", label: "Sort: Recently synced" },
  { value: "name", label: "Sort: Name" },
  { value: "kpis", label: "Sort: KPI count" },
];

function DatasetCard({ dataset, highlight }) {
  const stale = dataset.status === "stale";
  const visibleTags = (dataset.attributeTags || []).slice(0, 4);
  const more = (dataset.attributeTags || []).length - visibleTags.length;
  const kpiCount = dataset.linkedKpiNames?.length ?? 0;
  const sourceIcon = SOURCE_ICON[dataset.sourceId];

  return (
    <div
      className={`relative flex h-full min-h-0 min-w-0 flex-col rounded-2xl border bg-surface-default p-5 transition-shadow hover:shadow-md ${
        highlight
          ? "border-primary ring-2 ring-primary/35 ring-offset-2 ring-offset-[var(--app-surface-page)]"
          : "border-border"
      }`}
    >
      {/* Top: source icon | status + menu (matches Configure internal source cards) */}
      <div className="flex items-start justify-between gap-3">
        <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-xl border border-border bg-surface-secondary p-2">
          {sourceIcon ? (
            <img src={sourceIcon} alt="" className="h-full w-full object-contain" />
          ) : (
            <Database size={22} className="text-primary" aria-hidden />
          )}
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          {stale ? (
            <span className="inline-flex items-center gap-1.5 rounded-full border border-[#fec84b] bg-[#fffaeb] px-2.5 py-1 text-xs font-medium text-[#b54708]">
              <AlertTriangle className="h-3.5 w-3.5 shrink-0" aria-hidden />
              Stale
            </span>
          ) : (
            <span className="inline-flex items-center gap-1.5 rounded-full border border-[#6ce9a6] bg-[#ecfdf3] px-2.5 py-1 text-xs font-medium text-[#027a48]">
              <span className="h-1.5 w-1.5 rounded-full bg-[#12b76a]" aria-hidden />
              Healthy
            </span>
          )}
          <button
            type="button"
            className="rounded p-1 text-text-muted transition-colors hover:bg-surface-secondary"
            aria-label="More options"
          >
            <MoreVertical size={16} />
          </button>
        </div>
      </div>

      <h3
        className="mt-3 break-words font-sans text-[15px] font-bold leading-snug text-text-primary sm:text-base"
        title={dataset.technicalName}
      >
        {dataset.technicalName}
      </h3>

      <p className="mb-3 mt-2 line-clamp-2 text-[13.5px] leading-5 text-text-secondary">{dataset.description}</p>

      {/* Two-column stats (reference: volume + last sync) */}
      <div className="mb-3 flex justify-between border-t border-border pt-3">
        <div className="min-w-0 pr-2">
          <p className="text-[10px] font-semibold uppercase tracking-wide text-text-muted">Rows</p>
          <p className="mt-0.5 truncate text-sm font-semibold text-text-primary">{dataset.rowCountLabel}</p>
        </div>
        <div className="shrink-0 text-right">
          <p className="text-[10px] font-semibold uppercase tracking-wide text-text-muted">Last sync</p>
          <p className="mt-0.5 text-sm font-semibold text-text-primary">{dataset.lastSyncLabel}</p>
        </div>
      </div>

      <div className="flex min-h-0 flex-1 flex-col">
        <p className="mb-2 text-sm font-semibold text-primary">
          {kpiCount} KPI{kpiCount === 1 ? "" : "s"} linked
        </p>

        {visibleTags.length > 0 ? (
          <div className="mb-2 flex flex-wrap gap-1.5">
            {visibleTags.map((t) => (
              <span
                key={t}
                className="rounded-md border border-border bg-surface-secondary px-1.5 py-0.5 font-mono text-[11px] text-text-secondary"
              >
                {t}
              </span>
            ))}
            {more > 0 ? (
              <span className="rounded-md border border-dashed border-border-light px-1.5 py-0.5 text-[11px] font-medium text-text-muted">
                +{more}
              </span>
            ) : null}
          </div>
        ) : null}
        {(dataset.businessTags || []).length > 0 ? (
          <div className="mb-1 flex flex-wrap gap-1.5">
            {(dataset.businessTags || []).map((t) => (
              <span
                key={t}
                className="inline-flex items-center gap-1 rounded-full bg-primary-light px-2 py-0.5 text-[11px] font-medium text-primary dark:bg-surface-tertiary dark:ring-1 dark:ring-primary/30"
              >
                <Database size={10} aria-hidden />
                {t}
              </span>
            ))}
          </div>
        ) : null}
      </div>

      <div className="mt-auto flex shrink-0 items-center justify-end gap-1 border-t border-border pt-2.5">
        <button
          type="button"
          className="inline-flex items-center gap-1.5 rounded-md border border-border bg-surface-secondary px-2.5 py-1 text-[12px] font-semibold text-text-tertiary transition hover:border-border-light hover:bg-surface-tertiary"
          aria-label="Edit dataset"
        >
          <Pencil size={13} />
          Edit
        </button>
        <button
          type="button"
          className="inline-flex items-center gap-1.5 rounded-md border border-red-500/35 bg-red-500/10 px-2.5 py-1 text-[12px] font-semibold text-red-700 transition hover:bg-red-500/[0.14] dark:text-red-400"
          aria-label="Remove dataset"
        >
          <Trash2 size={13} />
          Remove
        </button>
      </div>
    </div>
  );
}

export default function KpiDataGallery() {
  const [searchParams] = useSearchParams();
  const highlightId = (searchParams.get("ds") || "").trim();
  const [search, setSearch] = useState("");
  const [source, setSource] = useState("");
  const [businessArea, setBusinessArea] = useState("");
  const [status, setStatus] = useState("");
  const [sort, setSort] = useState("sync");

  const { sources: userSources, removeSource } = useKpiSources();

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    // User-added KPI sources appear first so newly-added rows are visible.
    let rows = [...userSources, ...KPI_DATA_CATALOG_DATASETS];
    if (source) rows = rows.filter((d) => d.sourceId === source);
    if (status) rows = rows.filter((d) => d.status === status);
    if (businessArea) {
      rows = rows.filter((d) =>
        (d.businessTags || []).some((t) => t.toLowerCase() === businessArea.toLowerCase())
      );
    }
    if (q) {
      rows = rows.filter((d) => {
        const blob = [
          d.technicalName,
          d.description,
          ...(d.attributeTags || []),
          ...(d.businessTags || []),
          ...(d.linkedKpiNames || []),
        ]
          .join(" ")
          .toLowerCase();
        return blob.includes(q);
      });
    }
    if (sort === "name") {
      rows.sort((a, b) => a.technicalName.localeCompare(b.technicalName));
    } else if (sort === "kpis") {
      rows.sort((a, b) => (b.linkedKpiNames?.length || 0) - (a.linkedKpiNames?.length || 0));
    }
    return rows;
  }, [search, source, businessArea, status, sort, userSources]);

  useEffect(() => {
    if (!highlightId) return;
    const d = getDatasetById(highlightId);
    if (d) setSearch(d.technicalName);
  }, [highlightId]);

  useEffect(() => {
    if (!highlightId) return;
    const t = window.setTimeout(() => {
      document.getElementById(`kpi-gallery-ds-${highlightId}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
    }, 120);
    return () => window.clearTimeout(t);
  }, [highlightId, filtered]);

  const grouped = useMemo(() => {
    const byId = new Map(filtered.map((d) => [d.id, d]));
    const sources = groupDatasetsBySource(filtered);
    return sources.map((g) => ({
      ...g,
      datasets: g.datasets.filter((d) => byId.has(d.id)),
    })).filter((g) => g.datasets.length > 0);
  }, [filtered]);

  const totalAttrs = filtered.reduce((s, d) => s + (d.attributeTags?.length || 0), 0);
  const totalKpiLinks = new Set(filtered.flatMap((d) => d.linkedKpiNames || [])).size;

  return (
    <div className="w-full min-w-0">
      <div className="mb-6 flex flex-col gap-3 lg:flex-row lg:items-center" data-tour-kpi-gal="filters">
        <div className="relative min-w-0 flex-1">
          <Search
            className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted"
            aria-hidden
          />
          <input
            type="search"
            placeholder="Search datasets, attributes, or KPI names…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full rounded-xl border border-border bg-surface-default py-2.5 pl-11 pr-4 text-sm text-text-primary placeholder:text-text-muted shadow-sm outline-none transition hover:border-border-light focus:border-primary focus:ring-4 focus:ring-primary/25"
          />
        </div>
        <div className="grid w-full grid-cols-2 gap-2 sm:grid-cols-4 lg:w-auto lg:shrink-0">
          <Select label={null} options={SOURCE_OPTIONS} value={source} onChange={(e) => setSource(e.target.value)} />
          <Select
            label={null}
            options={AREA_OPTIONS}
            value={businessArea}
            onChange={(e) => setBusinessArea(e.target.value)}
          />
          <Select label={null} options={STATUS_OPTIONS} value={status} onChange={(e) => setStatus(e.target.value)} />
          <Select label={null} options={SORT_OPTIONS} value={sort} onChange={(e) => setSort(e.target.value)} />
        </div>
      </div>

      <div className="mb-5 flex flex-wrap items-center gap-x-1.5 gap-y-1 text-[13px] text-text-muted">
        <span className="tabular-nums font-semibold text-text-primary">{filtered.length}</span>
        <span>datasets</span>
        <span className="text-border-light">·</span>
        <span className="tabular-nums font-semibold text-text-primary">{totalAttrs}</span>
        <span>attributes</span>
        <span className="text-border-light">·</span>
        <span className="tabular-nums font-semibold text-text-primary">{totalKpiLinks}</span>
        <span>distinct KPI links</span>
      </div>

      <div className="space-y-10" data-tour-kpi-gal="grid">
        {grouped.map((group, groupIdx) => {
          const attrCount = group.datasets.reduce((s, d) => s + (d.attributeTags?.length || 0), 0);
          const kpiCount = new Set(group.datasets.flatMap((d) => d.linkedKpiNames || [])).size;
          const sourceIcon = SOURCE_ICON[group.sourceId];
          return (
            <section key={group.sourceId} className={groupIdx > 0 ? "border-t border-border pt-10" : ""}>
              <div className="mb-5 flex flex-wrap items-center gap-3">
                <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-border bg-surface-default p-1.5 shadow-sm">
                  {sourceIcon ? (
                    <img src={sourceIcon} alt="" className="h-full w-full object-contain" />
                  ) : (
                    <span className="text-[11px] font-bold text-primary">
                      {group.sourceLabel.slice(0, 2).toUpperCase()}
                    </span>
                  )}
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <h3 className="text-[18px] font-semibold tracking-[-0.2px] text-text-primary">
                      {group.sourceLabel}
                    </h3>
                    <span className="rounded-full bg-primary-light px-2.5 py-0.5 text-[11px] font-semibold text-primary dark:bg-surface-tertiary dark:ring-1 dark:ring-primary/30">
                      {group.datasets.length} dataset{group.datasets.length === 1 ? "" : "s"}
                    </span>
                  </div>
                  <p className="mt-0.5 text-[12px] text-text-muted">
                    {attrCount} attribute{attrCount === 1 ? "" : "s"} · {kpiCount} KPI{kpiCount === 1 ? "" : "s"} linked
                  </p>
                </div>
              </div>
              <div className="grid items-stretch gap-4 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
                {group.datasets.map((d) => (
                  <div key={d.id} id={`kpi-gallery-ds-${d.id}`} className="flex h-full min-h-0 min-w-0">
                    <DatasetCard dataset={d} highlight={highlightId === d.id} />
                  </div>
                ))}
              </div>
            </section>
          );
        })}
      </div>

      {filtered.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-primary/35 bg-surface-secondary/80 py-14 text-center dark:bg-surface-secondary/40">
          <p className="text-sm font-semibold text-text-primary">No datasets match the current filters.</p>
          <p className="mt-1 text-[13px] text-text-muted">Try clearing a filter or widening your search.</p>
        </div>
      ) : null}

      <p className="mt-10 text-center text-sm text-text-muted">
        Need to wire a new feed?{" "}
        <Link to="/configure/internal-sources" className="font-semibold text-primary hover:underline">
          Open Configure
        </Link>{" "}
        or{" "}
        <Link to="/kpis/registry" className="font-semibold text-primary hover:underline">
          manage KPIs in the registry
        </Link>
        .
      </p>
    </div>
  );
}
