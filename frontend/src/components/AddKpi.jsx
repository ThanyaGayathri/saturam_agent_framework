import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useBreadcrumbTrail } from "../hooks/useBreadcrumbTrail";
import {
  ArrowLeft,
  BarChart2,
  BookOpen,
  Briefcase,
  CheckCircle,
  CircleHelp,
  FileText,
  Layers,
  Ruler,
  Sparkles,
} from "lucide-react";
import { Input, Select } from "./ui";
import Breadcrumb from "./shared/Breadcrumb";
import { useKpiRegistry } from "../hooks/useKpiRegistry";
import { useProcesses } from "../hooks/useProcesses";
import { useDemoProfile } from "../context/DemoProfileContext";
import { isMiddlebyProfile } from "../domain/profile-capabilities";
import { kpiRegistryBasePath } from "../domain/middleby-analytics-builder";
import { useBusinessAreas } from "../hooks/useBusinessAreas";
import { suggestIndustryBenchmark } from "../domain/kpi-benchmark-suggestions";
import { defaultUnitForLevel, isUnitValidForLevel, unitOptionsForLevel } from "../domain/kpi-units";
import { ensureSection, normalizeProcessIdList } from "../storage/kpi-mutations";
import KpiProcessMappingField from "./KpiProcessMappingField";
import KpiDatasetMappingField from "./KpiDatasetMappingField";

const LEVEL_OPTIONS = ["Strategic", "Operational", "Tactical"].map((v) => ({ value: v, label: v }));

const ENTERPRISE_CATEGORY_INDEX = 0;

function SectionLabel({ label, required, hint }) {
  return (
    <div className="flex items-start gap-1.5 pt-1">
      <span className="text-sm font-semibold text-text-primary">{label}</span>
      {required && <span className="text-[#d92d20]">*</span>}
      {hint ? (
        <span title={hint}>
          <CircleHelp size={14} className="shrink-0 text-text-muted" />
        </span>
      ) : null}
    </div>
  );
}

