import { useMemo, useState } from "react";
import { Bot } from "lucide-react";
import { planFor } from "../../domain/use-case-plan";
import PlanPipeline from "./PlanPipeline";
import PlanOrchestration from "./PlanOrchestration";
import { displayName } from "../../domain/use-case-autonomy";

/**
 * The plan as a person reads it: who made it, who runs each step, and the two views of the
 * steps -- the pipeline and the orchestration canvas. Shared by the builder's "How it will
 * run" step and the use-case page's "How it runs" tab, so an already built use case shows the
 * same flow the person approved, derived from the spec the platform holds. Read-only.
 */

export const WHO = {
 // Agno's vocabulary, so the plan reads the way the deployed workflow is built: an executor is
 // code, an agent is a language model with a prompt, a person is a gate.
 system: { label: "Automated step", short: "automated", hint: "Code. Deterministic: same input, same output. Replayable. No AI is involved.", cls: "bg-surface-secondary text-text-secondary border-border" },
 model: { label: "AI step", short: "AI", hint: "A language model with a brief written for this role. Explains and recommends. Never decides what is allowed.", cls: "bg-primary-light text-primary border-primary/30" },
 person: { label: "Approval step", short: "approval", hint: "Waits for a person.", cls: "bg-status-warning-light text-status-warning border-status-warning-border" },
};

export default function PlanViews({ spec, generatedNote = true }) {
 const plan = useMemo(() => planFor(spec), [spec]);
 const [view, setView] = useState("flow");

 return (
  <div className="space-y-4">
   {/* Who made the plan, and who runs it. Two different questions. The orchestration agent
       composes the flow at design time -- the shape is the template, the spec fills its slots.
       At run time each step is taken by an executor, a model, or a person. */}
   {generatedNote && (
    <div className="grid gap-3 rounded-xl border border-border bg-surface p-3 sm:grid-cols-[auto_1fr]">
     <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-primary-light text-primary"><Bot size={16} /></span>
     <div className="min-w-0 text-sm">
      <p className="font-semibold text-text-primary">Workflow generated from the specification</p>
      <p className="text-text-secondary">
       The <strong className="text-text-primary">{displayName(spec)}</strong> workflow: {plan.stages.length} steps in a fixed order, with {plan.steps.reduce((n, st) => n + (st.consumes || []).filter((c) => c.value != null).length, 0)} values taken from the specification. Nothing is decided at run time; the workflow was generated once, and every run replays it.
      </p>
     </div>
    </div>
   )}

   {/* The run-time counts and the sentence qualifying them are one thought, so they sit in
       one panel. Loose on the page they read as two orphan lines between the card above and
       the tabs below, belonging to neither. */}
   <div className="space-y-2 rounded-xl border border-border bg-surface-secondary/60 px-3.5 py-3">
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
      alert: "Nothing is changed, and no AI runs on a run, every step is automated: a threshold decides, and it notifies only when it fires.",
      recommend: "Nothing is changed without passing every business guardrail and an approver releasing it.",
      act: "Nothing is changed beyond the business guardrails and the change limit, and every change records its rollback point.",
     }[plan.shape.id]}
    </span>
   </div>
   <p className="text-xs leading-relaxed text-text-muted">
    {spec.template === "document_to_record" ? `Runs once per document found; ${plan.stages.filter((s) => s.guardsWrite).length} steps that a change may not run without.` : `${plan.stages.filter((s) => s.satisfiedBy).length} of the ${plan.stages.length} steps already covered by the nightly KPI computation, ${plan.stages.filter((s) => s.guardsWrite).length} that a change may not run without.`} Every decision point is an automated step; an AI step, where there is one, explains or recommends and never decides.
   </p>
   </div>

   <div className="flex w-fit items-center gap-1 rounded-lg border border-border bg-surface-secondary p-0.5 text-xs font-semibold">
    {["flow", "orchestration"].map((v) => (
     <button key={v} type="button" onClick={() => setView(v)} aria-pressed={view === v} className={`rounded-md px-3 py-1 ${view === v ? "bg-surface text-text-primary shadow-sm" : "text-text-secondary hover:text-text-primary"}`}>
      {v === "flow" ? "Pipeline" : "Orchestration"}
     </button>
    ))}
   </div>

   {view === "flow" && <PlanPipeline steps={plan.steps} />}
   {view === "orchestration" && <PlanOrchestration steps={plan.steps} exceptionsTo={spec.exceptionsTo || spec.notify} />}
  </div>
 );
}
