import { useMemo, useState } from "react";
import {
  ChevronDown, ChevronUp, ListChecks, Settings2,
  BarChart2, Table2, LineChart, PieChart,
  ClipboardCheck, Zap, Bell, Play,
} from "lucide-react";
import UCBuilderLayout from "./shared/UCBuilderLayout";
import { Card } from "./ui";
import { useUseCaseDraft } from "../context/UseCaseDraftContext";
import { useUseCaseCatalog } from "../hooks/useUseCaseCatalog";
import { deriveTasksFromPlanItems, resolveContextualPlanItems } from "../storage/use-case-draft";

const AGENT_TYPES = [
  { value: "retriever", label: "Retriever Agent", color: "bg-surface-secondary text-text-tertiary" },
  { value: "analytics", label: "Analytics Agent", color: "bg-primary-light text-primary" },
  { value: "planning", label: "Planning Agent", color: "bg-[#ecfdf3] text-[#067647]" },
  { value: "display", label: "Display Agent", color: "bg-primary-light dark:bg-primary/20 text-primary" },
  { value: "action", label: "Action Agent", color: "bg-[#fff6ed] text-[#b93815]" },
  { value: "guard", label: "Guard Agent", color: "bg-[#fef3f2] text-[#b42318]" },
];

const CHART_TYPES = [
  { value: "table", label: "Table", icon: Table2 },
  { value: "bar", label: "Bar", icon: BarChart2 },
  { value: "line", label: "Line", icon: LineChart },
  { value: "pie", label: "Pie", icon: PieChart },
];

function getOutputOptions(autonomyLevel) {
  const base = [
    { value: "action", label: "Action", icon: Play, desc: "AI performs a step without producing a display output." },
    { value: "display-data", label: "Display Data", icon: Table2, desc: "Show a data table or chart." },
    { value: "display-kpi", label: "Display KPI", icon: BarChart2, desc: "Show KPI tiles with trend indicators." },
    { value: "alert", label: "Alert", icon: Bell, desc: "Send a notification to a configured channel." },
  ];
  if (autonomyLevel === "copilot") {
    base.push({ value: "decision-card", label: "Decision Card", icon: ClipboardCheck, desc: "AI prepares a Decision Card for human review and approval." });
  }
  if (autonomyLevel === "autopilot") {
    base.push({ value: "auto-execute", label: "Auto-execute", icon: Zap, desc: "AI executes autonomously within guardrails. Action logged." });
  }
  return base;
}

function OutputConfig({ task, onUpdate }) {
  if (task.outputType === "display-data") {
    return (
      <div className="mt-3 rounded-lg border border-border bg-surface-secondary p-3">
        <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-text-muted">Chart type</p>
        <div className="flex flex-wrap gap-2">
          {CHART_TYPES.map((ct) => {
            const Icon = ct.icon;
            const sel = task.chartType === ct.value;
            return (
              <button
                key={ct.value}
                type="button"
                onClick={() => onUpdate({ chartType: ct.value })}
                className={`flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-medium transition ${sel ? "border-primary bg-primary-light dark:bg-primary/20 text-primary" : "border-border bg-surface text-text-secondary hover:border-primary"}`}
              >
                <Icon size={13} />
                {ct.label}
              </button>
            );
          })}
        </div>
      </div>
    );
  }
  if (task.outputType === "display-kpi") {
    return (
      <div className="mt-3 rounded-lg border border-border bg-surface-secondary p-3">
        <p className="text-xs text-text-secondary">KPI tiles will be auto-suggested from the KPI registry based on your business area.</p>
      </div>
    );
  }
  return null;
}

