import { useState } from "react";
import { Check, MousePointerClick, Sparkles } from "lucide-react";
import Modal from "../../ui/Modal";
import AddKpiAiStep from "./AddKpiAiStep";
import AddKpiManualStep from "./AddKpiManualStep";

/**
 * "Add KPI" on the discovery page: a mode picker (AI generated vs. manual), then either a
 * chat-style request that starts a steered suggestion run (`onStartAi`), or a `KpiWrite` form
 * that posts straight to the registry (`onCreateManual`). The same shape as
 * `CreateDashboardModal.jsx`'s picker, so the two "make one" flows read alike.
 *
 * The page owns everything that happens after: the run's progress and proposals, or the new
 * registry row and its panel. This modal only collects the choice and the input.
 */
function ModeCard({ selected, onClick, Icon, title, children }) {
 return (
  <button
   type="button"
   onClick={onClick}
   className={`relative flex flex-col gap-2 rounded-lg border p-4 text-left transition-colors ${
    selected ? "border-primary bg-primary-light/40 ring-2 ring-primary/20" : "border-border bg-surface-default hover:border-primary/40"
   }`}
  >
   {selected && (
    <span className="absolute right-3 top-3 flex h-5 w-5 items-center justify-center rounded-full bg-primary text-white">
     <Check size={12} strokeWidth={3} />
    </span>
   )}
   <span
    className={`flex h-9 w-9 items-center justify-center rounded-lg ${
     selected ? "bg-surface-default text-primary" : "bg-surface-tertiary text-text-secondary"
    }`}
   >
    <Icon size={18} />
   </span>
   <span className="text-sm font-semibold text-text-primary">{title}</span>
   <span className="text-xs leading-relaxed text-text-muted">{children}</span>
  </button>
 );
}

export default function AddKpiModal({ areas = [], focusAreaLabels = [], onClose, onStartAi, onCreateManual }) {
 const [step, setStep] = useState("mode");
 const [mode, setMode] = useState(null);

 return (
  <Modal title="Add KPI" onClose={onClose} maxWidth={step === "manual" ? "max-w-[760px]" : "max-w-[600px]"}>
   {step === "mode" && (
    <>
     <p className="mb-2.5 text-sm font-semibold text-text-primary">How do you want to add it?</p>
     <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
      <ModeCard selected={mode === "ai"} onClick={() => setMode("ai")} Icon={Sparkles} title="AI generated KPI">
       Describe what you want to measure — the model proposes KPIs your connected data supports, for you to adopt.
      </ModeCard>
      <ModeCard selected={mode === "manual"} onClick={() => setMode("manual")} Icon={MousePointerClick} title="Define it myself">
       Name it, pick the area and grain, write the SQL and tick the tables it reads.
      </ModeCard>
     </div>
     <div className="mt-5 flex items-center justify-end gap-2 border-t border-border pt-4">
      <button
       type="button"
       onClick={onClose}
       className="rounded-lg px-3.5 py-2 text-sm font-semibold text-text-secondary hover:text-text-primary"
      >
       Cancel
      </button>
      <button
       type="button"
       disabled={!mode}
       onClick={() => setStep(mode)}
       className="rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-primary-hover disabled:cursor-not-allowed disabled:opacity-50"
      >
       Continue
      </button>
     </div>
    </>
   )}

   {step === "ai" && (
    <AddKpiAiStep focusAreaLabels={focusAreaLabels} onBack={() => setStep("mode")} onStart={onStartAi} />
   )}

   {step === "manual" && (
    <AddKpiManualStep areas={areas} onBack={() => setStep("mode")} onCreate={onCreateManual} />
   )}
  </Modal>
 );
}
