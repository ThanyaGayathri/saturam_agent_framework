import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
 ArrowRight, Bot, Calculator, Check, Cpu, Database, FileCheck, GitBranch, Megaphone, PenLine,
 Play, ShieldCheck, UserCheck,
} from "lucide-react";
import { planFor, STEP_KIND } from "../../domain/use-case-plan";
import { displayName } from "../../domain/use-case-autonomy";
import { loadSpecDraft, saveSpecDraft } from "../../domain/use-case-draft-spec";

/**
 * Step 4 of 6: the plan.
 *
 * What will happen, step by step, before it happens. Derived from the frozen spec and the
 * orchestration compiler's stage table for its shape (`SHAPE_STAGES` in use-case-plan.js, a
 * mirror of orchestration/compiler.py); not editable here, because editing a plan by hand is how
 * it stops matching the spec. If a step is wrong the fix is in the spec, and the plan follows.
 * Under each step is the parameter slot it consumes and the value the spec gave it, so a person
 * can see exactly which answer on the form drives which step.
 *
 * Each step is labelled with who performs it -- the system (deterministic, replayable), a model
 * (writes and proposes), or a person (the step waits). The point of the labels is the pattern
 * they make: the model steps are always followed by a system check, and nothing is written
 * without a check or a person in front of it.
 */

const ICON = {
 [STEP_KIND.trigger]: Play,
 [STEP_KIND.read]: Database,
 [STEP_KIND.compute]: Calculator,
 [STEP_KIND.check]: ShieldCheck,
 [STEP_KIND.model]: Bot,
 [STEP_KIND.gate]: UserCheck,
 [STEP_KIND.write]: PenLine,
 [STEP_KIND.notify]: Megaphone,
 [STEP_KIND.record]: FileCheck,
};

const WHO = {
 // Agno's vocabulary, so the plan reads the way the deployed workflow is built: an executor is
 // code, an agent is a language model with a prompt, a person is a gate.
 system: { label: "Automated step", short: "automated", hint: "Code. Deterministic: same input, same output. Replayable. No AI is involved.", cls: "bg-surface-secondary text-text-secondary border-border" },
 model: { label: "AI step", short: "AI", hint: "A language model with a brief written for this role. Explains and recommends. Never decides what is allowed.", cls: "bg-primary-light text-primary border-primary/30" },
 person: { label: "Approval step", short: "approval", hint: "Waits for a person.", cls: "bg-status-warning-light text-status-warning border-status-warning-border" },
};

/**
 * The same steps as a flowchart. Decision steps are diamonds with their "no" path drawn, because
 * that is what a chart shows that a list does not: where a run stops, and what happens to the
 * thing that stopped. Pure SVG, laid out from the step list -- nothing here is authored.
 */
function PlanFlow({ steps }) {
 const W = 680, CX = 230, NW = 260, NH = 46, DH = 68, GAP = 26, SX = 470, SW = 190;
 const fill = { system: "var(--color-surface-secondary, #f5f5f7)", model: "var(--color-primary-light, #efe6fb)", person: "var(--color-status-warning-light, #fff4e0)" };
 const stroke = { system: "#c9c9d1", model: "#731dd8", person: "#d98c00" };
 let y = 16;
 const nodes = steps.map((s) => {
  const h = s.branch ? DH : NH;
  const n = { ...s, y, h, cy: y + h / 2 };
  y += h + GAP;
  return n;
 });
 const H = y;
 return (
  <div className="overflow-x-auto rounded-xl border border-border bg-surface p-3">
   <svg width="100%" viewBox={`0 0 ${W} ${H}`} style={{ minWidth: 560, fontFamily: "Inter, system-ui, sans-serif" }} role="img" aria-label="Flow of the plan">
    <defs>
     <marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
      <path d="M0 0L10 5L0 10z" fill="#9a9aa6" />
     </marker>
    </defs>
    {nodes.map((n, i) => {
     const next = nodes[i + 1];
     const wide = n.branch;
     return (
      <g key={n.id}>
       {wide ? (
        <polygon
         points={`${CX},${n.y} ${CX + NW / 2 + 20},${n.cy} ${CX},${n.y + n.h} ${CX - NW / 2 - 20},${n.cy}`}
         fill={fill[n.who]} stroke={stroke[n.who]} strokeWidth={n.who === "system" ? 1 : 1.5}
        />
       ) : (
        <rect x={CX - NW / 2} y={n.y} width={NW} height={n.h} rx="10" fill={n.satisfiedBy ? "#fff" : fill[n.who]} stroke={stroke[n.who]} strokeWidth={n.who === "system" ? 1 : 1.5} strokeDasharray={n.satisfiedBy ? "4 3" : undefined} />
       )}
       <text x={CX} y={n.cy - (wide ? 2 : 3)} textAnchor="middle" fontSize="12.5" fontWeight="700" fill={n.satisfiedBy ? "#7a7a88" : "#1a1a2e"}>{n.title}</text>
       <text x={CX} y={n.cy + 12} textAnchor="middle" fontSize="10" fontWeight="600" fill={stroke[n.who] === "#c9c9d1" ? "#7a7a88" : stroke[n.who]} style={{ textTransform: "uppercase", letterSpacing: 0.6 }}>{n.satisfiedBy ? "already computed" : n.guardsWrite ? `${WHO[n.who].short} · protects the change` : WHO[n.who].short}</text>
       {next && (
        <>
         <line x1={CX} y1={n.y + n.h} x2={CX} y2={next.y - 2} stroke="#9a9aa6" strokeWidth="1.2" markerEnd="url(#arr)" />
         {n.branch && <text x={CX + 8} y={n.y + n.h + 15} fontSize="10" fill="#4a4a5a">{n.branch.yes}</text>}
        </>
       )}
       {n.branch && (
        <>
         <line x1={CX + NW / 2 + 20} y1={n.cy} x2={SX - 2} y2={n.cy} stroke="#9a9aa6" strokeWidth="1.2" strokeDasharray="4 3" markerEnd="url(#arr)" />
         <text x={(CX + NW / 2 + 20 + SX) / 2} y={n.cy - 5} textAnchor="middle" fontSize="10" fill="#4a4a5a">{n.branch.no}</text>
         <rect x={SX} y={n.cy - 20} width={SW} height={40} rx="8" fill="#fff" stroke="#c9c9d1" strokeDasharray="4 3" />
         <text x={SX + SW / 2} y={n.cy + 4} textAnchor="middle" fontSize="10.5" fill="#4a4a5a">{n.branch.outcome}</text>
        </>
       )}
      </g>
     );
    })}
   </svg>
  </div>
 );
}

