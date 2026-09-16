import { AlertTriangle, BookOpen, Check, Clock, ShieldCheck, Sparkles } from "lucide-react";

/**
 * The "Suggest KPIs" bar on the discovery page, in every state a suggestion run can be in.
 *
 * A run is two frontier-tier model calls and takes a minute or two (#254). A button that only
 * counts seconds gives the person no idea whether that is normal, what is happening, or how to
 * stop it, so the bar becomes a panel while the run is in flight: an expectation up front (the
 * deployment's own recent run times, `typical_duration_seconds`), the stage the run is in
 * (`phase`, a three-step stepper rather than a stopwatch), a Cancel, and a one-line receipt when
 * it lands -- how many, how long, how many adoptable -- with "Suggest again". Four renderings:
 *
 *  - idle:    the dashed bar and the button, as before
 *  - loading: the panel -- header, elapsed, Cancel, stepper
 *  - ready:   the receipt (until dismissed, then the idle bar again)
 *  - error:   the failure, with "Try again"
 *
 * Purely presentational: the page owns the run (start, poll, cancel) and hands the state in.
 */

const STEPS = [
 {
  phase: "reading",
  label: "Reading your data",
  caption: "The harvested schema and the business concepts mapped to it",
  Icon: BookOpen,
 },
 {
  phase: "proposing",
  label: "Proposing candidates",
  caption: "The model is drafting metrics against the standard retail catalogue",
  Icon: Sparkles,
 },
 {
  phase: "checking",
  label: "Checking feasibility",
  caption: "Every proposal is verified against the tables you actually have",
  Icon: ShieldCheck,
 },
];

// Rough share of a run each stage takes, for the active step's time-based bar. The model calls
// are nearly all of it; reading the graph and checking the drafts are seconds each.
const STEP_SHARE = [0.1, 0.8, 0.1];
// A time-based bar never claims to be done: it stops here and waits for the row to say so.
const BAR_CAP = 0.9;
// Past this multiple of the typical duration the header says so, rather than pretending the
// estimate still holds.
const SLOW_AFTER = 1.5;

