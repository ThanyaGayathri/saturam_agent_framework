import { AlertTriangle, Sparkles } from "lucide-react";

/**
 * Step 3 of the AI path: shown while `useDashboardSuggestionRun` is polling, and again (with
 * `error` set) if the run failed - the same status/error split `KpiDiscoveryPage.jsx`'s
 * suggestion-run state machine renders for `POST /kpis/suggestions`.
 */
export default function AiGeneratingStep({ purpose, error, onBack, onRetry }) {
 return (
  <>
   <div className="mb-4 flex justify-end">
    <span className="inline-flex items-center gap-1.5 rounded-full bg-primary-light px-2.5 py-1 text-xs font-semibold text-primary">
     <Sparkles size={11} />
     AI generated
    </span>
   </div>

   <div className="mb-4 flex justify-end">
    <div className="max-w-[85%] rounded-lg border border-border bg-surface-secondary px-3.5 py-2.5 text-sm leading-relaxed text-text-secondary">
     "{purpose}"
    </div>
   </div>

   <div className="flex gap-3">
    <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary text-white">
     <Sparkles size={13} />
    </div>
    {error ? (
     <div className="flex-1">
      <div className="flex items-start gap-2 rounded-lg border border-status-critical/30 bg-status-critical/10 px-3 py-2.5 text-sm text-text-primary">
       <AlertTriangle size={15} className="mt-0.5 shrink-0 text-status-critical" />
       <p>{error}</p>
      </div>
      <div className="mt-3 flex items-center gap-2">
       <button
        type="button"
        onClick={onRetry}
        className="rounded-lg bg-primary px-3.5 py-1.5 text-xs font-semibold text-white hover:bg-primary-hover"
       >
        Try again
       </button>
       <button
        type="button"
        onClick={onBack}
        className="text-xs font-semibold text-text-secondary hover:text-text-primary"
       >
        Change the purpose
       </button>
      </div>
     </div>
    ) : (
     <div className="flex items-center gap-2 pt-1.5">
      <span className="text-sm font-medium text-text-muted">
       Drafting metrics and a filter…
      </span>
      <span className="flex gap-1">
       <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-primary [animation-delay:-0.3s]" />
       <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-primary [animation-delay:-0.15s]" />
       <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-primary" />
      </span>
     </div>
    )}
   </div>
  </>
 );
}
