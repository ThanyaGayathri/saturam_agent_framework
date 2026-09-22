import { useMemo, useState } from "react";
import { Bot, Calculator, Cpu, Database, FileCheck, Megaphone, Minus, PenLine, Plus, ShieldCheck, UserCheck, Zap, Mail } from "lucide-react";
import { STEP_KIND } from "../../domain/use-case-plan";

/**
 * The plan as an orchestration canvas, the way workflow tools draw a run: a trigger node,
 * a main lane of step nodes joined by connectors, decision nodes with a second output that
 * curves into an exception lane, and one sink where every held document ends up. Nodes are
 * cards with an icon tile, a type badge and the executor that takes the step; edges carry
 * their condition. A dotted canvas and zoom, because that is the grammar people already read.
 * Laid out from the step list -- nothing here is drawn by hand.
 */
const ICON = {
 [STEP_KIND.trigger]: Zap, [STEP_KIND.read]: Database, [STEP_KIND.compute]: Calculator, [STEP_KIND.check]: ShieldCheck,
 [STEP_KIND.model]: Bot, [STEP_KIND.gate]: UserCheck, [STEP_KIND.write]: PenLine, [STEP_KIND.notify]: Megaphone, [STEP_KIND.record]: FileCheck,
};
const TILE = {
 trigger: "bg-status-warning text-white", model: "bg-primary text-white", person: "bg-status-warning text-white",
 guard: "bg-status-critical text-white", write: "bg-status-success-dot text-white", system: "bg-brand-dark text-white",
};
const tileOf = (s) => (s.kind === STEP_KIND.trigger ? "trigger" : s.who === "model" ? "model" : s.who === "person" ? "person" : s.guardsWrite || s.branch ? "guard" : s.kind === STEP_KIND.write ? "write" : "system");
const BADGE = { trigger: "Trigger", model: "AI agent", person: "Approval", guard: "Guard", write: "Action", system: "Executor" };

const NODE_W = 340, NODE_H = 80, GAP = 44, LANE_X = 40, SINK_X = 470, SINK_W = 250, SINK_H = 96;

