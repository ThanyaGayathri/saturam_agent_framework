import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowRight, Bot, Check } from "lucide-react";
import { planFor } from "../../domain/use-case-plan";
import PlanPipeline from "./PlanPipeline";
import PlanOrchestration from "./PlanOrchestration";
import { displayName } from "../../domain/use-case-autonomy";
import { loadSpecDraft, saveSpecDraft } from "../../domain/use-case-draft-spec";

/**
 * Step 4 of 6: the plan.
 *
 * What will happen, step by step, before it happens. Derived from the frozen spec and the
 * orchestration compiler's stage table for its shape (`SHAPE_STAGES` in use-case-plan.js, a
 * mirror of orchestration/compiler.py); not editable here, because editing a plan by hand is how
 * it stops matching the spec. If a step is wrong the fix is in the spec, and the plan follows.
 * Under each step is the parameter slot it consumes and the value the spec gave it, so a person
 * can see exactly which answer on the form drives which step.
 *
 * Each step is labelled with who performs it -- the system (deterministic, replayable), a model
 * (writes and proposes), or a person (the step waits). The point of the labels is the pattern
 * they make: the model steps are always followed by a system check, and nothing is written
 * without a check or a person in front of it.
 */

const WHO = {
 // Agno's vocabulary, so the plan reads the way the deployed workflow is built: an executor is
 // code, an agent is a language model with a prompt, a person is a gate.
 system: { label: "Automated step", short: "automated", hint: "Code. Deterministic: same input, same output. Replayable. No AI is involved.", cls: "bg-surface-secondary text-text-secondary border-border" },
 model: { label: "AI step", short: "AI", hint: "A language model with a brief written for this role. Explains and recommends. Never decides what is allowed.", cls: "bg-primary-light text-primary border-primary/30" },
 person: { label: "Approval step", short: "approval", hint: "Waits for a person.", cls: "bg-status-warning-light text-status-warning border-status-warning-border" },
};

export default function UseCasePlanScreen() {
 const navigate = useNavigate();
 const [spec, setSpec] = useState(() => loadSpecDraft());
 const plan = useMemo(() => planFor(spec), [spec]);
 const title = spec.title?.trim() || spec.goal?.trim() || "Untitled use case";
 const approved = !!spec.planApprovedAt;
 const [view, setView] = useState("flow");

 const approve = () => {
  const next = saveSpecDraft({ ...spec, planApprovedAt: new Date().toISOString() });
  setSpec(next);
  navigate("/use-cases/builder/rehearse");
 };

 return (
  <div className="mx-auto w-full max-w-3xl space-y-4 px-6 py-6">
   <div>
    <p className="text-xs font-semibold uppercase tracking-wide text-text-muted">Step 4 of 6</p>
    <h1 className="text-2xl font-bold text-text-primary">How it will run</h1>
    <p className="mt-1 max-w-2xl text-sm text-text-secondary">
     Every step <strong className="text-text-primary">{title}</strong> takes on each run, and who takes it. Derived from the spec — if a step is wrong, change the spec and this follows.
    </p>
   </div>

   {/* Who made the plan, and who runs it. Two different questions. The orchestration agent
       composes the flow at design time -- the shape is the template, the spec fills its slots.
       At run time each step is taken by an executor, a model, or a person; for most shapes no
       model is called at all. */}
   <div className="grid gap-3 rounded-xl border border-border bg-surface p-3 sm:grid-cols-[auto_1fr]">
    <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-primary-light text-primary"><Bot size={16} /></span>
    <div className="min-w-0 text-sm">
     <p className="font-semibold text-text-primary">Workflow generated from your specification</p>
     <p className="text-text-secondary">
      The <strong className="text-text-primary">{displayName(spec)}</strong> workflow — {plan.stages.length} steps in a fixed order — with {plan.steps.reduce((n, st) => n + (st.consumes || []).filter((c) => c.value != null).length, 0)} values taken from your specification. Nothing is decided at run time; the workflow was generated here, once, and every run replays it.
     </p>
    </div>
   </div>

   <div className="flex flex-wrap items-center gap-2 text-xs">
    <span className="text-text-muted">At run time:</span>
    {["system", "model", "person"].map((w) => (
     <span key={w} title={WHO[w].hint} className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 font-semibold ${WHO[w].cls}`}>
      {plan.counts[w]} {WHO[w].short} step{plan.counts[w] === 1 ? "" : "s"}
     </span>
    ))}
    <span className="text-text-muted">·</span>
    <span className="text-text-secondary">
     {{
      inform: "Nothing is changed. It reads, compares and explains.",
      alert: "Nothing is changed, and no AI runs on a run — every step is automated: a threshold decides, and it notifies only when it fires.",
      recommend: "Nothing is changed without passing every business guardrail and an approver releasing it.",
      act: "Nothing is changed beyond the business guardrails and the change limit, and every change records its rollback point.",
     }[plan.shape.id]}
    </span>
   </div>
   <p className="text-xs text-text-muted">
    {spec.template === "document_to_record" ? `Runs once per document found; ${plan.stages.filter((s) => s.guardsWrite).length} steps that a change may not run without.` : `${plan.stages.filter((s) => s.satisfiedBy).length} of the ${plan.stages.length} steps already covered by the nightly KPI computation, ${plan.stages.filter((s) => s.guardsWrite).length} that a change may not run without.`} Every decision point is an automated step; an AI step, where there is one, explains or recommends and never decides.
   </p>

   <div className="flex items-center gap-1 rounded-lg border border-border bg-surface-secondary p-0.5 text-xs font-semibold w-fit">
    {["flow", "orchestration"].map((v) => (
     <button key={v} type="button" onClick={() => setView(v)} aria-pressed={view === v} className={`rounded-md px-3 py-1 ${view === v ? "bg-surface text-text-primary shadow-sm" : "text-text-secondary hover:text-text-primary"}`}>
      {v === "flow" ? "Pipeline" : "Orchestration"}
     </button>
    ))}
   </div>

   {view === "flow" && <PlanPipeline steps={plan.steps} />}

   {view === "orchestration" && <PlanOrchestration steps={plan.steps} exceptionsTo={spec.exceptionsTo || spec.notify} />}

   <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border pt-4">
    <button type="button" onClick={() => navigate("/use-cases/builder/compose")} className="rounded-lg border border-border bg-surface px-3.5 py-2 text-sm font-semibold text-text-secondary hover:bg-surface-secondary">
     A step is wrong — back to the specification
    </button>
    <div className="flex items-center gap-3">
     {approved && <span className="inline-flex items-center gap-1 text-xs font-semibold text-status-success"><Check size={13} /> Approved</span>}
     <button type="button" onClick={approve} className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-white hover:bg-primary-hover">
      <Check size={15} /> This is how it should run <ArrowRight size={15} />
     </button>
    </div>
   </div>
  </div>
 );
}