export default function UseCasePlanScreen() {
 const navigate = useNavigate();
 const [spec, setSpec] = useState(() => loadSpecDraft());
 const plan = useMemo(() => planFor(spec), [spec]);
 const title = spec.title?.trim() || spec.goal?.trim() || "Untitled use case";
 const approved = !!spec.planApprovedAt;
 const [view, setView] = useState("flow");

 const approve = () => {
  const next = saveSpecDraft({ ...spec, planApprovedAt: new Date().toISOString() });
  setSpec(next);
  navigate("/use-cases/builder/rehearse");
 };

 return (
  <div className="mx-auto w-full max-w-3xl space-y-4 px-6 py-6">
   <div>
    <p className="text-xs font-semibold uppercase tracking-wide text-text-muted">Step 4 of 6</p>
    <h1 className="text-2xl font-bold text-text-primary">How it will run</h1>
    <p className="mt-1 max-w-2xl text-sm text-text-secondary">
     Every step <strong className="text-text-primary">{title}</strong> takes on each run, and who takes it. Derived from the spec — if a step is wrong, change the spec and this follows.
    </p>
   </div>

   {/* Who made the plan, and who runs it. Two different questions. The orchestration agent
       composes the flow at design time -- the shape is the template, the spec fills its slots.
       At run time each step is taken by an executor, a model, or a person; for most shapes no
       model is called at all. */}
   <div className="grid gap-3 rounded-xl border border-border bg-surface p-3 sm:grid-cols-[auto_1fr]">
    <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-primary-light text-primary"><Bot size={16} /></span>
    <div className="min-w-0 text-sm">
     <p className="font-semibold text-text-primary">Workflow generated from your specification</p>
     <p className="text-text-secondary">
      The <strong className="text-text-primary">{displayName(spec)}</strong> workflow — {plan.stages.length} steps in a fixed order — with {plan.steps.reduce((n, st) => n + (st.consumes || []).filter((c) => c.value != null).length, 0)} values taken from your specification. Nothing is decided at run time; the workflow was generated here, once, and every run replays it.
     </p>
    </div>
   </div>

   <div className="flex flex-wrap items-center gap-2 text-xs">
    <span className="text-text-muted">At run time:</span>
    {["system", "model", "person"].map((w) => (
     <span key={w} title={WHO[w].hint} className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 font-semibold ${WHO[w].cls}`}>
      {plan.counts[w]} {WHO[w].short} step{plan.counts[w] === 1 ? "" : "s"}
     </span>
    ))}
    <span className="text-text-muted">·</span>
    <span className="text-text-secondary">
     {{
      inform: "Nothing is changed. It reads, compares and explains.",
      alert: "Nothing is changed, and no AI runs on a run — every step is automated: a threshold decides, and it notifies only when it fires.",
      recommend: "Nothing is changed without passing every business guardrail and an approver releasing it.",
      act: "Nothing is changed beyond the business guardrails and the change limit, and every change records its rollback point.",
     }[plan.shape.id]}
    </span>
   </div>
   <p className="text-xs text-text-muted">
    {plan.stages.filter((s) => s.satisfiedBy).length} of the {plan.stages.length} steps already covered by the nightly KPI computation, {plan.stages.filter((s) => s.guardsWrite).length} that a change may not run without. Every decision point is an automated step; an AI step, where there is one, explains or recommends and never decides.
   </p>

   <div className="flex items-center gap-1 rounded-lg border border-border bg-surface-secondary p-0.5 text-xs font-semibold w-fit">
    {["flow", "steps"].map((v) => (
     <button key={v} type="button" onClick={() => setView(v)} aria-pressed={view === v} className={`rounded-md px-3 py-1 ${view === v ? "bg-surface text-text-primary shadow-sm" : "text-text-secondary hover:text-text-primary"}`}>
      {v === "flow" ? "Flow" : "Steps"}
     </button>
    ))}
   </div>

   {view === "flow" && <PlanFlow steps={plan.steps} />}

   {view === "steps" && <ol className="relative space-y-0">
    {plan.steps.map((s, i) => {
     const Icon = ICON[s.kind] || Cpu;
     const who = WHO[s.who];
     const last = i === plan.steps.length - 1;
     return (
      <li key={s.id} className="relative grid grid-cols-[2.25rem_1fr] gap-x-3">
       <div className="flex flex-col items-center">
        <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-full border ${s.who === "person" ? "border-status-warning-border bg-status-warning-light text-status-warning" : s.who === "model" ? "border-primary/30 bg-primary-light text-primary" : "border-border bg-surface text-text-secondary"}`}>
         <Icon size={16} />
        </span>
        {!last && <span className={`w-px flex-1 ${s.branch ? "border-l border-dashed border-border" : "bg-border"}`} style={{ minHeight: "1rem" }} />}
       </div>
       <div className={`pb-4 ${s.satisfiedBy ? "opacity-70" : ""}`}>
        <div className={`rounded-xl border px-4 py-3 ${s.who === "person" ? "border-status-warning-border bg-status-warning-light/40" : s.satisfiedBy ? "border-dashed border-border bg-surface" : "border-border bg-surface"}`}>
         <div className="flex flex-wrap items-center gap-2">
          <span className="text-[11px] font-semibold text-text-muted">{i + 1}</span>
          <p className="text-sm font-bold text-text-primary">{s.title}</p>
          <span title={who.hint} className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${who.cls}`}>{who.label}</span>
          {s.executorRef && <span className="font-mono text-[10px] text-text-muted">{s.executorRef}</span>}
          {s.role && <span className="font-mono text-[10px] text-text-muted">role: {s.role}</span>}
          {s.guardsWrite && <span className="rounded-full border border-status-critical-border bg-status-critical-light px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-status-critical">protects the change</span>}
          {s.satisfiedBy && <span className="rounded-full border border-border bg-surface-secondary px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-text-muted">already computed nightly</span>}
          {s.conditional && <span className="inline-flex items-center gap-1 text-[11px] text-text-muted"><GitBranch size={11} />{s.conditional}</span>}
         </div>
         <p className="mt-1 text-sm text-text-secondary">{s.detail}</p>
         {s.consumes?.length > 0 && (
          <p className="mt-1.5 text-[11px] text-text-muted">
           uses{" "}
           {s.consumes.map((c, k) => (
            <span key={c.slot}>{k > 0 && " · "}<span className="font-mono">{c.slot}</span> = <span className={c.value == null ? "text-status-warning" : "text-text-secondary"}>{c.value == null ? "not set" : String(c.value)}</span></span>
           ))}
          </p>
         )}
         {s.reads?.length > 0 && (
          <ul className="mt-2 space-y-1">
           {s.reads.map((r) => (
            <li key={r.source} className="text-xs text-text-secondary">
             <span className="font-semibold text-text-primary">{r.source}</span>
             {" · "}
             {r.datasets.map((d) => (
              <span key={d.name} className="mr-1.5 inline-flex items-center gap-1 rounded-md bg-surface-secondary px-1.5 py-0.5 font-mono text-[11px]">
               {d.name}{d.stale && <span className="font-sans text-status-warning">stale</span>}
              </span>
             ))}
            </li>
           ))}
          </ul>
         )}
         {s.chips?.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-1.5">
           {s.chips.map((c) => <span key={c} className="rounded-md bg-surface-secondary px-2 py-0.5 font-mono text-[11px] text-text-primary">{c}</span>)}
          </div>
         )}
        </div>
       </div>
      </li>
     );
    })}
   </ol>}

   <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border pt-4">
    <button type="button" onClick={() => navigate("/use-cases/builder/compose")} className="rounded-lg border border-border bg-surface px-3.5 py-2 text-sm font-semibold text-text-secondary hover:bg-surface-secondary">
     A step is wrong — back to the specification
    </button>
    <div className="flex items-center gap-3">
     {approved && <span className="inline-flex items-center gap-1 text-xs font-semibold text-status-success"><Check size={13} /> Approved</span>}
     <button type="button" onClick={approve} className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-white hover:bg-primary-hover">
      <Check size={15} /> This is how it should run <ArrowRight size={15} />
     </button>
    </div>
   </div>
  </div>
 );
}