export default function PlanOrchestration({ steps, exceptionsTo }) {
 const [zoom, setZoom] = useState(1);
 const layout = useMemo(() => {
  const nodes = steps.map((s, i) => ({ ...s, x: LANE_X, y: 24 + i * (NODE_H + GAP), w: NODE_W, h: NODE_H, tile: tileOf(s) }));
  const branches = nodes.filter((n) => n.branch);
  const sinkY = branches.length ? Math.round(branches.reduce((a, n) => a + n.y, 0) / branches.length) : 24;
  const height = 24 + steps.length * (NODE_H + GAP);
  return { nodes, branches, sink: { x: SINK_X, y: Math.max(24, Math.min(sinkY, height - SINK_H - 24)), w: SINK_W, h: SINK_H }, height, width: SINK_X + SINK_W + 40 };
 }, [steps]);
 const { nodes, sink, height, width } = layout;
 const edge = (x1, y1, x2, y2) => `M ${x1} ${y1} C ${x1 + (x2 - x1) * 0.5} ${y1}, ${x1 + (x2 - x1) * 0.5} ${y2}, ${x2} ${y2}`;

 return (
  <div className="rounded-xl border border-border bg-surface">
   <div className="flex items-center justify-between border-b border-border px-3 py-2 text-[11px] text-text-muted">
    <span className="flex flex-wrap items-center gap-3">
     {Object.entries(BADGE).map(([k, label]) => <span key={k} className="inline-flex items-center gap-1"><span className={`inline-block h-2.5 w-2.5 rounded-sm ${TILE[k].split(" ")[0]}`} />{label}</span>)}
    </span>
    <span className="flex items-center gap-1">
     <button type="button" onClick={() => setZoom((z) => Math.max(0.6, +(z - 0.1).toFixed(1)))} className="rounded border border-border-light p-1 hover:text-text-primary" aria-label="Zoom out"><Minus size={11} /></button>
     <span className="w-10 text-center tabular-nums">{Math.round(zoom * 100)}%</span>
     <button type="button" onClick={() => setZoom((z) => Math.min(1.4, +(z + 0.1).toFixed(1)))} className="rounded border border-border-light p-1 hover:text-text-primary" aria-label="Zoom in"><Plus size={11} /></button>
    </span>
   </div>
   <div className="overflow-auto" style={{ backgroundImage: "radial-gradient(var(--app-border, #e9e9eb) 1px, transparent 1px)", backgroundSize: "18px 18px", maxHeight: 760 }}>
    <div className="relative origin-top-left" style={{ width: width * zoom, height: height * zoom }}>
     <div className="absolute left-0 top-0 origin-top-left" style={{ width, height, transform: `scale(${zoom})` }}>
      <svg className="absolute inset-0" width={width} height={height} aria-hidden>
       <defs>
        <marker id="orch-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="var(--app-text-muted, #9a9aa6)" /></marker>
        <marker id="orch-arrow-red" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="var(--theme-status-critical, #dc2626)" /></marker>
       </defs>
       {nodes.map((n, i) => {
        const next = nodes[i + 1];
        const cx = n.x + n.w / 2;
        return (
         <g key={n.id}>
          {next && <line x1={cx} y1={n.y + n.h} x2={cx} y2={next.y - 3} stroke="var(--app-text-muted, #9a9aa6)" strokeWidth="1.5" markerEnd="url(#orch-arrow)" />}
          {next && n.branch && (
           <g>
            <rect x={cx + 8} y={n.y + n.h + 10} width={n.branch.yes.length * 6 + 14} height="16" rx="8" fill="var(--theme-status-success-light, #e6fffe)" stroke="var(--theme-status-success-border, #5eead4)" />
            <text x={cx + 15} y={n.y + n.h + 21} fontSize="10" fontWeight="600" fill="var(--theme-status-success-dot, #0d9488)">{n.branch.yes}</text>
           </g>
          )}
          {n.branch && (
           <g>
            <path d={edge(n.x + n.w, n.y + n.h / 2, sink.x - 3, sink.y + sink.h / 2)} fill="none" stroke="var(--theme-status-critical, #dc2626)" strokeWidth="1.4" strokeDasharray="5 4" markerEnd="url(#orch-arrow-red)" />
            <rect x={n.x + n.w + 10} y={n.y + n.h / 2 - 18} width={n.branch.no.length * 6 + 14} height="16" rx="8" fill="var(--theme-status-critical-light, #fef2f2)" stroke="var(--theme-status-critical-border, #fecaca)" />
            <text x={n.x + n.w + 17} y={n.y + n.h / 2 - 7} fontSize="10" fontWeight="600" fill="var(--theme-status-critical, #dc2626)">{n.branch.no}</text>
           </g>
          )}
         </g>
        );
       })}
      </svg>
      {nodes.map((n, i) => {
       const Icon = ICON[n.kind] || Cpu;
       const uses = (n.consumes || []).filter((c) => c.value != null);
       return (
        <div key={n.id} className={`absolute flex items-stretch overflow-hidden rounded-xl border bg-surface shadow-sm ${n.who === "model" ? "border-primary/50 ring-2 ring-primary/15" : n.guardsWrite ? "border-status-critical-border" : "border-border"}`} style={{ left: n.x, top: n.y, width: n.w, height: n.h }} title={n.detail}>
         <span className={`flex w-14 shrink-0 items-center justify-center ${TILE[n.tile]}`}><Icon size={20} /></span>
         <span className="flex min-w-0 flex-1 flex-col justify-center px-3">
          <span className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wide text-text-muted"><span>{i === 0 ? "" : `${i}. `}{BADGE[n.tile]}</span>{n.guardsWrite && <span className="rounded bg-status-critical-light px-1 text-status-critical">protects the change</span>}</span>
          <span className="line-clamp-2 text-[13px] font-bold leading-tight text-text-primary">{n.title}</span>
          <span className="truncate font-mono text-[10px] text-text-muted">{n.executorRef || (n.role ? `agent: ${n.role}` : n.kind === STEP_KIND.trigger ? String(uses[0]?.value || "on a schedule") : "")}{uses.length && n.kind !== STEP_KIND.trigger ? ` · ${uses.length} input${uses.length === 1 ? "" : "s"}` : ""}</span>
         </span>
        </div>
       );
      })}
      {layout.branches.length > 0 && (
       <div className="absolute flex items-stretch overflow-hidden rounded-xl border border-dashed border-status-critical-border bg-status-critical-light/70" style={{ left: sink.x, top: sink.y, width: sink.w, height: sink.h }}>
        <span className="flex w-14 shrink-0 items-center justify-center bg-status-critical text-white"><Mail size={20} /></span>
        <span className="flex min-w-0 flex-1 flex-col justify-center px-3">
         <span className="text-[10px] font-semibold uppercase tracking-wide text-status-critical">Exception lane</span>
         <span className="text-[13px] font-bold text-text-primary">Held, logged, emailed</span>
         <span className="text-[11px] text-text-secondary">{exceptionsTo ? `to ${exceptionsTo}` : "to the exceptions address"} · nothing written</span>
        </span>
       </div>
      )}
     </div>
    </div>
   </div>
  </div>
 );
}
