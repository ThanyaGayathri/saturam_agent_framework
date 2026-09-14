import { useEffect, useRef, useState } from "react";
import { AlertTriangle, Check, CheckCircle2, ClipboardList, X } from "lucide-react";
import { acceptProposal, fetchProposals, rejectProposal } from "../../services/semantic-proposals-api";

/**
 * The K7 review screen (#235/F2): `egpt-retail suggest` files KPI and use-case proposals here
 * (`ProposalStore`, `semantic/proposals.py`), and this is where a person accepts or rejects
 * them. A dedicated screen, not folded into `ConfigureSemanticLayer.jsx`'s "Needs review" tab --
 * that tab is a different, unrelated per-source mapping-decision UI (`join_path`/`measure`
 * kinds, local seed data, no fetch calls at all); this one is real, fetched, and about the two
 * kinds `egpt-retail suggest` actually produces.
 *
 * Accepting a `kind: kpi` proposal adopts it into the registry in the same call (the backend's
 * own design decision, `api/routers/semantic.py`) -- `applied_at` on the response is the
 * honest signal that actually happened, shown below rather than assumed from `state` alone.
 * A `kind: use_case` proposal has no consumer yet; accepting one only records the decision,
 * and this screen says so rather than implying a use case now exists somewhere.
 */

const FEASIBILITY_STYLE = {
 ready: { cls: "border-status-success/35 bg-status-success-light dark:bg-status-success/15", label: "text-status-success" },
 partial: { cls: "border-status-warning/35 bg-status-warning-light dark:bg-status-warning/15", label: "text-status-warning" },
 blocked: { cls: "border-status-critical/35 bg-status-critical-light dark:bg-status-critical/15", label: "text-status-critical" },
 unknown: { cls: "border-border bg-surface-secondary", label: "text-text-muted" },
};

function KpiProposalBody({ payload }) {
 const style = FEASIBILITY_STYLE[payload.feasibility] || FEASIBILITY_STYLE.unknown;
 return (
  <>
   <h3 className="font-bold text-text-primary">{payload.name}</h3>
   {payload.description && <p className="mt-1 text-sm text-text-secondary">{payload.description}</p>}
   <div className="mt-2 flex flex-wrap gap-1.5">
    {payload.business_area && (
     <span className="rounded-full bg-surface-tertiary px-2 py-0.5 text-[11px] font-medium text-text-secondary">
      {payload.business_area}
     </span>
    )}
   </div>
   {payload.sql && (
    <pre className="mt-2 max-h-24 overflow-auto rounded-lg bg-surface-tertiary p-2 text-[11px] text-text-secondary">
     {payload.sql}
    </pre>
   )}
   {payload.rationale && <p className="mt-2 text-xs text-text-muted">{payload.rationale}</p>}
   <section className={`mt-3 rounded-lg border p-3 ${style.cls}`}>
    <p className={`text-[10px] font-semibold uppercase tracking-wide ${style.label}`}>Feasibility</p>
    <p className={`mt-1 text-sm font-bold capitalize ${style.label}`}>{payload.feasibility || "unknown"}</p>
    {payload.feasibility_reason && payload.feasibility_reason !== "none" && (
     <p className="mt-1 text-xs text-text-secondary">{payload.feasibility_reason}</p>
    )}
    {payload.missing_tables?.length > 0 && (
     <p className="mt-1 text-xs text-text-muted">Missing: {payload.missing_tables.join(", ")}</p>
    )}
   </section>
  </>
 );
}

function UseCaseProposalBody({ payload }) {
 return (
  <>
   <h3 className="font-bold text-text-primary">{payload.question}</h3>
   {payload.description && <p className="mt-1 text-sm text-text-secondary">{payload.description}</p>}
   <div className="mt-2 flex flex-wrap gap-1.5">
    {payload.business_area && (
     <span className="rounded-full bg-surface-tertiary px-2 py-0.5 text-[11px] font-medium text-text-secondary">
      {payload.business_area}
     </span>
    )}
    {payload.shape && (
     <span className="rounded-full bg-surface-tertiary px-2 py-0.5 text-[11px] font-medium text-text-secondary">
      {payload.shape.replace(/_/g, " ")}
     </span>
    )}
   </div>
   {payload.kpi_identifiers?.length > 0 && (
    <p className="mt-2 text-xs text-text-muted">Depends on: {payload.kpi_identifiers.join(", ")}</p>
   )}
   {payload.rationale && <p className="mt-2 text-xs text-text-muted">{payload.rationale}</p>}
   {payload.shape_ready === false && payload.shape_missing?.length > 0 && (
    <p className="mt-2 text-xs text-status-warning">Not ready yet: missing {payload.shape_missing.join(", ")}</p>
   )}
  </>
 );
}

const KIND_LABEL = { kpi: "Suggested KPI", use_case: "Suggested question" };

