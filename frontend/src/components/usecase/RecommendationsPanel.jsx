import { useCallback, useEffect, useMemo, useState } from "react";
import { Check, Inbox, RefreshCw, Search, Undo2, X } from "lucide-react";
import { listRecommendations, setRecommendationStatus } from "../../services/use-cases-api";
import EmptyState from "../ui/EmptyState";

/**
 * The hand-off from an at-risk use case to Decision Intelligence (K26): the Recommendation
 * records its runs wrote, ranked by value at stake, with the play, who owns it and why.
 * Accept and dismiss write the same `status` a consuming use case would; a decided record is
 * kept as decided by later runs, an open one is refreshed.
 */
const money = (v) => { const n = Number(v) || 0; return n >= 1_000_000 ? `$${(n / 1_000_000).toFixed(2)}M` : n >= 10_000 ? `$${Math.round(n / 1000)}K` : `$${n.toLocaleString(undefined, { maximumFractionDigits: 0 })}`; };
const LEVEL = { high: "bg-status-critical-light text-status-critical", medium: "bg-status-warning-light text-status-warning", low: "bg-status-success-light text-status-success" };
const STATUS = { open: "bg-primary-light text-primary", accepted: "bg-status-success-light text-status-success", actioned: "bg-status-success-light text-status-success", dismissed: "bg-surface-secondary text-text-muted", expired: "bg-surface-secondary text-text-muted" };
const FILTERS = [["open", "Open"], ["accepted", "Accepted"], ["dismissed", "Dismissed"], ["expired", "Horizon ended"], ["", "All"]];

