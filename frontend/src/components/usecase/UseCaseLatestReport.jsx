import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { useNavigate } from "react-router-dom";
import { Download, ExternalLink, FileText, ShieldCheck, ShieldAlert, X } from "lucide-react";
import { listUseCaseRuns, listUseCases } from "../../services/use-cases-api";
import { reportResult } from "../../domain/use-case-analytics";
import DiagnosisInsight, { DiagnosisTiles } from "./DiagnosisInsight";

/**
 * The latest report of the use case that owns a dashboard (K25): its headline sentence,
 * when it ran, whether the narrative passed the grounding check, and -- for a diagnosis --
 * where the change sits. Found by the dashboard's name, which is the use case's title
 * (`compose_dashboard`). Renders nothing until a run exists.
 */

const money = (v) => { const n = Number(v) || 0; return n >= 1_000_000 ? `$${(n / 1_000_000).toFixed(2)}M` : n >= 10_000 ? `$${Math.round(n / 1000)}K` : `$${n.toLocaleString(undefined, { maximumFractionDigits: 0 })}`; };
const LEVEL = { high: "bg-status-critical-light text-status-critical", medium: "bg-status-warning-light text-status-warning", low: "bg-status-success-light text-status-success" };

/** The at-risk kind's first row (K26): what can still be saved, what has stalled, the accounts at risk -- and how the use case has done so far. */
function AtRiskTiles({ risk, recommendations, outcomes }) {
 const s = risk.summary || {};
 const rs = recommendations?.summary || {};
 const high = outcomes?.precision?.high;
 const tiles = [
  { label: "Act now", value: s.at_risk_within_range ?? 0, note: `quotes at risk, still in range · ${money(s.value_at_risk_within_range)}`, tone: "text-status-critical" },
  { label: "Already stalled", value: s.stalled ?? 0, note: `of ${(s.open_quotes ?? 0).toLocaleString()} open quotes past the usual time to close`, tone: "text-text-primary" },
  { label: "Accounts at risk", value: s.at_risk_accounts ?? 0, note: `of ${(s.accounts_scored ?? 0).toLocaleString()} · no quote in ${risk.churn_days} days`, tone: "text-status-warning" },
  { label: "Handed to Decision Intelligence", value: `${rs.recommendations ?? 0} + ${rs.alerts ?? 0}`, note: high?.named ? `recommendations + alerts · so far ${high.did_not_convert} of ${high.named} named high risk did not convert` : `recommendations + alerts · ${money(rs.value_at_stake)} at stake`, tone: "text-primary" },
 ];
 return (
  <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
   {tiles.map((t) => (
    <div key={t.label} className="rounded-xl border border-border bg-surface px-4 py-3">
     <p className="text-[11px] font-semibold uppercase tracking-wide text-text-muted">{t.label}</p>
     <p className={`mt-1 text-2xl font-bold tabular-nums ${t.tone}`}>{typeof t.value === "number" ? t.value.toLocaleString() : t.value}</p>
     <p className="text-[11px] text-text-muted">{t.note}</p>
    </div>
   ))}
  </div>
 );
}

/** The whole ranked list, as the report shows it: every recommendation the run made, with
 *  the play and why; the plays tallied above it. */
const matches = (r, q) => {
 if (!q) return true;
 const ctx = r.context || {};
 const hay = `${r.subject_label} ${r.account_label} ${r.owner} ${ctx.territory || ""} ${ctx.category || ""} ${ctx.segment || ""} ${(r.action || {}).title || ""} ${r.risk_level}`.toLowerCase();
 return q.toLowerCase().split(/\s+/).filter(Boolean).every((w) => hay.includes(w));
};

