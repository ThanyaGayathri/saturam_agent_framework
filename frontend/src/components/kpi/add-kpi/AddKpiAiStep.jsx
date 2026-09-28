import { useEffect, useRef, useState } from "react";
import { AlertTriangle, ArrowLeft, Sparkles, Zap } from "lucide-react";
import { startKpiSuggestionRun, fetchKpiSuggestionRun } from "../../../services/kpis-api";
import { CONFIDENCE, SUGGESTION_RATIONALE_LABEL, SUGGESTION_VALUE_LABEL } from "../KpiDiscovery";
import { FIELD_INPUT } from "../ModeCard";

const INPUT = `mt-1 ${FIELD_INPUT}`;

/** In order, `KpiSuggestionRunPhase` (contract): what the agent is doing right now. */
const PHASE_LABEL = {
 reading: "Reading what your data can back…",
 proposing: "Drafting KPIs…",
 checking: "Checking feasibility against your data…",
};

/** How often to re-read the run while it's still going. Foreground and watched (unlike the
 * standing-suggestions poll on the discovery page itself), so this can afford to be brisk. */
const POLL_MS = 2500;
// A little past the backend's own `retail_suggestion_timeout_seconds` (180s default) -- past
// that bound the run is reported `failed` by the read side, not left `running` forever, so a
// screen giving up first would show "taking a while" over a run that is, in fact, already done.
const GIVE_UP_AFTER_MS = 210_000;

/**
 * "Add KPI" -> AI-assisted: a free-text `ask`, the same K12 suggestion agent every automatic
 * post-harvest proposal already runs (contract 1.2.3's `ask`, added for exactly this screen --
 * see `KpiSuggestionRunRequest.ask`'s own description). No `source_id` is sent, so this reads
 * the deployment's own warehouse (not a connected source's still-separate scope) and the run
 * never joins `GET /kpis/suggestions/current`'s standing list -- it is read back once, by id.
 *
 * Unlike the old, removed "AI generated KPI" tab (F1/#234), a chosen draft is not adopted
 * directly: `onDraftSelected` hands the raw `KpiSuggestion` up to `AddKpiModal`, which passes
 * it to `AddKpiManualStep` as a prefill. The reasoning that removed the old tab (suggestions
 * from a harvest need no button; this is a person asking for a KPI a harvest never proposed) is
 * still true, but a request in someone's own words is exactly the case where the agent's first
 * guess is worth a look before it becomes a governed metric -- the manual form's server-side
 * validation is the same gate a hand-typed formula goes through, and the person can still fix
 * anything the agent got wrong before saving.
 */
