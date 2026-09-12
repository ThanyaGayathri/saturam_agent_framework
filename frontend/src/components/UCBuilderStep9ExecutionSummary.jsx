import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  CheckCircle2, BarChart3, ClipboardCheck, Zap,
  ExternalLink, AlertTriangle, Mail, MessageSquare, FileText,
  Truck, XCircle, ListChecks,
} from "lucide-react";
import Breadcrumb from "./shared/Breadcrumb";
import DecisionCard from "./shared/DecisionCard";
import { COPILOT_DECISIONS_KEY, USE_CASE_LAUNCH_SUMMARY_KEY } from "../storage/keys";
import { resolveExecutionStats, resolveContextualPlanItems } from "../storage/use-case-draft";
import { useUseCaseCatalog } from "../hooks/useUseCaseCatalog";

function StatTile({ label, value, sub }) {
  return (
    <div className="rounded-xl border border-border bg-surface p-4 shadow-sm">
      <p className="text-2xl font-semibold text-text-primary">{value}</p>
      <p className="mt-0.5 text-sm font-medium text-text-secondary">{label}</p>
      {sub && <p className="mt-1 text-xs text-text-muted">{sub}</p>}
    </div>
  );
}

// Co-pilot result section - Decision Cards rendered inline with approve /
// reject so the whole activate -> review -> approve flow happens on this
// page. No more jumping to a separate Decision Feed route.
function CopilotResult({ cards, onApprove, onReject, onModify }) {
  const pending = cards.filter((c) => c.status === "pending").length;
  const approved = cards.filter((c) => c.status === "approved").length;
  const approvedValue = cards
    .filter((c) => c.status === "approved")
    .reduce((s, c) => s + (c.value ?? 0), 0);
  const allDone = pending === 0 && cards.length > 0;

  return (
    <div className="space-y-4">
      <div className="flex items-start gap-3 rounded-xl border border-primary/30 bg-primary-light px-4 py-4 dark:border-primary/40 dark:bg-primary/15">
        <ClipboardCheck size={20} className="mt-0.5 shrink-0 text-primary" />
        <div>
          <p className="text-sm font-semibold text-primary">
            {cards.length} Decision Cards prepared
          </p>
          <p className="mt-1 text-sm text-text-secondary">
            {pending > 0
              ? `${pending} pending your approval. No actions have been taken yet. Approve or reject each card below.`
              : `All cards resolved. ${approved} approved, ${cards.length - approved} rejected.`}
          </p>
        </div>
      </div>

      {allDone && approved > 0 ? (
        <div className="flex items-center gap-3 rounded-xl border border-[#a7f3d0] bg-[#ecfdf3] px-4 py-3">
          <CheckCircle2 size={18} className="shrink-0 text-[#067647]" />
          <p className="text-sm font-medium text-[#067647]">
            All decisions resolved. ${approvedValue.toLocaleString()} in actions approved and queued.
          </p>
        </div>
      ) : null}

      <div className="space-y-4">
        {cards.map((card) => (
          <DecisionCard
            key={card.id}
            card={card}
            onApprove={onApprove}
            onReject={onReject}
            onModify={onModify}
          />
        ))}
      </div>
    </div>
  );
}

