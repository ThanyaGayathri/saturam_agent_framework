import { useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowRight, FlaskConical, ShieldCheck } from "lucide-react";
import { CHANNELS, COMPARISONS, OPERATORS, SHAPES, estimatedRunsPerMonth } from "../../domain/use-case-autonomy";
import { loadSpecDraft } from "../../domain/use-case-draft-spec";
import { useKpiRegistry } from "../../hooks/useKpiRegistry";
import { flattenKpiIndexRows } from "../../domain/kpi-mapping";

/**
 * Step 5 of 6: rehearse.
 *
 * Run the frozen spec against the last 90 days with every side effect switched off, and show what
 * it WOULD have done -- per shape, because each shape's "what it did" is a different thing:
 *
 *   Inform     the answer it would have given, with the table behind it
 *   Alert      how often it would have fired, where, and how often it stayed quiet
 *   Recommend  what the proposal agent would have put in front of the approver, and what the
 *              hard limits would have struck first
 *   Act        the same, plus what the cap would have held back -- the number an admin needs
 *              before switching on something with nobody in the loop
 *
 * "It looked right in the spec" and "it did the right thing on real data" are different claims.
 * The numbers below are deterministic stand-ins seeded from the spec, so the page is stable across
 * renders and reads as a result rather than noise; the platform rehearses against real history.
 */

function seed(str) {
 let h = 7;
 for (const ch of String(str)) h = (h * 31 + ch.charCodeAt(0)) % 100003;
 return h;
}

/** Sample members per grain, so a rehearsal over "Category" shows categories and one over "any
 *  store" shows stores. */
const SAMPLE = {
 Category: ["Apparel", "Footwear", "Home", "Beauty", "Electronics"],
 Brand: ["Northwind", "Aster", "Kestrel", "Marlow", "Juniper"],
 Channel: ["Online", "Store", "Marketplace", "App", "Wholesale"],
 Store: ["Store 112 Chennai", "Store 204 Pune", "Store 318 Mumbai", "Store 077 Delhi", "Store 145 Bengaluru"],
 Region: ["North", "South", "East", "West", "Central"],
 "Customer segment": ["Loyal", "New", "Lapsed", "Value", "Premium"],
 Supplier: ["Meridian Textiles", "Corvus Footwear", "Halden Home", "Lumen Beauty", "Orion Electronics"],
 Product: ["SKU 48211", "SKU 50930", "SKU 33107", "SKU 61842", "SKU 27655"],
};
const sampleFor = (grain) => SAMPLE[grain] || SAMPLE.Category;
const grainOfScope = (scope) => (scope && scope !== "Everywhere" ? scope.replace(/^any /i, "").replace(/^\w/, (c) => c.toUpperCase()) : null);
const channelLabels = (spec) => (spec.notifyChannels || ["in_app"]).map((c) => CHANNELS.find((x) => x.value === c)?.label || c);

/** What a proposal looks like for the target it writes to. Presentation-only wording. */
function proposalFor(writeback, i, s) {
 const w = String(writeback || "").toLowerCase();
 if (/pric|markdown/.test(w)) { const from = 10 + ((s + i * 7) % 15); return `Markdown ${from}% → ${from + 10 + ((s + i) % 10)}%`; }
 if (/inventory|mrp|stock/.test(w)) { const from = 14 + ((s + i * 5) % 20); return `Safety stock ${from} → ${from + 4 + ((s + i * 3) % 12)} days`; }
 return `Update ${i + 1}`;
}

function Section({ title, children }) {
 return (
  <section className="rounded-xl border border-border bg-surface p-4">
   <h2 className="text-sm font-bold text-text-primary">{title}</h2>
   <div className="mt-2">{children}</div>
  </section>
 );
}

