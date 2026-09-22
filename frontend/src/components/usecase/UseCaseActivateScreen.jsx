import { useMemo, useState } from "react";
import { mappingLabel } from "../../domain/record-mappings";
import { useNavigate } from "react-router-dom";
import {
 Bell, Check, CircleUser, Coins, Layers, ListChecks, Rocket, RefreshCw, ShieldAlert, Tag, Target,
 UserCheck,
} from "lucide-react";
import {
 CHANNELS, OPERATORS, POLL_INTERVALS, SCHEDULES, SHAPES, TIMEOUT_BEHAVIOURS, categoryOf, displayName, estimatedRunsPerMonth,
 requiresAdminActivation, requiresSeparationOfDuties,
} from "../../domain/use-case-autonomy";
import { loadSpecDraft, saveSpecDraft } from "../../domain/use-case-draft-spec";
import { loadConfigureWorkspace } from "../../storage/configure-workspace";
import { useDemoProfile } from "../../context/DemoProfileContext";
import { activateOnPlatform } from "../../services/use-case-platform";
import PlatformRunPanel, { explain } from "./PlatformRunPanel";
import { builderStepBackButton, builderStepEyebrow, builderStepFooter, builderStepH1, builderStepShell, builderStepSubtitle } from "../shared/useCaseBuilderStepStyles";

/**
 * Step 6 of 6: activation.
 *
 * Not "switch it on". What has to be settled before something runs unattended, and where:
 *
 *  - WHO OWNS IT, and for levels 3+, who ACTIVATES it -- which must be someone other than the
 *    author, and for level 4 an admin. Enforced here, not advised.
 *  - WHICH ENVIRONMENT. Sandbox first; production is a promotion of a version, not a toggle.
 *  - WHAT IT COSTS, before anyone finds out from the bill.
 *  - PERSONAL DATA, allowed explicitly or not at all.
 *  - HOW YOU WILL KNOW IT WORKED, so it can be graded later.
 *
 * Everything the spec already settled -- who receives it and how, what happens when the approver
 * does not answer, the cap, the way back -- is SHOWN here, not asked again. One source of truth;
 * the fix for any of it is in the spec. The three governance words appear together once, with
 * their meaning: level, shape, autonomy.
 */

const BUSINESS_AREAS = ["inventory", "pricing", "finance", "orders", "supply_chain", "sales", "marketing", "customer", "support", "workforce", "operations"];
const objectiveText = (o) => (o?.metric ? `${o.metric} ${o.direction} ${o.target}${o.unit || ""}` : null);

/** The autonomy axis is derived, never asked: whether a person sees the result first. */
function autonomyOf(spec) {
 if (spec.shape === "recommend") return { value: "copilot", why: "a person releases each action" };
 if (spec.shape === "act") return { value: "autopilot", why: "nobody is in the loop on a run" };
 const pushed = (spec.notifyChannels || []).some((c) => c !== "in_app");
 return pushed
  ? { value: "autopilot", why: "delivered unattended by channel" }
  : { value: "copilot", why: "waits in the app for someone to open it" };
}

function Card({ Icon, title, children, aside }) {
 return (
  <section className="rounded-xl border border-border bg-surface p-4">
   <div className="flex items-start justify-between gap-3">
    <h2 className="flex items-center gap-2 text-sm font-bold text-text-primary"><Icon size={15} className="text-text-secondary" />{title}</h2>
    {aside}
   </div>
   <div className="mt-2">{children}</div>
  </section>
 );
}

/** A value settled in the spec: shown, with the way to change it. */
function FromSpec({ navigate }) {
 return (
  <button type="button" onClick={() => navigate("/use-cases/builder/compose")} className="shrink-0 text-[11px] font-semibold text-text-muted hover:text-primary">
   from the spec · change
  </button>
 );
}

