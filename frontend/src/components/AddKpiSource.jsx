import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  ArrowLeft,
  BarChart2,
  Briefcase,
  CircleHelp,
  Clock,
  Database,
  FileText,
  Layers,
  X,
} from "lucide-react";
import Breadcrumb from "./shared/Breadcrumb";
import { useKpiRegistry } from "../hooks/useKpiRegistry";
import { useKpiSources } from "../hooks/useKpiSources";
import { newKpiSourceId } from "../storage/kpi-sources";
import { useBusinessAreas } from "../hooks/useBusinessAreas";

const SOURCE_TYPE_OPTIONS = [
  { value: "sap", label: "SAP" },
  { value: "snowflake", label: "Snowflake" },
  { value: "postgres", label: "PostgreSQL" },
  { value: "salesforce", label: "Salesforce" },
];

const SOURCE_TYPE_LABEL = Object.fromEntries(SOURCE_TYPE_OPTIONS.map((o) => [o.value, o.label]));

const REFRESH_CADENCE_OPTIONS = [
  { value: "real-time", label: "Real-time" },
  { value: "hourly", label: "Hourly" },
  { value: "daily", label: "Daily" },
  { value: "weekly", label: "Weekly" },
];

/** Cadence label pushed onto the gallery card's "last sync" line for user-added sources. */
const CADENCE_TO_SYNC_LABEL = {
  "real-time": "streaming",
  hourly: "1h ago",
  daily: "12h ago",
  weekly: "3d ago",
};

function SectionLabel({ label, required, hint }) {
  return (
    <div className="flex items-start gap-1.5 pt-1">
      <span className="text-sm font-semibold text-text-primary">{label}</span>
      {required ? <span className="text-[#d92d20]">*</span> : null}
      {hint ? (
        <span title={hint}>
          <CircleHelp size={14} className="shrink-0 text-text-muted" />
        </span>
      ) : null}
    </div>
  );
}

function flattenActiveKpiNames(categories) {
  const out = [];
  for (const cat of categories || []) {
    for (const sec of cat.sections || []) {
      for (const k of sec.kpis || []) {
        if (k.active !== false && k.name) out.push(k.name);
      }
    }
  }
  return Array.from(new Set(out)).sort((a, b) => a.localeCompare(b));
}

function ChipList({ items, onRemove, empty }) {
  if (!items.length) return <p className="text-xs text-text-muted">{empty}</p>;
  return (
    <div className="flex flex-wrap gap-1.5">
      {items.map((it) => (
        <span
          key={it}
          className="inline-flex items-center gap-1 rounded-md border border-primary/25 bg-primary-light px-2 py-0.5 text-xs font-medium text-primary dark:border-primary/35 dark:bg-surface-tertiary"
        >
          {it}
          <button
            type="button"
            onClick={() => onRemove(it)}
            className="text-primary/70 hover:text-primary"
            aria-label={`Remove ${it}`}
          >
            <X size={11} strokeWidth={2.5} />
          </button>
        </span>
      ))}
    </div>
  );
}