export default function AddKpiAiStep({ onBack, onDraftSelected }) {
 const [ask, setAsk] = useState("");
 const [run, setRun] = useState(null);
 const [starting, setStarting] = useState(false);
 const [error, setError] = useState(null);
 const pollRef = useRef(null);
 const abortRef = useRef(null);

 useEffect(
  () => () => {
   clearInterval(pollRef.current);
   abortRef.current?.abort();
  },
  []
 );

 const stopPolling = () => {
  clearInterval(pollRef.current);
  pollRef.current = null;
 };

 const poll = (runId) => {
  stopPolling();
  const startedAt = Date.now();
  pollRef.current = setInterval(async () => {
   try {
    const next = await fetchKpiSuggestionRun(runId);
    setRun(next);
    if (next.status === "succeeded" || next.status === "failed") {
     stopPolling();
     return;
    }
    if (Date.now() - startedAt > GIVE_UP_AFTER_MS) {
     stopPolling();
     setError("This is taking longer than usual. It may still finish -- check back in a bit, or try again.");
    }
   } catch (err) {
    if (err?.name === "AbortError") return;
    stopPolling();
    setError(err?.detail || err?.message || "Lost track of the run.");
   }
  }, POLL_MS);
 };

 const generate = async () => {
  if (!ask.trim() || starting) return;
  setError(null);
  setRun(null);
  setStarting(true);
  const controller = new AbortController();
  abortRef.current = controller;
  try {
   const started = await startKpiSuggestionRun({ ask, signal: controller.signal });
   if (controller.signal.aborted) return;
   setRun(started);
   poll(started.id);
  } catch (err) {
   if (err?.name === "AbortError") return;
   setError(err?.detail || err?.message || "Couldn't start the run.");
  } finally {
   if (!controller.signal.aborted) setStarting(false);
  }
 };

 const retry = () => {
  setRun(null);
  setError(null);
 };

 const working = starting || run?.status === "queued" || run?.status === "running";
 const suggestions = run?.status === "succeeded" ? run.result?.suggestions || [] : [];
 const notes = run?.status === "succeeded" ? run.result?.notes : null;

 return (
  <>
   {onBack && !working && (
    <button
     type="button"
     onClick={onBack}
     className="mb-3 inline-flex items-center gap-1 text-xs font-semibold text-text-secondary hover:text-text-primary"
    >
     <ArrowLeft size={12} />
     Back
    </button>
   )}

   {!run && (
    <div>
     <label htmlFor="kpi-ask" className="block text-xs font-semibold text-text-primary">
      What do you want to measure?
     </label>
     <textarea
      id="kpi-ask"
      autoFocus
      rows={3}
      maxLength={2000}
      value={ask}
      onChange={(e) => setAsk(e.target.value)}
      disabled={starting}
      placeholder="e.g. Share of quotes that turn into a closed-won deal within 30 days, by brand"
      className={`${INPUT} resize-none`}
      onKeyDown={(e) => {
       if ((e.metaKey || e.ctrlKey) && e.key === "Enter") generate();
      }}
     />
     <p className="mt-1 text-[11px] leading-relaxed text-text-muted">
      In your own words -- the agent proposes a metric your connected data can actually back,
      judged the same way an automatic suggestion is, and says so if it can't.
     </p>
    </div>
   )}

   {error && (
    <p role="alert" className="mt-3 flex items-start gap-2 rounded-lg border border-status-critical-border bg-status-critical-light px-3 py-2 text-xs text-status-critical">
     <AlertTriangle size={13} className="mt-0.5 shrink-0" />
     <span>{error}</span>
    </p>
   )}

   {working && (
    <div className="mt-4 flex items-center gap-2.5 rounded-lg border border-border bg-surface-secondary px-3 py-2.5 text-xs text-text-secondary">
     <span className="h-3.5 w-3.5 shrink-0 animate-spin rounded-full border-2 border-primary/30 border-t-primary" aria-hidden />
     <span className="min-w-0">
      {PHASE_LABEL[run?.phase] || "Starting…"}
      {run?.typical_duration_seconds ? (
       <span className="text-text-muted"> Usually about {Math.round(run.typical_duration_seconds)}s.</span>
      ) : null}
     </span>
    </div>
   )}

   {run?.status === "succeeded" && (
    <div className="mt-4">
     {suggestions.length === 0 ? (
      <p className="rounded-lg border border-dashed border-border-light bg-surface-secondary px-3 py-2.5 text-xs text-text-secondary">
       {notes || "The agent found nothing your connected data can back for this yet."}
      </p>
     ) : (
      <>
       <p className="mb-2 text-xs font-semibold text-text-primary">
        {suggestions.length === 1 ? "One draft -- review it below." : `${suggestions.length} drafts -- pick one to review.`}
       </p>
       <div className="space-y-2">
        {suggestions.map((s) => {
         const confidence = CONFIDENCE[s.confidence];
         return (
          <button
           key={s.identifier}
           type="button"
           onClick={() => onDraftSelected(s)}
           className="flex w-full flex-col gap-1.5 rounded-lg border border-border bg-surface-default p-3 text-left transition-colors hover:border-primary/50 hover:bg-primary-light/30"
          >
           <div className="flex items-start justify-between gap-2">
            <span className="text-sm font-semibold text-text-primary">{s.name}</span>
            {confidence && (
             <span className={`shrink-0 rounded-md px-1.5 py-0.5 text-[10px] font-semibold ${confidence.cls}`}>
              {confidence.label}
             </span>
            )}
           </div>
           <p className="text-xs text-text-secondary">{s.description}</p>
           {s.rationale && (
            <p className="text-[11px] text-text-muted">
             <span className="font-semibold">{SUGGESTION_RATIONALE_LABEL}:</span> {s.rationale}
            </p>
           )}
           {s.business_value && (
            <p className="text-[11px] text-text-muted">
             <span className="font-semibold">{SUGGESTION_VALUE_LABEL}:</span> {s.business_value}
            </p>
           )}
           <span className="mt-1 inline-flex items-center gap-1 text-[11px] font-semibold text-primary">
            <Zap size={11} />
            Use this draft
           </span>
          </button>
         );
        })}
       </div>
       {notes && <p className="mt-2 text-[11px] text-text-muted">{notes}</p>}
      </>
     )}
     <button
      type="button"
      onClick={retry}
      className="mt-3 text-xs font-semibold text-text-secondary hover:text-text-primary"
     >
      Ask again
     </button>
    </div>
   )}

   {run?.status === "failed" && (
    <div className="mt-4">
     <p className="rounded-lg border border-status-critical-border bg-status-critical-light px-3 py-2.5 text-xs text-status-critical">
      {run.error || "The run failed."}
     </p>
     <button
      type="button"
      onClick={retry}
      className="mt-3 text-xs font-semibold text-text-secondary hover:text-text-primary"
     >
      Try again
     </button>
    </div>
   )}

   {!run && (
    <div className="mt-5 flex items-center justify-end gap-2 border-t border-border pt-4">
     <button
      type="button"
      disabled={!ask.trim() || starting}
      onClick={generate}
      className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-primary-hover disabled:cursor-not-allowed disabled:opacity-50"
     >
      <Sparkles size={14} />
      {starting ? "Starting…" : "Generate"}
     </button>
    </div>
   )}
  </>
 );
}
