import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { AlertTriangle, Database, Search } from "lucide-react";
import { Modal, Select } from "./ui";
import { useDataCatalog } from "../hooks/useDataCatalog";

const SORT_OPTIONS = [
  { value: "kpis", label: "Sort: KPI count" },
  { value: "rows", label: "Sort: Row count" },
  { value: "name", label: "Sort: Name" },
];

const ROW_COUNT_FORMATTER = new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 });

function formatRowCount(n) {
  if (n == null) return "Unknown";
  return `${ROW_COUNT_FORMATTER.format(n)} rows`;
}

function formatHarvestedAt(value) {
  if (!value) return null;
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString();
}

function schemaLabel(schema) {
  if (!schema) return "Unspecified schema";
  return schema.charAt(0).toUpperCase() + schema.slice(1);
}

/** Datasets grouped by the semantic layer's own schema (silver/gold) -- the real grouping
 * key a table carries, unlike a source-system label the backend does not track per table. */
function groupDatasetsBySchema(datasets) {
  const map = new Map();
  for (const d of datasets) {
    const key = d.schema || "unspecified";
    if (!map.has(key)) map.set(key, { schema: key, datasets: [] });
    map.get(key).datasets.push(d);
  }
  return [...map.values()];
}

/** All of a dataset's columns, searchable and scrollable -- what the "+N" overflow on a card
 * opens rather than leaving the rest of the attribute list with no way to see it. */
function AttributeListModal({ dataset, onClose }) {
  const [q, setQ] = useState("");
  const all = dataset.attributeTags || [];
  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    if (!needle) return all;
    return all.filter((t) => t.toLowerCase().includes(needle));
  }, [all, q]);

  return (
    <Modal title="All attributes" onClose={onClose}>
      <p className="-mt-2 mb-3 truncate font-mono text-xs text-text-muted" title={dataset.technicalName}>
        {dataset.technicalName}
      </p>
      <div className="relative mb-3">
        <Search
          className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted"
          aria-hidden
        />
        <input
          type="search"
          autoFocus
          placeholder="Search attributes…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          className="w-full rounded-lg border border-border bg-surface-default py-2 pl-9 pr-3 text-sm text-text-primary placeholder:text-text-muted outline-none focus:border-primary focus:ring-4 focus:ring-primary/25"
        />
      </div>
      <p className="mb-2 text-xs text-text-muted">
        {filtered.length} of {all.length} attribute{all.length === 1 ? "" : "s"}
      </p>
      <div className="max-h-80 overflow-y-auto rounded-lg border border-border">
        {filtered.length === 0 ? (
          <p className="px-3 py-6 text-center text-sm text-text-muted">No attributes match "{q}".</p>
        ) : (
          <ul className="divide-y divide-border">
            {filtered.map((t) => (
              <li key={t} className="px-3 py-2 font-mono text-[12.5px] text-text-secondary">
                {t}
              </li>
            ))}
          </ul>
        )}
      </div>
    </Modal>
  );
}

function DatasetCard({ dataset, highlight }) {
  const [showAllAttrs, setShowAllAttrs] = useState(false);
  const visibleTags = (dataset.attributeTags || []).slice(0, 4);
  const more = (dataset.attributeTags || []).length - visibleTags.length;
  const kpiCount = dataset.linkedKpiNames?.length ?? 0;

  return (
    <div
      className={`relative flex h-full min-h-0 min-w-0 flex-col rounded-2xl border bg-surface-default p-5 transition-shadow hover:shadow-md ${
        highlight
          ? "border-primary ring-2 ring-primary/35 ring-offset-2 ring-offset-[var(--app-surface-page)]"
          : "border-border"
      }`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-xl border border-border bg-surface-secondary p-2">
          <Database size={22} className="text-primary" aria-hidden />
        </div>
        <span className="inline-flex shrink-0 items-center gap-1.5 rounded-full border border-border bg-surface-secondary px-2.5 py-1 text-xs font-medium text-text-secondary">
          {schemaLabel(dataset.schema)}
        </span>
      </div>

      <h3
        className="mt-3 break-words font-sans text-[15px] font-bold leading-snug text-text-primary sm:text-base"
        title={dataset.technicalName}
      >
        {dataset.technicalName}
      </h3>

      <p className="mb-3 mt-2 line-clamp-2 text-[13.5px] leading-5 text-text-secondary">
        {dataset.description || "No description recorded for this table."}
      </p>

      <div className="mb-3 flex justify-between border-t border-border pt-3">
        <div className="min-w-0 pr-2">
          <p className="text-[10px] font-semibold uppercase tracking-wide text-text-muted">Rows</p>
          <p className="mt-0.5 truncate text-sm font-semibold text-text-primary">
            {formatRowCount(dataset.rowCount)}
          </p>
        </div>
        <div className="shrink-0 text-right">
          <p className="text-[10px] font-semibold uppercase tracking-wide text-text-muted">Attributes</p>
          <p className="mt-0.5 text-sm font-semibold text-text-primary">{dataset.attributeTags?.length ?? 0}</p>
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
              <button
                type="button"
                onClick={() => setShowAllAttrs(true)}
                className="rounded-md border border-dashed border-border-light px-1.5 py-0.5 text-[11px] font-medium text-text-muted transition hover:border-primary hover:text-primary"
              >
                +{more}
              </button>
            ) : null}
          </div>
        ) : null}
        {showAllAttrs ? <AttributeListModal dataset={dataset} onClose={() => setShowAllAttrs(false)} /> : null}
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
    </div>
  );
}

