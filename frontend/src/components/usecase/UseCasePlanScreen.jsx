import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowRight, Check } from "lucide-react";
import PlanViews from "./PlanViews";
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
 * without a check or a person in front of it. The views themselves are `PlanViews`, shared
 * with the use-case page so a built use case shows the same flow.
 */

export default function UseCasePlanScreen() {
 const navigate = useNavigate();
 const [spec, setSpec] = useState(() => loadSpecDraft());
 const title = spec.title?.trim() || spec.goal?.trim() || "Untitled use case";
 const approved = !!spec.planApprovedAt;

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

   <PlanViews spec={spec} />

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
