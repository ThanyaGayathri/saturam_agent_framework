import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { AlertTriangle, ArrowRight, Check, Loader2, RefreshCw, ShieldCheck, X } from "lucide-react";
import { KPI_DATA_CATALOG_DATASETS } from "../../domain/kpi-data-catalog";
import { normalizeRules, unfedRules } from "../../domain/guardrail-rules";
import { templateLabel } from "../../domain/extraction-templates";
import { mappingLabel } from "../../domain/record-mappings";
import { AUTONOMY, MASTER_DATA_ROLES, displayName } from "../../domain/use-case-autonomy";
import { rolesNeeded } from "../../domain/use-case-roles";
import { isAnalyticsInform } from "../../domain/use-case-analytics";
import { loadSpecDraft } from "../../domain/use-case-draft-spec";
import { runUseCasePreflightProbe } from "../../services/use-cases-api";
import { syncUseCaseToPlatform } from "../../services/use-case-platform";
import { loadConfigureWorkspace } from "../../storage/configure-workspace";
import { useDemoProfile } from "../../context/DemoProfileContext";
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

// Checks read in groups (K19): what is connected, what data it reads, what keeps it safe.
const GROUP_ORDER = ["Connections", "Data", "Safeguards", "Checks"];
const GROUP_OF_ID = { mailbox: "Connections", target: "Connections", model: "Connections", writeback: "Connections", masterdata: "Data", template: "Data", golden: "Data", sources: "Data", measurable: "Data", stale: "Data", guardrails: "Safeguards", exceptions: "Safeguards", unattended: "Safeguards", approver: "Safeguards", gold: "Data", dry_answer: "Data", delivery: "Connections", coverage: "Data", outputs: "Checks", grounding: "Safeguards" };
const groupOf = (c) => GROUP_OF_ID[c.id] || (/pii|personal|privacy|owner/i.test(`${c.id} ${c.title}`) ? "Safeguards" : "Checks");

const SOURCE_TYPE = { SAP: "sap_s4hana", Snowflake: "snowflake", PostgreSQL: "postgres", Salesforce: "salesforce" };

