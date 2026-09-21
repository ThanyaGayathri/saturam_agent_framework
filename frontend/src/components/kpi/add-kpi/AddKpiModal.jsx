import { useState } from "react";
import { MousePointerClick, Sparkles } from "lucide-react";
import Modal from "../../ui/Modal";
import ModeCard from "../ModeCard";
import AddKpiAiStep from "./AddKpiAiStep";
import AddKpiManualStep from "./AddKpiManualStep";

/**
 * "Add KPI" on the discovery page: a mode picker (AI generated vs. manual), then either a
 * chat-style request that starts a steered suggestion run (`onStartAi`), or a `KpiWrite` form
 * that posts straight to the registry (`onCreateManual`). The same shape as
 * `CreateDashboardModal.jsx`'s picker -- literally the same `ModeCard` -- so the two "make
 * one" flows read alike.
 *
 * The page owns everything that happens after: the run's progress and proposals, or the new
 * registry row and its panel. This modal only collects the choice and the input.
 *
 * Two things live here rather than in the steps, because the steps cannot see them:
 *
 *  - `saving`: the manual step's `POST /kpis` in flight. `ui/Modal` calls `onClose` from its
 *    backdrop and X unconditionally, and the page then strips `?add=1` and unmounts the whole
 *    modal -- so a dismiss mid-save would drop a 422/409 on the floor (the form is the only
 *    place it is shown) or let a "cancelled" KPI appear with its panel open. The guard is here,
 *    where `onClose` is forwarded.
 *  - Once entered, a step stays mounted and is hidden rather than unmounted when the person
 *    goes back to the picker: "Change how" -> "Continue" used to remount the manual form,
 *    refetching `GET /semantic/tables` and discarding the whole draft (SQL, ticked tables, a
 *    422 message). Now the draft, the table list and the AI step's text all survive a look
 *    back at the cards. A step is not mounted until first entered, so picking AI never
 *    fetches the manual form's table list.
 */
export default function AddKpiModal({
 areaState = { status: "ready", areas: [], error: null },
 onRetryAreas,
 focusAreaLabels = [],
 runInFlight = false,
 onClose,
 onStartAi,
 onCreateManual,
}) {
 const [step, setStep] = useState("mode");
 const [mode, setMode] = useState(null);
 const [visited, setVisited] = useState({ ai: false, manual: false });
 const [saving, setSaving] = useState(false);

 const enter = (next) => {
  setVisited((prev) => (prev[next] ? prev : { ...prev, [next]: true }));
  setStep(next);
 };
 const close = () => {
  if (saving) return;
  onClose();
 };

 return (
  <Modal title="Add KPI" onClose={close} maxWidth={step === "manual" ? "max-w-[760px]" : "max-w-[600px]"}>
   {step === "mode" && (
    <>
     <p className="mb-2.5 text-sm font-semibold text-text-primary">How do you want to add it?</p>
     <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
      <ModeCard selected={mode === "ai"} onClick={() => setMode("ai")} Icon={Sparkles} title="AI generated KPI">
       Describe what you want to measure, the model proposes KPIs your connected data supports, for you to adopt.
      </ModeCard>
      <ModeCard selected={mode === "manual"} onClick={() => setMode("manual")} Icon={MousePointerClick} title="Define it myself">
       Name it, pick the area and grain, write the SQL and tick the tables it reads.
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
       onClick={() => enter(mode)}
       className="rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-primary-hover disabled:cursor-not-allowed disabled:opacity-50"
      >
       Continue
      </button>
     </div>
    </>
   )}

   {visited.ai && (
    <div hidden={step !== "ai"}>
     <AddKpiAiStep
      focusAreaLabels={focusAreaLabels}
      runInFlight={runInFlight}
      onBack={() => setStep("mode")}
      onStart={onStartAi}
     />
    </div>
   )}

   {visited.manual && (
    <div hidden={step !== "manual"}>
     <AddKpiManualStep
      areaState={areaState}
      onRetryAreas={onRetryAreas}
      saving={saving}
      onSavingChange={setSaving}
      onBack={() => setStep("mode")}
      onCreate={onCreateManual}
     />
    </div>
   )}
  </Modal>
 );
}
