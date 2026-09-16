import { ArrowDown, Bot, Cpu, Database, FileCheck, GitBranch, Megaphone, PenLine, Play, ShieldCheck, UserCheck, Calculator, Sparkles, CornerDownRight } from "lucide-react";

const cap = (t) => (t ? t.charAt(0).toUpperCase() + t.slice(1) : "");
import { STEP_KIND } from "../../domain/use-case-plan";

/**
 * "How it will run" as a pipeline a person can read top to bottom: a phase ribbon that says
 * what the run is made of, then a rail of stages, each a card with who takes it, what it
 * uses from the form, and, for a decision, where the thing that fails goes. The AI step is
 * the one lit differently, and every step that guards the write carries a shield, because
 * that pattern -- one model step, checks around every write -- is the point of showing the
 * plan at all.
 */
const ICON = {
 [STEP_KIND.trigger]: Play, [STEP_KIND.read]: Database, [STEP_KIND.compute]: Calculator, [STEP_KIND.check]: ShieldCheck,
 [STEP_KIND.model]: Bot, [STEP_KIND.gate]: UserCheck, [STEP_KIND.write]: PenLine, [STEP_KIND.notify]: Megaphone, [STEP_KIND.record]: FileCheck,
};

const PHASES = [
 { id: "intake", label: "Intake", hint: "what arrives, and whether it is fit to process", kinds: [STEP_KIND.trigger, STEP_KIND.read] },
 { id: "understand", label: "Understand", hint: "the AI step, then deterministic resolution", kinds: [STEP_KIND.model, STEP_KIND.compute] },
 { id: "check", label: "Check", hint: "business guardrails and limits; nothing is written past a failed one", kinds: [STEP_KIND.check, STEP_KIND.gate] },
 { id: "write", label: "Write", hint: "the record, and its rollback point", kinds: [STEP_KIND.write] },
 { id: "report", label: "Report", hint: "who is told, and the provenance that lets anyone ask why", kinds: [STEP_KIND.notify, STEP_KIND.record] },
];
const phaseOf = (step) => {
 if (step.guardsWrite && step.kind === STEP_KIND.record) return "write";
 if (step.kind === STEP_KIND.check && /screen|resolve/i.test(step.executorRef || "")) return /screen/i.test(step.executorRef) ? "intake" : "understand";
 return PHASES.find((p) => p.kinds.includes(step.kind))?.id || "check";
};

const WHO = {
 system: { label: "Automated", ring: "border-border bg-surface text-text-secondary", card: "border-border bg-surface", rail: "bg-border" },
 model: { label: "AI step", ring: "border-primary/40 bg-primary-light text-primary shadow-[0_0_0_4px_var(--theme-brand-light,#f3e9ff)]", card: "border-primary/40 bg-gradient-to-br from-primary-light/70 to-surface", rail: "bg-primary/40" },
 person: { label: "Approval", ring: "border-status-warning-border bg-status-warning-light text-status-warning", card: "border-status-warning-border bg-status-warning-light/40", rail: "bg-status-warning-border" },
};

function Ribbon({ steps }) {
 const counts = Object.fromEntries(PHASES.map((p) => [p.id, steps.filter((s) => phaseOf(s) === p.id).length]));
 const total = steps.length || 1;
 return (
  <div>
   <div className="flex w-full overflow-hidden rounded-lg border border-border">
    {PHASES.map((p, i) => (
     <div key={p.id} title={p.hint} className={`flex min-w-0 flex-col justify-center px-3 py-2 ${i > 0 ? "border-l border-border" : ""} ${p.id === "understand" ? "bg-primary-light/60" : p.id === "check" ? "bg-status-critical-light/60" : p.id === "write" ? "bg-status-success-light/60" : "bg-surface-secondary"}`} style={{ flexGrow: Math.max(counts[p.id], 1), flexBasis: 0 }}>
      <p className="truncate text-[11px] font-bold uppercase tracking-wide text-text-primary">{p.label}</p>
      <p className="truncate text-[11px] text-text-muted">{counts[p.id]} step{counts[p.id] === 1 ? "" : "s"}</p>
     </div>
    ))}
   </div>
   <p className="mt-1 text-[11px] text-text-muted">{PHASES.map((p) => `${p.label}: ${p.hint}`).join(" · ")}. {Math.round((counts.check / total) * 100)}% of the steps are checks.</p>
  </div>
 );
}

