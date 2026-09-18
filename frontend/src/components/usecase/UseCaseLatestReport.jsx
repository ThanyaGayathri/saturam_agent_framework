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
 const when = run.finished_at || run.started_at;
 const stamp = `${run.mode === "live" ? "live run" : "rehearsal"} · ${when ? new Date(when).toLocaleString() : ""}`;
 const verdict = <span className={`inline-flex items-center gap-1 text-[11px] font-semibold ${report.grounding?.passed === false ? "text-status-warning" : "text-status-success"}`}>{report.grounding?.passed === false ? <><ShieldAlert size={12} /> narrative withheld</> : <><ShieldCheck size={12} /> {report.grounding?.checked ?? 0} figures checked</>}</span>;
 return (
  <section className="mb-4 space-y-3">
   {/* The first row: the KPI (previous → now), the trend, where the change sits. Then why. */}
   {d && <DiagnosisTiles diagnosis={d} />}
   <div className="rounded-xl border border-primary/30 bg-primary-light/20 px-4 py-3">
   <div className="flex flex-wrap items-center justify-between gap-2">
    <p className="text-[11px] font-semibold uppercase tracking-wide text-primary">{d ? "Why it changed" : "Latest report"} <span className="font-normal normal-case tracking-normal text-text-muted">· {stamp}</span></p>
    {verdict}
   </div>
   <p className="mt-1.5 text-sm font-semibold text-text-primary">{report.explanation?.headline || report.headline}</p>
   {d && <div className="mt-3"><DiagnosisInsight diagnosis={d} compact hideHero hideMovers /></div>}
   <button type="button" onClick={() => navigate(`/use-cases/platform/${encodeURIComponent(useCase.id)}`)} className="mt-2 inline-flex items-center gap-1 text-xs font-semibold text-primary hover:underline">Open the run <ExternalLink size={11} /></button>
   </div>
  </section>
 );
}
