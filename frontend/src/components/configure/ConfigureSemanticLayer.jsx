import { useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { AlertTriangle, ArrowLeft, Check, GitMerge, Ruler, ShieldCheck, Sparkles } from "lucide-react";
import { useConfigureWorkspace } from "../../context/ConfigureWorkspaceContext";
import { metricsUnlockedBy, modelStateFor } from "./source-model-state";
import { decisionsForSource, entitiesForSource, sensitiveColumnsForSource } from "./semantic-model";

/**
 * What EnterpriseGPT understands about one source, and the decisions it needs a human to make.
 *
 * This is the only screen in the product where being slow is correct. A wrong join path produces
 * a plausible, wrong number and nobody downstream can tell -- not the dashboard, not the
 * playground, not the person reading it. The model proposes; a person confirms; and because they
 * confirmed, every answer built on it can be traced back to a decision someone actually made.
 *
 * Reached from the source card rather than from a tab of its own: the semantic layer belongs to
 * the source it was derived from, and a global tab would be empty until something is connected.
 */

const KIND_META = {
 join_path: { Icon: GitMerge, label: "Join path", tone: "text-status-warning" },
 measure: { Icon: Ruler, label: "Measure", tone: "text-status-info" },
};

function ConfidencePill({ value }) {
 const tone =
  value >= 85
   ? "bg-status-success-light text-status-success"
   : value >= 70
   ? "bg-status-warning-light text-status-warning"
   : "bg-status-critical-light text-status-critical";
 return <span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${tone}`}>Confidence {value}%</span>;
}

function DecisionCard({ decision, onResolve }) {
 const meta = KIND_META[decision.kind] || KIND_META.measure;
 const { Icon } = meta;
 return (
  <div className="rounded-xl border border-border bg-surface p-4">
   <div className="flex flex-wrap items-start justify-between gap-2">
    <div className="min-w-0">
     <div className="flex flex-wrap items-center gap-2">
      <Icon size={15} className={meta.tone} />
      <h3 className="font-mono text-sm font-bold text-text-primary">{decision.title}</h3>
      <span className="rounded-md bg-surface-tertiary px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-text-secondary">
       {meta.label}
      </span>
     </div>
     <p className="mt-1.5 max-w-2xl text-sm text-text-secondary">{decision.summary}</p>
    </div>
    <ConfidencePill value={decision.confidence} />
   </div>

   {decision.options && (
    <ul className="mt-3 space-y-2">
     {decision.options.map((opt) => (
      <li
       key={opt.label}
       className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-border bg-surface-secondary px-3 py-2"
      >
       <span className="min-w-0 flex-1 break-all font-mono text-xs text-text-primary">{opt.label}</span>
       <span className="text-xs text-text-muted">{opt.cardinality}</span>
       <span className="text-xs font-semibold text-text-secondary">match {opt.match}</span>
       <button
        type="button"
        onClick={() => onResolve(decision.id)}
        className={`shrink-0 rounded-md px-2.5 py-1 text-xs font-semibold ${
         opt.recommended
          ? "bg-primary text-white hover:bg-primary-hover"
          : "border border-border-light bg-surface text-text-secondary hover:text-text-primary"
        }`}
       >
        {opt.recommended ? "Use this" : "Choose"}
       </button>
      </li>
     ))}
    </ul>
   )}

   {decision.facts && (
    <>
     <div className="mt-3 flex flex-wrap gap-1.5">
      {decision.facts.map((f) => (
       <span
        key={f.label}
        className="rounded-full bg-surface-tertiary px-2.5 py-0.5 text-[11px] font-medium text-text-secondary"
       >
        {f.label}: <strong className="text-text-primary">{f.value}</strong>
       </span>
      ))}
     </div>
     <div className="mt-3 flex items-center gap-2">
      <button
       type="button"
       onClick={() => onResolve(decision.id)}
       className="rounded-lg bg-primary px-3.5 py-1.5 text-sm font-semibold text-white hover:bg-primary-hover"
      >
       Confirm
      </button>
      <button
       type="button"
       className="rounded-lg border border-border-light bg-surface px-3.5 py-1.5 text-sm font-semibold text-text-secondary hover:text-text-primary"
      >
       Change
      </button>
     </div>
    </>
   )}
  </div>
 );
}

const ROLE_STYLE = {
 identifier: "bg-surface-tertiary text-text-secondary",
 date: "bg-status-info-light text-status-info",
 measure: "bg-primary-light text-primary",
 dimension: "bg-surface-tertiary text-text-muted",
};

export default function ConfigureSemanticLayer() {
 const navigate = useNavigate();
 const [params] = useSearchParams();
 const { data, hydrated } = useConfigureWorkspace();
 const sourceId = params.get("source");

 const source = useMemo(
  () => (data.internalSources || []).find((s) => s.id === sourceId) || null,
  [data.internalSources, sourceId]
 );

 const [resolved, setResolved] = useState(() => new Set());
 const [tab, setTab] = useState("review");

 const sourceType = source?.sourceType;
 const entities = useMemo(() => entitiesForSource(sourceType), [sourceType]);
 const decisions = useMemo(() => decisionsForSource(sourceType), [sourceType]);
 const sensitive = useMemo(() => sensitiveColumnsForSource(sourceType), [sourceType]);
 const unlocked = useMemo(() => metricsUnlockedBy(sourceType), [sourceType]);

 if (!hydrated) return <p className="text-sm text-text-secondary">Loading…</p>;

 if (!source) {
  return (
   <div className="rounded-xl border border-dashed border-border-light bg-surface-secondary px-6 py-16 text-center">
    <p className="text-sm text-text-secondary">Pick a source to see what we understand about it.</p>
    <button
     type="button"
     onClick={() => navigate("/configure/internal-sources")}
     className="mt-3 text-sm font-semibold text-primary hover:underline"
    >
     Back to data sources
    </button>
   </div>
  );
 }

 const open = decisions.filter((d) => !resolved.has(d.id));
 const model = modelStateFor(source.id);

 return (
  <div className="space-y-5">
   <div className="flex flex-wrap items-start justify-between gap-3">
    <div>
     <button
      type="button"
      onClick={() => navigate("/configure/internal-sources")}
      className="mb-1 inline-flex items-center gap-1 text-xs font-semibold text-text-muted hover:text-text-primary"
     >
      <ArrowLeft size={13} />
      Data sources
     </button>
     <h2 className="text-xl font-bold text-text-primary">{source.name}</h2>
     <p className="mt-0.5 max-w-2xl text-sm text-text-secondary">
      What we understand about this source. Confirm what was inferred before answers are built on it, 
      a wrong join produces a believable, wrong number.
     </p>
    </div>
    {open.length === 0 && (
     <span className="inline-flex items-center gap-1.5 rounded-full bg-status-success-light px-3 py-1.5 text-sm font-semibold text-status-success">
      <Check size={15} />
      Everything confirmed
     </span>
    )}
   </div>

   <div className="flex gap-1 border-b border-border">
    {[
     { id: "review", label: "Needs review", count: open.length },
     { id: "entities", label: "Entities", count: entities.length },
     { id: "metrics", label: "Metrics unlocked", count: unlocked.length },
    ].map((t) => (
     <button
      key={t.id}
      type="button"
      onClick={() => setTab(t.id)}
      className={`relative -mb-px border-b-2 px-3 pb-2.5 pt-2 text-sm font-semibold transition-colors ${
       tab === t.id ? "border-primary text-primary" : "border-transparent text-text-muted hover:text-text-secondary"
      }`}
     >
      {t.label}
      <span
       className={`ml-1.5 rounded-full px-1.5 py-0.5 text-[11px] ${
        tab === t.id ? "bg-primary-light text-primary" : "bg-surface-secondary text-text-muted"
       }`}
      >
       {t.count}
      </span>
     </button>
    ))}
   </div>

   {tab === "review" && (
    <div className="space-y-3">
     {open.length === 0 ? (
      <div className="rounded-xl border border-dashed border-border-light bg-surface-secondary px-6 py-14 text-center">
       <Check size={26} className="mx-auto mb-3 text-status-success" />
       <p className="text-sm font-semibold text-text-primary">Nothing waiting on you</p>
       <p className="mt-1 text-sm text-text-muted">
        Every entity, measure and join for this source has been confirmed.
       </p>
      </div>
     ) : (
      open.map((d) => (
       <DecisionCard key={d.id} decision={d} onResolve={(id) => setResolved((p) => new Set(p).add(id))} />
      ))
     )}
    </div>
   )}

   {tab === "entities" && (
    <div className="space-y-3">
     {entities.map((e) => (
      <div key={e.id} className="rounded-xl border border-border bg-surface p-4">
       <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
         <h3 className="font-bold text-text-primary">{e.name}</h3>
         <p className="break-all font-mono text-xs text-text-muted">{e.technicalName}</p>
         <p className="mt-1.5 text-sm text-text-secondary">{e.description}</p>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1 text-xs text-text-muted">
         <span>{e.rowCountLabel}</span>
         {e.status === "stale" && (
          <span className="inline-flex items-center gap-1 font-semibold text-status-warning">
           <AlertTriangle size={11} />
           Stale
          </span>
         )}
        </div>
       </div>
       <p className="mt-3 text-[11px] font-semibold uppercase tracking-wide text-text-muted">
        Grain: one row per {e.grain}
       </p>
       <div className="mt-2 flex flex-wrap gap-1.5">
        {e.columns.map((c) => (
         <span
          key={c.name}
          title={c.role === "measure" ? `${c.type}, aggregated by ${c.aggregation}` : c.role}
          className={`rounded-md px-2 py-0.5 font-mono text-[11px] font-medium ${ROLE_STYLE[c.role]}`}
         >
          {c.name}
         </span>
        ))}
       </div>
      </div>
     ))}
    </div>
   )}

   {tab === "metrics" && (
    <div className="rounded-xl border border-border bg-surface p-4">
     <p className="text-sm text-text-secondary">
      Confirming this model is what makes these measurable. They appear under KPIs once every
      decision above is settled.
     </p>
     <div className="mt-3 flex flex-wrap gap-1.5">
      {unlocked.map((name) => (
       <span key={name} className="rounded-full bg-primary-light px-2.5 py-1 text-xs font-semibold text-primary">
        {name}
       </span>
      ))}
     </div>
     <button
      type="button"
      onClick={() => navigate(`/kpis/discover?unlocked=${encodeURIComponent(sourceType)}`)}
      className="mt-4 inline-flex items-center gap-1.5 rounded-lg bg-primary px-3.5 py-2 text-sm font-semibold text-white hover:bg-primary-hover"
     >
      <Sparkles size={14} />
      See them in KPIs
     </button>
    </div>
   )}

   {/* A clean scan is a result, not an absence. Saying so is what makes the check trustworthy. */}
   <div className="flex items-start gap-2.5 rounded-xl border border-border bg-surface-secondary px-4 py-3">
    <ShieldCheck size={16} className={sensitive.length ? "mt-0.5 text-status-warning" : "mt-0.5 text-status-success"} />
    <div className="text-sm">
     {sensitive.length === 0 ? (
      <>
       <p className="font-semibold text-text-primary">No personal data detected in this source</p>
       <p className="text-text-muted">
        {entities.reduce((n, e) => n + e.columns.length, 0)} columns scanned across {entities.length} entities.
       </p>
      </>
     ) : (
      <>
       <p className="font-semibold text-text-primary">{sensitive.length} columns hold personal data</p>
       <p className="text-text-muted">
        {sensitive.map((s) => `${s.entity}.${s.column}`).join(", ")}, excluded from answers and memory
        unless you allow them.
       </p>
      </>
     )}
    </div>
   </div>

   {model.state === "needs_review" && open.length === 0 && (
    <div className="rounded-xl border border-status-success-border bg-status-success-light px-4 py-3 text-sm text-status-success">
     All decisions confirmed. This source is ready to answer questions.
    </div>
   )}
  </div>
 );
}
