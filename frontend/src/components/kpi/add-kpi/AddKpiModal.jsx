import { useState } from "react";
import { MousePointerClick, Sparkles } from "lucide-react";
import Modal from "../../ui/Modal";
import ModeCard from "../ModeCard";
import AddKpiAiStep from "./AddKpiAiStep";
import AddKpiManualStep from "./AddKpiManualStep";

/**
 * "Add KPI" on the discovery page: a `KpiWrite` form that posts straight to the registry
 * (`onCreateManual`). The page owns everything that happens after -- the new registry row and its
 * panel; this modal only collects the input.
 *
 * Two ways in, picked with the same `ModeCard` picker `create-dashboard/ModePickerStep.jsx`
 * uses: **Manual**, unchanged -- straight to `AddKpiManualStep`; and **AI-assisted**
 * (`AddKpiAiStep`), a free-text description handed to the K12 suggestion agent (contract
 * 1.2.3's `ask`, added for this). Neither adopts anything on its own -- a chosen AI draft is
 * a `prefill` for the same manual form, reviewed and (if wanted) hand-edited before the same
 * `POST /kpis` either path ends at. KPIs the data supports are still proposed for a connected
 * source automatically when its harvest ends, unaffected by either path here; AI-assisted is
 * for a KPI in someone's own words, not a substitute for that.
 *
 * `saving` lives here rather than in the form because the form cannot see what it needs to guard:
 * the manual step's `POST /kpis` in flight. `ui/Modal` calls `onClose` from its backdrop and X
 * unconditionally, and the page then strips `?add=1` and unmounts the whole modal -- so a dismiss
 * mid-save would drop a 422/409 on the floor (the form is the only place it is shown) or let a
 * "cancelled" KPI appear with its panel open. The guard is here, where `onClose` is forwarded.
 */
export default function AddKpiModal({
 areaState = { status: "ready", areas: [], error: null },
 onRetryAreas,
 onClose,
 onCreateManual,
}) {
 // "choice" (the picker) -> "ai" | "manual". A draft chosen in "ai" moves straight to
 // "manual" with `aiDraft` set as its prefill -- there is no separate review-then-continue
 // step, the manual form *is* the review.
 const [step, setStep] = useState("choice");
 const [mode, setMode] = useState(null);
 const [aiDraft, setAiDraft] = useState(null);
 const [saving, setSaving] = useState(false);

 const close = () => {
  if (saving) return;
  onClose();
 };

 const continueFromChoice = () => {
  if (!mode) return;
  setStep(mode);
 };

 return (
  <Modal title="Add KPI" onClose={close} maxWidth="max-w-[760px]">
   {step === "choice" && (
    <>
     <p className="mb-2.5 text-sm font-semibold text-text-primary">How do you want to add it?</p>
     <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
      <ModeCard selected={mode === "ai"} onClick={() => setMode("ai")} Icon={Sparkles} title="AI-assisted">
       Describe what you want to measure, in your own words. The agent drafts the fields for
       you to review.
      </ModeCard>
      <ModeCard selected={mode === "manual"} onClick={() => setMode("manual")} Icon={MousePointerClick} title="Define it myself">
       Fill in the name, formula and source tables by hand.
      </ModeCard>
     </div>
     <div className="mt-5 flex items-center justify-end gap-2 border-t border-border pt-4">
      <button
       type="button"
       onClick={close}
       className="rounded-lg px-3.5 py-2 text-sm font-semibold text-text-secondary hover:text-text-primary"
      >
       Cancel
      </button>
      <button
       type="button"
       disabled={!mode}
       onClick={continueFromChoice}
       className="rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-primary-hover disabled:cursor-not-allowed disabled:opacity-50"
      >
       Continue
      </button>
     </div>
    </>
   )}

   {step === "ai" && (
    <AddKpiAiStep
     onBack={() => setStep("choice")}
     onDraftSelected={(draft) => {
      setAiDraft(draft);
      setStep("manual");
     }}
    />
   )}

   {step === "manual" && (
    <AddKpiManualStep
     areaState={areaState}
     onRetryAreas={onRetryAreas}
     saving={saving}
     onSavingChange={setSaving}
     onCreate={onCreateManual}
     prefill={aiDraft}
     onBack={aiDraft ? () => { setAiDraft(null); setStep("ai"); } : undefined}
    />
   )}
  </Modal>
 );
}