function NextBestActions({ recommendations }) {
 const all = recommendations?.recommendations || [];
 const [open, setOpen] = useState(true);
 const [query, setQuery] = useState("");
 if (!all.length) return null;
 const recs = all.filter((r) => matches(r, query));
 const plays = Object.entries(recommendations?.plays || {}).sort((a, b) => b[1] - a[1]);
 const value = all.reduce((s, r) => s + (Number(r.value_at_stake) || 0), 0);
 return (
  <div className="mt-3">
   <button type="button" onClick={() => setOpen((v) => !v)} className="flex w-full flex-wrap items-center gap-x-3 gap-y-1 text-left">
    <span className="text-[11px] font-semibold uppercase tracking-wide text-text-muted">Next best actions · {all.length} recommendations · {money(value)} at stake · ranked by what can still be saved</span>
    <span className="ml-auto text-[11px] font-semibold text-primary">{open ? "Hide" : "Show all"}</span>
   </button>
   <div className="mt-1 flex flex-wrap items-center gap-1.5">
    {plays.map(([code, n]) => <span key={code} className="rounded-full bg-primary-light px-2 py-0.5 text-[11px] font-semibold text-primary">{code.replace(/_/g, " ").toLowerCase().replace(/^\w/, (c) => c.toUpperCase())} · {n}</span>)}
    {open && <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Find an account, rep, territory, category or play…" aria-label="Find in the list" className="ml-auto w-full max-w-[320px] rounded-md border border-border bg-surface px-2.5 py-1 text-xs text-text-primary placeholder:text-text-muted focus:border-primary focus:outline-none" />}
   </div>
   {open && query && <p className="mt-1 text-[11px] text-text-muted">{recs.length} of {all.length} match "{query}"{recs.length ? ` · ${money(recs.reduce((s, r) => s + (Number(r.value_at_stake) || 0), 0))} at stake` : ""}</p>}
   {open && (
    <div className="mt-2 grid max-h-[520px] gap-1.5 overflow-y-auto pr-1">
     {recs.map((r, i) => { const a = r.action || {}; const ctx = r.context || {}; const acct = ctx.account || {}; return (
      <div key={r.id} className="grid grid-cols-[1.5rem_minmax(0,1fr)_auto_minmax(0,1.4fr)] items-start gap-2 rounded-lg border border-border bg-surface px-3 py-2 text-xs">
       <span className="pt-0.5 tabular-nums text-text-muted">{all.indexOf(r) + 1}</span>
       <div className="min-w-0"><p className="truncate font-semibold text-text-primary">{r.subject_label}</p><p className="truncate text-text-secondary">{r.account_label}{ctx.territory ? ` · ${ctx.territory}` : ""} · {ctx.age_days} of {ctx.usual_days} days{ctx.stalled ? " · past usual" : ""}</p><p className="truncate text-[11px] text-text-muted">RFM {acct.segment || "-"}{acct.at_risk ? " · account at risk of churn" : ""}{r.owner ? ` · rep ${r.owner}` : ""}</p></div>
       <span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${LEVEL[r.risk_level] || ""}`}>{r.risk_level} · {Math.round((r.risk_score || 0) * 100)}%</span>
       <div className="min-w-0"><p className="font-semibold text-text-primary">{a.title}</p><p className="text-text-secondary">{a.because}</p><p className="text-[11px] text-primary">{a.owner}{a.sla_hours ? ` · within ${a.sla_hours >= 24 ? `${Math.round(a.sla_hours / 24)}d` : `${a.sla_hours}h`}` : ""}</p></div>
      </div>
     ); })}
    </div>
   )}
  </div>
 );
}

/** The report itself -- the same document the email carries -- in a drawer from the
 *  right, the dashboard still in view behind it; Esc or the backdrop closes it. Download
 *  as PDF opens the report in its own window and the browser's print dialog offers
 *  "Save as PDF". */
function ReportDrawer({ report, runHref, name }) {
 const [open, setOpen] = useState(false);
 useEffect(() => {
  if (!open) return undefined;
  const onKey = (e) => { if (e.key === "Escape") setOpen(false); };
  window.addEventListener("keydown", onKey);
  const previous = document.body.style.overflow;
  document.body.style.overflow = "hidden";
  return () => { window.removeEventListener("keydown", onKey); document.body.style.overflow = previous; };
 }, [open]);
 if (!report?.html) return null;
 const [printing, setPrinting] = useState(false);
 // Print-to-PDF without a popup: the report in a hidden frame on this page, the
 // browser's print dialog opened on that frame ("Save as PDF" is its destination).
 // A popup would be blocked, and window.open with noopener returns null by spec.
 const download = () => {
  if (printing) return;
  setPrinting(true);
  const frame = document.createElement("iframe");
  frame.setAttribute("aria-hidden", "true");
  frame.style.cssText = "position:fixed;right:0;bottom:0;width:0;height:0;border:0;visibility:hidden";
  // The PDF's name and page header come from the top document's title while the
  // dialog is open: the use case's name and the run date, not the app's.
  const fileName = `${name || report.title || "Report"} - ${report.asOf || ""}`.trim();
  const appTitle = document.title;
  frame.onload = () => {
   const w = frame.contentWindow;
   if (!w) { frame.remove(); setPrinting(false); return; }
   setTimeout(() => {
    document.title = fileName;
    try { w.focus(); w.print(); } finally {
     // print() returns once the dialog closes (it is modal); the frame can go then.
     document.title = appTitle;
     setPrinting(false);
     setTimeout(() => frame.remove(), 1000);
    }
   }, 200);
  };
  frame.srcdoc = `<!doctype html><html><head><meta charset="utf-8"><title>${fileName.replace(/</g, "&lt;")}</title><style>@page{size:A4;margin:14mm} body{margin:0;-webkit-print-color-adjust:exact;print-color-adjust:exact}</style></head><body>${report.html}</body></html>`;
  document.body.appendChild(frame);
 };
 return (
  <>
   <button type="button" onClick={() => setOpen(true)} className="mt-2 inline-flex items-center gap-1 rounded-md border border-border bg-surface px-2.5 py-1 text-xs font-semibold text-text-secondary hover:text-text-primary"><FileText size={12} /> View the report</button>
   {open && createPortal(
    <div className="fixed inset-0 z-[100]" role="dialog" aria-modal="true" aria-label={report.title}>
     <div className="absolute inset-0 bg-black/30" onClick={() => setOpen(false)} />
     <aside className="absolute inset-y-0 right-0 flex w-full max-w-[820px] flex-col border-l border-border bg-surface shadow-2xl">
      <div className="flex items-center justify-between gap-2 border-b border-border px-4 py-2.5">
       <div className="min-w-0">
        <p className="text-[11px] font-semibold uppercase tracking-wide text-primary">The report · as sent</p>
        <p className="truncate text-sm font-semibold text-text-primary">{name || report.title}</p>
       </div>
       <div className="flex shrink-0 items-center gap-2">
        <button type="button" onClick={download} disabled={printing} className="inline-flex items-center gap-1 rounded-md bg-primary px-2.5 py-1 text-xs font-semibold text-white hover:opacity-90 disabled:opacity-60"><Download size={12} /> {printing ? "Preparing…" : "Download as PDF"}</button>
        {runHref && <a href={runHref} className="inline-flex items-center gap-1 rounded-md border border-border px-2.5 py-1 text-xs font-semibold text-text-secondary hover:text-text-primary">Open the run <ExternalLink size={11} /></a>}
        <button type="button" onClick={() => setOpen(false)} aria-label="Close" className="rounded-md border border-border px-2 py-1 text-xs font-semibold text-text-secondary hover:text-text-primary"><X size={12} /></button>
       </div>
      </div>
      <iframe title={report.title} srcDoc={report.html} sandbox="" className="h-full w-full flex-1 bg-[#f9fafb]" />
     </aside>
    </div>,
    document.body,
   )}
  </>
 );
}

export default function UseCaseLatestReport({ title }) {
 const navigate = useNavigate();
 const [state, setState] = useState(null); // { useCase, run, report }
 useEffect(() => {
  let cancelled = false;
  (async () => {
   try {
    const rows = await listUseCases();
    const list = Array.isArray(rows) ? rows : rows?.items || [];
    const useCase = list.find((u) => u.title === title);
    if (!useCase) return;
    const runs = await listUseCaseRuns(useCase.id);
    const run = (runs || []).find((r) => r.status === "succeeded" && r.result?.report);
    if (!cancelled && run) setState({ useCase, run, report: reportResult(run.result) });
   } catch { /* the dashboard stands on its own */ }
  })();
  return () => { cancelled = true; };
 }, [title]);
 if (!state) return null;
 const { useCase, run, report } = state;
 const d = report.diagnosis;
 const risk = report.risk;
 const when = run.finished_at || run.started_at;
 const stamp = `${run.mode === "live" ? "live run" : "rehearsal"} · ${when ? new Date(when).toLocaleString() : ""}`;
 const verdict = <span className={`inline-flex items-center gap-1 text-[11px] font-semibold ${report.grounding?.passed === false ? "text-status-warning" : "text-status-success"}`}>{report.grounding?.passed === false ? <><ShieldAlert size={12} /> narrative withheld</> : <><ShieldCheck size={12} /> {report.grounding?.checked ?? 0} figures checked</>}</span>;
 return (
  <section className="mb-4 space-y-3">
   {/* The first row: the KPI (previous → now), the trend, where the change sits. Then why. */}
   {d && <DiagnosisTiles diagnosis={d} />}
   {risk && <AtRiskTiles risk={risk} recommendations={report.recommendations} outcomes={run.result?.outcomes} />}
   <div className="rounded-xl border border-primary/30 bg-primary-light/20 px-4 py-3">
   <div className="flex flex-wrap items-center justify-between gap-2">
    <p className="text-[11px] font-semibold uppercase tracking-wide text-primary">{d ? "Why it changed" : risk ? "Pipeline at risk · the briefing" : "Latest report"} <span className="font-normal normal-case tracking-normal text-text-muted">· {stamp}</span></p>
    {verdict}
   </div>
   <p className="mt-1.5 text-sm font-semibold text-text-primary">{report.explanation?.headline || report.headline}</p>
   {d && <div className="mt-3"><DiagnosisInsight diagnosis={d} compact hideHero hideMovers /></div>}
   {risk && <NextBestActions recommendations={report.recommendations} />}
   {risk && <ReportDrawer report={report} name={useCase.title} runHref={`/use-cases/platform/${encodeURIComponent(useCase.id)}`} />}
   <button type="button" onClick={() => navigate(`/use-cases/platform/${encodeURIComponent(useCase.id)}`)} className="mt-2 inline-flex items-center gap-1 text-xs font-semibold text-primary hover:underline">Open the run <ExternalLink size={11} /></button>
   </div>
  </section>
 );
}