export default function AddKpi() {
  const navigate = useNavigate();
  const { profileId } = useDemoProfile();
  const middlebyKpis = isMiddlebyProfile(profileId);
  const kpiBase = kpiRegistryBasePath(profileId);
  const trail = useBreadcrumbTrail("Add KPI", [
    { label: middlebyKpis ? "Use Cases" : "Playground", to: middlebyKpis ? "/analytics" : "/playground" },
    { label: "KPIs", to: `${kpiBase}/registry` },
  ]);
  const { data, setData, hydrated } = useKpiRegistry();
  const { processes, hydrated: processesReady } = useProcesses();
  const { areas: BUSINESS_AREAS } = useBusinessAreas();

  const categories = data.categories || [];

  const [businessAreaId, setBusinessAreaId] = useState(
    () => BUSINESS_AREAS[0]?.id ?? (middlebyKpis ? "middleby-rfq" : "inventory")
  );
  const [subjectArea, setSubjectArea] = useState("");

  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const [active, setActive] = useState(true);
  const [level, setLevel] = useState("Operational");
  const [selectedProcessIds, setSelectedProcessIds] = useState([]);
  const [unit, setUnit] = useState(() => defaultUnitForLevel("Operational"));
  const [industryBenchmark, setIndustryBenchmark] = useState("");
  const [datasetIds, setDatasetIds] = useState([]);

  const businessOptions = useMemo(
    () => BUSINESS_AREAS.map((a) => ({ value: a.id, label: a.label })),
    [BUSINESS_AREAS]
  );
  const subjectOptions = useMemo(() => {
    const area = BUSINESS_AREAS.find((a) => a.id === businessAreaId);
    const subs = area?.subjectAreas || [];
    return subs.map((s) => ({ value: s, label: s }));
  }, [BUSINESS_AREAS, businessAreaId]);
  const unitOptions = useMemo(() => unitOptionsForLevel(level), [level]);

  const aiBenchmarkSuggestion = useMemo(
    () =>
      suggestIndustryBenchmark({
        level,
        businessAreaId,
        subjectArea: subjectArea.trim(),
      }),
    [level, businessAreaId, subjectArea]
  );

  useEffect(() => {
    if (!subjectOptions.length) {
      setSubjectArea("");
      return;
    }
    if (!subjectOptions.some((o) => o.value === subjectArea)) {
      setSubjectArea(subjectOptions[0].value);
    }
  }, [businessAreaId, subjectArea, subjectOptions]);

  const canSave =
    hydrated &&
    processesReady &&
    name.trim().length > 0 &&
    categories.length > 0 &&
    businessAreaId &&
    subjectArea.trim().length > 0;

  const onClose = () => navigate(`${kpiBase}/registry`);

  const onReset = () => {
    setName("");
    setDesc("");
    setActive(true);
    setLevel("Operational");
    setUnit(defaultUnitForLevel("Operational"));
    setBusinessAreaId(
      middlebyKpis ? MIDDLEBY_ANALYTICS_AREAS[0]?.id ?? "middleby-rfq" : BUSINESS_AREAS[0]?.id ?? "inventory"
    );
    setSubjectArea("");
    setSelectedProcessIds([]);
    setIndustryBenchmark("");
    setDatasetIds([]);
  };

  const onSave = () => {
    if (!canSave) return;
    setData((prev) => {
      const next = structuredClone(prev);
      const { section } = ensureSection(
        next.categories,
        ENTERPRISE_CATEGORY_INDEX,
        subjectArea.trim(),
        businessAreaId
      );
      section.kpis = section.kpis || [];
      section.kpis.push({
        name: name.trim(),
        desc: desc.trim(),
        active,
        businessAreaId,
        subjectArea: subjectArea.trim(),
        level: level.trim() || "Operational",
        processMapping: normalizeProcessIdList(selectedProcessIds),
        unit: unit.trim(),
        industryBenchmark: industryBenchmark.trim(),
        ...(datasetIds.length ? { datasetIds: [...datasetIds] } : {}),
      });
      return next;
    });
    navigate(`${kpiBase}/registry`);
  };

  return (
    <div className="mx-auto min-h-[calc(100vh-5rem)] w-full max-w-[1440px] px-8 py-6">
      <Breadcrumb items={trail} />

      <button
        type="button"
        onClick={onClose}
        className="mt-2 flex items-center gap-1.5 text-sm font-semibold text-primary hover:text-[#7f56d9]"
      >
        <ArrowLeft size={16} />
        Back to KPIs
      </button>

      <div className="mt-5 max-w-4xl space-y-5">
        <div className="rounded-2xl border border-border bg-surface">
          {/* Card header */}
          <div className="px-8 py-6">
            <h2 className="text-xl font-bold text-text-primary">Add KPI</h2>
            <p className="mt-1 text-sm text-text-secondary">
              Create a new KPI definition in the registry.
            </p>
          </div>

          <div className="border-t border-border" />

          {/* KPI Details section */}
          <div className="grid gap-8 px-8 py-6 md:grid-cols-[200px_1fr]">
            <SectionLabel label="KPI Details" required />
            <div className="space-y-4">
              <Input
                label="KPI name"
                required
                placeholder="e.g. Gross Margin Return on Inventory (GMROI)"
                icon={<BookOpen size={18} />}
                value={name}
                onChange={(e) => setName(e.target.value)}
              />

              <div>
                <label className="mb-1.5 block text-sm font-medium text-text-primary">
                  Description
                </label>
                <div className="relative">
                  <span className="absolute left-3 top-3 text-text-muted">
                    <FileText size={18} />
                  </span>
                  <textarea
                    rows={4}
                    placeholder="What does this KPI measure?"
                    value={desc}
                    onChange={(e) => setDesc(e.target.value)}
                    className="w-full rounded-lg border border-border-light bg-surface py-2.5 pl-10 pr-4 text-sm text-text-primary placeholder:text-text-muted outline-none focus:border-primary focus:ring-1 focus:ring-[#6941c6]"
                  />
                </div>
              </div>

              <label className="flex items-center gap-2.5 text-sm text-text-primary">
                <input
                  type="checkbox"
                  checked={active}
                  onChange={(e) => setActive(e.target.checked)}
                  className="h-4 w-4 rounded border-border-light text-primary focus:ring-[#6941c6]"
                />
                Enabled by default
              </label>
            </div>
          </div>

          <div className="border-t border-border" />

          {/* Classification section */}
          <div className="grid gap-8 px-8 py-6 md:grid-cols-[200px_1fr]">
            <SectionLabel label="Classification" required />
            <div className="space-y-4">
              <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                <Select
                  label="Business area"
                  required
                  options={businessOptions}
                  icon={<Briefcase size={18} />}
                  value={businessAreaId}
                  onChange={(e) => {
                    setBusinessAreaId(e.target.value);
                    setSubjectArea("");
                  }}
                />
                <Select
                  label="Subject area"
                  required
                  options={
                    subjectOptions.length
                      ? subjectOptions
                      : [{ value: "", label: "Select business area first" }]
                  }
                  icon={<Layers size={18} />}
                  value={subjectArea}
                  onChange={(e) => setSubjectArea(e.target.value)}
                  disabled={!businessAreaId}
                />
              </div>

              <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                <Select
                  label="Level"
                  options={LEVEL_OPTIONS}
                  value={level}
                  onChange={(e) => {
                    const next = e.target.value;
                    setLevel(next);
                    setUnit((u) => (isUnitValidForLevel(u, next) ? u : defaultUnitForLevel(next)));
                  }}
                  icon={<BarChart2 size={18} />}
                />
                <Select
                  label="Unit"
                  options={unitOptions}
                  icon={<Ruler size={18} />}
                  value={unit}
                  onChange={(e) => setUnit(e.target.value)}
                />
              </div>
            </div>
          </div>

          <div className="border-t border-border" />

          {/* Mappings section */}
          <div className="grid gap-8 px-8 py-6 md:grid-cols-[200px_1fr]">
            <SectionLabel label="Mappings" hint="Link this KPI to processes and source datasets." />
            <div className="space-y-5">
              <KpiProcessMappingField
                processes={processes}
                value={selectedProcessIds}
                onChange={setSelectedProcessIds}
              />
              <KpiDatasetMappingField value={datasetIds} onChange={setDatasetIds} />
            </div>
          </div>

          <div className="border-t border-border" />

          {/* Target section */}
          <div className="grid gap-8 px-8 py-6 md:grid-cols-[200px_1fr]">
            <SectionLabel label="Target" />
            <div className="space-y-3">
              <p
                id="add-kpi-ai-benchmark-hint"
                className="flex items-start gap-2 rounded-lg bg-primary-light dark:bg-primary/20 p-3 text-sm font-medium leading-relaxed text-text-tertiary"
              >
                <Sparkles className="mt-0.5 h-4 w-4 shrink-0 text-primary" aria-hidden />
                <span>{aiBenchmarkSuggestion}</span>
              </p>
              <Input
                label="Target"
                placeholder="Enter the range or target you want to track"
                aria-describedby="add-kpi-ai-benchmark-hint"
                value={industryBenchmark}
                onChange={(e) => setIndustryBenchmark(e.target.value)}
              />
            </div>
          </div>
        </div>

        {/* Footer actions */}
        <div className="flex items-center justify-between py-2">
          <button
            type="button"
            onClick={onReset}
            className="text-sm font-medium text-text-secondary hover:text-text-primary"
          >
            Reset to default
          </button>
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={onClose}
              className="rounded-lg border border-border-light px-4 py-2 text-sm font-semibold text-text-primary transition hover:bg-surface-secondary"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={onSave}
              disabled={!canSave}
              className={`flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-semibold transition-colors ${
                canSave
                  ? "bg-primary text-white hover:bg-primary-hover"
                  : "cursor-default bg-surface-tertiary text-text-muted"
              }`}
            >
              <CheckCircle size={15} />
              Save KPI
            </button>
          </div>
        </div>

        {!categories.length ? (
          <div className="rounded-xl border border-dashed border-border bg-surface-secondary px-5 py-4">
            <p className="text-sm text-text-secondary">
              No KPI categories found. Seed data should populate this automatically.
            </p>
          </div>
        ) : null}
      </div>
    </div>
  );
}
