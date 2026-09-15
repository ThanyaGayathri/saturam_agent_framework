import { Sparkles, MousePointerClick, Check } from "lucide-react";

/**
 * Step 1 of the create-dashboard flow: name, schedule (unchanged from the original modal),
 * then a choice between the two ways to build the rest - AI generated or manual. `onContinue`
 * is disabled until a mode is picked.
 */
export default function ModePickerStep({
 name,
 setName,
 refresh,
 setRefresh,
 refreshOptions,
 mode,
 setMode,
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
     placeholder="e.g. Inventory health — weekly"
     className="mt-1.5 w-full rounded-lg border border-border bg-surface-default px-3 py-2 text-sm text-text-primary placeholder:text-text-muted focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
    />
   </div>

   <div className="mb-5">
    <p className="mb-2.5 text-sm font-semibold text-text-primary">How do you want to build it?</p>
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
     <button
      type="button"
      onClick={() => setMode("ai")}
      className={`relative flex flex-col gap-2 rounded-lg border p-4 text-left transition-colors ${
       mode === "ai"
        ? "border-primary bg-primary-light/40 ring-2 ring-primary/20"
        : "border-border bg-surface-default hover:border-primary/40"
      }`}
     >
      {mode === "ai" && (
       <span className="absolute right-3 top-3 flex h-5 w-5 items-center justify-center rounded-full bg-primary text-white">
        <Check size={12} strokeWidth={3} />
       </span>
      )}
      <span
       className={`flex h-9 w-9 items-center justify-center rounded-lg ${
        mode === "ai" ? "bg-surface-default text-primary" : "bg-surface-tertiary text-text-secondary"
       }`}
      >
       <Sparkles size={18} />
      </span>
      <span className="text-sm font-semibold text-text-primary">AI generated dashboard</span>
      <span className="text-xs leading-relaxed text-text-muted">
       Describe what you want to track — get suggested metrics and a filter to start from.
      </span>
     </button>

     <button
      type="button"
      onClick={() => setMode("manual")}
      className={`relative flex flex-col gap-2 rounded-lg border p-4 text-left transition-colors ${
       mode === "manual"
        ? "border-primary bg-primary-light/40 ring-2 ring-primary/20"
        : "border-border bg-surface-default hover:border-primary/40"
      }`}
     >
      {mode === "manual" && (
       <span className="absolute right-3 top-3 flex h-5 w-5 items-center justify-center rounded-full bg-primary text-white">
        <Check size={12} strokeWidth={3} />
       </span>
      )}
      <span
       className={`flex h-9 w-9 items-center justify-center rounded-lg ${
        mode === "manual" ? "bg-surface-default text-primary" : "bg-surface-tertiary text-text-secondary"
       }`}
      >
       <MousePointerClick size={18} />
      </span>
      <span className="text-sm font-semibold text-text-primary">Manual dashboard</span>
      <span className="text-xs leading-relaxed text-text-muted">
       Pick metrics yourself from the KPI catalogue.
      </span>
     </button>
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
     className="mt-1.5 w-full rounded-lg border border-border bg-surface-default px-3 py-2 text-sm text-text-primary focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
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