// After all decision cards are resolved, this section shows what actually
// happened as a result of the human approvals - the equivalent of the
// Delivered phase in the playbook-backed activation flow.
function DecisionsDeliveredSummary({ cards, ucName }) {
  const approved = cards.filter((c) => c.status === "approved");
  const rejected = cards.filter((c) => c.status === "rejected");
  const totalValue = approved.reduce((s, c) => s + (c.value ?? 0), 0);

  // Derive mock delivery detail from the approved cards (PO number, ETA).
  const rows = useMemo(() => {
    const now = Date.now();
    return approved.map((c, i) => {
      const poNumber = `PO-${String(24000 + i + 1).padStart(5, "0")}`;
      const etaDays = 7 + i * 3;
      const eta = new Date(now + etaDays * 86400000);
      const skuMatch = c.title.match(/SKU-[A-Za-z0-9-]+/);
      return {
        poNumber,
        sku: skuMatch ? skuMatch[0] : "",
        title: c.title.replace(/^.*?- /, ""),
        supplier: c.supplier || "-",
        action: c.action,
        value: c.value ?? 0,
        eta: eta.toLocaleDateString(undefined, { month: "short", day: "numeric" }),
      };
    });
  }, [approved]);

  const sentAt = useMemo(
    () =>
      new Date().toLocaleTimeString(undefined, {
        hour: "numeric",
        minute: "2-digit",
      }),
    []
  );

  return (
    <div className="mt-6 space-y-4">
      <div className="rounded-2xl border border-[#a7f3d0] bg-[#ecfdf3] p-5">
        <div className="flex items-start gap-3">
          <CheckCircle2 size={22} className="mt-0.5 shrink-0 text-[#067647]" />
          <div>
            <p className="text-sm font-semibold text-[#067647]">
              {approved.length > 0 ? "Actions executed" : "No actions taken"}
            </p>
            <p className="mt-1 text-sm text-text-secondary">
              {approved.length > 0
                ? `${approved.length} of ${cards.length} decisions approved. $${totalValue.toLocaleString()} in purchase orders raised and notifications sent to procurement.`
                : `${cards.length} of ${cards.length} decisions rejected. No orders placed and no notifications sent.`}
            </p>
          </div>
        </div>
      </div>

      {approved.length > 0 ? (
        <>
          <section className="rounded-xl border border-border bg-surface p-5 shadow-sm">
            <div className="mb-4 flex items-center gap-2">
              <Truck className="h-4 w-4 text-primary" aria-hidden />
              <h3 className="text-sm font-semibold text-text-primary">
                Purchase orders raised
              </h3>
              <span className="rounded-full bg-primary-light px-2 py-0.5 text-[10px] font-semibold text-primary dark:bg-primary/15">
                {approved.length}
              </span>
            </div>
            <div className="overflow-hidden rounded-lg border border-border">
              <table className="w-full text-sm">
                <thead className="bg-surface-secondary/40 text-left text-xs uppercase tracking-wide text-text-muted">
                  <tr>
                    <th className="px-4 py-2 font-medium">PO</th>
                    <th className="px-4 py-2 font-medium">Item</th>
                    <th className="px-4 py-2 font-medium">Supplier</th>
                    <th className="px-4 py-2 font-medium text-right">Value</th>
                    <th className="px-4 py-2 font-medium">Est. delivery</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {rows.map((r) => (
                    <tr key={r.poNumber}>
                      <td className="px-4 py-2 font-medium text-text-primary">{r.poNumber}</td>
                      <td className="px-4 py-2 text-text-primary">
                        <div>{r.title}</div>
                        {r.sku ? (
                          <div className="text-xs text-text-muted">{r.sku}</div>
                        ) : null}
                      </td>
                      <td className="px-4 py-2 text-text-secondary">{r.supplier}</td>
                      <td className="px-4 py-2 text-right font-medium tabular-nums text-text-primary">
                        ${r.value.toLocaleString()}
                      </td>
                      <td className="px-4 py-2 text-text-secondary">{r.eta}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section className="rounded-xl border border-border bg-surface p-5 shadow-sm">
            <div className="mb-3 flex items-center gap-2">
              <CheckCircle2 className="h-4 w-4 text-primary" aria-hidden />
              <h3 className="text-sm font-semibold text-text-primary">Notifications sent</h3>
              <span className="text-xs text-text-muted">at {sentAt}</span>
            </div>
            <ul className="space-y-2">
              <li className="flex items-start gap-3 rounded-lg border border-border bg-surface-secondary/40 px-3 py-2 text-sm">
                <MessageSquare className="mt-0.5 h-4 w-4 shrink-0 text-text-muted" aria-hidden />
                <span className="text-text-primary">
                  Slack post to <span className="font-medium">#procurement-ops</span> summarising the
                  {" "}{approved.length} approved purchase orders and total value.
                </span>
              </li>
              <li className="flex items-start gap-3 rounded-lg border border-border bg-surface-secondary/40 px-3 py-2 text-sm">
                <Mail className="mt-0.5 h-4 w-4 shrink-0 text-text-muted" aria-hidden />
                <span className="text-text-primary">
                  Email to <span className="font-medium">procurement-lead@retailco.com</span> with the
                  PO detail table attached.
                </span>
              </li>
              <li className="flex items-start gap-3 rounded-lg border border-border bg-surface-secondary/40 px-3 py-2 text-sm">
                <FileText className="mt-0.5 h-4 w-4 shrink-0 text-text-muted" aria-hidden />
                <span className="text-text-primary">
                  ERP inventory system updated with {approved.length} new PO record{approved.length === 1 ? "" : "s"}.
                </span>
              </li>
              {rejected.length > 0 ? (
                <li className="flex items-start gap-3 rounded-lg border border-[#fca5a5] bg-[#fef3f2] px-3 py-2 text-sm">
                  <XCircle className="mt-0.5 h-4 w-4 shrink-0 text-[#b42318]" aria-hidden />
                  <span className="text-[#b42318]">
                    {rejected.length} recommendation{rejected.length === 1 ? "" : "s"} rejected. Stock
                    levels will be re-evaluated on the next run.
                  </span>
                </li>
              ) : null}
            </ul>
          </section>

          <section className="rounded-xl border border-border bg-surface p-5 shadow-sm">
            <div className="mb-3 flex items-center gap-2">
              <BarChart3 className="h-4 w-4 text-primary" aria-hidden />
              <h3 className="text-sm font-semibold text-text-primary">Where to look next</h3>
            </div>
            <ul className="space-y-2 text-sm text-text-secondary">
              <li>
                <span className="font-medium text-text-primary">Impact Dashboard</span> - track how
                these actions influence stockout rate, on-hand days, and gross margin over the
                next 30 days.
              </li>
              <li>
                <span className="font-medium text-text-primary">Monitor</span> - see live status of
                the raised POs as they move from Queued to Confirmed to Delivered.
              </li>
              <li>
                <span className="font-medium text-text-primary">Co-Pilot Decision Feed</span> - the
                same decisions are also logged here alongside other Co-Pilot actions from{" "}
                {ucName ? `"${ucName}"` : "this use case"} and others.
              </li>
            </ul>
          </section>
        </>
      ) : null}
    </div>
  );
}

// Autopilot result section
function AutopilotResult() {
  const actions = [
    { label: "Fetched inventory data for 847 SKUs", time: "0.8s" },
    { label: "Applied demand forecast model", time: "2.1s" },
    { label: "Identified 23 at-risk SKUs", time: "1.3s" },
    { label: "Generated KPI impact summary", time: "0.4s" },
    { label: "Raised 23 purchase orders within guardrail threshold", time: "3.2s" },
  ];

  return (
    <div className="space-y-4">
      <div className="flex items-start gap-3 rounded-xl border border-primary/35 bg-primary-light px-4 py-4 dark:border-primary/45 dark:bg-primary/15">
        <Zap size={20} className="mt-0.5 shrink-0 text-primary" />
        <div>
          <p className="text-sm font-semibold text-primary">Autopilot completed 5 actions</p>
          <p className="mt-1 text-sm text-text-secondary">
            All actions were executed within guardrails. Full log is available in Monitor.
          </p>
        </div>
      </div>

      <div className="space-y-2">
        {actions.map((a, i) => (
          <div key={i} className="flex items-center gap-3 rounded-lg border border-border bg-surface px-4 py-2.5">
            <CheckCircle2 size={14} className="shrink-0 text-status-success" />
            <p className="flex-1 text-sm text-text-primary">{a.label}</p>
            <span className="shrink-0 text-xs text-text-muted">{a.time}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function UCBuilderStep9ExecutionSummary() {
  const [payload, setPayload] = useState(null);
  const [allDecisions, setAllDecisions] = useState([]);

  useEffect(() => {
    try {
      // Launch summary is short-lived (only used immediately after Launch),
      // so it stays in sessionStorage. Decision cards below use localStorage
      // because a user's approvals must persist across refresh / tab close.
      const raw = sessionStorage.getItem(USE_CASE_LAUNCH_SUMMARY_KEY);
      if (raw) setPayload(JSON.parse(raw));
      const dec = localStorage.getItem(COPILOT_DECISIONS_KEY);
      if (dec) setAllDecisions(JSON.parse(dec));
    } catch {}
  }, []);

  // Filter the localStorage-wide decision queue to just the use case that
  // was just launched. Storage accumulates cards across multiple launched
  // use cases; Step 9 should only show the one the user just activated.
  const launchedUcId = payload?.id || null;
  const decisions = launchedUcId
    ? allDecisions.filter((c) => c.useCaseId === launchedUcId)
    : allDecisions;
  const setDecisions = (updater) => {
    setAllDecisions((prev) => {
      const visible = launchedUcId ? prev.filter((c) => c.useCaseId === launchedUcId) : prev;
      const nextVisible = typeof updater === "function" ? updater(visible) : updater;
      if (!launchedUcId) return nextVisible;
      const other = prev.filter((c) => c.useCaseId !== launchedUcId);
      return [...other, ...nextVisible];
    });
  };

  const updateCard = useCallback((id, patch) => {
    setAllDecisions((prev) => {
      const next = prev.map((c) => (c.id === id ? { ...c, ...patch } : c));
      try {
        localStorage.setItem(COPILOT_DECISIONS_KEY, JSON.stringify(next));
      } catch {
        /* ignore */
      }
      return next;
    });
  }, []);

  const handleApprove = useCallback((id) => updateCard(id, { status: "approved" }), [updateCard]);
  const handleReject = useCallback((id) => updateCard(id, { status: "rejected" }), [updateCard]);
  const handleModify = useCallback(
    (id, patch) => {
      setAllDecisions((prev) => {
        const next = prev.map((c) => (c.id === id ? { ...c, ...patch } : c));
        try {
          localStorage.setItem(COPILOT_DECISIONS_KEY, JSON.stringify(next));
        } catch {
          /* ignore */
        }
        return next;
      });
    },
    []
  );

  const autonomy = payload?.autonomy || (decisions.length > 0 ? "copilot" : "autopilot");
  const isCopilot = autonomy === "copilot";
  const hasCards = decisions.length > 0;
  const ucName = payload?.title || "Use Case";
  const catalogId = payload?.id;
  const question = payload?.primaryQuestion ?? "";
  const category = payload?.category ?? "";
  const stats = resolveExecutionStats(question, category);

  // Resolve the actual plan items that ran, using the same resolver Steps
  // 4/5/6 use so this page stays in sync with what the user configured.
  // Falls back gracefully when the catalog entry is not yet hydrated.
  const { entries } = useUseCaseCatalog();
  const catalogEntry = useMemo(
    () => (catalogId ? (entries || []).find((e) => e.id === catalogId) ?? null : null),
    [entries, catalogId]
  );
  const executedPlanItems = useMemo(() => {
    const snapshot = catalogEntry?.draftSnapshot;
    if (Array.isArray(snapshot?.step4?.planItems) && snapshot.step4.planItems.length > 0) {
      return snapshot.step4.planItems;
    }
    const sources = (snapshot?.step3?.confirmedSources || []).filter((s) => s.enabled);
    const knowledge = (snapshot?.step3?.confirmedKnowledge || []).filter((k) => k.enabled);
    return resolveContextualPlanItems(
      snapshot?.step2?.primaryQuestion || question,
      snapshot?.step1?.category || category,
      sources,
      knowledge,
      snapshot?.step1?.title || ucName,
      catalogEntry
    );
  }, [catalogEntry, question, category, ucName]);

  return (
    <div className="mx-auto max-w-3xl px-6 py-10">
      <Breadcrumb items={[{ label: "Use Cases", to: "/use-cases" }, { label: ucName }]} />

      {/* Success header */}
      <div className="mb-8 flex items-start gap-5 rounded-2xl border border-status-success-border bg-status-success-light/50 px-6 py-6 dark:bg-status-success/10 dark:border-status-success-border/50">
        <div className="flex h-14 w-14 shrink-0 items-center justify-center rounded-full bg-surface shadow-sm">
          <CheckCircle2 size={32} className="text-status-success" />
        </div>
        <div>
          <h1 className="text-xl font-semibold text-text-primary">Use case activated</h1>
          <p className="mt-1 text-sm text-text-secondary">
            <span className="font-medium">{ucName}</span> ran successfully. Here is what the AI did.
          </p>
        </div>
      </div>

      {/* Stats */}
      <div className="mb-6 grid gap-4 sm:grid-cols-3">
        <StatTile label="Tasks completed" value={`${executedPlanItems.length || (payload?.taskCount ?? 5)} of ${executedPlanItems.length || (payload?.taskCount ?? 5)}`} sub="All tasks ran without errors" />
        <StatTile label="Records analysed" value={stats.records} sub="Across confirmed data sources" />
        <StatTile label="Actions prepared" value={stats.findings} sub={stats.actions} />
      </div>

      {/* Steps that ran - compact title-only list. Sourced from the same
          resolver Steps 4/5/6 use, so it stays in sync with the plan the
          user configured. Full descriptions live on Step 4 (AI Plan). */}
      {executedPlanItems.length > 0 ? (
        <div className="mb-6 rounded-xl border border-border bg-surface-default px-5 py-4 shadow-sm">
          <div className="mb-3 flex items-center gap-2">
            <ListChecks size={14} className="text-primary" />
            <p className="text-xs font-semibold uppercase tracking-wide text-text-muted">Steps that ran</p>
          </div>
          <ol className="space-y-1.5">
            {executedPlanItems.map((item, i) => (
              <li
                key={i}
                title={item.description || undefined}
                className="flex items-start gap-2.5 text-sm text-text-primary"
              >
                <CheckCircle2 size={14} className="mt-0.5 shrink-0 text-status-success" />
                <span className="min-w-0">
                  <span className="font-medium text-text-muted mr-1.5">{i + 1}.</span>
                  {item.title}
                </span>
              </li>
            ))}
          </ol>
        </div>
      ) : null}

      {/* Co-pilot with cards / co-pilot analysis-only / autopilot. */}
      <div className="mb-6">
        <h2 className="mb-4 text-base font-semibold text-text-primary">
          {isCopilot
            ? hasCards
              ? "Decision Cards"
              : "Insights delivered"
            : "Autopilot action log"}
        </h2>
        {isCopilot ? (
          hasCards ? (
            <CopilotResult
              cards={decisions}
              onApprove={handleApprove}
              onReject={handleReject}
              onModify={handleModify}
            />
          ) : (
            <div className="flex items-start gap-3 rounded-xl border border-primary/30 bg-primary-light px-4 py-4 dark:border-primary/40 dark:bg-primary/15">
              <ClipboardCheck size={20} className="mt-0.5 shrink-0 text-primary" />
              <div>
                <p className="text-sm font-semibold text-primary">
                  No decisions to approve
                </p>
                <p className="mt-1 text-sm text-text-secondary">
                  This use case is designed to produce insights and reports rather than propose
                  actions. Findings are ready in the Impact Dashboard and Monitor. Nothing needs
                  your approval.
                </p>
              </div>
            </div>
          )
        ) : (
          <AutopilotResult />
        )}
      </div>

      {/* Delivered - only shows once every decision card is approved or
          rejected. Answers the "what actually happened?" question so the
          activate -> review -> approve -> outcome flow feels complete. */}
      {isCopilot && hasCards && decisions.every((c) => c.status !== "pending") ? (
        <DecisionsDeliveredSummary cards={decisions} ucName={ucName} />
      ) : null}

      {/* Note */}
      <div className="mb-8 flex items-start gap-3 rounded-xl border border-status-warning-border bg-status-warning-light px-4 py-3 dark:bg-status-warning/10">
        <AlertTriangle size={15} className="mt-0.5 shrink-0 text-status-warning" />
        <p className="text-sm text-status-warning">
          Note: 3 SKUs had no matching supplier record. These were excluded from recommendations and flagged for manual review in Monitor.
        </p>
      </div>

      {/* CTAs - Step 9 is the "just launched" moment. Send the user to the
          use case's own activation page as the ongoing home; Impact and
          Monitor links live there contextually. Duplicating them here would
          add noise. */}
      <div className="flex flex-wrap items-center gap-3 border-t border-border pt-6">
        {catalogId ? (
          <Link
            to={`/use-cases/activate/${encodeURIComponent(catalogId)}`}
            className="flex items-center gap-2 rounded-lg bg-primary px-5 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-primary-hover"
          >
            <ExternalLink size={14} />
            Go to use case
          </Link>
        ) : null}
        <Link
          to="/use-cases"
          className="ml-auto text-sm font-medium text-text-muted hover:text-text-primary"
        >
          Back to Use Cases
        </Link>
      </div>
    </div>
  );
}
