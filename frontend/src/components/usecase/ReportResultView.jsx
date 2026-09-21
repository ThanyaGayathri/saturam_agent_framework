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

const money = (v) => { const n = Number(v) || 0; return n >= 1_000_000 ? `$${(n / 1_000_000).toFixed(2)}M` : n >= 10_000 ? `$${Math.round(n / 1000)}K` : `$${n.toLocaleString(undefined, { maximumFractionDigits: 0 })}`; };
const LEVEL = { high: "bg-status-critical-light text-status-critical", medium: "bg-status-warning-light text-status-warning", low: "bg-status-success-light text-status-success" };

/** The run's own recommendations and alerts (K26), as the recommend stage produced them. */
function ActionsView({ r }) {
 const recs = r.recommendations?.recommendations || [];
 const alerts = r.recommendations?.alerts || [];
 const plays = Object.entries(r.recommendations?.plays || {}).sort((a, b) => b[1] - a[1]);
 const byRep = alerts.reduce((m, a) => { const k = a.owner || "—"; m[k] = (m[k] || 0) + 1; return m; }, {});
 return (
  <div className="space-y-3">
   <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-text-secondary">
    <span><strong className="text-text-primary">{recs.length}</strong> recommendations · <strong className="text-text-primary">{money(r.recommendations?.summary?.value_at_stake)}</strong> at stake · ranked by what can still be saved</span>
    {plays.map(([code, n]) => <span key={code} className="rounded-full bg-primary-light px-2 py-0.5 text-[11px] font-semibold text-primary">{code.replace(/_/g, " ").toLowerCase().replace(/^\w/, (c) => c.toUpperCase())} · {n}</span>)}
   </p>
   <div className="overflow-x-auto rounded-lg border border-border">
    <table className="w-full min-w-[720px] text-sm">
     <thead className="bg-surface-secondary text-[11px] uppercase tracking-wide text-text-muted"><tr><th className="px-3 py-2 text-left">Quote · account</th><th className="px-3 py-2 text-center">Risk</th><th className="px-3 py-2 text-left">Play · why</th><th className="px-3 py-2 text-left">Owner</th></tr></thead>
     <tbody>
      {recs.map((x) => { const ctx = x.context || {}; const acct = ctx.account || {}; const a = x.action || {}; return (
       <tr key={x.id} className="border-t border-border align-top">
        <td className="px-3 py-2"><p className="font-semibold text-text-primary">{x.subject_label}</p><p className="text-xs text-text-secondary">{x.account_label}{ctx.territory ? ` · ${ctx.territory}` : ""} · {ctx.age_days} of {ctx.usual_days} days{ctx.stalled ? " · past usual" : ""}</p><p className="text-[11px] text-text-muted">RFM {acct.segment || "—"}{acct.at_risk ? " · account at risk of churn" : ""}</p></td>
        <td className="px-3 py-2 text-center"><span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${LEVEL[x.risk_level] || ""}`}>{x.risk_level}</span><p className="text-xs tabular-nums text-text-secondary">{Math.round((x.risk_score || 0) * 100)}%</p></td>
        <td className="px-3 py-2"><p className="font-semibold text-text-primary">{a.title}</p><p className="text-xs text-text-secondary">{a.because}</p><p className="text-[11px] text-text-muted">{(x.drivers || []).filter((d) => !(a.because || "").includes(d.text)).slice(0, 2).map((d) => d.text).join("; ")}</p></td>
        <td className="px-3 py-2 text-xs text-text-secondary">{a.owner}{a.sla_hours ? <><br />{a.sla_hours >= 24 ? `within ${Math.round(a.sla_hours / 24)}d` : `within ${a.sla_hours}h`}</> : null}</td>
       </tr>
      ); })}
     </tbody>
    </table>
   </div>
   {alerts.length > 0 && <p className="text-xs text-text-secondary"><strong className="text-text-primary">{alerts.length}</strong> high-risk quotes in one alert digest, a section per rep: {Object.entries(byRep).sort((a, b) => b[1] - a[1]).map(([rep, n]) => `${rep} (${n})`).join(", ")}{r.alerts?.status === "held" ? " — composed, not sent (rehearsal)" : r.alerts?.status === "sent" ? " — emailed" : ""}.</p>}
  </div>
 );
}

/** How much to trust the ranking: the model's holdout evaluation, its calibration and its strongest coefficients. */
function ModelView({ risk }) {
 const m = risk.model || {};
 const cal = m.calibration || [];
 const coefs = m.top_coefficients || [];
 return (
  <div className="grid gap-3 md:grid-cols-2">
   <div className="rounded-lg border border-border bg-surface px-4 py-3 text-sm">
    <p className="font-semibold text-text-primary">Trained at this run on the pipeline's own history</p>
    <ul className="mt-1.5 space-y-1 text-xs text-text-secondary">
     <li><strong className="text-text-primary">{m.closed_quotes}</strong> closed quotes, seen at ages 0, 7, 14, 21 and 28 days ({m.training_rows} training rows); a quote counts as converted when it closed won within its category's usual time to close (the 90th percentile).</li>
     <li>The newest quarter by quote date ({m.holdout_rows} rows from {m.holdout_from}) was held back and scored before the final fit: <strong className="text-text-primary">AUC {m.auc}</strong> (floor 0.65), Brier {m.brier}, base rate {m.base_rate}.</li>
     <li>AUC {m.auc} means the model ranks a converting quote above a non-converting one {Math.round((m.auc || 0) * 100)}% of the time. Logistic regression: every score is the sum of its drivers, which is why each row can say why.</li>
     <li>Risk = 1 − the chance of converting in time; high ≥ {risk.levels?.high ?? 0.7}, medium ≥ {risk.levels?.medium ?? 0.4}. Model version {m.version}.</li>
    </ul>
    {cal.length > 0 && <div className="mt-2"><p className="text-[11px] font-semibold uppercase tracking-wide text-text-muted">Calibration on the held-out rows</p><table className="mt-1 w-full text-xs"><thead className="text-text-muted"><tr><th className="text-left font-medium">predicted</th><th className="text-right font-medium">observed</th><th className="text-right font-medium">rows</th></tr></thead><tbody>{cal.map((b, i) => <tr key={i}><td className="tabular-nums text-text-secondary">{b.from?.toFixed(2)}–{b.to?.toFixed(2)} → {Number(b.predicted).toFixed(2)}</td><td className="text-right tabular-nums text-text-primary">{Number(b.observed).toFixed(2)}</td><td className="text-right tabular-nums text-text-muted">{b.rows}</td></tr>)}</tbody></table></div>}
   </div>
   <div className="rounded-lg border border-border bg-surface px-4 py-3 text-sm">
    <p className="font-semibold text-text-primary">What moves the score most</p>
    <p className="text-[11px] text-text-muted">Standardised coefficients; negative pushes towards risk.</p>
    <ul className="mt-1.5 space-y-1">
     {coefs.map((c) => { const w = Math.min(100, Math.abs(c.coefficient) / Math.max(...coefs.map((x) => Math.abs(x.coefficient)), 0.01) * 100); return (
      <li key={c.feature} className="grid grid-cols-[minmax(0,1fr)_80px_48px] items-center gap-2 text-xs"><span className="truncate font-mono text-text-secondary">{c.feature.replace(/=$/, "= (none)")}</span><span className="h-2 rounded bg-surface-secondary"><span className={`block h-2 rounded ${c.coefficient < 0 ? "bg-status-critical" : "bg-status-success"}`} style={{ width: `${w}%` }} /></span><span className={`text-right tabular-nums ${c.coefficient < 0 ? "text-status-critical" : "text-status-success"}`}>{c.coefficient > 0 ? "+" : ""}{c.coefficient}</span></li>
     ); })}
    </ul>
   </div>
  </div>
 );
}

export default function ReportResultView({ run, live }) {
 const [view, setView] = useState("report");
 const r = reportResult(run?.result);
 if (!r) return null;
 const withBaseline = r.kpis.filter((k) => k.baseline != null).length;
 return (
  <div className="mt-3 space-y-3">
   <div className="grid grid-cols-4 gap-2 text-center">
    {r.risk ? <>
     <div className="rounded-lg bg-surface-secondary px-3 py-2.5"><p className="text-2xl font-bold tabular-nums text-text-primary">{(r.risk.summary?.open_quotes || 0).toLocaleString()}</p><p className="text-[11px] text-text-muted">open quotes scored · {(r.risk.summary?.stalled || 0).toLocaleString()} past the usual time</p></div>
     <div className="rounded-lg bg-status-critical-light px-3 py-2.5"><p className="text-2xl font-bold tabular-nums text-status-critical">{r.risk.summary?.at_risk_within_range || 0}</p><p className="text-[11px] text-text-muted">of {r.risk.summary?.within_range || 0} still in their window are at risk · {money(r.risk.summary?.value_at_risk_within_range)}</p></div>
     <div className="rounded-lg bg-primary-light px-3 py-2.5"><p className="text-2xl font-bold tabular-nums text-primary">{r.recommendations?.summary?.recommendations || 0} <span className="text-base font-semibold text-text-secondary">({r.risk.summary?.high_within_range ?? r.recommendations?.summary?.alerts ?? 0} high + {r.risk.summary?.medium_within_range ?? 0} medium)</span></p><p className="text-[11px] text-text-muted">recommendations · the high-risk ones also alerted to reps{r.recommendations?.held ? " · held (rehearsal)" : r.recommendations?.written ? ` · ${r.recommendations.written.created} new, ${r.recommendations.written.refreshed} refreshed, ${r.recommendations.written.kept} kept` : ""}</p></div>
    </> : <>
    <div className="rounded-lg bg-surface-secondary px-3 py-2.5"><p className="text-2xl font-bold tabular-nums text-text-primary">{r.diagnosis ? 1 : r.kpis.length}</p><p className="text-[11px] text-text-muted">{r.diagnosis ? "KPI diagnosed" : r.compared ? "KPIs compared" : "KPIs read"}</p></div>
    {r.compared || r.diagnosis
     ? <div className="rounded-lg bg-surface-secondary px-3 py-2.5"><p className="text-2xl font-bold tabular-nums text-text-primary">{r.diagnosis ? (r.diagnosis.ranking || []).filter((e) => e.drivers?.length).length : withBaseline}</p><p className="text-[11px] text-text-muted">{r.diagnosis ? "dimensions with a named slice" : `with a baseline${r.backfilled.length ? ` (${r.backfilled.length} computed for it)` : ""}`}</p></div>
     : <div className="rounded-lg bg-surface-secondary px-3 py-2.5"><p className="text-sm font-bold text-text-primary">Not compared</p><p className="text-[11px] text-text-muted">the numbers as they are{r.computedNow.length ? ` (${r.computedNow.length} computed for this date)` : ""}</p></div>}
    <div className={`rounded-lg px-3 py-2.5 ${r.grounding?.passed === false ? "bg-status-warning-light" : "bg-status-success-light"}`}><p className={`text-2xl font-bold tabular-nums ${r.grounding?.passed === false ? "text-status-warning" : "text-status-success"}`}>{r.grounding?.checked ?? 0}</p><p className="text-[11px] text-text-muted">{r.grounding?.passed === false ? "figures checked · narrative withheld" : "figures checked, all in the numbers"}</p></div>
    </>}
    {r.risk && <div className="rounded-lg bg-surface-secondary px-3 py-2.5"><p className="text-sm font-bold text-text-primary">{r.delivery?.status === "sent" ? "Report emailed" : r.delivery?.status === "held" ? "Report composed" : r.delivery?.status === "no_recipient" ? "In-app only" : r.delivery?.status || "—"}</p><p className="text-[11px] text-text-muted">{r.alerts?.status === "sent" ? `alert digest emailed · ${r.recommendations?.summary?.alerts || 0} quotes, ${r.recommendations?.summary?.owners || 0} reps` : r.alerts?.status === "held" ? `alert digest composed · ${r.recommendations?.summary?.alerts || 0} quotes, ${r.recommendations?.summary?.owners || 0} reps` : r.alerts?.status === "not_requested" ? "no alerts asked for" : r.alerts?.status || ""} · grounding {r.grounding?.passed === false ? "withheld" : `${r.grounding?.checked ?? 0} figures ok`}</p></div>}
    {!r.risk && <div className="rounded-lg bg-surface-secondary px-3 py-2.5"><p className="text-sm font-bold text-text-primary">{r.delivery?.status === "sent" ? "Emailed" : r.delivery?.status === "held" ? "Composed, not sent" : r.delivery?.status === "no_recipient" ? "In-app only" : r.delivery?.status || "—"}</p><p className="text-[11px] text-text-muted">{(r.delivery?.to || []).join(", ") || (live ? "no address" : "rehearsal")}</p></div>}
   </div>

   <p className="flex flex-wrap items-center gap-2 text-xs text-text-secondary">
    {r.grounding?.passed === false
     ? <span className="inline-flex items-center gap-1 rounded-full bg-status-warning-light px-2 py-0.5 font-semibold text-status-warning"><ShieldAlert size={12} /> narrative withheld: {(r.grounding.unsupported || []).join(", ")} not in the numbers</span>
     : <span className="inline-flex items-center gap-1 rounded-full bg-status-success-light px-2 py-0.5 font-semibold text-status-success"><ShieldCheck size={12} /> every figure in the narrative is one the comparison carried</span>}
    <span>as of {r.asOf}{r.compared ? ` vs ${r.comparisonLabel}` : ""}</span>
    {run.provenance_ref && <span>· provenance {run.provenance_ref}</span>}
   </p>

   <div className="flex w-fit items-center gap-1 rounded-lg border border-border bg-surface-secondary p-0.5 text-xs font-semibold">
    {[["report", "The report"], ...(r.diagnosis ? [["why", "Where the change sits"]] : []), ...(r.risk ? [["actions", "Next best actions"], ["model", "The model"]] : []), ["numbers", "The numbers"]].map(([v, label]) => (
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

   {view === "actions" && r.risk && <ActionsView r={r} />}
   {view === "model" && r.risk && <ModelView risk={r.risk} />}

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
