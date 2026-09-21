import { AlertTriangle, Loader2, RefreshCw } from "lucide-react";

/**
 * What the KPIs page says about the suggestions it does not have yet -- the two states where a
 * connected source's KPI suggestions are not simply on the page.
 *
 * KPI suggestions are proposed for a source automatically, when its harvest ends, so there is no
 * "Suggest KPIs" bar to start one and no stepper to watch one (that panel, its receipt and its
 * cancel are gone). What is left is what a person needs to know without having asked:
 *
 *  - {@link SuggestionsGenerating}: a run is still working for a source they just connected. No
 *    stage, no clock, no cancel -- nothing here to act on, and a fabricated estimate would be the
 *    wrong thing to show. The page reads the standing list again on its own; the suggestions
 *    simply appear.
 *  - {@link SourceRunFailure}: the run failed and nothing has replaced it. Nobody clicked anything
 *    to watch it fail, so this is where they find out, with the reason and one way to try again.
 *
 * Purely presentational: the page owns the polling and the retry.
 */

/** A run is still working. `sourceName` is null for a run over the deployment's own warehouse. */
export function SuggestionsGenerating({ sourceName }) {
 return (
  <div
   className="flex items-center gap-2.5 rounded-xl border border-status-info-border bg-status-info-light px-4 py-2.5 text-sm text-status-info"
   role="status"
   data-testid="suggestions-generating"
  >
   <Loader2 size={15} className="shrink-0 animate-spin" />
   <span>
    Suggesting KPIs{sourceName ? ` for ${sourceName}` : ""}. This can take a couple of minutes; they
    will appear here when they are ready.
   </span>
  </div>
 );
}

/** A suggestion run that failed on its own -- after a source's harvest, with nobody watching --
 * and has not been replaced by a later success (`GET /kpis/suggestions/current`'s `failed`).
 * `sourceName` is null for a run over the deployment's own warehouse. `busy` and `retryError` are
 * the retry's own state: it can be refused (a source whose harvest has not finished is a 409). */
export function SourceRunFailure({ error, sourceName, onRetry, busy = false, retryError = null }) {
 const reason = error || "The run failed without saying why.";
 return (
  <div
   className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-status-critical-border bg-status-critical-light px-4 py-2.5 text-sm text-status-critical"
   role="alert"
   data-testid="suggestion-run-failure"
  >
   <span className="flex flex-col gap-0.5">
    <span className="flex items-center gap-2">
     <AlertTriangle size={15} className="shrink-0" />
     {sourceName ? `Couldn't suggest KPIs for ${sourceName}: ${reason}` : `Couldn't suggest KPIs: ${reason}`}
    </span>
    {retryError && <span className="pl-[23px] text-xs">{retryError}</span>}
   </span>
   <button
    type="button"
    onClick={onRetry}
    disabled={busy}
    className="inline-flex shrink-0 items-center gap-2 rounded-lg bg-primary px-3 py-1.5 text-xs font-semibold text-white transition-colors hover:bg-primary-hover disabled:cursor-not-allowed disabled:opacity-60"
   >
    <RefreshCw size={13} className={busy ? "animate-spin" : ""} />
    Try again
   </button>
  </div>
 );
}