function formatClock(seconds) {
 const s = Math.max(0, Math.floor(seconds));
 return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function formatDuration(seconds) {
 if (seconds == null || !Number.isFinite(seconds)) return "";
 const s = Math.max(0, Math.round(seconds));
 if (s < 60) return `${s}s`;
 const m = Math.floor(s / 60);
 const rest = s % 60;
 return rest ? `${m}m ${rest}s` : `${m}m`;
}

function formatTime(iso) {
 const d = iso ? new Date(iso) : null;
 if (!d || Number.isNaN(d.getTime())) return null;
 return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

/** Where the active step's bar should sit, 0..BAR_CAP, from elapsed time against the estimate. */
function activeFill(stepIndex, elapsed, typical) {
 if (!typical || typical <= 0) return null; // no history: the bar is indeterminate
 const before = STEP_SHARE.slice(0, stepIndex).reduce((a, b) => a + b, 0) * typical;
 const length = Math.max(STEP_SHARE[stepIndex] * typical, 1);
 return Math.min(Math.max((elapsed - before) / length, 0), BAR_CAP);
}

function Step({ step, state, fill }) {
 const done = state === "done";
 const active = state === "active";
 const StepIcon = step.Icon;
 return (
  <div className="flex flex-col gap-2">
   <div
    className={`h-1 overflow-hidden rounded-sm ${done ? "bg-status-success" : "bg-border"}`}
    role="progressbar"
    aria-label={step.label}
    aria-valuemin={0}
    aria-valuemax={100}
    aria-valuenow={done ? 100 : active && fill != null ? Math.round(fill * 100) : undefined}
   >
    {active && (
     <div
      className={`h-full rounded-sm bg-status-info ${fill == null ? "w-2/5 animate-pulse" : "transition-[width] duration-1000 ease-linear"}`}
      style={fill == null ? undefined : { width: `${Math.round(fill * 100)}%` }}
     />
    )}
   </div>
   <div
    className={`flex items-center gap-2 text-[13px] font-semibold ${active || done ? "text-text-primary" : "text-text-muted"}`}
   >
    {done ? (
     <Check size={16} className="shrink-0 text-status-success" />
    ) : active ? (
     <Clock size={16} className="shrink-0 animate-pulse text-status-info" />
    ) : (
     <StepIcon size={16} className="shrink-0 text-text-muted" />
    )}
    {step.label}
   </div>
   <div className="text-xs text-text-muted">{step.caption}</div>
  </div>
 );
}

function StartButton({ onClick, label = "Suggest KPIs" }) {
 return (
  <button
   type="button"
   onClick={onClick}
   className="inline-flex shrink-0 items-center gap-2 rounded-lg bg-primary px-3 py-1.5 text-xs font-semibold text-white transition-colors hover:bg-primary-hover"
  >
   <Sparkles size={13} />
   {label}
  </button>
 );
}

function QuietButton({ onClick, children }) {
 return (
  <button
   type="button"
   onClick={onClick}
   className="rounded-lg border border-border-light bg-surface px-3 py-1.5 text-xs font-semibold text-text-secondary transition-colors hover:bg-surface-secondary"
  >
   {children}
  </button>
 );
}

function IdleBar({ onStart }) {
 return (
  <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-dashed border-border-light bg-surface-secondary px-4 py-3">
   <span className="flex items-center gap-2 text-sm text-text-secondary">
    <Sparkles size={15} className="shrink-0 text-primary" />
    Ask the model what else your connected data could measure.
   </span>
   <StartButton onClick={onStart} />
  </div>
 );
}

/** The request a steered run was started with ("Add KPI" -> AI generated, contract 1.2.3),
 * quoted back so the person sees the run is answering *their* words, not the standing ask. */
function AskLine({ ask }) {
 if (!ask) return null;
 return (
  <p className="mt-1 text-[13px] leading-snug text-text-secondary">
   For: <q className="italic text-text-primary">{ask}</q>
  </p>
 );
}

function RunningPanel({ phase, elapsed, typicalDuration, ask, onCancel }) {
 // A `queued` row, or a `running` one that has not reported a stage yet, reads as the first
 // stage: from where the person sits, the run has started and is reading.
 const activeIndex = Math.max(
  STEPS.findIndex((s) => s.phase === phase),
  0
 );
 const fill = activeFill(activeIndex, elapsed, typicalDuration);
 const slow = typicalDuration > 0 && elapsed > typicalDuration * SLOW_AFTER;

 let expectation;
 if (slow) {
  expectation = (
   <>
    Taking longer than usual — still working. It stops on its own if it exceeds the
    deployment's limit.
   </>
  );
 } else if (typicalDuration > 0) {
  expectation = (
   <>
    Usually takes about{" "}
    <strong className="font-semibold text-text-primary">{formatDuration(typicalDuration)}</strong>{" "}
    here, going by your recent runs. You can keep using the page.
   </>
  );
 } else {
  expectation = <>Usually takes a minute or two. You can keep using the page.</>;
 }

 return (
  <div
   className="overflow-hidden rounded-xl border border-status-info-border bg-surface shadow-sm"
   role="status"
   aria-live="polite"
  >
   <div className="flex flex-wrap items-start justify-between gap-4 px-5 pb-3.5 pt-4">
    <div className="flex items-start gap-3">
     <div className="flex h-[34px] w-[34px] shrink-0 items-center justify-center rounded-lg bg-status-info-light">
      <Sparkles size={18} className="animate-pulse text-status-info" />
     </div>
     <div>
      <div className="text-[15px] font-semibold text-text-primary">
       {ask ? "Finding KPIs for your request" : "Finding KPIs in your data"}
      </div>
      <AskLine ask={ask} />
      <div className="mt-0.5 text-[13px] text-text-secondary">{expectation}</div>
     </div>
    </div>
    <div className="flex shrink-0 items-center gap-3.5">
     <span className="text-[13px] tabular-nums text-text-secondary">{formatClock(elapsed)} elapsed</span>
     <QuietButton onClick={onCancel}>Cancel</QuietButton>
    </div>
   </div>
   <div className="grid grid-cols-1 gap-3 px-5 pb-4 sm:grid-cols-3">
    {STEPS.map((step, index) => (
     <Step
      key={step.phase}
      step={step}
      state={index < activeIndex ? "done" : index === activeIndex ? "active" : "pending"}
      fill={index === activeIndex ? fill : null}
     />
    ))}
   </div>
  </div>
 );
}

function Receipt({ suggestions, runId, startedAt, finishedAt, clientSeconds, ask, onDismiss, onStart }) {
 const total = suggestions.length;
 const ready = suggestions.filter((s) => s?.feasibility?.verdict === "ready").length;
 const notReady = total - ready;
 const started = startedAt ? new Date(startedAt).getTime() : NaN;
 const finished = finishedAt ? new Date(finishedAt).getTime() : NaN;
 const seconds =
  Number.isFinite(started) && Number.isFinite(finished) && finished >= started
   ? (finished - started) / 1000
   : clientSeconds;
 const took = formatDuration(seconds);
 const at = formatTime(finishedAt);
 const shortId = runId ? `${String(runId).slice(0, 7)}…` : null;

 return (
  <div
   className="flex flex-wrap items-center justify-between gap-4 rounded-xl border border-status-success-border bg-surface px-5 py-3"
   role="status"
  >
   <div className="flex flex-wrap items-center gap-3">
    <div className="flex h-[30px] w-[30px] shrink-0 items-center justify-center rounded-lg bg-status-success-light">
     <Check size={16} className="text-status-success" />
    </div>
    <div className="text-sm text-text-primary">
     {total === 0 ? (
      <>
       <strong className="font-semibold">No KPIs proposed</strong>
       {took ? ` in ${took}` : ""}.
      </>
     ) : (
      <>
       <strong className="font-semibold">
        {total} KPI{total === 1 ? "" : "s"} proposed
       </strong>
       {took ? ` in ${took}` : ""} — {ready} ready to adopt
       {notReady > 0 ? `, ${notReady} need${notReady === 1 ? "s" : ""} data you haven't connected` : ""}.
      </>
     )}
    </div>
    <span className="text-xs text-text-muted">
     {at ? `Finished ${at}` : "Just now"}
     {shortId ? ` · run ${shortId}` : ""}
    </span>
    {ask && (
     <span className="basis-full text-xs text-text-secondary">
      For: <q className="italic text-text-primary">{ask}</q>
     </span>
    )}
   </div>
   <div className="flex items-center gap-2.5">
    <QuietButton onClick={onDismiss}>Dismiss</QuietButton>
    <StartButton onClick={onStart} label="Suggest again" />
   </div>
  </div>
 );
}

function Failure({ error, phase, onStart }) {
 // The backend keeps `phase` on a failed row so it can say where the run got to (see
 // `mark_phase`'s only-from-`running` guard) -- surface it when we have it rather than
 // leaving that guarantee unread on the frontend.
 const step = STEPS.find((s) => s.phase === phase);
 return (
  <div
   className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-status-critical-border bg-status-critical-light px-4 py-2.5 text-sm text-status-critical"
   role="alert"
  >
   <span className="flex items-center gap-2">
    <AlertTriangle size={15} className="shrink-0" />
    {step ? `Couldn't get suggestions while ${step.label.toLowerCase()}: ${error}` : `Couldn't get suggestions: ${error}`}
   </span>
   <StartButton onClick={onStart} label="Try again" />
  </div>
 );
}

export default function SuggestionRunPanel({ run, elapsed, onStart, onCancel, onDismiss }) {
 if (run.status === "loading") {
  return (
   <RunningPanel
    phase={run.phase}
    elapsed={elapsed}
    typicalDuration={run.typicalDuration}
    ask={run.ask}
    onCancel={onCancel}
   />
  );
 }
 if (run.status === "error") return <Failure error={run.error} phase={run.phase} onStart={onStart} />;
 if (run.status === "ready" && run.receipt) {
  return (
   <Receipt
    suggestions={run.suggestions}
    runId={run.runId}
    startedAt={run.serverStartedAt}
    finishedAt={run.serverFinishedAt}
    clientSeconds={run.clientSeconds}
    ask={run.ask}
    onDismiss={onDismiss}
    onStart={onStart}
   />
  );
 }
 return <IdleBar onStart={onStart} />;
}
