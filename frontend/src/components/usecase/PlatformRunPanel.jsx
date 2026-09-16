import { Fragment, useEffect, useRef, useState } from "react";
import { ChevronDown, ChevronRight, ExternalLink, Loader2, Play, RefreshCw } from "lucide-react";
import { runResultRows } from "../../domain/use-case-platform";
import { runOnPlatform } from "../../services/use-case-platform";

/**
 * One run on the platform, from the button to the last row: queue it, poll it, show what the
 * last executor returned. Used twice -- the rehearse screen runs it with `mode="rehearse"`
 * (side effects held, the sample set injected) and the activation screen with `mode="live"`
 * (the real mailbox, the real target). Same rows either way; a live run's created records
 * carry a link into the target.
 */

const PILL = {
 queued: "bg-surface-secondary text-text-secondary",
 running: "bg-status-info-light text-status-info",
 succeeded: "bg-status-success-light text-status-success",
 failed: "bg-status-critical-light text-status-critical",
 blocked: "bg-status-warning-light text-status-warning",
 cancelled: "bg-surface-secondary text-text-muted",
};

/** An API error as a sentence: a preflight refusal names its failed checks. */
export function explain(err) {
 const checks = err?.body?.checks;
 if (Array.isArray(checks)) {
  const failed = checks.filter((c) => !c.passed);
  if (failed.length) return failed.map((c) => `${c.name}: ${c.reason}`).join(" ");
 }
 return err?.detail || err?.message || String(err);
}

export default function PlatformRunPanel({ useCaseId, mode, label, description, beforeRun, disabled, disabledReason, createdAs, buttonLabel, request, onFinished, onAccept, compact }) {
 const [run, setRun] = useState(null);
 const [error, setError] = useState(null);
 const [busy, setBusy] = useState(false);
 const controller = useRef(null);

 useEffect(() => () => controller.current?.abort(), []);

 const start = async () => {
  controller.current?.abort();
  const ac = new AbortController();
  controller.current = ac;
  setBusy(true); setError(null); setRun(null);
  try {
   const id = beforeRun ? await beforeRun({ signal: ac.signal }) : useCaseId;
   const body = typeof request === "function" ? await request({ signal: ac.signal, useCaseId: id }) : request;
   const finished = await runOnPlatform(id, mode, { signal: ac.signal, onTick: setRun, request: body || undefined });
   setRun(finished);
   onFinished?.(finished);
  } catch (err) {
   if (err?.name !== "AbortError") setError(explain(err));
  } finally {
   setBusy(false);
  }
 };

 const rows = run?.result ? runResultRows(run.result) : null;
 const live = mode === "live";

 return (
  <section className={compact ? "" : "rounded-xl border border-border bg-surface p-4"}>
   <div className="flex flex-wrap items-start justify-between gap-3">
    <div>
     {label && <h2 className="text-sm font-bold text-text-primary">{label}</h2>}
     {description && <p className="mt-1 text-sm text-text-secondary">{description}</p>}
    </div>
    <div className="flex items-center gap-2">
     {run && <span className={`rounded-full px-2.5 py-0.5 text-[11px] font-semibold ${PILL[run.status] || PILL.queued}`}>{run.status}{run.mode ? ` · ${run.mode}` : ""}</span>}
     <button type="button" disabled={busy || disabled} onClick={start} title={disabled ? disabledReason : undefined} className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3.5 py-2 text-sm font-semibold text-white hover:bg-primary-hover disabled:cursor-not-allowed disabled:opacity-50">
      {busy ? <Loader2 size={14} className="animate-spin" /> : run ? <RefreshCw size={14} /> : <Play size={14} />}
      {busy ? (run?.status === "running" ? "Running on the platform…" : "Queuing…") : run ? "Run again" : buttonLabel || (live ? "Run now" : "Rehearse on the platform")}
     </button>
    </div>
   </div>
   {disabled && disabledReason && <p className="mt-2 text-xs text-text-muted">{disabledReason}</p>}
   {error && (
    <div className="mt-3 rounded-lg border border-status-critical-border bg-status-critical-light px-3 py-2 text-sm text-status-critical">
     <strong>The platform refused or could not be reached.</strong> {error}
    </div>
   )}
   {run && run.status === "failed" && (
    <div className="mt-3 rounded-lg border border-status-critical-border bg-status-critical-light px-3 py-2 text-sm text-status-critical">
     <strong>The run failed.</strong> {run.blocked_reason || "No reason recorded."}
    </div>
   )}
   {run && !rows && run.status !== "failed" && (
    <p className="mt-3 text-sm text-text-secondary">
     {run.status === "succeeded" ? "Finished with nothing to report: no new messages matched." : `Run ${run.id} is ${run.status}; the page polls every few seconds.`}
    </p>
   )}
   <RunResultView run={run} live={live} createdAs={createdAs} onAccept={onAccept} />
  </section>
 );
}