export default function PlanPipeline({ steps }) {
 return (
  <div className="space-y-4">
   <Ribbon steps={steps} />
   <ol className="relative">
    {steps.map((s, i) => {
     const Icon = ICON[s.kind] || Cpu;
     const who = WHO[s.who] || WHO.system;
     const last = i === steps.length - 1;
     const phase = PHASES.find((p) => p.id === phaseOf(s));
     const prevPhase = i > 0 ? phaseOf(steps[i - 1]) : null;
     const uses = (s.consumes || []).filter((c) => c.value != null);
     return (
      <li key={s.id} className="relative grid grid-cols-[3rem_minmax(0,1fr)] gap-x-3">
       {phase && phase.id !== prevPhase && (
        <div className="col-span-2 mb-2 mt-1 flex items-center gap-2 pl-14 text-[10px] font-bold uppercase tracking-[0.12em] text-text-muted"><span className="h-px w-6 bg-border" />{phase.label}</div>
       )}
       <div className="flex flex-col items-center">
        <span className={`relative flex h-11 w-11 shrink-0 items-center justify-center rounded-full border-2 ${who.ring}`}>
         <Icon size={17} />
         <span className="absolute -bottom-1 -right-1 flex h-5 min-w-5 items-center justify-center rounded-full border border-border bg-surface px-1 text-[10px] font-bold text-text-secondary">{i + 1}</span>
        </span>
        {!last && (
         <span className="relative w-0.5 flex-1" style={{ minHeight: "1.5rem" }}>
          <span className={`absolute inset-0 rounded ${s.branch ? "border-l-2 border-dashed border-border" : who.rail}`} />
         </span>
        )}
       </div>
       <div className={`mb-3 ${s.satisfiedBy ? "opacity-70" : ""}`}>
        <div className={`rounded-xl border px-4 py-3 shadow-sm ${who.card} ${s.guardsWrite ? "border-l-4 !border-l-status-critical" : ""}`}>
         <div className="flex flex-wrap items-center gap-2">
          <p className="text-sm font-bold text-text-primary">{s.title}</p>
          <span className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${s.who === "model" ? "border-primary/30 bg-primary text-white" : s.who === "person" ? "border-status-warning-border bg-status-warning-light text-status-warning" : "border-border bg-surface-secondary text-text-secondary"}`}>{who.label}</span>
          {s.who === "model" && <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-primary"><Sparkles size={11} /> the only AI step · extracts, never decides</span>}
          {s.guardsWrite && <span className="inline-flex items-center gap-1 rounded-full border border-status-critical-border bg-status-critical-light px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-status-critical"><ShieldCheck size={10} /> protects the change</span>}
          {s.satisfiedBy && <span className="rounded-full border border-border bg-surface-secondary px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-text-muted">already computed nightly</span>}
          {s.conditional && <span className="inline-flex items-center gap-1 text-[11px] text-text-muted"><GitBranch size={11} />{s.conditional}</span>}
         </div>
         {s.detail && <p className="mt-1 text-sm text-text-secondary">{s.detail}</p>}
         {uses.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-1.5">
           {uses.map((c) => (
            <span key={c.slot} className="inline-flex max-w-full items-center gap-1 rounded-md border border-border bg-surface px-2 py-0.5 text-[11px]">
             <span className="text-text-muted">{c.slot}</span>
             <span className="truncate font-medium text-text-primary" title={String(c.value)}>{String(c.value).length > 48 ? `${String(c.value).slice(0, 48)}…` : String(c.value)}</span>
            </span>
           ))}
          </div>
         )}
         {s.chips?.length > 0 && <div className="mt-2 flex flex-wrap gap-1.5">{s.chips.map((c) => <span key={c} className="rounded-md bg-surface-secondary px-2 py-0.5 font-mono text-[11px] text-text-primary">{c}</span>)}</div>}
         {s.branch && (
          <div className="mt-2.5 grid gap-1.5 text-xs sm:grid-cols-2">
           <div className="flex items-start gap-2 rounded-lg border border-status-success-border bg-status-success-light/60 px-3 py-2">
            <ArrowDown size={13} className="mt-0.5 shrink-0 text-status-success" />
            <p className="text-text-secondary"><span className="font-semibold text-status-success">{cap(s.branch.yes)}:</span> on to step {i + 2}.</p>
           </div>
           <div className="flex items-start gap-2 rounded-lg border border-dashed border-status-critical-border bg-status-critical-light/50 px-3 py-2">
            <CornerDownRight size={13} className="mt-0.5 shrink-0 text-status-critical" />
            <p className="text-text-secondary"><span className="font-semibold text-status-critical">{cap(s.branch.no)}:</span> {s.branch.outcome}. The rest of the batch continues.</p>
           </div>
          </div>
         )}
        </div>
       </div>
      </li>
     );
    })}
   </ol>
  </div>
 );
}
