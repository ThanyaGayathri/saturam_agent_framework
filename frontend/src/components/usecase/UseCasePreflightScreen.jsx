import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { AlertTriangle, ArrowRight, Check, ShieldCheck, X } from "lucide-react";
import { KPI_DATA_CATALOG_DATASETS } from "../../domain/kpi-data-catalog";
import { normalizeRules, unfedRules } from "../../domain/guardrail-rules";
import { templateLabel } from "../../domain/extraction-templates";
import { mappingLabel } from "../../domain/record-mappings";
import { AUTONOMY, MASTER_DATA_ROLES, displayName } from "../../domain/use-case-autonomy";
import { loadSpecDraft } from "../../domain/use-case-draft-spec";
import { fetchGoldenCases } from "../../services/use-cases-api";
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

 // Golden cases live on the platform (K19): their pass rate is a readiness check here, read
 // once the use case has been synced (any rehearsal does that).
 const [golden, setGolden] = useState(null);
 useEffect(() => {
  const id = spec.platform?.useCaseId;
  if (!id || spec.template !== "document_to_record") return;
  fetchGoldenCases(id).then(setGolden).catch(() => setGolden([]));
 }, [spec.platform?.useCaseId, spec.template]);

 const checks = useMemo(() => {
  const spec_ = "/use-cases/builder/compose";
  if (spec.template === "document_to_record") {
   const md = spec.masterData || {};
   const bound = MASTER_DATA_ROLES.filter((r) => md[r.id]);
   const missingRequired = MASTER_DATA_ROLES.filter((r) => r.required && !md[r.id]);
   const rows = normalizeRules(spec.recordGuardrails);
   const unfed = unfedRules(rows, md).map((u) => u.rule);
   const mdSets = KPI_DATA_CATALOG_DATASETS.filter((d) => bound.some((r) => md[r.id] && d.technicalName.toLowerCase().endsWith(md[r.id].toLowerCase().replace(/ /g, "_"))));
   const rowsLabel = mdSets.map((d) => `${d.technicalName.split(".").pop()} ${d.rowCountLabel}`).join(" · ");
   return [
    { id: "mailbox", title: "Source mailbox", level: spec.mailbox ? "pass" : "fail", detail: spec.mailbox ? `${spec.mailbox} is connected and readable; the mailbox is only read; each message is carried once by its id, never flagged, never deleted.` : "No mailbox chosen.", fix: spec.mailbox ? null : { label: "Choose a mailbox", to: spec_ } },
    { id: "target", title: "Target system", level: spec.writeback ? "pass" : "fail", detail: spec.writeback ? `${spec.writeback} is connected with write permission; ${mappingLabel(spec) || (spec.targetObjects ? spec.targetObjects.replace(/_/g, " + ") : "the target objects")} will be created as ${spec.createdAs || "draft"}.` : "No target system chosen.", fix: spec.writeback ? null : { label: "Choose a target system", to: spec_ } },
    { id: "masterdata", title: "Master data", level: missingRequired.length ? "fail" : "pass", detail: missingRequired.length ? `${missingRequired.map((r) => r.label).join(", ")} not bound.` : `${bound.length} objects bound and harvested: ${rowsLabel || bound.map((r) => md[r.id]).join(", ")}.`, fix: missingRequired.length ? { label: "Bind master data", to: spec_ } : null },
    { id: "template", title: "Extraction template", level: spec.extractionTemplate ? (spec.exampleDocument ? "pass" : "warn") : "fail", detail: spec.extractionTemplate ? (spec.exampleDocument ? `${templateLabel(spec) || spec.extractionTemplate.replace(/_/g, " ")} with an example document — the AI step has a golden case to be scored against.` : `${spec.extractionTemplate.replace(/_/g, " ")} without an example document. The simulation still runs on the sample set; a real example improves extraction on your format.`) : "No template chosen.", fix: spec.extractionTemplate ? null : { label: "Choose a template", to: spec_ } },
    { id: "guardrails", title: "Business guardrails", level: rows.length === 0 ? "fail" : unfed.length ? "warn" : "pass", detail: rows.length === 0 ? "No guardrail. A use case that writes needs at least one." : unfed.length ? `${rows.length} guardrails; ${unfed.map((g) => g.label).join(", ")} need${unfed.length === 1 ? "s" : ""} ${[...new Set(unfed.map((g) => MASTER_DATA_ROLES.find((r) => r.id === g.needs)?.label.toLowerCase()))].join(", ")} bound to run.` : `${rows.length} guardrails, evaluated as deterministic checks before any record is written — never by the AI step.`, fix: rows.length === 0 ? { label: "Add a guardrail", to: spec_ } : null },
    { id: "exceptions", title: "Exceptions", level: /@/.test(spec.exceptionsTo || "") ? "pass" : "fail", detail: /@/.test(spec.exceptionsTo || "") ? `Anything the automation cannot complete goes to ${spec.exceptionsTo}, and to the Alerts page.` : "No exceptions address.", fix: /@/.test(spec.exceptionsTo || "") ? null : { label: "Set the exceptions address", to: spec_ } },
    { id: "pii", title: "Personal data", level: "warn", detail: "Buyer names and email addresses are read from the documents and written to the target as contacts. Allowed for order processing; acknowledge that this use case handles personal data.", fix: null },
    { id: "ownership", title: "Ownership", level: spec.owner ? "pass" : "fail", detail: spec.owner ? `${spec.owner} owns this once it is live.` : "No owner.", fix: spec.owner ? null : { label: "Set an owner", to: spec_ } },
    ...(() => {
     if (golden === null) return [{ id: "golden", title: "Golden cases", level: "warn", detail: spec.platform?.useCaseId ? "Reading the cases from the platform…" : "No rehearsal yet. Rehearse on the platform (step 5) to run the golden cases; their pass rate gates activation.", fix: null }];
     const withExpected = golden.filter((c) => c.expected);
     const pass = withExpected.filter((c) => c.last_status === "pass").length;
     const fail = withExpected.filter((c) => c.last_status === "fail");
     const untested = withExpected.length - pass - fail.length;
     if (!withExpected.length) return [{ id: "golden", title: "Golden cases", level: "warn", detail: golden.length ? `${golden.length} case(s) uploaded, none with an expected output yet. Run them and accept their output on the rehearse screen.` : "No golden cases yet. Upload documents or generate samples on the rehearse screen; their pass rate gates activation.", fix: null }];
     return [{ id: "golden", title: "Golden cases", level: fail.length ? "fail" : untested ? "warn" : "pass", detail: `${pass} of ${withExpected.length} pass${fail.length ? `; failing: ${fail.slice(0, 3).map((c) => c.label).join(", ")}${fail.length > 3 ? "…" : ""}` : ""}${untested ? `; ${untested} not run since the last change` : ""}.`, fix: fail.length ? { label: "See the failing cases", to: "/use-cases/builder/rehearse" } : null }];
    })(),
    { id: "unattended", title: "Unattended action", level: spec.blastRadius && spec.rollback ? "warn" : "fail", detail: spec.blastRadius && spec.rollback ? `Change limit ${spec.blastRadius} per run; rollback plan "${spec.rollback}". Going live requires an admin.` : "Set the change limit and rollback plan.", fix: spec.blastRadius && spec.rollback ? null : { label: "Set the change limit and rollback plan", to: spec_ } },
   ];
  }
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
 }, [spec, level, golden]);

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
