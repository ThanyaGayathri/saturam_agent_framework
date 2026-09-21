import { Sparkles, MousePointerClick } from "lucide-react";
import ModeCard, { FIELD_INPUT } from "../ModeCard";

/**
 * Step 1 of the create-dashboard flow: name, schedule (unchanged from the original modal),
 * then a choice between the two ways to build the rest - AI generated or manual. `onContinue`
 * is disabled until a mode is picked. `preselectedCount`, when positive, means the caller
 * opened this modal with metrics already picked (from the KPI-discovery grid) - both paths
 * carry those into the builder, so the note here applies to either choice.
 */
export default function ModePickerStep({
 name,
 setName,
 refresh,
 setRefresh,
 refreshOptions,
 mode,
 setMode,
 preselectedCount = 0,
 onCancel,
 onContinue,
}) {
 return (
  <>
   <div className="mb-5">
    <label htmlFor="db-name" className="block text-sm font-semibold text-text-primary">
     Name
    </label>
    <input
     id="db-name"
     autoFocus
     value={name}
     onChange={(e) => setName(e.target.value)}
     placeholder="e.g. Inventory health: weekly"
     className={`mt-1.5 ${FIELD_INPUT}`}
    />
   </div>

   <div className="mb-5">
    <p className="mb-2.5 text-sm font-semibold text-text-primary">How do you want to build it?</p>
    {preselectedCount > 0 && (
     <p className="mb-2.5 text-xs text-text-muted">
      {preselectedCount} metric{preselectedCount === 1 ? "" : "s"} selected on the KPI page will
      be included either way.
     </p>
    )}
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
     <ModeCard selected={mode === "ai"} onClick={() => setMode("ai")} Icon={Sparkles} title="AI generated dashboard">
      Describe what you want to track, get suggested metrics and a filter to start from.
     </ModeCard>
     <ModeCard selected={mode === "manual"} onClick={() => setMode("manual")} Icon={MousePointerClick} title="Manual dashboard">
      Pick metrics yourself from the KPI catalogue.
     </ModeCard>
    </div>
   </div>

   <div className="mb-0">
    <label htmlFor="db-refresh" className="block text-sm font-semibold text-text-primary">
     Refresh the numbers
    </label>
    <select
     id="db-refresh"
     value={refresh}
     onChange={(e) => setRefresh(e.target.value)}
     className={`mt-1.5 ${FIELD_INPUT}`}
    >
     {refreshOptions.map((o) => (
      <option key={o.value} value={o.value}>
       {o.label}
      </option>
     ))}
    </select>
   </div>

   <div className="mt-5 flex items-center justify-end gap-2 border-t border-border pt-4">
    <button
     type="button"
     onClick={onCancel}
     className="rounded-lg px-3.5 py-2 text-sm font-semibold text-text-secondary hover:text-text-primary"
    >
     Cancel
    </button>
    <button
     type="button"
     disabled={!mode}
     onClick={onContinue}
     className="rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-primary-hover disabled:cursor-not-allowed disabled:opacity-50"
    >
     Continue
    </button>
   </div>
  </>
 );
}