export default function UseCasePreflightScreen() {
 const navigate = useNavigate();
 const spec = useMemo(() => loadSpecDraft(), []);
 const level = AUTONOMY[spec.autonomy] || AUTONOMY.inform;
 const [acked, setAcked] = useState(() => new Set());

 // Live probes (K19): for a document-driven use case the connection and data rows are not
 // computed from the form -- the platform logs into the mailbox, takes a token from the
 // target, reads the master data, calls the model once and reads the golden cases, one
 // after another, and each row lands when its probe answers.
 const PROBES = spec.template === "document_to_record" ? ["mailbox", "target", "master_data", "model", "golden"] : isAnalyticsInform(spec) ? ["gold", "delivery", "model", "dry_answer"] : [];
 const [probes, setProbes] = useState({});
 const [probeError, setProbeError] = useState(null);
 const [runKey, setRunKey] = useState(0);
 const { profileId, storageKeys } = useDemoProfile();
 useEffect(() => {
  if (!PROBES.length) return undefined;
  const ac = new AbortController();
  setProbes({}); setProbeError(null);
  (async () => {
   try {
    const sources = loadConfigureWorkspace(storageKeys.CONFIGURE_WORKSPACE, profileId).internalSources || [];
    const synced = await syncUseCaseToPlatform(loadSpecDraft(), sources, { signal: ac.signal });
    for (const probe of PROBES) {
     const out = await runUseCasePreflightProbe(synced.platform.useCaseId, probe, { signal: ac.signal });
     if (ac.signal.aborted) return;
     setProbes((p) => ({ ...p, [probe]: out }));
    }
   } catch (err) {
    if (err?.name !== "AbortError") setProbeError(err?.detail || err?.message || "The platform could not be reached.");
   }
  })();
  return () => ac.abort();
  // eslint-disable-next-line react-hooks/exhaustive-deps
 }, [runKey]);

 const rawChecks = useMemo(() => {
  const spec_ = "/use-cases/builder/compose";
  if (spec.template === "document_to_record") {
   const md = spec.masterData || {};
   const needed = rolesNeeded(spec);
   const neededRoles = needed.map((r) => r.role);
   const bound = neededRoles.filter((r) => md[r.id]);
   const rows = normalizeRules(spec.recordGuardrails);
   const unfed = unfedRules(rows, md).map((u) => u.rule);
   const mdSets = KPI_DATA_CATALOG_DATASETS.filter((d) => bound.some((r) => md[r.id] && d.technicalName.toLowerCase().endsWith(md[r.id].toLowerCase().replace(/ /g, "_"))));
   const rowsLabel = mdSets.map((d) => `${d.technicalName.split(".").pop()} ${d.rowCountLabel}`).join(" · ");
   return [
    { id: "mailbox", title: "Source mailbox", level: spec.mailbox ? "pass" : "fail", detail: spec.mailbox ? `${spec.mailbox} is connected and readable; the mailbox is only read; each message is carried once by its id, never flagged, never deleted.` : "No mailbox chosen.", fix: spec.mailbox ? null : { label: "Choose a mailbox", to: spec_ } },
    { id: "target", title: "Target system", level: spec.writeback ? "pass" : "fail", detail: spec.writeback ? `${spec.writeback} is connected with write permission; ${mappingLabel(spec) || (spec.targetObjects ? spec.targetObjects.replace(/_/g, " + ") : "the target objects")} will be created as ${spec.createdAs || "draft"}.` : "No target system chosen.", fix: spec.writeback ? null : { label: "Choose a target system", to: spec_ } },
    { id: "masterdata", title: `Reference data in ${spec.writeback ? spec.writeback.split(/\s*[-–]\s*/)[0] : "the target"}`, level: "warn", detail: neededRoles.length ? `Probing ${neededRoles.map((r) => r.label.toLowerCase()).join(", ")} — the roles the mapping reads and the guardrails need…` : "Nothing to resolve against: the mapping reads no master-data role and no guardrail needs one.", fix: null },
    { id: "model", title: "Extraction model", level: "warn", detail: "Probing…", fix: null },
    { id: "template", title: "Extraction template", level: spec.extractionTemplate ? (spec.exampleDocument ? "pass" : "warn") : "fail", detail: spec.extractionTemplate ? (spec.exampleDocument ? `${templateLabel(spec) || spec.extractionTemplate.replace(/_/g, " ")} with an example document — the AI step has a golden case to be scored against.` : `${spec.extractionTemplate.replace(/_/g, " ")} without an example document. Extraction works from the template's fields alone; an example of your own format makes it better, and the rehearse screen can save one as a golden case.`) : "No template chosen.", fix: spec.extractionTemplate ? null : { label: "Choose a template", to: spec_ } },
    { id: "guardrails", title: "Business guardrails", level: rows.length === 0 ? "fail" : unfed.length ? "warn" : "pass", detail: rows.length === 0 ? "No guardrail. A use case that writes needs at least one." : unfed.length ? `${rows.length} guardrails; ${unfed.map((g) => g.label).join(", ")} need${unfed.length === 1 ? "s" : ""} ${[...new Set(unfed.map((g) => MASTER_DATA_ROLES.find((r) => r.id === g.needs)?.label.toLowerCase()))].join(", ")} bound to run.` : `${rows.length} guardrails, evaluated as deterministic checks before any record is written — never by the AI step.`, fix: rows.length === 0 ? { label: "Add a guardrail", to: spec_ } : null },
    { id: "exceptions", title: "Exceptions", level: /@/.test(spec.exceptionsTo || "") ? "pass" : "fail", detail: /@/.test(spec.exceptionsTo || "") ? `Anything the automation cannot complete goes to ${spec.exceptionsTo}, and to the Alerts page.` : "No exceptions address.", fix: /@/.test(spec.exceptionsTo || "") ? null : { label: "Set the exceptions address", to: spec_ } },
    { id: "pii", title: "Personal data", level: "warn", detail: "Buyer names and email addresses are read from the documents and written to the target as contacts. Allowed for order processing; acknowledge that this use case handles personal data.", fix: null },
    { id: "ownership", title: "Ownership", level: spec.owner ? "pass" : "fail", detail: spec.owner ? `${spec.owner} owns this once it is live.` : "No owner.", fix: spec.owner ? null : { label: "Set an owner", to: spec_ } },
    { id: "golden", title: "Golden cases", level: "warn", detail: "Probing…", fix: null },
    { id: "unattended", title: "Unattended action", level: spec.blastRadius && spec.rollback ? "warn" : "fail", detail: spec.blastRadius && spec.rollback ? `Change limit ${spec.blastRadius} per run; rollback plan "${spec.rollback}". Going live requires an admin.` : "Set the change limit and rollback plan.", fix: spec.blastRadius && spec.rollback ? null : { label: "Set the change limit and rollback plan", to: spec_ } },
   ];
  }
  if (isAnalyticsInform(spec)) {
   // An Analytics Automation the platform runs (K23): the rows are the platform's probes --
   // gold for every KPI, the SMTP login for the report, one model call, and the numbers as
   // they are now -- plus what the form alone can say.
   const recipients = (spec.notify || "").split(/[,;]/).map((x) => x.trim()).filter((x) => /@/.test(x));
   return [
    { id: "gold", title: "KPIs in gold", level: "warn", detail: "Probing…", fix: null },
    { id: "dry_answer", title: "The numbers now", level: "warn", detail: "Probing…", fix: null },
    { id: "delivery", title: "Report delivery", level: "warn", detail: "Probing…", fix: null },
    { id: "model", title: "Explanation model", level: "warn", detail: "Probing…", fix: null },
    { id: "coverage", title: "Coverage", level: spec.metricIds.length === 0 ? "fail" : spec.metricIds.length === 1 ? "warn" : "pass", detail: spec.metricIds.length === 0 ? "No KPI chosen." : spec.metricIds.length === 1 ? "One KPI: the report will say what moved, not where." : `${spec.metricIds.length} KPIs${spec.dimensions.length ? ` broken down by ${spec.dimensions.map((d) => d.replace(/_/g, " ")).join(", ")}` : ""}, compared with ${spec.comparison === "prior_year" ? "the same period last year" : "the period before"}.`, fix: spec.metricIds.length === 0 ? { label: "Choose KPIs", to: spec_ } : null },
    { id: "outputs", title: "Outputs", level: (spec.outputs || []).length ? "pass" : "warn", detail: (spec.outputs || []).length ? `${spec.outputs.includes("report") ? "The report, every run" : ""}${spec.outputs.includes("report") && spec.outputs.includes("dashboard") ? "; " : ""}${spec.outputs.includes("dashboard") ? "a dashboard composed at activation under KPIs → Dashboards" : ""}.${recipients.length ? ` Emailed to ${recipients.join(", ")}.` : " In-app only: no email address given."}` : "No output chosen; the report alone will be produced.", fix: null },
    { id: "grounding", title: "Narrative checked against the numbers", level: "pass", detail: "The AI step writes the narrative from the comparison; a deterministic check rejects it if any figure is not one the numbers carry, and the report then goes out with the numbers alone.", fix: null },
    { id: "ownership", title: "Ownership", level: spec.owner ? "pass" : "fail", detail: spec.owner ? `${spec.owner} owns this once it is live.` : "Nobody owns this yet.", fix: spec.owner ? null : { label: "Set an owner", to: spec_ } },
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
 }, [spec, level]);
 const PROBE_FOR_ID = { mailbox: "mailbox", target: "target", masterdata: "master_data", model: "model", golden: "golden", gold: "gold", delivery: "delivery", dry_answer: "dry_answer" };
 const checks = useMemo(() => {
  const merged = rawChecks.map((c) => {
   const probe = PROBES.length ? PROBE_FOR_ID[c.id] : null;
   if (!probe) return c;
   const r = probes[probe];
   if (!r) return { ...c, probe, pending: true };
   const goldenWarn = probe === "golden" && r.passed && !(r.with_expected > 0);
   return { ...c, probe, pending: false, level: r.passed ? (goldenWarn ? "warn" : "pass") : "fail", detail: `${r.reason} (${r.elapsed_ms >= 1000 ? `${(r.elapsed_ms / 1000).toFixed(1)} s` : `${r.elapsed_ms} ms`})`, fix: r.passed ? null : c.fix || (probe === "golden" ? { label: "See the failing cases", to: "/use-cases/builder/rehearse" } : null) };
  });
  return merged.sort((a, b) => GROUP_ORDER.indexOf(groupOf(a)) - GROUP_ORDER.indexOf(groupOf(b)));
  // eslint-disable-next-line react-hooks/exhaustive-deps
 }, [rawChecks, probes]);

 // The checks land one at a time. A probe-backed row waits for its probe; a form-computed row
 // follows the one before it after a short beat, so the sequence reads top to bottom and the
 // progress is the platform's, not a timer's. Reduced motion skips the beat.
 const reduceMotion = typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
 const [ticks, setTicks] = useState(0);
 useEffect(() => {
  setTicks(0);
  if (reduceMotion) { setTicks(checks.length); return undefined; }
  const timers = checks.map((_, i) => setTimeout(() => setTicks((t) => Math.max(t, i + 1)), 250 + i * 320));
  return () => timers.forEach(clearTimeout);
 }, [checks.length, reduceMotion, runKey]);
 let resolved = 0;
 for (const c of checks) {
  if (c.pending || resolved >= ticks) break;
  resolved += 1;
 }
 const done = resolved >= checks.length;
 const landed = checks.slice(0, resolved);

 const fails = landed.filter((c) => c.level === "fail");
 const warns = landed.filter((c) => c.level === "warn");
 const passes = landed.filter((c) => c.level === "pass");
 const unacked = warns.filter((w) => !acked.has(w.id));
 const blocked = !done || fails.length > 0 || unacked.length > 0;

 return (
  <div className="mx-auto w-full max-w-3xl space-y-4 px-6 py-6">
   <div>
    <p className="text-xs font-semibold uppercase tracking-wide text-text-muted">Step 3 of 6</p>
    <h1 className="text-2xl font-bold text-text-primary">Readiness check</h1>
    <p className="mt-1 max-w-2xl text-sm text-text-secondary">
     Checked before this goes any further. <strong className="text-text-primary">{displayName(spec)}</strong> · level {level.level} — {checks.length} checks apply. Failures must be fixed; warnings must be acknowledged.
    </p>
   </div>

   <div className="rounded-xl border border-border bg-surface px-4 py-3">
    <div className="flex flex-wrap items-center gap-3 text-sm">
     {done ? (
      fails.length ? <span className="inline-flex items-center gap-1.5 font-semibold text-status-critical"><X size={15} /> {fails.length} to fix before this goes further</span>
      : unacked.length ? <span className="inline-flex items-center gap-1.5 font-semibold text-status-warning"><AlertTriangle size={15} /> {unacked.length} to read and acknowledge</span>
      : <span className="inline-flex items-center gap-1.5 font-semibold text-status-success"><ShieldCheck size={15} /> Ready to go further</span>
     ) : probeError ? (
      <span className="inline-flex items-center gap-1.5 font-semibold text-status-critical"><X size={15} /> The platform could not be reached: {probeError}</span>
     ) : (
      <span className="inline-flex items-center gap-1.5 font-semibold text-text-primary"><Loader2 size={15} className="animate-spin text-primary" /> {checks[resolved]?.probe ? `Probing ${checks[resolved].title.toLowerCase()} on the platform…` : `Checking ${Math.min(resolved + 1, checks.length)} of ${checks.length}…`}</span>
     )}
     <span className="ml-auto flex items-center gap-2 text-xs text-text-muted">
      <span className="text-status-success">{passes.length} passed</span>·<span className="text-status-warning">{warns.length} to note</span>·<span className="text-status-critical">{fails.length} to fix</span>
      {(done || probeError) && <button type="button" onClick={() => { setAcked(new Set()); setRunKey((k) => k + 1); }} className="ml-2 inline-flex items-center gap-1 rounded-md border border-border-light px-2 py-1 font-semibold text-text-secondary hover:text-text-primary"><RefreshCw size={11} /> Re-run</button>}
     </span>
    </div>
    <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-surface-secondary" aria-hidden>
     <div className={`h-full rounded-full transition-[width] duration-500 ease-out ${done ? (fails.length ? "bg-status-critical" : unacked.length ? "bg-status-warning" : "bg-status-success") : "bg-primary"}`} style={{ width: `${checks.length ? Math.round((resolved / checks.length) * 100) : 0}%` }} />
    </div>
   </div>

   <div className="space-y-2">
    {checks.map((c, i) => {
     if (i > resolved) return null;
     const arriving = i === resolved && !done && !probeError;
     const groupLabel = groupOf(c);
     const firstOfGroup = i === 0 || groupOf(checks[i - 1]) !== groupLabel;
     if (arriving) {
      return (
       <div key={`${c.id}-arriving`}>
        {firstOfGroup && <p className="mb-1 mt-3 text-[10px] font-bold uppercase tracking-[0.12em] text-text-muted">{groupLabel}</p>}
        <div className="flex items-center gap-2.5 rounded-xl border border-dashed border-border px-4 py-3 text-sm text-text-muted">
         <Loader2 size={15} className="animate-spin text-primary" /> {c.probe ? `Connecting: ${c.title.toLowerCase()} — the platform is opening it now…` : `Checking ${c.title.toLowerCase()}…`}
        </div>
       </div>
      );
     }
     const meta = LEVEL[c.level];
     const { Icon } = meta;
     const needsAck = c.level === "warn" && !acked.has(c.id);
     return (
      <div key={c.id} className="readiness-check">
       {firstOfGroup && <p className="mb-1 mt-3 text-[10px] font-bold uppercase tracking-[0.12em] text-text-muted">{groupLabel}</p>}
       <div className={`rounded-xl border px-4 py-3 ${meta.bg}`}>
       <div className="flex flex-wrap items-start gap-2.5">
        <span className={`mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full ${c.level === "pass" ? "bg-status-success-light" : c.level === "warn" ? "bg-status-warning-light" : "bg-status-critical-light"}`}><Icon size={13} className={meta.cls} /></span>
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
      </div>
     );
    })}
   </div>

   <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border pt-4">
    <button type="button" onClick={() => navigate("/use-cases/builder/compose")} className="rounded-lg border border-border bg-surface px-3.5 py-2 text-sm font-semibold text-text-secondary hover:bg-surface-secondary">Back to the specification</button>
    <div className="flex items-center gap-3">
     {blocked && <span className="text-xs text-text-muted">{!done ? "checking…" : fails.length > 0 ? `${fails.length} to fix` : `${unacked.length} to acknowledge`}</span>}
     <button type="button" disabled={blocked} onClick={() => navigate("/use-cases/builder/plan")} className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-white hover:bg-primary-hover disabled:cursor-not-allowed disabled:opacity-40">
      <ShieldCheck size={15} /> See how it will run <ArrowRight size={15} />
     </button>
    </div>
   </div>
  </div>
 );
}
