import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ExternalLink, ShieldCheck, ShieldAlert } from "lucide-react";
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

/** The top of the ranked list, as the report shows it. */
function NextBestActions({ recommendations }) {
 const recs = (recommendations?.recommendations || []).slice(0, 5);
 if (!recs.length) return null;
 return (
  <div className="mt-3 grid gap-1.5">
   <p className="text-[11px] font-semibold uppercase tracking-wide text-text-muted">Next best actions · ranked by what can still be saved</p>
   {recs.map((r) => { const a = r.action || {}; const ctx = r.context || {}; return (
    <div key={r.id} className="grid grid-cols-[minmax(0,1fr)_auto_minmax(0,1.4fr)] items-start gap-2 rounded-lg border border-border bg-surface px-3 py-2 text-xs">
     <div className="min-w-0"><p className="truncate font-semibold text-text-primary">{r.subject_label}</p><p className="truncate text-text-secondary">{r.account_label} · {ctx.age_days} of {ctx.usual_days} days</p></div>
     <span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${LEVEL[r.risk_level] || ""}`}>{r.risk_level} · {Math.round((r.risk_score || 0) * 100)}%</span>
     <div className="min-w-0"><p className="font-semibold text-text-primary">{a.title}</p><p className="truncate text-text-secondary">{a.because}</p></div>
    </div>
   ); })}
  </div>
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
   <button type="button" onClick={() => navigate(`/use-cases/platform/${encodeURIComponent(useCase.id)}`)} className="mt-2 inline-flex items-center gap-1 text-xs font-semibold text-primary hover:underline">Open the run <ExternalLink size={11} /></button>
   </div>
  </section>
 );
}
