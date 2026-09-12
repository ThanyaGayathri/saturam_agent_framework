import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { AlertTriangle, ArrowRight, Check, ShieldCheck, X } from "lucide-react";
import { KPI_DATA_CATALOG_DATASETS } from "../../domain/kpi-data-catalog";
import { AUTONOMY, displayName } from "../../domain/use-case-autonomy";
import { loadSpecDraft } from "../../domain/use-case-draft-spec";
import { sensitiveColumnsForSource } from "../configure/semantic-model";

/**
 * Step 3 of 6: preflight.
 *
 * Validation the platform already performs, surfaced where someone can act on it: schema
 * validation with field-level errors, and the capability policy that checks every model, tool
 * and connector against the tenant's allow-list. This screen is their face.
 *
 * The checks that run depend on the autonomy level. A report does not need an approver; an
 * unattended action needs a cap and a way back. Running every check on every use case is how a
 * preflight becomes a wall of green ticks people scroll past.
 *
 * A FAIL blocks. A WARN has to be acknowledged -- "I saw this and went anyway" and "I did not
 * notice" are different acts, and only one is defensible later.
 */

const LEVEL = {
 pass: { Icon: Check, cls: "text-status-success", bg: "border-border bg-surface" },
 warn: { Icon: AlertTriangle, cls: "text-status-warning", bg: "border-status-warning-border bg-status-warning-light" },
 fail: { Icon: X, cls: "text-status-critical", bg: "border-status-critical-border bg-status-critical-light" },
};

const SOURCE_TYPE = { SAP: "sap_s4hana", Snowflake: "snowflake", PostgreSQL: "postgres", Salesforce: "salesforce" };

