import { useMemo, useState } from "react";
import {
  Briefcase,
  ChevronDown,
  ChevronRight,
  FolderTree,
  Info,
  Layers,
  Pencil,
  Plus,
  Trash2,
  X,
} from "lucide-react";
import { useBusinessAreas } from "../../hooks/useBusinessAreas";

/**
 * Business hierarchy admin tab.
 *
 * Source of truth: `retailgpt:business-areas:v1` via `useBusinessAreas`.
 * Consumers across the product (Playground pills, Processes chips, Alerts
 * filter, KPI Registry dropdowns, Business Areas catalog) all read from
 * the same store so add / rename / delete propagate everywhere.
 *
 * Delete is blocked whenever an area still contains KPIs — the impact
 * modal makes that explicit and tells the admin to move the KPIs first.
 */

const CONSUMER_LIST = [
  "Playground business area pills",
  "Processes filter chips",
  "Alerts business area filter",
  "KPI Registry business area dropdown",
  "Business Areas catalog page",
  "Monitor and KPI Dashboard headers",
];

function EditPanel({ open, mode, initial, areas, onClose, onSave }) {
  const [label, setLabel] = useState("");
  const [desc, setDesc] = useState("");

  // Reset state when the panel opens/closes or target changes.
  useMemo(() => {
    if (!open) return;
    setLabel(initial?.label || "");
    setDesc(initial?.description || "");
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, initial?.id, initial?.subjectName]);

  if (!open) return null;

  const titleByMode = {
    "add-area": "Add business area",
    "edit-area": "Edit business area",
    "add-subject": `Add subject area in ${initial?.areaLabel || ""}`,
    "edit-subject": `Rename subject area in ${initial?.areaLabel || ""}`,
  };
  const helperByMode = {
    "add-area": "Top-level slice of the business, like Inventory or Store Ops.",
    "edit-area": "Rename this business area. The internal id stays the same, so KPIs already tagged with this area remain linked.",
    "add-subject": "Group of related KPIs inside this business area.",
    "edit-subject": "Rename this subject area. All KPIs currently tagged with the old name will be updated to the new name after you save.",
  };
  const isSubjectMode = mode === "add-subject" || mode === "edit-subject";
  const canSave = label.trim().length > 0;

  return (
    <>
      <button
        type="button"
        aria-label="Close"
        onClick={onClose}
        className="fixed inset-x-0 bottom-0 top-[72px] z-30 bg-[#0a0d12]/40 backdrop-blur-[1px]"
      />
      <aside className="fixed bottom-0 right-0 top-[72px] z-30 flex w-full max-w-md flex-col border-l border-border bg-surface-default shadow-2xl">
        <div className="flex items-center justify-between border-b border-border px-5 py-4">
          <div>
            <h3 className="text-base font-bold text-text-primary">{titleByMode[mode]}</h3>
            <p className="text-xs text-text-muted">{helperByMode[mode]}</p>
          </div>
          <button
            type="button"
            className="rounded-lg p-2 text-text-muted hover:bg-surface-secondary"
            onClick={onClose}
          >
            <X size={20} />
          </button>
        </div>

        <div className="flex-1 space-y-5 overflow-y-auto px-5 py-5">
          <div>
            <label className="mb-1 block text-xs font-semibold text-text-primary">
              Name <span className="text-[#d92d20]">*</span>
            </label>
            <input
              type="text"
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              placeholder={isSubjectMode ? "e.g. Replenishment" : "e.g. Store Ops"}
              className="w-full rounded-lg border border-border px-3 py-2 text-sm text-text-primary placeholder:text-[#9da4ae] focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
            />
          </div>
          {!isSubjectMode ? (
            <div>
              <label className="mb-1 block text-xs font-semibold text-text-primary">Description</label>
              <textarea
                value={desc}
                onChange={(e) => setDesc(e.target.value)}
                rows={3}
                placeholder="Describe what this area covers."
                className="w-full resize-none rounded-lg border border-border px-3 py-2 text-sm text-text-primary placeholder:text-[#9da4ae] focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
              />
            </div>
          ) : null}
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-border px-5 py-4">
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg border border-border-light bg-surface-default px-4 py-2 text-sm font-semibold text-text-tertiary hover:bg-surface-secondary"
          >
            Cancel
          </button>
          <button
            type="button"
            disabled={!canSave}
            onClick={() => onSave({ mode, label: label.trim(), description: desc.trim(), initial })}
            className="rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-white hover:bg-primary-hover disabled:opacity-40"
          >
            Save
          </button>
        </div>
      </aside>
    </>
  );
}