export default function RecommendationsPanel({ useCaseId, by = "", compact = false }) {
 const [status, setStatus] = useState("open");
 const [kind, setKind] = useState("recommendation");
 const [data, setData] = useState({ items: [], counts: {} });
 const [loading, setLoading] = useState(true);
 const [error, setError] = useState(null);
 const [busy, setBusy] = useState(null);
 const load = useCallback(async () => {
  setLoading(true); setError(null);
  try { setData(await listRecommendations({ useCaseId, status: status || undefined, kind: kind || undefined, limit: 200 })); }
  catch (e) { setError(e?.message || String(e)); }
  finally { setLoading(false); }
 }, [useCaseId, status, kind]);
 useEffect(() => { load(); }, [load]);
 const decide = async (rec, next, note = "") => {
  setBusy(rec.id);
  try { await setRecommendationStatus(rec.id, { status: next, note, by }); await load(); }
  catch (e) { setError(e?.message || String(e)); }
  finally { setBusy(null); }
 };
 const [query, setQuery] = useState("");
 const all = data.items || [];
 const items = all.filter((r) => {
  if (!query) return true;
  const ctx = r.context || {};
  const hay = `${r.subject_label} ${r.account_label} ${r.owner} ${ctx.territory || ""} ${ctx.category || ""} ${ctx.segment || ""} ${(r.action || {}).title || ""} ${r.risk_level}`.toLowerCase();
  return query.toLowerCase().split(/\s+/).filter(Boolean).every((w) => hay.includes(w));
 });
 const counts = data.counts || {};
 const totals = useMemo(() => ({ value: items.reduce((a, r) => a + (Number(r.value_at_stake) || 0), 0), plays: items.reduce((m, r) => { const t = r.action?.title; if (t) m[t] = (m[t] || 0) + 1; return m; }, {}) }), [items]);

 return (
  <div className="space-y-3">
   <div className="flex flex-wrap items-center justify-between gap-2">
    <div className="flex flex-wrap items-center gap-1.5">
     {FILTERS.map(([v, label]) => (
      <button key={v || "all"} type="button" aria-pressed={status === v} onClick={() => setStatus(v)} className={`rounded-full px-2.5 py-1 text-xs font-semibold ${status === v ? "bg-primary text-white" : "bg-surface-secondary text-text-secondary hover:text-text-primary"}`}>{label}{v && counts[v] != null ? <span className="ml-1 opacity-80">{counts[v]}</span> : null}</button>
     ))}
     <span className="mx-1 h-4 w-px bg-border" />
     {[["recommendation", "Recommendations"], ["alert", "Alerts"], ["", "Both"]].map(([v, label]) => (
      <button key={v || "both"} type="button" aria-pressed={kind === v} onClick={() => setKind(v)} className={`rounded-full px-2.5 py-1 text-xs font-semibold ${kind === v ? "bg-surface text-text-primary shadow-sm ring-1 ring-border" : "text-text-secondary hover:text-text-primary"}`}>{label}</button>
     ))}
    </div>
    <div className="flex items-center gap-2">
     <span className="relative inline-block"><Search size={14} aria-hidden className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-text-muted" /><input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Find an account, rep, territory…" aria-label="Find in the list" className="w-[260px] rounded-lg border border-border-light bg-surface py-2 pl-9 pr-3 text-sm text-text-primary outline-none transition placeholder:text-text-muted focus:border-primary focus:ring-1 focus:ring-primary" /></span>
     <button type="button" onClick={load} className="inline-flex items-center gap-1 text-xs font-semibold text-text-secondary hover:text-text-primary"><RefreshCw size={12} className={loading ? "animate-spin" : ""} /> Refresh</button>
    </div>
   </div>

   {items.length > 0 && (
    <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-text-secondary">
     <span><strong className="text-text-primary">{items.length}</strong>{query ? ` of ${all.length}` : ""} record{items.length === 1 ? "" : "s"} · <strong className="text-text-primary">{money(totals.value)}</strong> at stake</span>
     {Object.entries(totals.plays).sort((a, b) => b[1] - a[1]).slice(0, 5).map(([t, n]) => <span key={t} className="rounded-full bg-primary-light px-2 py-0.5 text-[11px] font-semibold text-primary">{t} · {n}</span>)}
    </p>
   )}

   {error && <p className="rounded-lg bg-status-critical-light px-3 py-2 text-xs text-status-critical">{error}</p>}
   {!loading && !error && items.length === 0 && <div className="rounded-xl border border-dashed border-border"><EmptyState icon={<Inbox size={20} aria-hidden className="text-text-muted" />} title={status === "open" ? "No open recommendations. A live run writes them; a rehearsal holds them." : "Nothing here."} /></div>}

   <div className="grid gap-2">
    {items.slice(0, compact ? 8 : 200).map((r) => {
     const ctx = r.context || {}; const acct = ctx.account || {}; const a = r.action;
     const pct = `${Math.round((Number(r.risk_score) || 0) * 100)}%`;
     const sla = a?.sla_hours ? (a.sla_hours >= 24 ? `within ${Math.round(a.sla_hours / 24)}d` : `within ${a.sla_hours}h`) : null;
     return (
      <div key={r.id} className="grid gap-2 rounded-xl border border-border bg-surface px-3 py-2.5 md:grid-cols-[minmax(0,1.1fr)_auto_minmax(0,1.6fr)_auto] md:items-start">
       <div className="min-w-0">
        <p className="truncate text-sm font-semibold text-text-primary">{r.subject_label}</p>
        <p className="truncate text-xs text-text-secondary">{r.account_label}{ctx.territory ? ` · ${ctx.territory}` : ""}{r.owner ? ` · rep ${r.owner}` : ""}</p>
        <p className="text-[11px] text-text-muted">{ctx.age_days != null ? `${ctx.age_days} of ${ctx.usual_days} days${ctx.stalled ? " · past usual" : ""}` : ""}{acct.segment ? ` · RFM ${acct.segment}` : ""}{acct.at_risk ? <span className="ml-1 font-semibold text-status-warning">· account at risk of churn</span> : null}</p>
       </div>
       <div className="flex flex-row items-center gap-1.5 md:flex-col md:items-center">
        <span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${LEVEL[r.risk_level] || ""}`}>{r.risk_level}</span>
        <span className="text-xs tabular-nums text-text-secondary">{pct}</span>
        <span className="text-xs font-semibold tabular-nums text-text-primary">{money(r.value_at_stake)}</span>
       </div>
       <div className="min-w-0">
        {a ? (<>
         <p className="text-sm font-semibold text-text-primary">{a.title}</p>
         <p className="text-xs text-text-secondary">{a.because}</p>
         {!compact && <p className="mt-0.5 text-[11px] text-text-muted">{a.action}</p>}
         <p className="mt-0.5 text-[11px] font-semibold text-primary">{a.owner}{sla ? ` · ${sla}` : ""}</p>
        </>) : (<>
         <p className="text-sm font-semibold text-text-primary">Alert to {r.owner || "the rep"}</p>
         <p className="text-xs text-text-secondary">{(r.drivers || []).slice(0, 2).map((d) => d.text).join("; ")}</p>
        </>)}
        {a && (r.drivers || []).length > 0 && !compact && <p className="mt-0.5 text-[11px] text-text-muted">{r.drivers.filter((d) => !(a.because || "").includes(d.text)).slice(0, 2).map((d) => d.text).join("; ")}</p>}
        {r.status !== "open" && <p className="mt-1 text-[11px] text-text-muted"><span className={`rounded-full px-1.5 py-0.5 font-semibold ${STATUS[r.status] || ""}`}>{r.status}</span>{r.status_by ? ` by ${r.status_by}` : ""}{r.status_note ? ` · ${r.status_note}` : ""}{r.outcome ? ` · outcome: ${r.outcome.replace(/_/g, " ")}` : ""}</p>}
       </div>
       <div className="flex items-center gap-1 md:flex-col md:items-stretch">
        {r.status === "open" ? (<>
         <button type="button" disabled={busy === r.id} onClick={() => decide(r, "accepted")} className="inline-flex items-center justify-center gap-1 rounded-md bg-primary px-2.5 py-1 text-xs font-semibold text-white hover:opacity-90 disabled:opacity-50"><Check size={12} /> Accept</button>
         <button type="button" disabled={busy === r.id} onClick={() => decide(r, "dismissed")} className="inline-flex items-center justify-center gap-1 rounded-md border border-border px-2.5 py-1 text-xs font-semibold text-text-secondary hover:text-text-primary disabled:opacity-50"><X size={12} /> Dismiss</button>
        </>) : r.status === "accepted" ? (<>
         <button type="button" disabled={busy === r.id} onClick={() => decide(r, "actioned")} className="inline-flex items-center justify-center gap-1 rounded-md bg-status-success px-2.5 py-1 text-xs font-semibold text-white hover:opacity-90 disabled:opacity-50"><Check size={12} /> Done</button>
         <button type="button" disabled={busy === r.id} onClick={() => decide(r, "open")} className="inline-flex items-center justify-center gap-1 rounded-md border border-border px-2.5 py-1 text-xs font-semibold text-text-secondary hover:text-text-primary disabled:opacity-50"><Undo2 size={12} /> Reopen</button>
        </>) : r.status === "dismissed" ? (
         <button type="button" disabled={busy === r.id} onClick={() => decide(r, "open")} className="inline-flex items-center justify-center gap-1 rounded-md border border-border px-2.5 py-1 text-xs font-semibold text-text-secondary hover:text-text-primary disabled:opacity-50"><Undo2 size={12} /> Reopen</button>
        ) : null}
       </div>
      </div>
     );
    })}
   </div>
   {compact && items.length > 8 && <p className="text-[11px] text-text-muted">{items.length - 8} more under Recommendations.</p>}
  </div>
 );
}