export default function AddKpiSource() {
  const navigate = useNavigate();
  const { data: registry, hydrated: registryReady } = useKpiRegistry();
  const { addSource, hydrated: sourcesReady } = useKpiSources();
  const { areas: BUSINESS_AREAS } = useBusinessAreas();

  const [sourceType, setSourceType] = useState("snowflake");
  const [technicalName, setTechnicalName] = useState("");
  const [description, setDescription] = useState("");
  const [rowCountLabel, setRowCountLabel] = useState("");
  const [refreshCadence, setRefreshCadence] = useState("daily");
  const [businessAreas, setBusinessAreas] = useState([]);
  const [attributesText, setAttributesText] = useState("");
  const [linkedKpiNames, setLinkedKpiNames] = useState([]);
  const [kpiPicker, setKpiPicker] = useState("");
  const [areaPicker, setAreaPicker] = useState("");

  const kpiOptions = useMemo(
    () => flattenActiveKpiNames(registry?.categories),
    [registry?.categories]
  );
  const areaOptions = useMemo(() => BUSINESS_AREAS.map((a) => a.label), [BUSINESS_AREAS]);

  const nameValid = technicalName.trim().length > 0;
  const canSave = nameValid && businessAreas.length > 0;

  const addKpi = (name) => {
    const n = String(name || "").trim();
    if (!n || linkedKpiNames.includes(n)) return;
    setLinkedKpiNames((prev) => [...prev, n]);
    setKpiPicker("");
  };

  const addArea = (label) => {
    const n = String(label || "").trim();
    if (!n || businessAreas.includes(n)) return;
    setBusinessAreas((prev) => [...prev, n]);
    setAreaPicker("");
  };

  const parseAttributes = (raw) =>
    raw
      .split(/[\n,]/g)
      .map((s) => s.trim())
      .filter(Boolean);

  const onSave = () => {
    if (!canSave) return;
    const attributeTags = parseAttributes(attributesText);
    const record = {
      id: newKpiSourceId(),
      sourceId: sourceType,
      sourceLabel: SOURCE_TYPE_LABEL[sourceType] || sourceType,
      technicalName: technicalName.trim(),
      description: description.trim(),
      rowCountLabel: rowCountLabel.trim() || "New source",
      lastSyncLabel: CADENCE_TO_SYNC_LABEL[refreshCadence] || "New source",
      refreshCadence,
      status: "healthy",
      attributeTags,
      businessTags: businessAreas,
      linkedKpiNames,
      isUserCreated: true,
      createdAt: new Date().toISOString(),
    };
    addSource(record);
    navigate(`/kpis/gallery?ds=${encodeURIComponent(record.id)}`);
  };

  if (!registryReady || !sourcesReady) {
    return (
      <div className="mx-auto min-h-[calc(100vh-5rem)] w-full max-w-[1440px] px-8 py-6">
        <p className="text-sm text-text-secondary">Loading…</p>
      </div>
    );
  }

  return (
    <div className="mx-auto min-h-[calc(100vh-5rem)] w-full max-w-[1440px] px-8 py-6">
      <Breadcrumb
        items={[
          { label: "KPIs", to: "/kpis/registry" },
          { label: "KPI gallery", to: "/kpis/gallery" },
          { label: "Add KPI source" },
        ]}
      />
      <div className="mt-4 flex items-center gap-3">
        <button
          type="button"
          onClick={() => navigate("/kpis/gallery")}
          className="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-border text-text-secondary transition hover:border-primary/40 hover:text-primary"
          aria-label="Back"
        >
          <ArrowLeft size={16} />
        </button>
        <div className="min-w-0">
          <h1 className="text-2xl font-semibold tracking-tight text-text-primary">
            Add KPI source
          </h1>
          <p className="mt-1 text-sm text-text-secondary">
            Register a dataset from a connected data source. Add the business context (attributes, business areas, KPIs it powers) so the gallery shows how it feeds the KPI registry.
          </p>
        </div>
      </div>

      <div className="mt-6 grid max-w-5xl gap-5 lg:grid-cols-2">
        <div className="space-y-5">
          <div>
            <SectionLabel
              label="Underlying data source"
              required
              hint="The connected system this dataset lives in. Manage connections in Configure."
            />
            <select
              value={sourceType}
              onChange={(e) => setSourceType(e.target.value)}
              className="mt-1.5 w-full rounded-lg border border-border-light py-2.5 pl-3 pr-8 text-sm text-text-primary focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary/40"
            >
              {SOURCE_TYPE_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </div>

          <div>
            <SectionLabel
              label="Dataset technical name"
              required
              hint="Fully-qualified table or view name (e.g. snowflake.marketing.email_events)."
            />
            <div className="relative mt-1.5">
              <Database size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted" />
              <input
                type="text"
                value={technicalName}
                onChange={(e) => setTechnicalName(e.target.value)}
                placeholder="schema.table_name"
                className="w-full rounded-lg border border-border-light py-2.5 pl-9 pr-3 text-sm text-text-primary placeholder:text-text-muted focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary/40"
              />
            </div>
          </div>

          <div>
            <SectionLabel label="Description" hint="One line describing what this dataset holds." />
            <div className="relative mt-1.5">
              <FileText size={15} className="absolute left-3 top-3 text-text-muted" />
              <textarea
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="SKU-level inventory snapshots, updated hourly."
                rows={3}
                className="w-full rounded-lg border border-border-light py-2.5 pl-9 pr-3 text-sm text-text-primary placeholder:text-text-muted focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary/40"
              />
            </div>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <SectionLabel label="Refresh cadence" />
              <select
                value={refreshCadence}
                onChange={(e) => setRefreshCadence(e.target.value)}
                className="mt-1.5 w-full rounded-lg border border-border-light py-2.5 pl-3 pr-8 text-sm text-text-primary focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary/40"
              >
                {REFRESH_CADENCE_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <SectionLabel label="Row count estimate" hint="Shown on the gallery card for context. Optional." />
              <div className="relative mt-1.5">
                <Clock size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted" />
                <input
                  type="text"
                  value={rowCountLabel}
                  onChange={(e) => setRowCountLabel(e.target.value)}
                  placeholder="e.g. 1.2M rows"
                  className="w-full rounded-lg border border-border-light py-2.5 pl-9 pr-3 text-sm text-text-primary placeholder:text-text-muted focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary/40"
                />
              </div>
            </div>
          </div>
        </div>

        <div className="space-y-5">
          <div>
            <SectionLabel
              label="Business areas"
              required
              hint="Which parts of the business this dataset serves."
            />
            <div className="mt-1.5 flex gap-2">
              <select
                value={areaPicker}
                onChange={(e) => setAreaPicker(e.target.value)}
                className="min-w-0 flex-1 rounded-lg border border-border-light py-2.5 pl-3 pr-8 text-sm text-text-primary focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary/40"
              >
                <option value="">Choose a business area</option>
                {areaOptions
                  .filter((a) => !businessAreas.includes(a))
                  .map((a) => (
                    <option key={a} value={a}>
                      {a}
                    </option>
                  ))}
              </select>
              <button
                type="button"
                onClick={() => addArea(areaPicker)}
                disabled={!areaPicker}
                className="shrink-0 rounded-lg bg-primary px-3 py-2 text-xs font-semibold text-white transition hover:bg-primary-hover disabled:cursor-not-allowed disabled:bg-primary/40"
              >
                Add
              </button>
            </div>
            <div className="mt-2">
              <ChipList
                items={businessAreas}
                onRemove={(a) => setBusinessAreas((prev) => prev.filter((x) => x !== a))}
                empty="No business areas selected."
              />
            </div>
          </div>

          <div>
            <SectionLabel
              label="Attributes"
              hint="The columns exposed by this dataset. One per line or comma-separated."
            />
            <div className="relative mt-1.5">
              <Layers size={15} className="absolute left-3 top-3 text-text-muted" />
              <textarea
                value={attributesText}
                onChange={(e) => setAttributesText(e.target.value)}
                placeholder="sku_id, quantity_on_hand, dc_id"
                rows={4}
                className="w-full rounded-lg border border-border-light py-2.5 pl-9 pr-3 text-sm text-text-primary placeholder:text-text-muted focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary/40"
              />
            </div>
          </div>

          <div>
            <SectionLabel
              label="Linked KPIs"
              hint="Which registry KPIs this dataset powers. Pick one or more."
            />
            <div className="mt-1.5 flex gap-2">
              <select
                value={kpiPicker}
                onChange={(e) => setKpiPicker(e.target.value)}
                className="min-w-0 flex-1 rounded-lg border border-border-light py-2.5 pl-3 pr-8 text-sm text-text-primary focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary/40"
              >
                <option value="">Choose a KPI</option>
                {kpiOptions
                  .filter((k) => !linkedKpiNames.includes(k))
                  .map((k) => (
                    <option key={k} value={k}>
                      {k}
                    </option>
                  ))}
              </select>
              <button
                type="button"
                onClick={() => addKpi(kpiPicker)}
                disabled={!kpiPicker}
                className="shrink-0 rounded-lg bg-primary px-3 py-2 text-xs font-semibold text-white transition hover:bg-primary-hover disabled:cursor-not-allowed disabled:bg-primary/40"
              >
                Add
              </button>
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              {linkedKpiNames.length ? (
                <BarChart2 size={12} className="text-text-muted" aria-hidden />
              ) : null}
              <ChipList
                items={linkedKpiNames}
                onRemove={(n) => setLinkedKpiNames((prev) => prev.filter((x) => x !== n))}
                empty="No KPIs linked yet."
              />
            </div>
          </div>

          <div>
            <SectionLabel label="Owner" hint="Auto-filled from the current profile." />
            <div className="relative mt-1.5">
              <Briefcase size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted" />
              <input
                type="text"
                value="Current user"
                readOnly
                className="w-full cursor-not-allowed rounded-lg border border-border-light bg-surface-secondary py-2.5 pl-9 pr-3 text-sm text-text-muted"
              />
            </div>
          </div>
        </div>
      </div>

      <div className="mt-8 flex max-w-5xl items-center justify-end gap-2 border-t border-border pt-5">
        <button
          type="button"
          onClick={() => navigate("/kpis/gallery")}
          className="rounded-lg border border-border-light bg-surface-default px-4 py-2.5 text-sm font-semibold text-text-tertiary transition hover:bg-surface-secondary"
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={onSave}
          disabled={!canSave}
          className="rounded-lg bg-primary px-4 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-primary-hover disabled:cursor-not-allowed disabled:bg-primary/40"
        >
          Save KPI source
        </button>
      </div>
    </div>
  );
}
