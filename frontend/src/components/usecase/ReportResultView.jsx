import { useState } from "react";
import { ExternalLink, ShieldCheck, ShieldAlert } from "lucide-react";
import { reportResult } from "../../domain/use-case-analytics";
import DiagnosisInsight from "./DiagnosisInsight";

/**
 * An Inform run's result (K23): the report as the recipient sees it, the grounding verdict
 * on its narrative, the KPI comparison table, and where it went. The HTML is the platform's
 * own (`render_report`), shown in a sandboxed frame so the email and the screen are the same
 * document.
 */
const fmt = (v, unit) => {
 if (v == null) return "—";
 const n = Number(v);
 if (unit === "%") return `${n.toFixed(1)}%`;
 if (String(unit).toUpperCase() === "USD") return `$${n.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
 return `${Number.isInteger(n) ? n.toLocaleString() : n.toFixed(1)}${unit && unit !== "%" ? ` ${unit}` : ""}`;
};

export default function ReportResultView({ run, live }) {
 const [view, setView] = useState("report");
 const r = reportResult(run?.result);
 if (!r) return null;
 const withBaseline = r.kpis.filter((k) => k.baseline != null).length;
 return (
  <div className="mt-3 space-y-3">
   <div className="grid grid-cols-4 gap-2 text-center">
    <div className="rounded-lg bg-surface-secondary px-3 py-2.5"><p className="text-2xl font-bold tabular-nums text-text-primary">{r.diagnosis ? 1 : r.kpis.length}</p><p className="text-[11px] text-text-muted">{r.diagnosis ? "KPI diagnosed" : r.compared ? "KPIs compared" : "KPIs read"}</p></div>
    {r.compared || r.diagnosis
     ? <div className="rounded-lg bg-surface-secondary px-3 py-2.5"><p className="text-2xl font-bold tabular-nums text-text-primary">{r.diagnosis ? (r.diagnosis.ranking || []).filter((e) => e.drivers?.length).length : withBaseline}</p><p className="text-[11px] text-text-muted">{r.diagnosis ? "dimensions with a named slice" : `with a baseline${r.backfilled.length ? ` (${r.backfilled.length} computed for it)` : ""}`}</p></div>
     : <div className="rounded-lg bg-surface-secondary px-3 py-2.5"><p className="text-sm font-bold text-text-primary">Not compared</p><p className="text-[11px] text-text-muted">the numbers as they are{r.computedNow.length ? ` (${r.computedNow.length} computed for this date)` : ""}</p></div>}
    <div className={`rounded-lg px-3 py-2.5 ${r.grounding?.passed === false ? "bg-status-warning-light" : "bg-status-success-light"}`}><p className={`text-2xl font-bold tabular-nums ${r.grounding?.passed === false ? "text-status-warning" : "text-status-success"}`}>{r.grounding?.checked ?? 0}</p><p className="text-[11px] text-text-muted">{r.grounding?.passed === false ? "figures checked · narrative withheld" : "figures checked, all in the numbers"}</p></div>
    <div className="rounded-lg bg-surface-secondary px-3 py-2.5"><p className="text-sm font-bold text-text-primary">{r.delivery?.status === "sent" ? "Emailed" : r.delivery?.status === "held" ? "Composed, not sent" : r.delivery?.status === "no_recipient" ? "In-app only" : r.delivery?.status || "—"}</p><p className="text-[11px] text-text-muted">{(r.delivery?.to || []).join(", ") || (live ? "no address" : "rehearsal")}</p></div>
   </div>

   <p className="flex flex-wrap items-center gap-2 text-xs text-text-secondary">
    {r.grounding?.passed === false
     ? <span className="inline-flex items-center gap-1 rounded-full bg-status-warning-light px-2 py-0.5 font-semibold text-status-warning"><ShieldAlert size={12} /> narrative withheld: {(r.grounding.unsupported || []).join(", ")} not in the numbers</span>
     : <span className="inline-flex items-center gap-1 rounded-full bg-status-success-light px-2 py-0.5 font-semibold text-status-success"><ShieldCheck size={12} /> every figure in the narrative is one the comparison carried</span>}
    <span>as of {r.asOf}{r.compared ? ` vs ${r.comparisonLabel}` : ""}</span>
    {run.provenance_ref && <span>· provenance {run.provenance_ref}</span>}
   </p>

   <div className="flex w-fit items-center gap-1 rounded-lg border border-border bg-surface-secondary p-0.5 text-xs font-semibold">
    {[["report", "The report"], ...(r.diagnosis ? [["why", "Where the change sits"]] : []), ["numbers", "The numbers"]].map(([v, label]) => (
     <button key={v} type="button" onClick={() => setView(v)} aria-pressed={view === v} className={`rounded-md px-3 py-1 ${view === v ? "bg-surface text-text-primary shadow-sm" : "text-text-secondary hover:text-text-primary"}`}>{label}</button>
    ))}
   </div>

   {view === "report" && (
    <div className="overflow-hidden rounded-lg border border-border bg-surface">
     <iframe title={r.title} srcDoc={r.html} sandbox="" className="h-[640px] w-full bg-[#f9fafb]" />
    </div>
   )}

   {view === "why" && r.diagnosis && (
    <div className="rounded-lg border border-border bg-surface px-4 py-3"><DiagnosisInsight diagnosis={r.diagnosis} /></div>
   )}

   {view === "numbers" && (
    <div className="overflow-x-auto rounded-lg border border-border">
     <table className="w-full min-w-[560px] text-sm">
      <thead className="bg-surface-secondary text-[11px] uppercase tracking-wide text-text-muted">
       <tr><th className="px-3 py-2 text-left">KPI</th><th className="px-3 py-2 text-right">Now</th><th className="px-3 py-2 text-right">Baseline</th><th className="px-3 py-2 text-right">Change</th><th className="px-3 py-2 text-left">Breakdown</th></tr>
      </thead>
      <tbody>
       {r.kpis.map((k) => (
        <tr key={k.id} className="border-t border-border">
         <td className="px-3 py-2"><span className="font-semibold text-text-primary">{k.name}</span><span className="ml-1.5 font-mono text-[10px] text-text-muted">{k.id}</span></td>
         <td className="px-3 py-2 text-right tabular-nums text-text-primary">{fmt(k.current, k.unit)}</td>
         <td className="px-3 py-2 text-right tabular-nums text-text-secondary">{fmt(k.baseline, k.unit)}</td>
         <td className={`px-3 py-2 text-right tabular-nums ${k.delta == null ? "text-text-muted" : k.delta >= 0 ? "text-status-success" : "text-status-critical"}`}>{k.delta == null ? (k.reason || "—") : `${k.delta > 0 ? "+" : ""}${fmt(k.delta, k.unit === "%" ? "pts" : k.unit)}${k.deltaPct != null && k.unit !== "%" ? ` (${k.deltaPct > 0 ? "+" : ""}${k.deltaPct}%)` : ""}`}</td>
         <td className="px-3 py-2 text-xs text-text-muted">{k.rows ? `${k.rows} rows by ${k.grain.replace(/_/g, " ")}` : ""}</td>
        </tr>
       ))}
      </tbody>
     </table>
    </div>
   )}

   {r.delivery?.subject && <p className="text-[11px] text-text-muted">Subject: {r.delivery.subject}{run.result?.dashboard_url ? <> · <a href={run.result.dashboard_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-0.5 text-primary hover:underline">dashboard <ExternalLink size={10} /></a></> : null}</p>}
  </div>
 );
}
