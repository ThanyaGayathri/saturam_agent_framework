import { useCallback, useEffect, useMemo, useState } from "react";
import { CheckCircle, CircleHelp, ClipboardCheck, FileText, Layers, Sparkles, Target } from "lucide-react";
import { Input, Select, SlidePanel } from "./ui";
import { useProcesses } from "../hooks/useProcesses";
import { pickProcessIconKey } from "../storage/processes";
import { useBusinessAreas } from "../hooks/useBusinessAreas";

/**
 * @param {{ open: boolean, onClose: () => void, editTarget?: object | null }} props
 * editTarget: process object to edit (must have __supabaseId for edit to persist).
 */
export default function AddProcessPanel({ open, onClose, editTarget = null }) {
  const { addProcess, updateCustomProcess } = useProcesses();
  const { areas: BUSINESS_AREAS } = useBusinessAreas();
  const isEdit = Boolean(editTarget?.__supabaseId);

  const businessOptions = useMemo(
    () => BUSINESS_AREAS.map((a) => ({ value: a.id, label: a.label })),
    [BUSINESS_AREAS]
  );
  const [businessAreaId, setBusinessAreaId] = useState(() => BUSINESS_AREAS[0]?.id ?? "inventory");
  const subjectOptions = useMemo(() => {
    const area = BUSINESS_AREAS.find((a) => a.id === businessAreaId);
    return (area?.subjectAreas || []).map((s) => ({ value: s, label: s }));
  }, [BUSINESS_AREAS, businessAreaId]);
  const [subjectArea, setSubjectArea] = useState(() => BUSINESS_AREAS[0]?.subjectAreas?.[0] ?? "");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [impact, setImpact] = useState("");

  useEffect(() => {
    if (!open) return;
    if (editTarget) {
      setBusinessAreaId(editTarget.businessAreaId || BUSINESS_AREAS[0]?.id || "inventory");
      setSubjectArea(editTarget.subjectArea || BUSINESS_AREAS[0]?.subjectAreas?.[0] || "");
      setName(editTarget.name || "");
      setDescription(editTarget.description || "");
      setImpact(editTarget.impact || editTarget.extras?.impact || "");
    } else {
      setBusinessAreaId(BUSINESS_AREAS[0]?.id ?? "inventory");
      setSubjectArea(BUSINESS_AREAS[0]?.subjectAreas?.[0] ?? "");
      setName("");
      setDescription("");
      setImpact("");
    }
  }, [open, editTarget]);

  useEffect(() => {
    setSubjectArea((prev) => {
      if (subjectOptions.some((o) => o.value === prev)) return prev;
      return subjectOptions[0]?.value ?? "";
    });
  }, [businessAreaId, subjectOptions]);

  const iconKey = useMemo(
    () => pickProcessIconKey({ name, description, impact }),
    [name, description, impact]
  );

  const canSave =
    name.trim().length > 0 &&
    description.trim().length > 0 &&
    Boolean(businessAreaId) &&
    Boolean(subjectArea);

  const onConfirm = useCallback(async () => {
    if (!canSave) return;
    if (isEdit && editTarget?.__supabaseId) {
      await updateCustomProcess(editTarget.__supabaseId, {
        name,
        description,
        business_area_id: businessAreaId,
        subject_area: subjectArea,
        extras: { ...(editTarget.extras || {}), impact, iconKey },
      });
    } else {
      addProcess({
        name,
        description,
        impact,
        iconKey,
        businessAreaId,
        subjectArea,
      });
    }
    onClose?.();
  }, [
    addProcess,
    updateCustomProcess,
    businessAreaId,
    canSave,
    description,
    iconKey,
    impact,
    isEdit,
    editTarget,
    name,
    onClose,
    subjectArea,
  ]);

  if (!open) return null;

  const headerIcon = (
    <div className="flex h-10 w-10 items-center justify-center rounded-lg border border-border-light bg-primary-light text-primary">
      <ClipboardCheck size={20} strokeWidth={1.75} aria-hidden />
    </div>
  );

  const footer = (
    <>
      <button
        type="button"
        onClick={onClose}
        className="rounded-lg border border-border bg-surface px-4 py-2.5 text-sm font-semibold text-text-tertiary transition hover:bg-surface-secondary"
      >
        Cancel
      </button>
      <button
        type="button"
        onClick={onConfirm}
        disabled={!canSave}
        className={`inline-flex items-center gap-1.5 rounded-lg px-4 py-2.5 text-sm font-semibold text-white shadow-sm transition-colors ${
          canSave ? "bg-primary hover:bg-primary-hover" : "cursor-not-allowed bg-primary/40"
        }`}
      >
        <CheckCircle size={16} strokeWidth={2} aria-hidden />
        {isEdit ? "Save changes" : "Save process"}
      </button>
    </>
  );

  return (
    <SlidePanel
      title={isEdit ? "Edit process" : "Add process"}
      subtitle="Name it, place it in the hierarchy, and describe what it does."
      onClose={onClose}
      icon={headerIcon}
      widthClass="max-w-[480px]"
      footer={footer}
    >
      <div className="space-y-5">
        <div className="grid grid-cols-1 gap-3">
          <Select
            label="Business area"
            required
            options={businessOptions}
            icon={<Target size={18} />}
            value={businessAreaId}
            onChange={(e) => setBusinessAreaId(e.target.value)}
          />
          <Select
            label="Subject area"
            required
            options={
              subjectOptions.length ? subjectOptions : [{ value: "", label: "Select business area first" }]
            }
            icon={<Layers size={18} />}
            value={subjectArea}
            onChange={(e) => setSubjectArea(e.target.value)}
            disabled={!businessAreaId}
          />
        </div>

        <Input
          label="Process name"
          required
          placeholder="e.g. Smart returns"
          icon={<ClipboardCheck size={18} />}
          value={name}
          onChange={(e) => setName(e.target.value)}
        />

        <div>
          <label className="mb-1.5 block text-sm font-medium text-text-primary">
            Description <span className="text-red-500">*</span>
          </label>
          <div className="relative">
            <span className="absolute left-3 top-3 text-text-muted">
              <FileText size={18} />
            </span>
            <textarea
              rows={4}
              placeholder="What does this process cover end to end?"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              className="w-full rounded-lg border border-border bg-surface py-2.5 pl-10 pr-3 text-sm text-text-primary placeholder:text-text-muted focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
            />
          </div>
        </div>

        <div>
          <div className="mb-1.5 flex items-start gap-1">
            <label className="text-sm font-medium text-text-primary">Expected impact</label>
            <span title="How this process moves the business.">
              <CircleHelp size={14} className="text-text-muted" />
            </span>
          </div>
          <Input
            placeholder="e.g. Cut return cost 35-50% and improve NPS 10-15 points."
            icon={<Target size={18} />}
            value={impact}
            onChange={(e) => setImpact(e.target.value)}
          />
        </div>

        <div className="flex gap-3 rounded-lg border border-primary/20 bg-primary-light/40 p-3">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-surface text-primary">
            <Sparkles size={18} strokeWidth={1.75} aria-hidden />
          </div>
          <div>
            <p className="text-sm font-medium text-text-primary">Icon</p>
            <p className="mt-0.5 text-xs text-text-secondary">
              Picked from your text. Current: <span className="font-mono text-text-tertiary">{iconKey}</span>
            </p>
          </div>
        </div>

      </div>
    </SlidePanel>
  );
}