export default function UCBuilderStep4PlanBreakdown() {
  const { draft, updateDraft } = useUseCaseDraft();
  const { entries } = useUseCaseCatalog();
  const [expandedIdx, setExpandedIdx] = useState(0);

  const autonomyLevel = draft.step1.autonomyLevel || "copilot";
  const outputOptions = getOutputOptions(autonomyLevel);

  // Resolve the catalog entry this draft belongs to (if any) so plan items
  // and derived tasks are use-case-specific rather than generic-category.
  const catalogEntry = useMemo(() => {
    const targetId =
      draft.builderSession?.reopenedCatalogId ||
      draft.builderSession?.fromSeedTemplateId ||
      null;
    if (targetId) {
      const found = (entries || []).find((e) => e.id === targetId);
      if (found) return found;
    }
    return draft.step1?.title ? { title: draft.step1.title } : null;
  }, [entries, draft.builderSession, draft.step1?.title]);

  // Contextual tasks derived from the Step 4 plan items when the draft
  // doesn't already carry Step 5 tasks. Falls back to computing plan items
  // on the fly from the catalog entry / title if Step 4 was skipped,
  // so tasks always reflect this specific use case (never a generic list).
  const contextualTasks = useMemo(() => {
    const items = draft.step4?.planItems && draft.step4.planItems.length > 0
      ? draft.step4.planItems
      : resolveContextualPlanItems(
          draft.step2?.primaryQuestion ?? "",
          draft.step1?.category ?? "",
          (draft.step3?.confirmedSources ?? []).filter((s) => s.enabled),
          (draft.step3?.confirmedKnowledge ?? []).filter((k) => k.enabled),
          draft.step1?.title ?? "",
          catalogEntry
        );
    return deriveTasksFromPlanItems(items, autonomyLevel);
  }, [draft, autonomyLevel, catalogEntry]);

  const tasks = (draft.step5?.tasks?.length ? draft.step5.tasks : contextualTasks).map((t) => ({
    ...t,
    outputType: t.outputType || "action",
    agentType: t.agentType || "analytics",
    chartType: t.chartType || "table",
    kpiIds: t.kpiIds || [],
  }));

  const updateTask = (idx, patch) => {
    updateDraft((d) => {
      const base = d.step5?.tasks?.length ? d.step5.tasks : contextualTasks;
      const updated = base.map((t, i) => (i === idx ? { ...t, ...patch } : t));
      return { ...d, step5: { ...d.step5, tasks: updated } };
    });
  };

  const agentMeta = (val) => AGENT_TYPES.find((a) => a.value === val) ?? AGENT_TYPES[0];
  const outputMeta = (val) => outputOptions.find((o) => o.value === val) ?? outputOptions[0];

  return (
    <UCBuilderLayout
      currentStep={5}
      title="Plan Breakdown"
      subtitle="Review each task, set the output type, and assign agents. These tasks become the nodes in the orchestration diagram."
      backPath="/use-cases/builder/step-4"
      nextPath="/use-cases/builder/step-6"
    >
      <div className="max-w-2xl space-y-4">
        {tasks.map((task, i) => {
          const isOpen = expandedIdx === i;
          const agent = agentMeta(task.agentType);
          const output = outputMeta(task.outputType);

          return (
            <Card key={task.id} hover className="!p-0">
              {/* Header row */}
              <button
                type="button"
                onClick={() => setExpandedIdx(isOpen ? -1 : i)}
                className="flex w-full items-center justify-between gap-3 px-5 py-4"
              >
                <div className="flex min-w-0 items-center gap-3">
                  <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary-light dark:bg-primary/20 text-xs font-bold text-primary">
                    {i + 1}
                  </span>
                  <div className="min-w-0 text-left">
                    <p className="truncate text-sm font-semibold text-text-primary">{task.name}</p>
                    <div className="mt-1 flex flex-wrap items-center gap-1.5">
                      <span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${agent.color}`}>{agent.label}</span>
                      <span className="text-[#d5d7da]">·</span>
                      <span className="text-[11px] text-text-muted">{output.label}</span>
                      <span className="text-[#d5d7da]">·</span>
                      <span className="text-[11px] text-text-muted">{task.estimatedTime}</span>
                    </div>
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <Settings2 size={14} className="text-text-muted" />
                  {isOpen ? <ChevronUp size={16} className="text-text-muted" /> : <ChevronDown size={16} className="text-text-muted" />}
                </div>
              </button>

              {/* Expanded config */}
              {isOpen && (
                <div className="border-t border-border px-5 py-4 space-y-4">
                  {/* Agent type */}
                  <div>
                    <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-text-muted">Agent</p>
                    <div className="flex flex-wrap gap-2">
                      {AGENT_TYPES.map((a) => (
                        <button
                          key={a.value}
                          type="button"
                          onClick={() => updateTask(i, { agentType: a.value })}
                          className={`rounded-full px-3 py-1 text-xs font-semibold transition ${
                            task.agentType === a.value
                              ? a.color + " ring-2 ring-current ring-offset-1"
                              : "bg-surface-tertiary text-text-secondary hover:bg-border"
                          }`}
                        >
                          {a.label}
                        </button>
                      ))}
                    </div>
                  </div>

                  {/* Output type */}
                  <div>
                    <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-text-muted">Output type</p>
                    <div className="flex flex-wrap gap-2">
                      {outputOptions.map((o) => {
                        const Icon = o.icon;
                        const sel = task.outputType === o.value;
                        return (
                          <button
                            key={o.value}
                            type="button"
                            onClick={() => updateTask(i, { outputType: o.value })}
                            title={o.desc}
                            className={`flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-medium transition ${
                              sel
                                ? "border-primary bg-primary-light dark:bg-primary/20 text-primary"
                                : "border-border bg-surface text-text-secondary hover:border-primary"
                            }`}
                          >
                            <Icon size={13} />
                            {o.label}
                          </button>
                        );
                      })}
                    </div>
                    <p className="mt-1.5 text-xs text-text-muted">{output.desc}</p>
                  </div>

                  {/* Conditional output config */}
                  <OutputConfig task={task} onUpdate={(patch) => updateTask(i, patch)} />
                </div>
              )}
            </Card>
          );
        })}

        {/* Autonomy context note */}
        <div className="rounded-xl border border-border bg-surface-secondary px-4 py-3">
          <p className="text-xs text-text-muted">
            <span className="font-semibold text-text-secondary">Mode: {autonomyLevel === "copilot" ? "Co-Pilot" : "Autopilot"}.</span>{" "}
            {autonomyLevel === "copilot"
              ? "Use Decision Card output on tasks that require human approval before execution."
              : "Use Auto-execute output on tasks the AI should run autonomously within guardrails."}
          </p>
        </div>
      </div>
    </UCBuilderLayout>
  );
}