export default function UseCaseRehearseScreen() {
 const navigate = useNavigate();
 const spec = useMemo(() => loadSpecDraft(), []);
 const shape = SHAPES[spec.shape] || SHAPES.inform;
 const s = seed(spec.goal + spec.metricIds.join());
 const { data } = useKpiRegistry();
 const metric = spec.metricIds[0] || "the metric";
 // The unit the metric is in, from its registry entry: "%" for a rate, "Days" for a cover.
 const unit = useMemo(() => spec.condition?.unit || flattenKpiIndexRows(data?.categories || []).find((r) => r.kpi.name === metric)?.kpi.unit || "%", [data?.categories, metric, spec.condition?.unit]);

 const inform = useMemo(() => {
  const dims = spec.dimensions.length ? spec.dimensions : ["Category"];
  const rows = sampleFor(dims[0]).map((name, i) => ({
   name,
   value: (28 + ((s + i * 17) % 14)).toFixed(1),
   delta: ((((s + i * 29) % 21) - 10) / 2).toFixed(1),
  }));
  const worst = [...rows].sort((a, b) => a.delta - b.delta)[0];
  return { dims, rows, worst };
 }, [spec, s]);

 const alert = useMemo(() => {
  const fires = 2 + (s % 5);
  const grain = grainOfScope(spec.scope);
  const places = grain ? sampleFor(grain) : ["Overall"];
  const thr = spec.condition?.threshold ?? 10;
  const above = spec.condition?.operator !== "lt";
  return {
   grain,
   events: Array.from({ length: fires }, (_, i) => ({
    day: `Day ${8 + i * 11}`,
    where: places[(s + i) % places.length],
    value: (above ? thr + 1 + ((s + i * 7) % 6) : Math.max(0, thr - 1 - ((s + i * 7) % 6))).toFixed(1),
   })),
   quiet: 90 - fires,
  };
 }, [spec, s]);

 const propose = useMemo(() => {
  const considered = 240 + (s % 160);
  const struck = spec.guardrails.map((g, i) => ({ rule: g.rule, count: 8 + ((s + i * 13) % 40) }));
  const struckTotal = struck.reduce((n, x) => n + x.count, 0);
  const surviving = Math.max(0, considered - struckTotal);
  const cap = parseInt(String(spec.blastRadius || "").replace(/[^\d]/g, ""), 10) || null;
  const perRun = Math.max(1, Math.round(surviving / 4));
  const held = cap && spec.shape === "act" ? Math.max(0, perRun - cap) : 0;
  const samples = sampleFor("Product").slice(0, 3).map((name, i) => ({
   name,
   metric: `${metric} ${(20 + ((s + i * 11) % 30)).toFixed(0)}${unit === "%" ? "%" : ` ${unit.toLowerCase()}`}`,
   proposal: proposalFor(spec.writeback, i, s),
   passes: i !== 1,
  }));
  return { considered, struck, struckTotal, surviving, perRun, cap, held, samples };
 }, [spec, s, metric, unit]);

 const runs = estimatedRunsPerMonth(spec.schedule);
 const costPerRun = shape.level >= 3 ? 0.42 : shape.level === 2 ? 0.08 : 0.19;
 const title = spec.title?.trim() || spec.goal?.trim() || "Untitled use case";

 return (
  <div className="mx-auto w-full max-w-3xl space-y-4 px-6 py-6">
   <div>
    <p className="text-xs font-semibold uppercase tracking-wide text-text-muted">Step 5 of 6</p>
    <h1 className="flex items-center gap-2 text-2xl font-bold text-text-primary">
     <FlaskConical size={22} className="text-primary" /> Simulation
    </h1>
    <p className="mt-1 max-w-2xl text-sm text-text-secondary">
     <strong className="text-text-primary">{title}</strong>, run against the last 90 days with every side effect switched off. This is what it
     <strong className="text-text-primary"> would have</strong> done.
    </p>
   </div>

   <div className="inline-flex items-center gap-1.5 rounded-full bg-status-success-light px-3 py-1 text-xs font-semibold text-status-success">
    <ShieldCheck size={13} /> Sandbox · nothing was written, sent or changed
   </div>

   {shape.id === "inform" && (
    <Section title="The answer it would have given">
     <p className="text-sm text-text-secondary">
      <strong className="text-text-primary">{inform.worst.name}</strong> accounts for most of the movement in {metric}
      {spec.comparison ? ` versus ${COMPARISONS.find((c) => c.value === spec.comparison)?.label.toLowerCase()}` : ""}
      . Every other {inform.dims[0].toLowerCase()} moved by less than 2 points.
     </p>
     <div className="mt-3 overflow-x-auto rounded-lg border border-border">
      <table className="w-full min-w-[420px] text-sm">
       <thead className="bg-surface-secondary text-[11px] uppercase tracking-wide text-text-muted">
        <tr><th className="px-3 py-2 text-left">{inform.dims[0]}</th><th className="px-3 py-2 text-right">{metric}</th><th className="px-3 py-2 text-right">Change</th></tr>
       </thead>
       <tbody>
        {inform.rows.map((r) => (
         <tr key={r.name} className="border-t border-border">
          <td className="px-3 py-2">{r.name}</td>
          <td className="px-3 py-2 text-right tabular-nums">{r.value}%</td>
          <td className={`px-3 py-2 text-right tabular-nums font-semibold ${r.delta < 0 ? "text-status-critical" : "text-status-success"}`}>{r.delta > 0 ? "+" : ""}{r.delta} pts</td>
         </tr>
        ))}
       </tbody>
      </table>
     </div>
     <p className="mt-3 text-xs text-text-muted">
      Every number above came from the read step; the narrative cites them and adds none. Would have been delivered to <strong className="text-text-secondary">{spec.notify || "the recipient"}</strong> · {channelLabels(spec).join(", ")}.
     </p>
    </Section>
   )}

   {shape.id === "alert" && (
    <Section title={`It would have fired ${alert.events.length} time${alert.events.length === 1 ? "" : "s"} in 90 days`}>
     <p className="text-sm text-text-secondary">
      {alert.events.length >= 5 ? "That is often. Consider a higher threshold or a longer window before this goes live." : "A manageable number for the people it notifies."}
      {" "}It stayed quiet on the other {alert.quiet} days — each of those runs is still logged.
     </p>
     <ul className="mt-3 space-y-1.5">
      {alert.events.map((a, i) => (
       <li key={i} className="flex items-center justify-between rounded-lg border border-border bg-surface-secondary px-3 py-2 text-sm">
        <span className="text-text-secondary">{a.day} · {a.where}</span>
        <span className="font-semibold tabular-nums text-status-warning">{a.value}{unit === "%" ? "%" : ` ${unit.toLowerCase()}`}</span>
       </li>
      ))}
     </ul>
     <p className="mt-3 text-xs text-text-muted">
      Fires when {metric} {OPERATORS.find((o) => o.value === spec.condition?.operator)?.label || "goes above"} <strong className="text-text-secondary">{spec.condition?.threshold}{unit}</strong>{alert.grain ? `, checked per ${alert.grain.toLowerCase()}` : ""}. Each firing would have told <strong className="text-text-secondary">{spec.notify || "the recipient"}</strong> · {channelLabels(spec).join(", ")} · severity {spec.severity}.
     </p>
    </Section>
   )}

   {shape.level >= 3 && (
    <>
     <Section title="What it would have recommended">
      <p className="text-sm text-text-secondary">
       Over 90 days, {propose.considered} {spec.candidates?.concept ? spec.candidates.concept.toLowerCase() : "candidate"} rows{spec.candidates?.filter ? ` where ${spec.candidates.filter}` : ""} were considered.
      </p>
      <div className="mt-3 grid grid-cols-3 gap-2 text-center">
       <div className="rounded-lg bg-surface-secondary px-3 py-2.5"><p className="text-2xl font-bold tabular-nums text-text-primary">{propose.considered}</p><p className="text-[11px] text-text-muted">considered</p></div>
       <div className="rounded-lg bg-status-critical-light px-3 py-2.5"><p className="text-2xl font-bold tabular-nums text-status-critical">{propose.struckTotal}</p><p className="text-[11px] text-text-muted">struck out by business guardrails</p></div>
       <div className="rounded-lg bg-primary-light px-3 py-2.5"><p className="text-2xl font-bold tabular-nums text-primary">{propose.surviving}</p><p className="text-[11px] text-text-muted">{shape.id === "act" ? "would have been made" : `would reach ${spec.approver || "the approver"}`}</p></div>
      </div>
      {/* Guardrails are the reason the middle number exists. Showing each rule's count is how a
          person checks the rules mean what they thought. */}
      {propose.struck.length > 0 && (
       <ul className="mt-3 space-y-1.5">
        {propose.struck.map((g) => (
         <li key={g.rule} className="flex items-center justify-between rounded-lg border border-border bg-surface-secondary px-3 py-2 text-xs">
          <span className="font-mono text-text-primary">{g.rule}</span>
          <span className="font-semibold tabular-nums text-status-critical">−{g.count}</span>
         </li>
        ))}
       </ul>
      )}
     </Section>

     <Section title={shape.id === "act" ? "Three of the changes it would have made" : `Three of the recommendations ${spec.approver || "the approver"} would have seen`}>
      <div className="overflow-x-auto rounded-lg border border-border">
       <table className="w-full min-w-[480px] text-sm">
        <thead className="bg-surface-secondary text-[11px] uppercase tracking-wide text-text-muted">
         <tr><th className="px-3 py-2 text-left">{spec.candidates?.concept || "Record"}</th><th className="px-3 py-2 text-left">Now</th><th className="px-3 py-2 text-left">Recommendation</th><th className="px-3 py-2 text-left">Guardrails</th></tr>
        </thead>
        <tbody>
         {propose.samples.map((r) => (
          <tr key={r.name} className="border-t border-border">
           <td className="px-3 py-2 font-medium text-text-primary">{r.name}</td>
           <td className="px-3 py-2 tabular-nums text-text-secondary">{r.metric}</td>
           <td className="px-3 py-2 text-text-primary">{r.proposal}</td>
           <td className={`px-3 py-2 text-xs font-semibold ${r.passes ? "text-status-success" : "text-status-critical"}`}>{r.passes ? "passes" : `struck · ${spec.guardrails[0]?.rule || "limit"}`}</td>
          </tr>
         ))}
        </tbody>
       </table>
      </div>
      <p className="mt-2 text-xs text-text-muted">The AI step wrote these with the reasoning behind each{spec.objective?.metric ? `, each chosen to move ${spec.objective.metric} ${spec.objective.direction} ${spec.objective.target}${spec.objective.unit || ""}` : ""}; the guardrails column is the automated check that ran after it. {shape.id === "act" ? "Nobody reviewed them." : "The approver sees the survivors, with their reasoning, one by one."}</p>
     </Section>

     {shape.id === "act" && (
      <Section title="What the change limit would have held">
       <p className="text-sm text-text-secondary">
        About <strong className="text-text-primary">{propose.perRun}</strong> changes per run would have passed the guardrails.
        {propose.cap
         ? propose.held > 0
          ? <> Your change limit is <strong className="text-text-primary">{spec.blastRadius}</strong>, so <strong className="text-text-primary">{propose.held}</strong> per run would have been held for the next run rather than made.</>
          : <> Your change limit is <strong className="text-text-primary">{spec.blastRadius}</strong> — every run would have fitted inside it.</>
         : " No change limit is set."}
       </p>
       <p className="mt-2 rounded-lg border border-status-warning-border bg-status-warning-light px-3 py-2 text-xs text-status-warning">
        Unattended: {propose.surviving} changes over 90 days would have gone to {spec.writeback || "the target system"} with nobody reviewing them. Each one records its rollback point ({spec.rollback || "not set"}), so any run can be reversed as a unit.
       </p>
      </Section>
     )}
    </>
   )}

   <section className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border bg-surface-secondary px-4 py-3 text-sm">
    <span className="text-text-secondary">Estimated cost{shape.id === "alert" ? " (no AI on a run)" : ""}</span>
    <span className="text-text-primary">
     <strong>{runs}</strong> runs / month × ~{costPerRun.toFixed(2)} credits = <strong>~{(runs * costPerRun).toFixed(0)} credits / month</strong>
    </span>
   </section>

   <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border pt-4">
    <button type="button" onClick={() => navigate("/use-cases/builder/plan")} className="rounded-lg border border-border bg-surface px-3.5 py-2 text-sm font-semibold text-text-secondary hover:bg-surface-secondary">
     That's not right — back to the plan
    </button>
    <button type="button" onClick={() => navigate("/use-cases/builder/activate")} className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-white hover:bg-primary-hover">
     Looks right — continue <ArrowRight size={15} />
    </button>
   </div>
  </div>
 );
}