/** A finished run's result, as the panel and the use-case page render it. */
export function RunResultView({ run, live, createdAs, onAccept }) {
 const [open, setOpen] = useState(null);
 const rows = run?.result ? runResultRows(run.result) : null;
 if (!rows) return null;
 return (
  <>
    <div className="mt-3 space-y-3">
     <div className="grid grid-cols-4 gap-2 text-center">
      <div className="rounded-lg bg-surface-secondary px-3 py-2.5"><p className="text-2xl font-bold tabular-nums text-text-primary">{rows.total}</p><p className="text-[11px] text-text-muted">received</p></div>
      <div className="rounded-lg bg-status-success-light px-3 py-2.5"><p className="text-2xl font-bold tabular-nums text-status-success">{rows.created}</p><p className="text-[11px] text-text-muted">{live ? "created" : "would have been created"}</p></div>
      <div className="rounded-lg bg-status-critical-light px-3 py-2.5"><p className="text-2xl font-bold tabular-nums text-status-critical">{rows.exceptions}</p><p className="text-[11px] text-text-muted">exceptions</p></div>
      <div className="rounded-lg bg-status-warning-light px-3 py-2.5"><p className="text-2xl font-bold tabular-nums text-status-warning">{rows.held}</p><p className="text-[11px] text-text-muted">held for the next poll</p></div>
     </div>
     <p className="text-xs text-text-secondary">
      {run.result?.rehearsal_source === "mailbox" ? "Read from your mailbox as it is now, nothing flagged. " : run.result?.rehearsal_source === "sample set" ? "Over the built-in sample set (no mailbox or no extraction model was reachable). " : ""}{run.summary}{rows.notify ? ` · exceptions ${rows.notify.status === "sent" ? "emailed" : "composed for"} ${(rows.notify.to || []).join(", ")}` : ""}{rows.rollback?.points ? ` · ${rows.rollback.points} rollback point(s) recorded` : ""}{run.provenance_ref ? ` · provenance ${run.provenance_ref}` : ""}
     </p>
     {Array.isArray(run.result?.cases) && run.result.cases.length > 0 && (
      <div className="overflow-x-auto rounded-lg border border-border">
       <table className="w-full min-w-[560px] text-sm">
        <thead className="bg-surface-secondary text-[11px] uppercase tracking-wide text-text-muted"><tr><th className="px-3 py-2 text-left">Case</th><th className="px-3 py-2 text-left">Expected</th><th className="px-3 py-2 text-left">Got</th><th className="px-3 py-2 text-left">Verdict</th><th className="px-3 py-2" /></tr></thead>
        <tbody>
         {run.result.cases.map((v) => (
          <tr key={v.case_id} className="border-t border-border">
           <td className="px-3 py-2 text-text-primary">{v.label}</td>
           <td className="px-3 py-2 font-mono text-xs text-text-secondary">{v.expected_outcome || "—"}</td>
           <td className="px-3 py-2 font-mono text-xs text-text-secondary">{v.outcome}{v.detail ? <span className="ml-1 font-sans text-text-muted">· {v.detail}</span> : ""}</td>
           <td className="px-3 py-2"><span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${v.status === "pass" ? "bg-status-success-light text-status-success" : v.status === "fail" ? "bg-status-critical-light text-status-critical" : "bg-surface-secondary text-text-secondary"}`}>{v.status}</span>{v.status === "fail" && (v.checks || []).filter((c) => !c.passed).map((c) => <span key={c.name} className="ml-1.5 text-[11px] text-text-muted">{c.name}: {c.detail}</span>)}</td>
           <td className="px-3 py-2 text-right">{onAccept && v.status !== "pass" && v.outcome !== "not_run" && <button type="button" onClick={() => onAccept(v.case_id)} className="text-[11px] font-semibold text-primary hover:underline">Accept as expected</button>}</td>
          </tr>
         ))}
        </tbody>
       </table>
      </div>
     )}
     {Object.keys(rows.byClass).length > 0 && (
      <ul className="flex flex-wrap gap-1.5">
       {Object.entries(rows.byClass).map(([k, n]) => <li key={k} className="rounded-md border border-border bg-surface-secondary px-2 py-0.5 font-mono text-[11px] text-text-primary">{k} <span className="text-status-critical">×{n}</span></li>)}
      </ul>
     )}
     <div className="overflow-x-auto rounded-lg border border-border">
      <table className="w-full min-w-[560px] text-sm">
       <thead className="bg-surface-secondary text-[11px] uppercase tracking-wide text-text-muted">
        <tr><th className="px-3 py-2 text-left">From</th><th className="px-3 py-2 text-left">PO</th><th className="px-3 py-2 text-left">Reached</th><th className="px-3 py-2 text-left">Outcome</th></tr>
       </thead>
       <tbody>
        {rows.results.map((r) => (
         <Fragment key={r.id}>
          <tr className="cursor-pointer border-t border-border hover:bg-surface-secondary/60" onClick={() => setOpen(open === r.id ? null : r.id)}>
           <td className="px-3 py-2 text-text-secondary">{open === r.id ? <ChevronDown size={12} className="mr-1 inline" /> : <ChevronRight size={12} className="mr-1 inline" />}{r.from}</td>
           <td className="px-3 py-2 font-mono text-xs text-text-primary">{r.po}</td>
           <td className="px-3 py-2 text-xs text-text-muted">{r.steps[r.steps.length - 1]}</td>
           <td className="px-3 py-2">
            {r.status === "created"
             ? <span className="inline-flex items-center gap-1 rounded-full bg-status-success-light px-2 py-0.5 text-[11px] font-semibold text-status-success">{r.recordId ? `created · ${r.recordStatus || (createdAs === "active" ? "Activated" : "Draft")}` : "would be created"}{r.url && <a href={r.url} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()} className="ml-1 inline-flex items-center gap-0.5 underline"><ExternalLink size={10} /> open</a>}</span>
             : r.status === "held"
              ? <span className="rounded-full bg-status-warning-light px-2 py-0.5 text-[11px] font-semibold text-status-warning">held</span>
              : <span className="rounded-full bg-status-critical-light px-2 py-0.5 font-mono text-[11px] font-semibold text-status-critical">{r.classification}</span>}
           </td>
          </tr>
          {open === r.id && (
           <tr className="border-t border-border-light bg-surface-secondary/40">
            <td colSpan={4} className="px-4 py-3 text-xs text-text-secondary">
             <p className="mb-1"><span className="font-semibold text-text-primary">Subject</span> {r.subject} · <span className="font-semibold text-text-primary">steps</span> {r.steps.join(" → ")}</p>
             {r.detail && <p className="mb-1"><span className="font-semibold text-status-critical">Why</span> {r.detail}</p>}
             {r.account && <p className="mb-1"><span className="font-semibold text-text-primary">Resolved</span> {r.account.name}{r.shipTo?.street ? ` · ship-to ${r.shipTo.street}${r.shipTo.postcode ? `, ${r.shipTo.postcode}` : ""}` : ""} · {r.lines.map((l) => `${l.qty} ${l.productRec.uom || ""} ${l.productRec.name} (${l.score >= 1 ? "exact" : Number(l.score).toFixed(2)})`).join("; ")}</p>}
             {r.payload && <pre className="mt-1 overflow-x-auto rounded-md border border-border bg-surface p-2 font-mono text-[11px] text-text-primary">{JSON.stringify(r.payload, null, 1)}</pre>}
            </td>
           </tr>
          )}
         </Fragment>
        ))}
       </tbody>
      </table>
     </div>
    </div>
  </>
 );
}
