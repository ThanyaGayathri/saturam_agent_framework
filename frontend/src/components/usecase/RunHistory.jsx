import { Fragment, useEffect, useMemo, useState } from "react";
import { ChevronDown, ChevronRight, Clock, ExternalLink, FlaskConical, Play, RefreshCw, RotateCcw } from "lucide-react";
import { RunResultView } from "./PlatformRunPanel";
import { money, runStats } from "../../domain/use-case-platform";
import { relativeTimeFromNow as _rel } from "../../domain/alerts-view";

const rel = (ts) => _rel(typeof ts === "string" ? new Date(ts).getTime() : ts);
const STATUS = { succeeded: "bg-status-success-light text-status-success", failed: "bg-status-critical-light text-status-critical", blocked: "bg-status-warning-light text-status-warning", running: "bg-status-info-light text-status-info", queued: "bg-surface-secondary text-text-secondary", cancelled: "bg-surface-secondary text-text-muted" };
const TRIGGER = { schedule: { Icon: Clock, label: "Scheduled" }, manual: { Icon: Play, label: "Run now" }, regression: { Icon: RotateCcw, label: "Regression" }, activation: { Icon: Play, label: "Activation" } };

/**
 * A use case's runs as what they did (K20): a strip of totals across the history, then one
 * row per run showing what arrived and where it went -- created, held, exceptions -- the
 * order value of what was created, the exception classes, and the created records linked
 * into the target. A poll that found nothing is one quiet line, not a table.
 */