export default function KpiDataGallery() {
  const [searchParams] = useSearchParams();
  const highlightId = (searchParams.get("ds") || "").trim();
  const [search, setSearch] = useState("");
  const [schema, setSchema] = useState("");
  const [businessArea, setBusinessArea] = useState("");
  const [sort, setSort] = useState("kpis");

  const { status, datasets, snapshot, error, reload } = useDataCatalog();

  const schemaOptions = useMemo(() => {
    const schemas = [...new Set(datasets.map((d) => d.schema).filter(Boolean))].sort();
    return [{ value: "", label: "Schema: All" }, ...schemas.map((s) => ({ value: s, label: schemaLabel(s) }))];
  }, [datasets]);

  const areaOptions = useMemo(() => {
    const areas = [...new Set(datasets.flatMap((d) => d.businessTags || []))].sort();
    return [{ value: "", label: "Business area: All" }, ...areas.map((a) => ({ value: a, label: a }))];
  }, [datasets]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    let rows = [...datasets];
    if (schema) rows = rows.filter((d) => d.schema === schema);
    if (businessArea) {
      rows = rows.filter((d) => (d.businessTags || []).some((t) => t.toLowerCase() === businessArea.toLowerCase()));
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
    } else if (sort === "rows") {
      rows.sort((a, b) => (b.rowCount ?? -1) - (a.rowCount ?? -1));
    } else if (sort === "kpis") {
      rows.sort((a, b) => (b.linkedKpiNames?.length || 0) - (a.linkedKpiNames?.length || 0));
    }
    return rows;
  }, [datasets, search, schema, businessArea, sort]);

  useEffect(() => {
    if (!highlightId) return;
    if (datasets.some((d) => d.id === highlightId)) setSearch(highlightId);
  }, [highlightId, datasets]);

  useEffect(() => {
    if (!highlightId) return;
    const t = window.setTimeout(() => {
      document.getElementById(`kpi-gallery-ds-${highlightId}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
    }, 120);
    return () => window.clearTimeout(t);
  }, [highlightId, filtered]);

  const grouped = useMemo(() => {
    const byId = new Map(filtered.map((d) => [d.id, d]));
    const schemas = groupDatasetsBySchema(filtered);
    return schemas
      .map((g) => ({ ...g, datasets: g.datasets.filter((d) => byId.has(d.id)) }))
      .filter((g) => g.datasets.length > 0);
  }, [filtered]);

  const totalAttrs = filtered.reduce((s, d) => s + (d.attributeTags?.length || 0), 0);
  const totalKpiLinks = new Set(filtered.flatMap((d) => d.linkedKpiNames || [])).size;
  const harvestedAt = formatHarvestedAt(snapshot?.harvested_at);

  if (status === "loading") {
    return <p className="text-sm text-text-secondary">Loading data catalog…</p>;
  }

  return (
    <div className="w-full min-w-0">
      {status === "error" ? (
        <div className="mb-4 flex items-center gap-2 rounded-xl border border-status-warning/30 bg-status-warning/10 px-4 py-2.5 text-sm text-text-secondary">
          <AlertTriangle size={15} className="shrink-0 text-status-warning" />
          <span className="flex-1">{error || "Couldn't load the data catalog, try again shortly."}</span>
          <button
            type="button"
            onClick={reload}
            className="shrink-0 rounded-lg border border-status-warning/40 px-2.5 py-1 text-xs font-semibold text-text-primary transition-colors hover:bg-status-warning/15"
          >
            Retry
          </button>
        </div>
      ) : null}

      {harvestedAt ? (
        <p className="mb-4 text-xs text-text-muted">
          Synced as of {harvestedAt}
          {snapshot?.version ? ` · semantic snapshot ${snapshot.version}` : ""}.
        </p>
      ) : null}

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
        <div className="grid w-full grid-cols-1 gap-2 sm:grid-cols-3 lg:w-auto lg:shrink-0">
          <Select label={null} options={schemaOptions} value={schema} onChange={(e) => setSchema(e.target.value)} />
          <Select
            label={null}
            options={areaOptions}
            value={businessArea}
            onChange={(e) => setBusinessArea(e.target.value)}
          />
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
          return (
            <section key={group.schema} className={groupIdx > 0 ? "border-t border-border pt-10" : ""}>
              <div className="mb-5 flex flex-wrap items-center gap-3">
                <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-border bg-surface-default p-1.5 shadow-sm">
                  <Database size={18} className="text-primary" aria-hidden />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <h3 className="text-[18px] font-semibold tracking-[-0.2px] text-text-primary">
                      {schemaLabel(group.schema)}
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
          <p className="text-sm font-semibold text-text-primary">
            {status === "error" ? "No datasets to show." : "No datasets match the current filters."}
          </p>
          <p className="mt-1 text-[13px] text-text-muted">
            {status === "error"
              ? "The catalog couldn't be loaded, see the message above."
              : "Try clearing a filter or widening your search."}
          </p>
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
