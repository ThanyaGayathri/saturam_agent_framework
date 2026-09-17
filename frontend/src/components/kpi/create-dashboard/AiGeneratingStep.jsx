import { AlertTriangle, Check, Sparkles } from "lucide-react";

/**
 * Step 3 of the AI path: shown while `useDashboardSuggestionRun` is polling, and again (with
 * `error` set) if the run failed - the same status/error split `KpiDiscoveryPage.jsx`'s
 * suggestion-run state machine renders for `POST /kpis/suggestions`. `stage` is the backend's
 * own progress word for the run, so each line below is a fact the server has finished, not an
 * animation.
 */

const STAGES = [
 { key: "reading", label: "Reading the metrics and the tables behind them" },
 { key: "drafting", label: "Drafting charts for the purpose" },
 { key: "guarding", label: "Checking each query against the warehouse read guard" },
 { key: "previewing", label: "Previewing each chart with real rows" },
];

const ORDER = STAGES.map((s) => s.key);

export default function AiGeneratingStep({ purpose, stage, error, onBack, onRetry }) {
 const reached = stage ? ORDER.indexOf(stage) : -1;

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
     <ul className="flex-1 divide-y divide-dashed divide-border">
      {STAGES.map((s, i) => {
       const done = reached > i || stage === "done";
       const running = reached === i;
       return (
        <li key={s.key} className="flex items-center gap-3 py-2 text-sm">
         <span
          className={`flex h-[18px] w-[18px] shrink-0 items-center justify-center rounded-full ${
           done
            ? "border border-status-success/40 bg-status-success/10 text-status-success"
            : running
              ? "animate-spin border-2 border-primary border-r-transparent"
              : "border border-border-light"
          }`}
         >
          {done ? <Check size={11} strokeWidth={3} /> : null}
         </span>
         <span className={done || running ? "text-text-primary" : "text-text-muted"}>{s.label}</span>
        </li>
       );
      })}
      <li className="pt-2 text-xs text-text-muted">Usually under a minute.</li>
     </ul>
    )}
   </div>
  </>
 );
}