export default function RunHistory({ runs, onRefresh, refreshing = false, refreshedAt = null, createdAs = "draft" }) {
 const [open, setOpen] = useState(null);
 // "Updated 14:32:07" ticks so a fresh read is visibly fresh; a read in flight spins.
 const [, tick] = useState(0);
 useEffect(() => { const t = setInterval(() => tick((n) => n + 1), 5000); return () => clearInterval(t); }, []);
 const age = refreshedAt ? Math.round((Date.now() - refreshedAt.getTime()) / 1000) : null;
 const updated = age == null ? "" : age < 8 ? "updated just now" : age < 60 ? `updated ${age} s ago` : `updated ${refreshedAt.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
 const [showIdle, setShowIdle] = useState(false);
 const stats = useMemo(() => runs.map((r) => ({ run: r, s: runStats(r) })), [runs]);
 const live = stats.filter((x) => x.run.mode === "live");
 const totals = live.reduce((t, { s }) => ({ received: t.received + s.received, created: t.created + s.created, exceptions: t.exceptions + s.exceptions, value: t.value + s.value, hasValue: t.hasValue || s.hasValue }), { received: 0, created: 0, exceptions: 0, value: 0, hasValue: false });
 const objectLabel = [...new Set(stats.flatMap((x) => x.s.objects))].join(" / ") || "record";
 const idleCount = stats.filter((x) => x.s.idle && x.run.status === "succeeded").length;
 const visible = showIdle ? stats : stats.filter((x) => !(x.s.idle && x.run.status === "succeeded"));
 const maxReceived = Math.max(1, ...live.map((x) => x.s.received));

 return (
  <section className="rounded-xl border border-border bg-surface p-4">
   <div className="flex flex-wrap items-center justify-between gap-3">
    <div>
     <h2 className="text-sm font-bold text-text-primary">Run history</h2>
     <p className="text-xs text-text-secondary">{runs.length} run{runs.length === 1 ? "" : "s"} · {live.length} live, {runs.length - live.length} rehearsal{runs.length - live.length === 1 ? "" : "s"}</p>
    </div>
    <div className="flex items-center gap-2 text-xs text-text-muted">
     {updated && <span className="text-[11px]">{updated}</span>}
     <button type="button" onClick={() => onRefresh?.()} disabled={refreshing} aria-busy={refreshing} className="inline-flex items-center gap-1 text-xs text-text-muted hover:text-primary disabled:opacity-70"><RefreshCw size={11} className={refreshing ? "animate-spin" : ""} /> {refreshing ? "Refreshing…" : "Refresh"}</button>
    </div>
   </div>

   {live.length > 0 && (
    <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
     <div className="rounded-lg bg-surface-secondary px-3 py-2.5"><p className="text-2xl font-bold tabular-nums text-text-primary">{totals.received}</p><p className="text-[11px] text-text-muted">{stats.some((x) => x.s.report) ? (stats.some((x) => x.s.compared) ? "KPIs compared, live" : "KPIs read, live") : "documents received, live"}</p></div>
     <div className="rounded-lg bg-status-success-light px-3 py-2.5"><p className="text-2xl font-bold tabular-nums text-status-success">{totals.created}</p><p className="text-[11px] text-text-muted">{stats.some((x) => x.s.report) ? "reports sent" : "records created"}</p></div>
     {totals.hasValue
      ? <div className="rounded-lg bg-status-success-light px-3 py-2.5"><p className="text-2xl font-bold tabular-nums text-status-success">{money(totals.value)}</p><p className="text-[11px] text-text-muted">value of the {objectLabel.toLowerCase()}s created</p></div>
      : <div className="rounded-lg bg-status-success-light px-3 py-2.5"><p className="text-2xl font-bold tabular-nums text-status-success">{objectLabel}</p><p className="text-[11px] text-text-muted">{stats.some((x) => x.s.report) ? "what each run produces" : "what each document becomes"}</p></div>}
     <div className="rounded-lg bg-status-critical-light px-3 py-2.5"><p className="text-2xl font-bold tabular-nums text-status-critical">{stats.some((x) => x.s.report) ? live.filter((x) => x.s.withheld).length : totals.exceptions}</p><p className="text-[11px] text-text-muted">{stats.some((x) => x.s.report) ? "narratives withheld by the grounding check" : "held for a person, with a reason"}</p></div>
    </div>
   )}

   <ul className="mt-3 space-y-2">
    {visible.map(({ run, s }) => {
     const trig = TRIGGER[run.trigger] || TRIGGER.manual;
     const TrigIcon = run.mode === "rehearse" ? FlaskConical : trig.Icon;
     const isOpen = open === run.id;
     const quiet = s.idle && run.status === "succeeded";
     return (
      <li key={run.id} className={`rounded-xl border ${run.status === "failed" ? "border-status-critical-border" : "border-border"} ${quiet ? "bg-surface-secondary/40" : "bg-surface"}`}>
       <button type="button" onClick={() => setOpen(isOpen ? null : run.id)} className="flex w-full flex-wrap items-center gap-x-3 gap-y-1.5 px-3 py-2.5 text-left">
        <span className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full ${run.mode === "rehearse" ? "bg-primary-light text-primary" : run.status === "failed" ? "bg-status-critical-light text-status-critical" : "bg-surface-secondary text-text-secondary"}`}><TrigIcon size={14} /></span>
        <span className="min-w-[11rem]">
         <span className="block text-sm font-semibold text-text-primary">{run.mode === "rehearse" ? "Rehearsal" : trig.label} · {rel(run.started_at)}</span>
         <span className="block text-[11px] text-text-muted">{new Date(run.started_at).toLocaleString()}{s.durationMs != null ? ` · ${Math.max(1, Math.round(s.durationMs / 1000))} s` : ""}</span>
        </span>
        {quiet ? (
         <span className="text-xs text-text-muted">Polled the mailbox; nothing new to process.</span>
        ) : (
         <span className="flex min-w-0 flex-1 flex-wrap items-center gap-x-3 gap-y-1">
          <span className="flex h-2 w-40 overflow-hidden rounded-full bg-surface-secondary" title={`${s.received} received: ${s.created + s.previewed} created, ${s.exceptions} exceptions, ${s.held} held`}>
           <span className="bg-status-success-dot" style={{ width: `${((s.created + s.previewed) / maxReceived) * 100}%` }} />
           <span className="bg-status-critical-dot" style={{ width: `${(s.exceptions / maxReceived) * 100}%` }} />
           <span className="bg-status-warning-dot" style={{ width: `${(s.held / maxReceived) * 100}%` }} />
          </span>
          {s.report && s.scored ? <span className="text-xs text-text-secondary"><strong className="text-text-primary">{s.scored.open.toLocaleString()}</strong> quotes scored, <strong className="text-status-critical">{s.scored.atRiskInRange}</strong> of {s.scored.withinRange} in their window at risk → {s.scored.recommendations} recommendation{s.scored.recommendations === 1 ? "" : "s"}, {s.scored.alerts} of them {s.scored.alertsStatus === "sent" ? <strong className="text-status-success">alerted to reps</strong> : s.scored.alertsStatus === "held" ? "alerts composed" : s.scored.alertsStatus || ""}{s.headline ? <span className="text-text-muted"> · {s.headline}</span> : null}</span>
          : s.report ? <span className="text-xs text-text-secondary"><strong className="text-text-primary">{s.received}</strong> {s.diagnosed ? `KPI${s.received === 1 ? "" : "s"} diagnosed` : s.compared ? "KPIs compared" : "KPIs read"} → report {s.notify === "sent" ? <strong className="text-status-success">emailed</strong> : s.notify === "held" ? "composed, not sent" : "in-app"}{s.headline ? <span className="text-text-muted"> · {s.headline}</span> : null}</span>
          : <span className="text-xs text-text-secondary"><strong className="text-text-primary">{s.received}</strong> in → <strong className="text-status-success">{s.created + s.previewed}</strong> {run.mode === "rehearse" ? "would be created" : "created"}{s.hasValue && s.value ? <span className="text-status-success"> ({money(s.value)})</span> : null} · <strong className="text-status-critical">{s.exceptions}</strong> held{s.held ? ` · ${s.held} over the cap` : ""}</span>}
          {Object.keys(s.byClass).length > 0 && (
           <span className="flex flex-wrap gap-1">{Object.entries(s.byClass).slice(0, 4).map(([k, n]) => <span key={k} className="rounded bg-status-critical-light px-1.5 py-0.5 font-mono text-[10px] text-status-critical">{k}{n > 1 ? ` ×${n}` : ""}</span>)}{Object.keys(s.byClass).length > 4 && <span className="text-[10px] text-text-muted">+{Object.keys(s.byClass).length - 4}</span>}</span>
          )}
         </span>
        )}
        <span className="ml-auto flex items-center gap-2">
         {run.status === "failed" && <span className="text-xs text-status-critical">{run.blocked_reason}</span>}
         <span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${STATUS[run.status] || STATUS.queued}`}>{run.status}</span>
         {isOpen ? <ChevronDown size={14} className="text-text-muted" /> : <ChevronRight size={14} className="text-text-muted" />}
        </span>
       </button>
       {isOpen && (
        <div className="border-t border-border-light px-4 pb-3">
         {s.records.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-1.5 text-[11px]">
           {s.records.map((r) => r.url ? <a key={r.po} href={r.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 rounded-md border border-status-success-border bg-status-success-light px-2 py-0.5 font-semibold text-status-success hover:underline">{r.object || "Record"} {r.po}{r.customer ? ` · ${r.customer}` : ""} <ExternalLink size={10} /></a> : <span key={r.po} className="rounded-md bg-surface-secondary px-2 py-0.5">{r.object || "Record"} {r.po}</span>)}
          </div>
         )}
         {s.notify && <p className="mt-2 text-[11px] text-text-muted">{s.report ? (s.scored ? "Report and alerts" : "Report") : "Exceptions"} {s.notify === "sent" ? "emailed to" : s.notify === "previewed" || s.notify === "held" ? "composed for" : ""} {s.recipients.join(", ")}{run.provenance_ref ? ` · provenance ${run.provenance_ref}` : ""}</p>}
         {run.result ? <RunResultView run={run} live={run.mode === "live"} createdAs={createdAs} /> : <p className="py-2 text-xs text-text-muted">{run.blocked_reason || "No result recorded for this run."}</p>}
        </div>
       )}
      </li>
     );
    })}
   </ul>
   {idleCount > 0 && <button type="button" onClick={() => setShowIdle((v) => !v)} className="mt-2 text-xs font-semibold text-text-muted hover:text-primary">{showIdle ? "Hide" : "Show"} {idleCount} quiet poll{idleCount === 1 ? "" : "s"} that found nothing new</button>}
   {runs.length === 0 && <p className="mt-2 text-sm text-text-muted">No runs yet.</p>}
  </section>
 );
}
