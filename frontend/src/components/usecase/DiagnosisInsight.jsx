/**
 * A diagnosis as an insight (K25) -- the screen twin of the report's "Why it changed":
 * the KPI's move as two numbers with a delta pill; the driver as a card with what it did,
 * its share of the change as a bar, how unusual it is against its own history, and the
 * KPI's recent periods as small bars; then what else moved, each with a signed bar; then
 * why -- the reason tally. Used on the run view and on the dashboard the use case owns.
 * Every number is the decomposition's own; nothing here computes.
 */
const fmt = (v, unit) => {
 if (v == null) return "—";
 const n = Number(v);
 if (unit === "%") return `${n.toFixed(1)}%`;
 if (String(unit).toUpperCase() === "USD") return `$${n.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
 return `${Number.isInteger(n) ? n.toLocaleString() : n.toFixed(1)}${unit && unit !== "%" ? ` ${unit}` : ""}`;
};
const EFFECT = { rate: "its own rate moved", mix: "its share of the mix moved", volume: "its count moved" };

function Bar({ share, tone = "brand", width = "w-32" }) {
 const pct = Math.max(2, Math.min(100, Math.round(Math.abs(share || 0) * 100)));
 const fill = tone === "brand" ? "bg-primary" : tone === "teal" ? "bg-status-success" : "bg-status-warning";
 return <span className={`inline-block ${width} h-2 overflow-hidden rounded-full bg-primary-light align-middle`}><span className={`block h-2 rounded-full ${fill}`} style={{ width: `${pct}%` }} /></span>;
}

function History({ history, current, unit }) {
 const values = [...(history || [])].reverse().concat(current == null ? [] : [current]).map(Number).filter((v) => !Number.isNaN(v));
 if (values.length < 3) return null;
 const low = Math.min(...values); const high = Math.max(...values); const span = high - low || 1;
 return (
  <div>
   <div className="flex h-10 items-end gap-[3px]">{values.map((v, i) => <span key={i} title={fmt(v, unit)} className={`w-2 rounded-sm bg-primary ${i === values.length - 1 ? "" : "opacity-30"}`} style={{ height: `${6 + Math.round(30 * (v - low) / span)}px` }} />)}</div>
   <p className="mt-0.5 text-[10px] text-text-muted">the last {values.length - 1} periods, then now</p>
  </div>
 );
}

/**
 * The KPI's trend as a readable chart: every bar labelled with its value, the dates under
 * the first, the baseline and the current bar, the median of the history as a dashed
 * "usual" line, and the two compared periods darker than the rest.
 */
function TrendChart({ values, unit, asOf, stepDays }) {
 const n = values.length; const low = Math.min(...values); const high = Math.max(...values); const span = high - low || 1;
 const history = values.slice(0, -1); const sorted = [...history].sort((a, b) => a - b); const median = sorted.length ? (sorted.length % 2 ? sorted[(sorted.length - 1) / 2] : (sorted[sorted.length / 2 - 1] + sorted[sorted.length / 2]) / 2) : null;
 const dateOf = (i) => { if (!asOf) return ""; const d = new Date(asOf); d.setDate(d.getDate() - (n - 1 - i) * (stepDays || 7)); return `${d.getDate()} ${d.toLocaleString(undefined, { month: "short" })}`; };
 const short = (v) => (unit === "%" ? `${Number(v).toFixed(1)}` : Number.isInteger(Number(v)) ? Number(v).toLocaleString() : Number(v).toFixed(1));
 const y = (v) => 8 + Math.round(84 * (v - low) / span); // percent of the plot height
 return (
  <div className="mt-2 flex flex-1 flex-col">
   <div className="relative flex h-28 items-end gap-1">
    {median != null && <div className="pointer-events-none absolute left-0 right-0 border-t border-dashed border-text-muted/60" style={{ bottom: `${y(median)}%` }}><span className="absolute right-0 -top-3.5 rounded bg-surface px-1 text-[9px] text-text-muted">usual {short(median)}{unit === "%" ? "%" : ""}</span></div>}
    {values.map((v, i) => { const isNow = i === n - 1; const isBase = i === n - 2; return (
     <div key={i} className="group relative flex flex-1 flex-col items-center justify-end" style={{ height: "100%" }} title={`${dateOf(i)} · ${fmt(v, unit)}`}>
      <span className={`mb-0.5 text-[9px] tabular-nums leading-none ${isNow ? "font-bold text-text-primary" : isBase ? "font-semibold text-text-secondary" : "text-text-muted"}`}>{short(v)}</span>
      <span className={`w-full rounded-t-sm bg-primary ${isNow ? "" : isBase ? "opacity-60" : "opacity-25"}`} style={{ height: `${y(v)}%` }} />
     </div>
    ); })}
   </div>
   <div className="mt-1 flex justify-between text-[10px] text-text-muted"><span>{dateOf(0)}</span><span className="text-text-secondary">{dateOf(n - 2)} → <b className="text-text-primary">{dateOf(n - 1)}</b></span></div>
   <p className="mt-1 text-[10px] text-text-muted">{unit === "%" ? "percent, " : ""}each bar a period {stepDays || 7} days apart · darker: the two compared · dashed: the usual (median of the history)</p>
  </div>
 );
}

/** The first row of a diagnosis dashboard as three tiles: the KPI (previous → now), the
 *  trend (the last twelve periods, now darkest), and where the change sits (each dimension's
 *  top slice as a signed bar). Same numbers as the report; nothing computed here. */
export function DiagnosisTiles({ diagnosis }) {
 const d = diagnosis; const h = d?.headline; if (!h) return null;
 const unit = h.unit || ""; const delta = h.delta == null ? null : Number(h.delta); const up = delta != null && delta >= 0;
 const deltaText = delta == null ? "—" : unit === "%" ? `${up ? "+" : ""}${delta.toFixed(1)} pts` : `${up ? "+" : ""}${fmt(delta, unit)}`;
 const w = d.winner; const zBar = d.z_bar || 2;
 const tops = (d.ranking || []).map((e) => e.drivers?.[0]).filter(Boolean).slice(0, 5);
 const values = [...(h.history || [])].reverse().concat(h.current == null ? [] : [h.current]).map(Number).filter((v) => !Number.isNaN(v));
 const low = Math.min(...values); const high = Math.max(...values); const span = high - low || 1;
 const tile = "rounded-xl border border-border bg-surface px-4 py-3 flex flex-col";
 const label = "text-[11px] font-semibold uppercase tracking-wide text-text-muted";
 return (
  <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
   <div className={tile}>
    <p className={label}>{h.name}</p>
    <div className="mt-2 flex items-end gap-4">
     <div><p className="text-[10px] text-text-muted">{h.baseline_as_of}</p><p className="text-2xl font-semibold tabular-nums text-text-muted">{fmt(h.baseline, unit)}</p></div>
     <span className="pb-1 text-text-muted">→</span>
     <div><p className="text-[10px] text-text-muted">{h.as_of}</p><p className="text-3xl font-bold tabular-nums text-text-primary">{fmt(h.current, unit)}</p></div>
    </div>
    <p className="mt-2"><span className={`rounded-full px-2 py-0.5 text-xs font-semibold ${up ? "bg-status-success-light text-status-success" : "bg-status-critical-light text-status-critical"}`}>{deltaText}</span>{h.delta_pct != null && unit !== "%" ? <span className="ml-1.5 text-xs text-text-muted">{h.delta_pct > 0 ? "+" : ""}{h.delta_pct}%</span> : null}</p>
    <p className="mt-auto pt-2 text-[11px] text-text-muted">{h.rows_in_period} rows in the period · {h.rows_in_baseline} in the one before</p>
   </div>
   <div className={tile}>
    <p className={label}>Trend · {h.name}, the last {Math.max(0, values.length - 1)} periods</p>
    {values.length >= 3 ? <TrendChart values={values} unit={unit} asOf={h.as_of} stepDays={h.step_days} /> : <p className="mt-2 text-xs text-text-muted">Not enough history yet.</p>}
   </div>
   <div className={tile}>
    <p className={label}>Where the change sits</p>
    <table className="mt-2 text-xs"><tbody>
     {tops.map((t) => { const share = Number(t.share_of_gap || 0); const sameWay = delta == null ? share >= 0 : (share >= 0) === up; const win = w && t.dimension === w.dimension && t.value === w.value; const z = t.z == null ? null : Number(t.z); const unusual = z != null && Math.abs(z) >= zBar; return (
      <tr key={`${t.dimension}:${t.value}`}>
       <td className="max-w-[10rem] py-0.5 pr-2 leading-tight" title={`${t.dimension.replace(/_/g, " ")} · ${t.value}`}><span className={`block truncate ${win ? "font-semibold text-text-primary" : "text-text-secondary"}`}>{t.value}{unusual && <span className="ml-1 text-status-critical">●</span>}</span><span className="block truncate text-[10px] text-text-muted">{t.dimension.replace(/_/g, " ")}</span></td>
       <td className="py-0.5"><Bar share={share} tone={win ? "brand" : sameWay ? "brand" : "warn"} width="w-20" /><span className={`ml-1.5 tabular-nums ${sameWay ? "text-text-secondary" : "text-status-warning"}`}>{share >= 0 ? "+" : ""}{Math.round(share * 100)}%</span></td>
      </tr>
     ); })}
    </tbody></table>
    <p className="mt-auto pt-2 text-[11px] text-text-muted">share of the change · <span className="text-status-critical">●</span> unusual for itself · amber pulled the other way</p>
   </div>
  </div>
 );
}

/** The KPI as a tile: previous → now from the two compared periods, the delta, and the trend. */
export function DiagnosisKpiTile({ diagnosis }) {
 const h = diagnosis?.headline; if (!h) return null;
 const unit = h.unit || ""; const delta = h.delta == null ? null : Number(h.delta); const up = delta != null && delta >= 0;
 const deltaText = delta == null ? "—" : unit === "%" ? `${up ? "+" : ""}${delta.toFixed(1)} pts` : `${up ? "+" : ""}${fmt(delta, unit)}`;
 const pct = h.delta_pct != null && unit !== "%" ? ` (${h.delta_pct > 0 ? "+" : ""}${h.delta_pct}%)` : "";
 return (
  <div className="rounded-xl border border-border bg-surface px-4 py-3">
   <p className="text-[11px] font-semibold uppercase tracking-wide text-text-muted">{h.name}</p>
   <div className="mt-1 grid grid-cols-[auto_auto_1fr] items-end gap-x-4">
    <div><p className="text-[10px] uppercase tracking-wide text-text-muted">{h.baseline_as_of}</p><p className="text-2xl font-semibold tabular-nums text-text-muted">{fmt(h.baseline, unit)}</p></div>
    <div><p className="text-[10px] uppercase tracking-wide text-text-muted">{h.as_of}</p><p className="text-3xl font-bold tabular-nums text-text-primary">{fmt(h.current, unit)} <span className={`align-middle rounded-full px-2 py-0.5 text-xs font-semibold ${up ? "bg-status-success-light text-status-success" : "bg-status-critical-light text-status-critical"}`}>{deltaText}{pct}</span></p></div>
    <div className="justify-self-end"><History history={h.history} current={h.current} unit={unit} /></div>
   </div>
   <p className="mt-1 text-[11px] text-text-muted">{h.rows_in_period} rows in the period · {h.rows_in_baseline} in the one before · {h.step_days} days apart</p>
  </div>
 );
}

export default function DiagnosisInsight({ diagnosis, compact = false, hideHero = false, hideMovers = false }) {
 const d = diagnosis; if (!d) return null;
 const h = d.headline || {}; const w = d.winner; const unit = h.unit || "";
 const delta = h.delta == null ? null : Number(h.delta); const up = delta != null && delta >= 0;
 const deltaText = delta == null ? "—" : unit === "%" ? `${up ? "+" : ""}${delta.toFixed(1)} pts` : `${up ? "+" : ""}${fmt(delta, unit)}`;
 const zBar = d.z_bar || 2;
 const tops = (d.ranking || []).map((e) => e.drivers?.[0]).filter(Boolean).filter((t) => !(w && t.dimension === w.dimension && t.value === w.value)).slice(0, compact ? 3 : 5);
 const wz = w?.z == null ? null : Number(w.z); const wUnusual = wz != null && Math.abs(wz) >= zBar;
 const tallies = Object.entries(d.evidence?.tallies || {});
 return (
  <div className="space-y-4">
   {!hideHero && <div>
    {!compact && <p className="text-[11px] font-semibold uppercase tracking-wide text-primary">Why it changed</p>}
    <p className="text-sm font-semibold text-text-primary">{h.name}</p>
    <p className="mt-1 flex flex-wrap items-baseline gap-2 text-3xl font-bold tabular-nums text-text-primary"><span className="font-medium text-text-muted">{fmt(h.baseline, unit)}</span><span className="text-text-muted">→</span>{fmt(h.current, unit)}<span className={`rounded-full px-2.5 py-0.5 text-xs font-semibold ${up ? "bg-status-success-light text-status-success" : "bg-status-critical-light text-status-critical"}`}>{deltaText}</span></p>
    <p className="text-xs text-text-muted">{h.baseline_as_of} → {h.as_of} · {h.rows_in_period} rows in the period</p>
   </div>}

   {w && (
    <div className="rounded-xl border border-primary/30 bg-gradient-to-b from-surface to-primary-light/30 px-4 py-3">
     <p className="text-[11px] font-semibold uppercase tracking-wide text-primary">The driver · {String(w.dimension).replace(/_/g, " ")}</p>
     <p className="text-2xl font-bold text-text-primary">{w.value}</p>
     <p className="mt-1 text-sm text-text-secondary">{EFFECT[w.effect] || w.effect}: <b className="text-text-primary">{fmt(w.baseline, unit)} → {fmt(w.current, unit)}</b> over {w.support} rows</p>
     <div className={`mt-3 grid gap-4 ${compact ? "grid-cols-2" : "grid-cols-3"}`}>
      <div><p className="text-[10px] font-semibold uppercase tracking-wide text-text-muted">Of the change</p><p className="text-xl font-bold tabular-nums text-text-primary">{w.share_of_gap != null ? `${Math.round(w.share_of_gap * 100)}%` : "—"}</p><Bar share={w.share_of_gap} /></div>
      <div className="border-l border-primary/20 pl-4"><p className="text-[10px] font-semibold uppercase tracking-wide text-text-muted">Against its usual</p><p className={`text-xl font-bold tabular-nums ${wUnusual ? "text-status-critical" : "text-text-primary"}`}>{fmt(w.norm, unit)} <span className={`text-xs font-semibold ${wUnusual ? "text-status-critical" : "text-text-muted"}`}>{wUnusual ? `unusual · z ${wz > 0 ? "+" : ""}${wz.toFixed(1)}` : wz != null ? "within its range" : "no history"}</span></p></div>
      {!compact && !hideHero && <div className="border-l border-primary/20 pl-4"><p className="text-[10px] font-semibold uppercase tracking-wide text-text-muted">{h.name}, the last periods</p><History history={h.history} current={h.current} unit={unit} /></div>}
     </div>
    </div>
   )}

   {tops.length > 0 && !hideMovers && (
    <div>
     <p className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-text-muted">Also moving · share of the change</p>
     <table className="text-sm"><tbody>
      {tops.map((t) => { const share = Number(t.share_of_gap || 0); const sameWay = delta == null ? share >= 0 : (share >= 0) === up; const z = t.z == null ? null : Number(t.z); const unusual = z != null && Math.abs(z) >= zBar; return (
       <tr key={`${t.dimension}:${t.value}`}>
        <td className="whitespace-nowrap py-1 pr-3 text-text-primary"><span className="text-text-muted">{String(t.dimension).replace(/_/g, " ")} · </span><b>{t.value}</b>{unusual && <span className="ml-1.5 rounded-full bg-status-critical-light px-1.5 py-px text-[10px] font-semibold text-status-critical">unusual</span>}</td>
        <td className="whitespace-nowrap py-1 pr-3 text-xs tabular-nums text-text-secondary">{fmt(t.baseline, unit)} → {fmt(t.current, unit)}</td>
        <td className="whitespace-nowrap py-1"><Bar share={share} tone={sameWay ? "brand" : "warn"} width="w-28" /> <span className={`ml-1.5 text-xs tabular-nums ${sameWay ? "text-text-secondary" : "text-status-warning"}`}>{share >= 0 ? "+" : ""}{Math.round(share * 100)}%{sameWay ? "" : " · the other way"}</span></td>
       </tr>
      ); })}
     </tbody></table>
    </div>
   )}

   {(d.intersections || []).slice(0, 1).map((i) => { const t = i.drivers?.[0]; return t ? <p key={i.dimension} className="text-sm text-text-secondary"><span className="text-[11px] font-semibold uppercase tracking-wide text-text-muted">Inside {w?.value} · </span>{String(t.dimension).replace(/_/g, " ")} · <b className="text-text-primary">{t.value}</b> — {EFFECT[t.effect] || t.effect}, {fmt(t.baseline, unit)} → {fmt(t.current, unit)}, {t.support} rows.</p> : null; })}

   {tallies.length > 0 && tallies.map(([col, vals]) => { const top = Math.max(1, ...vals.map((v) => Number(v.count || 0))); return (
    <div key={col}>
     <p className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-text-muted">Why · {col.replace(/_/g, " ")} across the {d.evidence.count} {d.evidence.side === "losing" ? "that did not convert" : "rows"}</p>
     <table className="text-sm"><tbody>{vals.slice(0, compact ? 4 : 6).map((v) => <tr key={v.value}><td className="whitespace-nowrap py-0.5 pr-3 text-text-primary">{v.value}</td><td className="whitespace-nowrap py-0.5"><Bar share={Number(v.count) / top} tone="teal" width="w-36" /> <span className="ml-1.5 text-xs tabular-nums text-text-secondary">{v.count}</span></td></tr>)}</tbody></table>
    </div>
   ); })}

   {!compact && d.evidence?.rows?.length > 0 && (
    <div>
     <p className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-text-muted">The evidence · {d.evidence.rows.length} of the {d.evidence.count} {d.evidence.side === "losing" ? `${w?.value || ""} rows that did not convert` : `${w?.value || ""} rows`}</p>
     <div className="overflow-x-auto rounded-lg border border-border">
      <table className="w-full text-xs"><thead className="bg-surface-secondary text-[10px] uppercase tracking-wide text-text-muted"><tr>{(d.evidence.columns || []).map((c) => <th key={c} className="px-2 py-1.5 text-left font-semibold">{c.replace(/_/g, " ")}</th>)}</tr></thead>
       <tbody>{d.evidence.rows.slice(0, 12).map((r, i) => <tr key={i} className="border-t border-border">{(d.evidence.columns || []).map((c) => <td key={c} className="whitespace-nowrap px-2 py-1 tabular-nums text-text-secondary">{r[c] == null ? "—" : String(r[c])}</td>)}</tr>)}</tbody></table>
     </div>
     {d.evidence.rows.length > 12 && <p className="mt-1 text-[10px] text-text-muted">{d.evidence.rows.length - 12} more rows carried on the run.</p>}
    </div>
   )}

   {!compact && <p className="border-t border-border pt-2 text-[11px] text-text-muted">Searched across {(d.dimensions_searched || []).map((x) => x.replace(/_/g, " ")).join(", ")} · how sure: {({ clear: "very sure", usual: "balanced", more: "show more" })[d.sensitivity] || d.sensitivity} · a slice needs {d.support_floor} rows to be named · the norm is each slice's last {d.history_periods} periods.{(d.caveats || []).length ? ` ${d.caveats.join(" ")}` : ""}</p>}
  </div>
 );
}