export default function UseCaseActivateScreen() {
 const navigate = useNavigate();
 const [spec, setSpec] = useState(() => loadSpecDraft());
 const set = (field, value) => setSpec((s) => saveSpecDraft({ ...s, [field]: value }));
 const shape = SHAPES[spec.shape] || SHAPES.inform;
 const sod = requiresSeparationOfDuties(spec.shape);
 const admin = requiresAdminActivation(spec.shape);
 const autonomy = autonomyOf(spec);
 const runs = estimatedRunsPerMonth(spec.schedule);
 const costPerRun = shape.level >= 3 ? 0.42 : shape.level === 2 ? 0.08 : 0.19;
 const title = useMemo(() => spec.title?.trim() || spec.goal?.trim() || "Untitled use case", [spec.title, spec.goal]);
 const channels = (spec.notifyChannels || ["in_app"]).map((c) => CHANNELS.find((x) => x.value === c)?.label || c);
 const timeout = TIMEOUT_BEHAVIOURS.find((t) => t.value === (spec.onTimeout || "cancel"));
 const unit = spec.condition?.unit || "";

 // Who may press the button. The author never activates a level 3+ use case; an admin must for
 // level 4. Rendered as a hand-off rather than hidden, so the reason is visible.
 const handoff = admin ? "an admin" : sod ? `${spec.approver || "the approver"}` : null;
 const blocked = !spec.planApprovedAt || !spec.owner;

 const { profileId, storageKeys } = useDemoProfile();
 const sources = useMemo(() => loadConfigureWorkspace(storageKeys.CONFIGURE_WORKSPACE, profileId).internalSources || [], [storageKeys.CONFIGURE_WORKSPACE, profileId]);
 const [activating, setActivating] = useState(false);
 const [activationError, setActivationError] = useState(null);

 // Going live is a platform call: the spec is stored, compiled and versioned there, and its
 // preflight decides. A refusal comes back as the checks that failed, shown here as they
 // are; the interface never marks something active that the platform did not activate.
 const activate = async () => {
  setActivating(true); setActivationError(null);
  try {
   const { spec: next, useCase } = await activateOnPlatform(spec, sources);
   // The platform's answer is the status. With authorization on, a level 3+ activation by
   // a non-admin is refused there (403) and lands in the error path; locally, with
   // authorization off, the caller is the admin and the use case is active.
   const status = useCase.status === "activated" ? "active" : handoff ? "submitted" : "active";
   setSpec(saveSpecDraft({ ...next, activation: { status, at: new Date().toISOString(), environment: next.environment, by: next.author, to: handoff, useCaseId: useCase.id, version: useCase.version } }));
  } catch (err) {
   setActivationError(explain(err));
  } finally {
   setActivating(false);
  }
 };
 const done = spec.activation;

 if (done) {
  const first = SCHEDULES.find((x) => x.value === spec.schedule)?.label || "on schedule";
  return (
   <div className={builderStepShell}>
    <div>
     <p className={builderStepEyebrow}>Step 6 of 6</p>
     <h1 className={`flex items-center gap-2 ${builderStepH1}`}>
      <span className="flex h-8 w-8 items-center justify-center rounded-full bg-status-success-light text-status-success"><Check size={18} /></span>
      {done.status === "active" ? `Active in ${done.environment}` : `Submitted to ${done.to}`}
     </h1>
     <p className={builderStepSubtitle}>
      {done.status === "active"
       ? <><strong className="text-text-primary">{title}</strong> is live in {done.environment}. The frozen version runs; a change to the spec is a new version.</>
       : <><strong className="text-text-primary">{title}</strong> is frozen and waiting for {done.to} to activate it. Nothing runs until they do; they see the same workflow, simulation and cost you saw.</>}
     </p>
    </div>
    <Card Icon={ListChecks} title="What happens next">
     <ul className="space-y-1.5 text-sm text-text-secondary">
      <li>· Runs <strong className="text-text-primary">{(spec.template === "document_to_record" ? POLL_INTERVALS.find((x) => x.value === spec.schedule)?.label || first : first).toLowerCase()}</strong> once its trigger is on: locally the Run button below or the in-process poller, in the cloud the orchestrator (a Logic App on Azure, a Step Functions task on AWS) calling the same run endpoint.{spec.environment === "sandbox" ? " In the sandbox it writes nowhere and sends nothing outside the app." : ""}</li>
      {spec.template === "document_to_record" && <li>· Each new document in {spec.mailbox} becomes {mappingLabel(spec) ? `${/^[aeiou]/i.test(mappingLabel(spec)) ? "an" : "a"} ${mappingLabel(spec)}` : "a record"} in {spec.writeback}; the mailbox is only read; each message is carried once by its id, never flagged, never deleted.</li>}
      <li>· Results land {shape.id === "alert" ? <>on the <strong className="text-text-primary">Alerts</strong> page{channels.length > 1 ? ` with a pointer by ${channels.filter((c) => c !== "In app").join(" and ")}` : ""}</> : <>in this use case's <strong className="text-text-primary">runs</strong>{channels.length > 1 ? ` with a pointer by ${channels.filter((c) => c !== "In app").join(" and ")}` : ""}</>}, for {spec.notify || spec.owner}.</li>
      {shape.level >= 3 && <li>· {shape.id === "recommend" ? `${spec.approver} gets each recommendation to release; ` : ""}every change to {spec.writeback} is stamped with its run{shape.id === "act" ? " and its rollback point" : ""}.</li>}
      <li>· Every run is recorded with what it read, decided and sent. {spec.owner} and any admin hold the kill switch.</li>
     </ul>
    </Card>
    {done.useCaseId && (
     <PlatformRunPanel
      useCaseId={done.useCaseId}
      mode={done.environment === "production" ? "live" : "rehearse"}
      label={done.environment === "production" ? "Run it now" : "Run it now, in the sandbox"}
      description={done.environment === "production"
       ? `Version ${done.version} on the platform${spec.template === "document_to_record" ? `: polls ${spec.mailbox || "the mailbox"}, creates in ${spec.writeback || "the target"}, emails exceptions to ${spec.exceptionsTo || "the configured address"}.` : "."} The same run the schedule makes; in the cloud the orchestrator calls this endpoint.`
       : `Version ${done.version} on the platform, with every side effect held: ${spec.template === "document_to_record" ? "the sample set instead of the mailbox, nothing created, nothing sent" : "nothing written, nothing sent"}. Promote to production to run it for real.`}
      createdAs={spec.createdAs}
      buttonLabel={done.environment === "production" ? "Run now" : "Run now in the sandbox"}
     />
    )}
    <div className={builderStepFooter}>
     <button type="button" onClick={() => set("activation", null)} className={builderStepBackButton}>Undo</button>
     <button type="button" onClick={() => navigate("/use-cases/gallery")} className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-white hover:bg-primary-hover">Go to use cases</button>
    </div>
   </div>
  );
 }

 return (
  <div className={builderStepShell}>
   <div>
    <p className={builderStepEyebrow}>Step 6 of 6</p>
    <h1 className={builderStepH1}>Go live</h1>
    <p className={builderStepSubtitle}>How <strong className="text-text-primary">{title}</strong> behaves once it is live.</p>
    {/* The three governance words, together, once, with what each means for this use case. */}
    <div className="mt-3 grid gap-2 sm:grid-cols-3">
     {[
      ["Type", displayName(spec), categoryOf(spec)?.plain || shape.verb],
      [`Level ${shape.level}`, shape.level >= 4 ? "an admin switches it on" : shape.level >= 3 ? "not the author" : "the author may switch it on", `side effect: ${categoryOf(spec)?.sideEffect || shape.sideEffect}`],
      ["Autonomy", autonomy.value, autonomy.why],
     ].map(([k, v, why]) => (
      <div key={k} className="rounded-lg border border-border bg-surface-secondary px-3 py-2">
       <p className="text-[11px] font-semibold uppercase tracking-wide text-text-muted">{k}</p>
       <p className="text-sm font-semibold text-text-primary">{v}</p>
       <p className="text-[11px] text-text-muted">{why}</p>
      </div>
     ))}
    </div>
   </div>

   <Card Icon={ListChecks} title="What was approved">
    <p className="text-sm text-text-secondary">
     {spec.planApprovedAt
      ? <>The workflow: every step and who performs it, was approved by {spec.author} on {new Date(spec.planApprovedAt).toLocaleDateString()}. Going live runs that workflow and no other.</>
      : <>The workflow has not been approved. <button type="button" onClick={() => navigate("/use-cases/builder/plan")} className="font-semibold text-primary hover:underline">Review it</button> before going live.</>}
    </p>
   </Card>

   <div className="grid gap-4 sm:grid-cols-2">
    <Card Icon={CircleUser} title="Owner">
     <p className="text-sm text-text-secondary">
      {spec.owner ? <><strong className="text-text-primary">{spec.owner}</strong> is accountable for this.</> : "No owner set: go back to the spec."}
      {" "}Authored by {spec.author}.
     </p>
    </Card>
    <Card Icon={Tag} title="Business area" aside={<span className="text-[11px] text-text-muted">{spec.origin?.businessArea === "authored" ? "typed" : "from the metric"}</span>}>
     <select value={spec.businessArea || ""} onChange={(e) => setSpec((s) => saveSpecDraft({ ...s, businessArea: e.target.value, origin: { ...(s.origin || {}), businessArea: "authored" } }))} className="w-full rounded-lg border border-border bg-surface px-2.5 py-2 text-sm focus:border-primary focus:outline-none">
      <option value="">Choose…</option>
      {[...new Set([spec.businessArea, ...BUSINESS_AREAS].filter(Boolean))].map((a) => <option key={a} value={a}>{a.replace(/_/g, " ")}</option>)}
     </select>
     <p className="mt-1 text-[11px] text-text-muted">Where it sits in the catalogue. Derived from {spec.metricIds[0] || "the metric"}'s registry entry.</p>
    </Card>
   </div>

   <Card Icon={Layers} title="Environment">
    <div className="flex flex-wrap gap-2">
     {[
      { v: "sandbox", l: "Sandbox", h: "Runs, but writes nowhere and sends nothing outside the app. Where every use case starts." },
      { v: "production", l: "Production", h: "Live. Promotes this frozen version." },
     ].map((e) => (
      <button key={e.v} type="button" onClick={() => set("environment", e.v)} aria-pressed={spec.environment === e.v} className={`flex-1 rounded-lg border px-3 py-2.5 text-left ${spec.environment === e.v ? "border-primary bg-primary-light/50" : "border-border hover:bg-surface-secondary"}`}>
       <span className="block text-sm font-semibold text-text-primary">{e.l}</span>
       <span className="block text-xs text-text-muted">{e.h}</span>
      </button>
     ))}
    </div>
   </Card>

   <Card Icon={RefreshCw} title="When it runs, and who sees it" aside={<FromSpec navigate={navigate} />}>
    <div className="grid gap-3 sm:grid-cols-2">
     <label className="block"><span className="text-xs font-semibold text-text-secondary">Runs</span>
      <select value={spec.schedule} onChange={(e) => set("schedule", e.target.value)} className="mt-1 w-full rounded-lg border border-border bg-surface px-2.5 py-2 text-sm focus:border-primary focus:outline-none">
       {(spec.template === "document_to_record" ? POLL_INTERVALS : SCHEDULES).map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select></label>
     <div>
      <span className="text-xs font-semibold text-text-secondary">{spec.template === "document_to_record" ? "Exceptions go to" : shape.level >= 3 ? "Owner is told" : "Delivered to"}</span>
      <p className="mt-1 text-sm text-text-primary"><strong>{spec.template === "document_to_record" ? spec.exceptionsTo || "nobody yet" : spec.notify || spec.owner || "nobody yet"}</strong></p>
      <div className="mt-1 flex flex-wrap gap-1">
       {channels.map((c) => <span key={c} className="rounded-full border border-border bg-surface-secondary px-2 py-0.5 text-[11px] font-semibold text-text-secondary">{c}</span>)}
      </div>
      <p className="mt-1 text-[11px] text-text-muted">Lands in the app; other channels get a link to it.</p>
     </div>
    </div>
   </Card>

   {shape.id === "alert" && (
    <Card Icon={Bell} title="When it fires" aside={<FromSpec navigate={navigate} />}>
     <p className="text-sm text-text-secondary">
      When <strong className="text-text-primary">{spec.metricIds[0]}</strong> {OPERATORS.find((o) => o.value === spec.condition?.operator)?.label || "goes above"} <strong className="text-text-primary">{spec.condition?.threshold}{unit}</strong>{spec.scope ? <> in <strong className="text-text-primary">{spec.scope.toLowerCase()}</strong></> : null}, it tells <strong className="text-text-primary">{spec.notify || "nobody yet"}</strong> at severity <strong className="text-text-primary">{spec.severity}</strong>. Runs that do not fire are logged, not sent.
     </p>
    </Card>
   )}

   {shape.id === "recommend" && (
    <Card Icon={UserCheck} title="Approval" aside={<FromSpec navigate={navigate} />}>
     <p className="text-sm text-text-secondary"><strong className="text-text-primary">{spec.approver}</strong> releases each recommendation before it lands in <strong className="text-text-primary">{spec.writeback}</strong>. {spec.guardrails.length} business guardrail{spec.guardrails.length === 1 ? "" : "s"} run before {spec.approver ? spec.approver.split(" ")[0] : "the approver"} sees anything.</p>
     <p className="mt-2 text-sm text-text-secondary">If {spec.approver || "the approver"} does not respond: <strong className="text-text-primary">{timeout?.label.toLowerCase()}</strong>.</p>
     {timeout?.value === "proceed" && (
      <p className="mt-2 rounded-lg border border-status-critical-border bg-status-critical-light px-3 py-2 text-xs text-status-critical">
       This writes to {spec.writeback} with nobody watching. Silence is not consent, only keep it for changes you would be comfortable discovering after the fact.
      </p>
     )}
    </Card>
   )}

   {shape.id === "act" && spec.template === "document_to_record" && (
    <Card Icon={ShieldAlert} title="Document to record, unattended" aside={<FromSpec navigate={navigate} />}>
     <p className="text-sm text-text-secondary">Polls <strong className="text-text-primary">{spec.mailbox || "the mailbox"}</strong> {POLL_INTERVALS.find((p) => p.value === spec.schedule)?.label.toLowerCase() || ""}. Each {spec.extractionTemplate?.replace(/_/g, " ") || "document"} is extracted, resolved, checked by {(spec.recordGuardrails || []).length} guardrails, and created in <strong className="text-text-primary">{spec.writeback}</strong> as {spec.createdAs === "active" ? "activated" : "a draft"}, at most <strong className="text-text-primary">{spec.blastRadius}</strong> per poll. Exceptions go to <strong className="text-text-primary">{spec.exceptionsTo}</strong>{spec.acknowledge ? "; the sender is acknowledged either way" : ""}. Rollback: <strong className="text-text-primary">{spec.rollback}</strong>. A kill switch stops the poll; in-flight documents complete.</p>
    </Card>
   )}
   {shape.id === "act" && spec.template !== "document_to_record" && (
    <Card Icon={ShieldAlert} title={spec.objective?.metric ? "Optimisation, unattended" : "Unattended"} aside={<FromSpec navigate={navigate} />}>
     <p className="text-sm text-text-secondary">{objectiveText(spec.objective) ? <>Tunes the process towards <strong className="text-text-primary">{objectiveText(spec.objective)}</strong>. </> : null}Runs unattended. {spec.guardrails.length} business guardrail{spec.guardrails.length === 1 ? "" : "s"}, then at most <strong className="text-text-primary">{spec.blastRadius}</strong> per run; every change to <strong className="text-text-primary">{spec.writeback}</strong> records its rollback point: <strong className="text-text-primary">{spec.rollback}</strong>. A kill switch is available to the owner and any admin.</p>
    </Card>
   )}

   {spec.piiTouched && (
    <Card Icon={ShieldAlert} title="Personal data">
     <label className="flex items-start gap-2 text-sm text-text-secondary">
      <input type="checkbox" checked={!!spec.piiAllowed} onChange={(e) => set("piiAllowed", e.target.checked)} className="mt-0.5" />
      I allow this use case to read the personal-data columns preflight identified.
     </label>
    </Card>
   )}

   <Card Icon={Coins} title="Cost">
    {spec.template === "document_to_record" ? <p className="text-sm text-text-secondary">One AI step per document, ~0.12 credits; polls cost nothing. At ~60 documents a month, <strong className="text-text-primary">≈ 7 credits / month</strong>, against the tenant budget.</p> : <p className="text-sm text-text-secondary"><strong className="text-text-primary">{runs}</strong> runs / month × ~{costPerRun.toFixed(2)} credits ≈ <strong className="text-text-primary">{(runs * costPerRun).toFixed(0)} credits / month</strong>, against the tenant budget.{shape.id === "alert" ? " No model is called on a run." : ""}</p>}
   </Card>

   <Card Icon={Target} title="How you will know it worked">
    <p className="text-sm text-text-secondary">Stated now so it can be graded later. Without it you collect use cases nobody can evaluate.</p>
    <input value={spec.successMeasure || ""} onChange={(e) => set("successMeasure", e.target.value)} placeholder={{ inform: "e.g. finance stops asking for the margin deck by Tuesday", alert: "e.g. stockouts caught a day earlier than today", recommend: "e.g. margin holds while markdown rate falls 2 points", act: "e.g. stockout rate below 5% with no manual safety-stock edits" }[shape.id]} className="mt-2 w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm focus:border-primary focus:outline-none" />
   </Card>

   {activationError && (
    <div className="rounded-xl border border-status-critical-border bg-status-critical-light px-4 py-3 text-sm text-status-critical">
     <strong>The platform did not activate it.</strong> {activationError}
    </div>
   )}

   {handoff && (
    <div className="rounded-xl border border-status-info-border bg-status-info-light px-4 py-3 text-sm text-text-secondary">
     <strong className="text-text-primary">Going live is a hand-off.</strong> Level {shape.level} use cases cannot be switched on by their author. This will be sent to <strong className="text-text-primary">{handoff}</strong>, who activates the frozen version after seeing the same plan, rehearsal and cost.
    </div>
   )}

   <div className={builderStepFooter}>
    <button type="button" onClick={() => navigate("/use-cases/builder/rehearse")} className={builderStepBackButton}>Back to the simulation</button>
    <div className="flex items-center gap-3">
     {blocked && <span className="text-xs text-text-muted">{!spec.planApprovedAt ? "Approve the workflow first." : "Set an owner first."}</span>}
     <button type="button" disabled={blocked || activating} onClick={activate} className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-white hover:bg-primary-hover disabled:cursor-not-allowed disabled:opacity-40">
      <Rocket size={15} /> {activating ? "Activating on the platform…" : handoff ? "Submit to go live" : `Go live in ${spec.environment}`}
     </button>
    </div>
   </div>
  </div>
 );
}
