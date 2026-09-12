import { useEffect, useMemo, useState } from "react";
import {
  BarChart2,
  BookOpen,
  Briefcase,
  FileText,
  Layers,
  Ruler,
  Sparkles,
} from "lucide-react";
import { Input, Select, SlidePanel } from "./ui";
import { useKpiRegistry } from "../hooks/useKpiRegistry";
import { useProcesses } from "../hooks/useProcesses";
import { inferBusinessAreaIdForSubjectArea } from "../domain/business-areas";
import { inferMiddlebyBusinessAreaIdForSubject } from "../domain/middleby-analytics-areas";
import { useDemoProfile } from "../context/DemoProfileContext";
import { useBusinessAreas } from "../hooks/useBusinessAreas";
import { isMiddlebyProfile } from "../domain/profile-capabilities";
import { suggestIndustryBenchmark } from "../domain/kpi-benchmark-suggestions";
import {
  defaultUnitForLevel,
  isUnitValidForLevel,
  unitOptionsForLevel,
} from "../domain/kpi-units";
import {
  ensureSection,
  normalizeProcessIdList,
  pruneEmptySections,
  removeKpiAt,
} from "../storage/kpi-mutations";
import KpiProcessMappingField from "./KpiProcessMappingField";
import KpiDatasetMappingField from "./KpiDatasetMappingField";

const ENTERPRISE_CATEGORY_INDEX = 0;
const LEVEL_OPTIONS = ["Strategic", "Operational", "Tactical"].map((v) => ({
  value: v,
  label: v,
}));

/**
 * @param {{
 *   open: boolean,
 *   onClose: () => void,
 *   editTarget?: { ci: number, si: number, ki: number } | null,
 * }} props
 */