function ProposalCard({ proposal, busy, onAccept, onReject }) {
 const [note, setNote] = useState(null);
 return (
  <div className="rounded-xl border border-border bg-surface p-4">
   <div className="flex items-start justify-between gap-2">
    <span className="rounded-md bg-surface-tertiary px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-text-secondary">
     {KIND_LABEL[proposal.kind] || proposal.kind}
    </span>
   </div>
   <div className="mt-2">
    {proposal.kind === "kpi" ? (
     <KpiProposalBody payload={proposal.payload} />
    ) : proposal.kind === "use_case" ? (
     <UseCaseProposalBody payload={proposal.payload} />
    ) : (
     <p className="text-sm text-text-secondary">{proposal.target}</p>
    )}
   </div>
   {note && (
    <p className="mt-3 flex items-center gap-1.5 text-xs font-semibold text-status-success">
     <CheckCircle2 size={13} />
     {note}
    </p>
   )}
   <div className="mt-3 flex items-center gap-2">
    <button
     type="button"
     disabled={busy}
     onClick={() =>
      onAccept(proposal, (applied) =>
       setNote(proposal.kind === "kpi" && applied ? "Added to your KPI registry." : null),
      )
     }
     className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3.5 py-1.5 text-sm font-semibold text-white hover:bg-primary-hover disabled:opacity-50"
    >
     <Check size={14} />
     Accept
    </button>
    <button
     type="button"
     disabled={busy}
     onClick={() => onReject(proposal)}
     className="inline-flex items-center gap-1.5 rounded-lg border border-border-light bg-surface px-3.5 py-1.5 text-sm font-semibold text-text-secondary hover:text-text-primary disabled:opacity-50"
    >
     <X size={14} />
     Reject
    </button>
   </div>
  </div>
 );
}

export default function ConfigureProposals() {
 const [state, setState] = useState({ status: "loading", proposals: [], error: null });
 const [busyIds, setBusyIds] = useState(() => new Set());
 const [actionError, setActionError] = useState(null);
 const mountedRef = useRef(true);

 useEffect(() => {
  mountedRef.current = true;
  return () => {
   mountedRef.current = false;
  };
 }, []);

 useEffect(() => {
  let cancelled = false;
  fetchProposals({ state: "pending", kinds: ["kpi", "use_case"] })
   .then((result) => {
    if (cancelled) return;
    setState({ status: "ready", proposals: result.proposals || [], error: null });
   })
   .catch((error) => {
    if (!cancelled) setState({ status: "error", proposals: [], error });
   });
  return () => {
   cancelled = true;
  };
 }, []);

 const setBusy = (id, isBusy) =>
  setBusyIds((prev) => {
   const next = new Set(prev);
   if (isBusy) next.add(id);
   else next.delete(id);
   return next;
  });

 const remove = (id) =>
  setState((prev) => ({ ...prev, proposals: prev.proposals.filter((p) => p.id !== id) }));

 const handleAccept = async (proposal, onApplied) => {
  setActionError(null);
  setBusy(proposal.id, true);
  try {
   const updated = await acceptProposal(proposal.id);
   // A 200 here only means the decision was recorded -- `apply_error` set (with
   // `applied_at` still null) means the KPI registry adoption itself failed after that.
   // Surfacing it and leaving the card in place beats removing it as if it had fully
   // succeeded, which is what a plain `catch` block can never see since this path
   // never throws.
   if (updated.apply_error) {
    setActionError(updated.apply_error);
    setBusy(proposal.id, false);
    return;
   }
   onApplied(!!updated.applied_at);
   // A brief pause so the "Added to your KPI registry" note is actually seen before the
   // card leaves the list, rather than the accept and the disappearance looking like one
   // instant, unreadable flash. Guarded the same way the fetch effect above guards its own
   // setState: if the screen was left within that window, this must not update it.
   setTimeout(() => {
    if (mountedRef.current) remove(proposal.id);
   }, 1100);
  } catch (error) {
   setActionError(error?.detail || "Couldn't accept this proposal.");
   setBusy(proposal.id, false);
  }
 };

 const handleReject = async (proposal) => {
  setActionError(null);
  setBusy(proposal.id, true);
  try {
   await rejectProposal(proposal.id);
   remove(proposal.id);
  } catch (error) {
   setActionError(error?.detail || "Couldn't reject this proposal.");
   setBusy(proposal.id, false);
  }
 };

 if (state.status === "loading") {
  return <p className="text-sm text-text-secondary">Loading proposals…</p>;
 }

 if (state.status === "error") {
  return (
   <div className="flex flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-status-critical/40 bg-status-critical/5 px-6 py-16 text-center">
    <AlertTriangle size={22} className="text-status-critical" />
    <p className="text-sm font-semibold text-text-primary">Couldn't load proposals</p>
    <p className="max-w-sm text-xs text-text-muted">
     {state.error?.detail || "The review service didn't respond."}
    </p>
   </div>
  );
 }

 const { proposals } = state;

 return (
  <div className="space-y-3">
   <div>
    <h2 className="text-xl font-bold text-text-primary">Proposals</h2>
    <p className="mt-0.5 max-w-2xl text-sm text-text-secondary">
     KPIs and questions <code className="text-xs">egpt-retail suggest</code> found in your connected data,
     waiting on a decision. Accepting a KPI adds it to your registry immediately.
    </p>
   </div>
   {actionError && (
    <p className="rounded-lg border border-status-critical/40 bg-status-critical/5 px-3 py-2 text-xs text-status-critical">
     {actionError}
    </p>
   )}
   {proposals.length === 0 ? (
    <div className="rounded-xl border border-dashed border-border-light bg-surface-secondary px-6 py-16 text-center">
     <ClipboardList size={26} className="mx-auto mb-3 text-text-muted" />
     <p className="text-sm font-semibold text-text-primary">Nothing waiting on review</p>
     <p className="mt-1 text-sm text-text-muted">
      Run <code className="text-xs">egpt-retail suggest</code> against a connected source to generate some.
     </p>
    </div>
   ) : (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
     {proposals.map((proposal) => (
      <ProposalCard
       key={proposal.id}
       proposal={proposal}
       busy={busyIds.has(proposal.id)}
       onAccept={handleAccept}
       onReject={handleReject}
      />
     ))}
    </div>
   )}
  </div>
 );
}