export default function UseCasePreflightScreen() {
 const navigate = useNavigate();
 const spec = useMemo(() => loadSpecDraft(), []);
 const level = AUTONOMY[spec.autonomy] || AUTONOMY.inform;
 const [acked, setAcked] = useState(() => new Set());

 const checks = useMemo(() => {
  const measurable = new Set();
  const sources = new Set();
  const staleSets = [];
  for (const ds of KPI_DATA_CATALOG_DATASETS) {
   for (const n of ds.linkedKpiNames || []) measurable.add(n);
   if ((ds.linkedKpiNames || []).some((n) => spec.metricIds.includes(n))) {
    sources.add(ds.sourceLabel);
    if (ds.status === "stale") staleSets.push(ds);
   }
  }
  const unmeasurable = spec.metricIds.filter((m) => !measurable.has(m));
  const pii = [...sources].flatMap((label) => sensitiveColumnsForSource(SOURCE_TYPE[label]));
  const spec_ = "/use-cases/builder/compose";

  const out = [
   {
    id: "data", title: "Data availability",
    level: unmeasurable.length ? "fail" : "pass",
    detail: unmeasurable.length
     ? `${unmeasurable.join(", ")} cannot be computed from connected data.`
     : `All ${spec.metricIds.length} KPIs resolve to connected datasets across ${sources.size} source${sources.size === 1 ? "" : "s"}.`,
    fix: unmeasurable.length ? { label: "Back to the specification", to: spec_ } : null,
   },
   {
    id: "join", title: "Cross-system joins",
    level: sources.size > 1 ? "warn" : "pass",
    detail: sources.size > 1
     ? `This spans ${[...sources].join(" and ")}. The join between them is inferred — confirm it in the semantic layer or the numbers that cross it are unverified.`
     : "Everything it reads comes from one system. No join to confirm.",
    fix: sources.size > 1 ? { label: "Review joins", to: "/configure/internal-sources" } : null,
   },
   {
    id: "freshness", title: "Freshness",
    level: staleSets.length ? "warn" : "pass",
    detail: staleSets.length ? `${staleSets.length} dataset${staleSets.length === 1 ? " is" : "s are"} stale — answers will lag until they refresh.` : "Every dataset this reads is current.",
   },
   {
    id: "policy", title: "Permissions and guardrails",
    level: "pass",
    detail: "Every model, tool and connector this references is on your tenant's allow-list.",
   },
   {
    id: "pii", title: "Personal data",
    level: pii.length ? "warn" : "pass",
    detail: pii.length
     ? `Reads ${pii.length} column${pii.length === 1 ? "" : "s"} holding personal data (${pii.map((p) => `${p.entity}.${p.column}`).join(", ")}). Activation will ask you to allow this explicitly.`
     : `No personal data in anything this reads.`,
   },
   {
    id: "ownership", title: "Ownership",
    level: spec.owner ? "pass" : "fail",
    detail: spec.owner ? `${spec.owner} owns this once it is live.` : "Nobody owns this yet.",
    fix: spec.owner ? null : { label: "Set an owner", to: spec_ },
   },
  ];

  if (level.level === 1) {
   out.splice(2, 0, {
    id: "coverage", title: "Coverage",
    level: spec.metricIds.length <= 1 ? "warn" : "pass",
    detail: spec.metricIds.length <= 1
     ? "Built on a single metric — it will show what moved, but not why."
     : `${spec.metricIds.length} metrics${spec.dimensions.length ? ` across ${spec.dimensions.join(", ")}` : ""} give it enough to explain a change, not just report it.`,
    fix: spec.metricIds.length <= 1 ? { label: "Add a metric", to: spec_ } : null,
   });
  }

  if (level.level === 2) {
   out.push({
    id: "condition", title: "Trigger",
    level: spec.condition?.threshold != null ? "pass" : "fail",
    detail: spec.condition?.threshold != null ? `Fires when ${spec.condition.metric || spec.metricIds[0]} ${spec.condition.operator === "lt" ? "falls below" : spec.condition.operator === "delta_gt" ? "moves by more than" : "goes above"} ${spec.condition.threshold}${spec.condition.unit || ""}${spec.scope ? `, in ${spec.scope.toLowerCase()}` : ""}.` : "No trigger condition.",
    fix: spec.condition?.threshold != null ? null : { label: "Set the trigger", to: spec_ },
   });
  }

  if (level.level >= 3) {
   out.push(
    {
     id: "guardrails", title: "Business guardrails",
     level: spec.guardrails.length ? "pass" : "fail",
     detail: spec.guardrails.length
      ? `${spec.guardrails.length} guardrail${spec.guardrails.length === 1 ? "" : "s"}, evaluated as deterministic checks before any change — never by the AI step.`
      : "Nothing it may not do. A use case that makes changes needs at least one business guardrail.",
     fix: spec.guardrails.length ? null : { label: "Add a guardrail", to: spec_ },
    },
    {
     id: "writeback", title: "Target system",
     level: spec.writeback ? "pass" : "fail",
     detail: spec.writeback ? `Changes land in ${spec.writeback}, which is on the allow-list.` : "Nowhere to write the action.",
     fix: spec.writeback ? null : { label: "Choose a target", to: spec_ },
    }
   );
  }

  if (level.level === 3) {
   const sod = spec.approver && spec.approver.trim().toLowerCase() !== spec.author.toLowerCase();
   out.push({
    id: "approver", title: "Separation of duties",
    level: sod ? "pass" : "fail",
    detail: sod ? `${spec.approver} approves each action; ${spec.author} authored it.` : "The approver must be someone other than the author.",
    fix: sod ? null : { label: "Set an approver", to: spec_ },
   });
  }

  if (level.level >= 4) {
   out.push({
    id: "unattended", title: "Unattended action",
    level: spec.blastRadius && spec.rollback ? "warn" : "fail",
    detail: spec.blastRadius && spec.rollback
     ? `Change limit ${spec.blastRadius} per run; rollback plan "${spec.rollback}". Going live requires an admin.`
     : "Acting with nobody in the loop needs both a per-run cap and a way to undo.",
    fix: spec.blastRadius && spec.rollback ? null : { label: "Set the change limit and rollback plan", to: spec_ },
   });
  }
  return out;
 }, [spec, level]);

 const fails = checks.filter((c) => c.level === "fail");
 const warns = checks.filter((c) => c.level === "warn");
 const unacked = warns.filter((w) => !acked.has(w.id));
 const blocked = fails.length > 0 || unacked.length > 0;

 return (
  <div className="mx-auto w-full max-w-3xl space-y-4 px-6 py-6">
   <div>
    <p className="text-xs font-semibold uppercase tracking-wide text-text-muted">Step 3 of 6</p>
    <h1 className="text-2xl font-bold text-text-primary">Readiness check</h1>
    <p className="mt-1 max-w-2xl text-sm text-text-secondary">
     Checked before this goes any further. <strong className="text-text-primary">{displayName(spec)}</strong> · level {level.level} — {checks.length} checks apply. Failures must be fixed; warnings must be acknowledged.
    </p>
   </div>

   <div className="space-y-2">
    {checks.map((c) => {
     const meta = LEVEL[c.level];
     const { Icon } = meta;
     const needsAck = c.level === "warn" && !acked.has(c.id);
     return (
      <div key={c.id} className={`rounded-xl border px-4 py-3 ${meta.bg}`}>
       <div className="flex flex-wrap items-start gap-2.5">
        <Icon size={16} className={`mt-0.5 shrink-0 ${meta.cls}`} />
        <div className="min-w-0 flex-1">
         <p className="text-sm font-bold text-text-primary">{c.title}</p>
         <p className="mt-0.5 text-sm text-text-secondary">{c.detail}</p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
         {c.fix && <button type="button" onClick={() => navigate(c.fix.to)} className="rounded-lg border border-border-light bg-surface px-2.5 py-1.5 text-xs font-semibold text-text-secondary hover:text-text-primary">{c.fix.label}</button>}
         {needsAck && <button type="button" onClick={() => setAcked((p) => new Set(p).add(c.id))} className="rounded-lg bg-status-warning px-2.5 py-1.5 text-xs font-semibold text-white">I understand</button>}
         {c.level === "warn" && acked.has(c.id) && <span className="text-xs font-semibold text-status-warning">Acknowledged</span>}
        </div>
       </div>
      </div>
     );
    })}
   </div>

   <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border pt-4">
    <button type="button" onClick={() => navigate("/use-cases/builder/compose")} className="rounded-lg border border-border bg-surface px-3.5 py-2 text-sm font-semibold text-text-secondary hover:bg-surface-secondary">Back to the specification</button>
    <div className="flex items-center gap-3">
     {blocked && <span className="text-xs text-text-muted">{fails.length > 0 ? `${fails.length} to fix` : `${unacked.length} to acknowledge`}</span>}
     <button type="button" disabled={blocked} onClick={() => navigate("/use-cases/builder/plan")} className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-white hover:bg-primary-hover disabled:cursor-not-allowed disabled:opacity-40">
      <ShieldCheck size={15} /> See how it will run <ArrowRight size={15} />
     </button>
    </div>
   </div>
  </div>
 );
}