export default function AddKpiPanel({ open, onClose, editTarget = null }) {
  const {
    data,
    setData,
    hydrated,
    addCustomKpi,
    updateCustomKpi,
    supabaseEnabled,
  } = useKpiRegistry();
  const { processes } = useProcesses();
  const { profileId } = useDemoProfile();
  const { areas: BUSINESS_AREAS } = useBusinessAreas();
  const middlebyKpis = isMiddlebyProfile(profileId);
  const isEdit = !!editTarget;

  const existingKpi = useMemo(() => {
    if (!isEdit) return null;
    const { ci, si, ki } = editTarget;
    return data?.categories?.[ci]?.sections?.[si]?.kpis?.[ki] ?? null;
  }, [data, editTarget, isEdit]);

  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const [businessAreaId, setBusinessAreaId] = useState(BUSINESS_AREAS[0]?.id ?? "inventory");
  const [subjectArea, setSubjectArea] = useState("");
  const [level, setLevel] = useState("Operational");
  const [unit, setUnit] = useState(() => defaultUnitForLevel("Operational"));
  const [selectedProcessIds, setSelectedProcessIds] = useState([]);
  const [datasetIds, setDatasetIds] = useState([]);
  const [industryBenchmark, setIndustryBenchmark] = useState("");
  const [active, setActive] = useState(true);

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

  // Reset / prefill whenever the panel opens
  useEffect(() => {
    if (!open) return;
    if (isEdit && existingKpi) {
      setName(existingKpi.name || "");
      setDesc(existingKpi.desc || "");
      const inferredBa = middlebyKpis
        ? existingKpi.businessAreaId || inferMiddlebyBusinessAreaIdForSubject(existingKpi.subjectArea)
        : existingKpi.businessAreaId ||
          inferBusinessAreaIdForSubjectArea(existingKpi.subjectArea) ||
          BUSINESS_AREAS[0]?.id ||
          "";
      setBusinessAreaId(inferredBa);
      setSubjectArea(existingKpi.subjectArea || "");
      const startLevel = existingKpi.level || "Operational";
      setLevel(startLevel);
      setUnit(
        existingKpi.unit && isUnitValidForLevel(existingKpi.unit, startLevel)
          ? existingKpi.unit
          : defaultUnitForLevel(startLevel)
      );
      setSelectedProcessIds(normalizeProcessIdList(existingKpi.processMapping || []));
      setDatasetIds(Array.isArray(existingKpi.datasetIds) ? [...existingKpi.datasetIds] : []);
      setIndustryBenchmark(existingKpi.industryBenchmark || "");
      setActive(existingKpi.active !== false);
    } else {
      setName("");
      setDesc("");
      const defaultBa = BUSINESS_AREAS[0]?.id ?? (middlebyKpis ? "middleby-rfq" : "inventory");
      setBusinessAreaId(defaultBa);
      setSubjectArea("");
      setLevel("Operational");
      setUnit(defaultUnitForLevel("Operational"));
      setSelectedProcessIds([]);
      setDatasetIds([]);
      setIndustryBenchmark("");
      setActive(true);
    }
  }, [open, isEdit, existingKpi, middlebyKpis]);

  // Keep subjectArea consistent with the selected businessArea
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
    name.trim().length > 0 &&
    !!businessAreaId &&
    subjectArea.trim().length > 0 &&
    (!isEdit || !!existingKpi);

  const onConfirm = async () => {
    if (!canSave) return;

    // Custom rows (with __supabaseId) live in Supabase. Route edits there.
    // Seed rows and legacy localStorage-only rows use the old setData path
    // (localStorage). New adds go to Supabase when it's enabled; otherwise
    // fall back to localStorage so the app still works without a backend.
    const supabaseId = existingKpi?.__supabaseId || null;

    if (isEdit && supabaseId) {
      await updateCustomKpi(supabaseId, {
        name: name.trim(),
        description: desc.trim() || null,
        business_area_id: businessAreaId || null,
        subject_area: subjectArea.trim() || null,
        level: (level || "Operational").trim() || "Operational",
        unit: unit.trim(),
        industry_benchmark: industryBenchmark.trim() || null,
        dataset_ids: datasetIds.length ? [...datasetIds] : [],
        process_mapping: normalizeProcessIdList(selectedProcessIds),
        extras: { active },
      });
      onClose?.();
      return;
    }

    if (isEdit && existingKpi) {
      setData((prev) => {
        const next = structuredClone(prev);
        const { ci, si, ki } = editTarget;
        const removed = removeKpiAt(next.categories, ci, si, ki);
        if (!removed) return prev;
        pruneEmptySections(next.categories);

        const { section } = ensureSection(
          next.categories,
          ENTERPRISE_CATEGORY_INDEX,
          subjectArea.trim(),
          businessAreaId
        );
        section.kpis = section.kpis || [];

        const updated = {
          ...removed,
          name: name.trim(),
          desc: desc.trim(),
          active,
          businessAreaId,
          subjectArea: subjectArea.trim(),
          level: (level || "Operational").trim() || "Operational",
          unit: unit.trim(),
          industryBenchmark: industryBenchmark.trim(),
          processMapping: normalizeProcessIdList(selectedProcessIds),
        };
        if (datasetIds.length) {
          updated.datasetIds = [...datasetIds];
        } else {
          delete updated.datasetIds;
        }
        delete updated.seed;
        section.kpis.push(updated);
        return next;
      });
      onClose?.();
      return;
    }

    // Brand-new KPI: prefer Supabase (shared across users). Fall back to
    // localStorage if Supabase isn't configured so local dev still works.
    if (supabaseEnabled) {
      await addCustomKpi({
        name: name.trim(),
        desc: desc.trim(),
        businessAreaId,
        subjectArea: subjectArea.trim(),
        level: (level || "Operational").trim() || "Operational",
        unit: unit.trim(),
        industryBenchmark: industryBenchmark.trim(),
        datasetIds: [...datasetIds],
        processMapping: normalizeProcessIdList(selectedProcessIds),
        extras: { active },
      });
    } else {
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
          level: (level || "Operational").trim() || "Operational",
          processMapping: normalizeProcessIdList(selectedProcessIds),
          unit: unit.trim(),
          industryBenchmark: industryBenchmark.trim(),
          ...(datasetIds.length ? { datasetIds: [...datasetIds] } : {}),
        });
        return next;
      });
    }

    onClose?.();
  };

  if (!open) return null;

  // Edit-target that no longer exists (e.g. deleted in another tab)
  if (isEdit && !existingKpi) {
    return (
      <SlidePanel
        title="Edit KPI"
        subtitle="This KPI can no longer be found."
        onClose={onClose}
        icon={null}
        widthClass="max-w-[640px]"
        footer={
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg border border-border-light bg-surface px-4 py-2 text-sm font-semibold text-text-tertiary hover:bg-surface-secondary"
          >
            Close
          </button>
        }
      >
        <p className="text-sm text-text-muted">
          The KPI you were editing no longer exists in the registry. Close this panel and pick a
          different one.
        </p>
      </SlidePanel>
    );
  }

  const title = isEdit ? "Edit KPI" : "Add KPI";
  const subtitle = isEdit
    ? "Update this KPI's definition and mapping."
    : "Create a new KPI definition in the registry.";
  const submitLabel = isEdit ? "Save changes" : "Save KPI";

  const headerActions = (
    <>
      <button
        type="button"
        onClick={onClose}
        className="rounded-lg border border-border-light bg-surface px-4 py-2 text-sm font-semibold text-text-tertiary transition hover:bg-surface-secondary"
      >
        Cancel
      </button>
      <button
        type="button"
        onClick={onConfirm}
        disabled={!canSave}
        className={`rounded-lg px-4 py-2 text-sm font-semibold text-white shadow-sm transition-colors ${
          canSave ? "bg-primary hover:bg-primary-hover" : "cursor-default bg-[#c7b8f5]"
        }`}
      >
        {submitLabel}
      </button>
    </>
  );

  return (
    <SlidePanel
      title={title}
      subtitle={subtitle}
      onClose={onClose}
      icon={null}
      widthClass="max-w-[640px]"
      headerActions={headerActions}
    >
      <div className="space-y-5">
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
              const nextLevel = e.target.value;
              setLevel(nextLevel);
              setUnit((u) =>
                isUnitValidForLevel(u, nextLevel) ? u : defaultUnitForLevel(nextLevel)
              );
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

        <KpiProcessMappingField
          processes={processes}
          value={selectedProcessIds}
          onChange={setSelectedProcessIds}
        />

        <KpiDatasetMappingField value={datasetIds} onChange={setDatasetIds} />

        <div className="space-y-3">
          <p
            id="add-kpi-panel-ai-benchmark-hint"
            className="flex items-start gap-2 rounded-lg bg-primary-light dark:bg-primary/20 p-3 text-sm font-medium leading-relaxed text-text-tertiary"
          >
            <Sparkles className="mt-0.5 h-4 w-4 shrink-0 text-primary" aria-hidden />
            <span>{aiBenchmarkSuggestion}</span>
          </p>
          <Input
            label="Target"
            placeholder="Enter the range or target you want to track"
            aria-describedby="add-kpi-panel-ai-benchmark-hint"
            value={industryBenchmark}
            onChange={(e) => setIndustryBenchmark(e.target.value)}
          />
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
    </SlidePanel>
  );
}