function DeleteConfirmModal({ open, title, kpiCount, extraLines, onCancel, onConfirm }) {
  if (!open) return null;
  const blocked = kpiCount > 0;
  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-[#0a0d12]/40 backdrop-blur-[1px]">
      <div className="w-full max-w-md rounded-2xl border border-border bg-surface-default shadow-2xl">
        <div className="flex items-start justify-between border-b border-border px-5 py-4">
          <h3 className="text-base font-bold text-text-primary">{title}</h3>
          <button
            type="button"
            onClick={onCancel}
            className="rounded-lg p-1.5 text-text-muted hover:bg-surface-secondary"
            aria-label="Close"
          >
            <X size={18} />
          </button>
        </div>
        <div className="space-y-4 px-5 py-5 text-sm text-text-secondary">
          {blocked ? (
            <div className="rounded-lg border border-status-critical-border bg-status-critical-light px-3 py-2.5 text-status-critical dark:bg-status-critical/15">
              <p className="text-sm font-semibold">Cannot delete yet.</p>
              <p className="mt-1 text-[13px] leading-5">
                {kpiCount} KPI{kpiCount === 1 ? "" : "s"} still tagged with this area. Move or delete
                those KPIs first from the KPI Registry, then come back here.
              </p>
            </div>
          ) : (
            <p className="leading-6">This action removes the entry from the workspace's business hierarchy.</p>
          )}

          <div>
            <p className="text-xs font-semibold uppercase tracking-wide text-text-muted">This will affect</p>
            <ul className="mt-2 list-disc space-y-1 pl-5 text-[13px] leading-6">
              {(extraLines || CONSUMER_LIST).map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          </div>
        </div>
        <div className="flex items-center justify-end gap-2 border-t border-border px-5 py-4">
          <button
            type="button"
            onClick={onCancel}
            className="rounded-lg border border-border-light bg-surface-default px-4 py-2 text-sm font-semibold text-text-tertiary hover:bg-surface-secondary"
          >
            Cancel
          </button>
          <button
            type="button"
            disabled={blocked}
            onClick={onConfirm}
            className="rounded-lg bg-red-600 px-4 py-2 text-sm font-semibold text-white hover:bg-red-700 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {blocked ? "Blocked" : "Delete"}
          </button>
        </div>
      </div>
    </div>
  );
}

export default function ConfigureBusinessHierarchy() {
  const {
    areas,
    hydrated,
    addArea,
    renameArea,
    removeArea,
    addSubjectArea,
    renameSubjectArea,
    removeSubjectArea,
    kpiCountByAreaId,
    kpiCountBySubjectArea,
  } = useBusinessAreas();

  const [expanded, setExpanded] = useState(() => new Set([0]));
  const [editState, setEditState] = useState({ open: false, mode: null, initial: null });
  const [deleteState, setDeleteState] = useState({ open: false });

  const totals = useMemo(() => {
    let subjects = 0;
    let totalKpis = 0;
    for (const a of areas) {
      subjects += (a.subjectAreas || []).length;
      totalKpis += kpiCountByAreaId[a.id] || 0;
    }
    return { business: areas.length, subjects, kpis: totalKpis };
  }, [areas, kpiCountByAreaId]);

  const toggleExpanded = (idx) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      next.has(idx) ? next.delete(idx) : next.add(idx);
      return next;
    });
  };

  const openAddArea = () => setEditState({ open: true, mode: "add-area", initial: null });
  const openEditArea = (area) => setEditState({ open: true, mode: "edit-area", initial: area });
  const openAddSubject = (area) =>
    setEditState({ open: true, mode: "add-subject", initial: { areaId: area.id, areaLabel: area.label } });
  const openEditSubject = (area, subjectName) =>
    setEditState({
      open: true,
      mode: "edit-subject",
      initial: { areaId: area.id, areaLabel: area.label, subjectName, label: subjectName },
    });
  const closeEdit = () => setEditState({ open: false, mode: null, initial: null });

  const handleSave = ({ mode, label, description, initial }) => {
    if (mode === "add-area") {
      addArea({ label, description });
    } else if (mode === "edit-area" && initial?.id) {
      renameArea(initial.id, label, description);
    } else if (mode === "add-subject" && initial?.areaId) {
      addSubjectArea(initial.areaId, label);
    } else if (mode === "edit-subject" && initial?.areaId && initial?.subjectName) {
      renameSubjectArea(initial.areaId, initial.subjectName, label);
    }
    closeEdit();
  };

  const openDeleteArea = (area) => {
    const kpiCount = kpiCountByAreaId[area.id] || 0;
    setDeleteState({
      open: true,
      kind: "area",
      areaId: area.id,
      title: `Delete "${area.label}"?`,
      kpiCount,
    });
  };

  const openDeleteSubject = (area, subjectName) => {
    const kpiCount = kpiCountBySubjectArea[`${area.id}::${subjectName}`] || 0;
    setDeleteState({
      open: true,
      kind: "subject",
      areaId: area.id,
      subjectName,
      title: `Delete "${subjectName}" in ${area.label}?`,
      kpiCount,
      extraLines: [
        `${area.label} business area on Playground and Processes will still exist`,
        `${kpiCount} KPI${kpiCount === 1 ? "" : "s"} tagged with "${subjectName}"`,
      ],
    });
  };

  const closeDelete = () => setDeleteState({ open: false });

  const handleConfirmDelete = () => {
    if (deleteState.kind === "area" && deleteState.areaId) {
      removeArea(deleteState.areaId);
    } else if (deleteState.kind === "subject" && deleteState.areaId) {
      removeSubjectArea(deleteState.areaId, deleteState.subjectName);
    }
    closeDelete();
  };

  if (!hydrated) return <p className="text-sm text-text-secondary">Loading…</p>;

  return (
    <div className="space-y-5">
      <div className="flex items-start gap-3 rounded-xl border border-primary/25 bg-primary-light/60 px-4 py-3 text-sm text-text-secondary dark:border-primary/35 dark:bg-surface-tertiary">
        <Info size={16} strokeWidth={2} className="mt-0.5 shrink-0 text-primary" aria-hidden />
        <div>
          <p className="font-semibold text-text-primary">
            The business hierarchy is used across the whole product.
          </p>
          <p className="mt-1 leading-6">
            Business areas group Processes, KPIs, Use Cases, and Alerts. Subject areas nest inside a business area
            and group related KPIs. Adding, renaming, or deleting an entry here updates every surface that shows
            the hierarchy. Playground pills, Processes and Alerts filters, KPI Registry dropdowns, and more.
          </p>
        </div>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-4 text-sm text-text-muted">
          <span className="inline-flex items-center gap-1.5 font-semibold text-text-primary">
            <Briefcase size={14} className="text-primary" aria-hidden />
            {totals.business} business {totals.business === 1 ? "area" : "areas"}
          </span>
          <span className="inline-flex items-center gap-1.5 font-semibold text-text-primary">
            <Layers size={14} className="text-primary" aria-hidden />
            {totals.subjects} subject {totals.subjects === 1 ? "area" : "areas"}
          </span>
          <span className="tabular-nums">{totals.kpis} KPIs tagged</span>
        </div>
        <button
          type="button"
          onClick={openAddArea}
          className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-white transition hover:bg-primary-hover"
        >
          <Plus size={16} />
          Add business area
        </button>
      </div>

      {areas.length === 0 ? (
        <div className="flex flex-col items-center justify-center rounded-2xl border border-dashed border-border bg-surface-secondary/40 py-14 text-center">
          <div className="mb-3 flex h-12 w-12 items-center justify-center rounded-xl border border-border bg-surface">
            <FolderTree size={22} className="text-text-secondary" />
          </div>
          <p className="text-base font-semibold text-text-primary">No business areas yet</p>
          <p className="mt-1 max-w-md text-sm text-text-muted">
            Start by defining the top-level slices of your business. Each area can nest one or more subject areas.
          </p>
          <button
            type="button"
            onClick={openAddArea}
            className="mt-4 inline-flex items-center gap-1.5 rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-white transition hover:bg-primary-hover"
          >
            <Plus size={16} />
            Add business area
          </button>
        </div>
      ) : (
        <ul className="space-y-3">
          {areas.map((area, idx) => {
            const isOpen = expanded.has(idx);
            const subjectCount = (area.subjectAreas || []).length;
            const areaKpiCount = kpiCountByAreaId[area.id] || 0;
            return (
              <li
                key={area.id}
                className="overflow-hidden rounded-2xl border border-border bg-surface-default shadow-sm"
              >
                <div className="flex items-center gap-3 px-4 py-3 sm:px-5">
                  <button
                    type="button"
                    onClick={() => toggleExpanded(idx)}
                    className="flex flex-1 items-center gap-3 text-left"
                    aria-expanded={isOpen}
                  >
                    <span className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-primary/25 bg-primary-light text-primary dark:border-primary/35 dark:bg-surface-tertiary">
                      <Briefcase size={16} strokeWidth={1.75} aria-hidden />
                    </span>
                    <div className="min-w-0">
                      <p className="truncate text-[15px] font-semibold text-text-primary">{area.label}</p>
                      <p className="mt-0.5 text-xs text-text-muted">
                        {subjectCount} {subjectCount === 1 ? "subject area" : "subject areas"}
                        <span className="mx-1.5 text-border-light">·</span>
                        {areaKpiCount} {areaKpiCount === 1 ? "KPI tagged" : "KPIs tagged"}
                      </p>
                    </div>
                    <span className="ml-auto text-text-muted">
                      {isOpen ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                    </span>
                  </button>
                  <button
                    type="button"
                    onClick={() => openEditArea(area)}
                    aria-label={`Rename ${area.label}`}
                    title="Rename business area"
                    className="rounded-md p-1.5 text-text-muted transition hover:bg-surface-secondary hover:text-primary"
                  >
                    <Pencil size={14} />
                  </button>
                  <button
                    type="button"
                    onClick={() => openDeleteArea(area)}
                    aria-label={`Delete ${area.label}`}
                    title="Delete business area"
                    className="rounded-md p-1.5 text-text-muted transition hover:bg-red-500/10 hover:text-red-500"
                  >
                    <Trash2 size={14} />
                  </button>
                </div>

                {isOpen ? (
                  <div className="border-t border-border bg-surface-secondary/40 px-4 py-3 sm:px-5">
                    {(area.subjectAreas || []).length === 0 ? (
                      <p className="mb-3 text-xs text-text-muted">No subject areas yet.</p>
                    ) : (
                      <ul className="mb-3 space-y-1.5">
                        {(area.subjectAreas || []).map((subject) => {
                          const subjectKpis = kpiCountBySubjectArea[`${area.id}::${subject}`] || 0;
                          return (
                            <li
                              key={subject}
                              className="flex items-center gap-2 rounded-lg border border-border bg-surface-default px-3 py-2"
                            >
                              <Layers size={13} className="shrink-0 text-primary" aria-hidden />
                              <span className="min-w-0 flex-1 truncate text-sm font-medium text-text-primary">
                                {subject}
                              </span>
                              <span className="text-[11px] text-text-muted">
                                {subjectKpis} {subjectKpis === 1 ? "KPI" : "KPIs"}
                              </span>
                              <button
                                type="button"
                                onClick={() => openEditSubject(area, subject)}
                                aria-label={`Rename ${subject}`}
                                title="Rename subject area"
                                className="rounded-md p-1 text-text-muted transition hover:bg-surface-secondary hover:text-primary"
                              >
                                <Pencil size={12} />
                              </button>
                              <button
                                type="button"
                                onClick={() => openDeleteSubject(area, subject)}
                                aria-label={`Delete ${subject}`}
                                title="Delete subject area"
                                className="rounded-md p-1 text-text-muted transition hover:bg-red-500/10 hover:text-red-500"
                              >
                                <Trash2 size={12} />
                              </button>
                            </li>
                          );
                        })}
                      </ul>
                    )}
                    <button
                      type="button"
                      onClick={() => openAddSubject(area)}
                      className="inline-flex items-center gap-1.5 rounded-lg border border-dashed border-primary/40 bg-surface-default px-3 py-1.5 text-xs font-semibold text-primary transition hover:border-primary hover:bg-primary-light/50"
                    >
                      <Plus size={13} />
                      Add subject area
                    </button>
                  </div>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}

      <EditPanel
        open={editState.open}
        mode={editState.mode}
        initial={editState.initial}
        areas={areas}
        onClose={closeEdit}
        onSave={handleSave}
      />
      <DeleteConfirmModal
        open={deleteState.open}
        title={deleteState.title}
        kpiCount={deleteState.kpiCount || 0}
        extraLines={deleteState.extraLines}
        onCancel={closeDelete}
        onConfirm={handleConfirmDelete}
      />
    </div>
  );
}
