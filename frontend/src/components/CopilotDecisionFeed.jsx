import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { CheckCircle2, ClipboardCheck } from "lucide-react";
import Breadcrumb from "./shared/Breadcrumb";
import DecisionCard from "./shared/DecisionCard";
import { COPILOT_DECISIONS_KEY } from "../storage/keys";

const DEFAULT_CARDS = [
  {
    id: "dc1",
    title: "Reorder recommendation - SKU-4821 (Sony WH-1000XM5)",
    reasoning: "Stock level is at 3 days remaining based on retail_db.inventory. 14-day forecast shows demand of 115 units. Reorder threshold: 7 days.",
    action: "Raise PO: 120 units from Supplier A (TechDistrib Ltd). Est. delivery in 7 days.",
    value: 18400,
    supplier: "TechDistrib Ltd",
    status: "pending",
  },
  {
    id: "dc2",
    title: "Reorder recommendation - SKU-7234 (Samsung 65\" QLED)",
    reasoning: "Stock level is at 5 days remaining. Seasonal uplift of 35% expected next week per electronics buying guide. Reorder threshold: 10 days.",
    action: "Raise PO: 80 units from Supplier B (Samsung Direct). Est. delivery in 10 days.",
    value: 11200,
    supplier: "Samsung Direct",
    status: "pending",
  },
  {
    id: "dc3",
    title: "Reorder recommendation - SKU-1199 (Apple AirPods Pro)",
    reasoning: "Current stock covers 8 days. Supplier C has a 12-day lead time - ordering now avoids a 4-day gap in stock coverage.",
    action: "Raise PO: 50 units from Supplier C (Apple AU). Est. delivery in 12 days.",
    value: 7500,
    supplier: "Apple AU",
    status: "pending",
  },
];

// DecisionCard is now in shared/DecisionCard.jsx (used by both this page
// and the inline card list on the Builder Step 9 activation summary).

export default function CopilotDecisionFeed() {
  const [searchParams] = useSearchParams();
  const fromKpiDashboard = searchParams.get("source") === "kpi-dashboard";
  // When Review Decisions is clicked from a specific use case's activation
  // page, the link carries ?useCaseId=X so this feed can show just that
  // use case's cards instead of the mixed queue.
  const filterUseCaseId = searchParams.get("useCaseId") || "";
  const [allCards, setAllCards] = useState([]);

  useEffect(() => {
    try {
      const raw = localStorage.getItem(COPILOT_DECISIONS_KEY);
      const parsed = raw ? JSON.parse(raw) : null;
      setAllCards(parsed?.length ? parsed : DEFAULT_CARDS);
    } catch {
      setAllCards(DEFAULT_CARDS);
    }
  }, []);

  const cards = filterUseCaseId
    ? allCards.filter((c) => c.useCaseId === filterUseCaseId)
    : allCards;
  const filterUseCaseTitle = filterUseCaseId
    ? allCards.find((c) => c.useCaseId === filterUseCaseId)?.useCaseTitle || ""
    : "";
  const setCards = setAllCards;

  const updateCard = (id, patch) => {
    setCards((prev) => {
      const next = prev.map((c) => (c.id === id ? { ...c, ...patch } : c));
      try { localStorage.setItem(COPILOT_DECISIONS_KEY, JSON.stringify(next)); } catch {}
      return next;
    });
  };

  const approve = (id) => updateCard(id, { status: "approved" });
  const reject = (id) => updateCard(id, { status: "rejected" });
  const modify = (id, patch) => {
    setCards((prev) => {
      const next = prev.map((c) => (c.id === id ? { ...c, ...patch } : c));
      try { localStorage.setItem(COPILOT_DECISIONS_KEY, JSON.stringify(next)); } catch {}
      return next;
    });
  };

  const pending = cards.filter((c) => c.status === "pending").length;
  const approved = cards.filter((c) => c.status === "approved").length;
  const approvedValue = cards.filter((c) => c.status === "approved").reduce((s, c) => s + c.value, 0);

  return (
    <div className="mx-auto max-w-3xl px-6 py-10">
      <Breadcrumb items={[{ label: "Use Cases", to: "/use-cases" }, { label: "Co-Pilot Decision Feed" }]} />

      {/* Header */}
      {fromKpiDashboard ? (
        <div
          className="mb-6 rounded-xl border border-primary/30 bg-primary-light dark:bg-primary/15 px-4 py-3 text-sm text-text-secondary"
          role="status"
        >
          Opened from the <span className="font-semibold text-text-primary">KPI Dashboard</span> anomaly feed. Use
          Approve or Reject to simulate Co-Pilot actions tied to KPI guardrails.
        </div>
      ) : null}

      <div className="mb-8">
        <div className="inline-flex items-center gap-2 rounded-full bg-[#fffaeb] px-3 py-1 text-xs font-semibold uppercase tracking-wide text-[#b54708]">
          <ClipboardCheck size={13} />
          Co-Pilot Decision Feed
        </div>
        <h1 className="mt-3 text-2xl font-semibold tracking-tight text-text-primary">
          {filterUseCaseTitle
            ? `Decisions for ${filterUseCaseTitle}`
            : "Review AI recommendations"}
        </h1>
        <p className="mt-1 text-sm text-text-secondary">
          {filterUseCaseTitle
            ? "Only decisions from this use case are shown. No actions are taken until you approve."
            : "The AI has prepared these actions for your approval. No orders are placed until you approve."}
        </p>
        {filterUseCaseTitle && allCards.length > cards.length ? (
          <a
            href="/copilot/decisions"
            className="mt-2 inline-block text-xs font-semibold text-primary hover:underline"
          >
            View all {allCards.length} decisions across use cases
          </a>
        ) : null}
      </div>

      {/* Summary row */}
      <div className="mb-6 grid grid-cols-3 gap-4">
        <div className="rounded-xl border border-[#fde68a] bg-[#fffaeb] p-4 text-center">
          <p className="text-2xl font-semibold text-[#b54708]">{pending}</p>
          <p className="mt-0.5 text-xs font-medium text-[#92400e]">Pending</p>
        </div>
        <div className="rounded-xl border border-[#a7f3d0] bg-[#ecfdf3] p-4 text-center">
          <p className="text-2xl font-semibold text-[#067647]">{approved}</p>
          <p className="mt-0.5 text-xs font-medium text-[#055c39]">Approved</p>
        </div>
        <div className="rounded-xl border border-border bg-surface p-4 text-center">
          <p className="text-2xl font-semibold text-text-primary">${approvedValue.toLocaleString()}</p>
          <p className="mt-0.5 text-xs font-medium text-text-secondary">Value approved</p>
        </div>
      </div>

      {pending === 0 && approved === cards.length && (
        <div className="mb-6 flex items-center gap-3 rounded-xl border border-[#a7f3d0] bg-[#ecfdf3] px-4 py-3">
          <CheckCircle2 size={18} className="text-[#067647]" />
          <p className="text-sm font-medium text-[#067647]">
            All {cards.length} cards approved. ${approvedValue.toLocaleString()} in purchase orders raised.
          </p>
        </div>
      )}

      {/* Cards grouped by use case so multiple contributing use cases stay
          separated. Cards without a useCaseTitle group under "Other". */}
      <div className="space-y-8">
        {(() => {
          const groups = new Map();
          for (const card of cards) {
            const key = card.useCaseTitle || "Other";
            if (!groups.has(key)) groups.set(key, []);
            groups.get(key).push(card);
          }
          return Array.from(groups.entries()).map(([useCaseTitle, groupCards]) => (
            <section key={useCaseTitle} aria-label={`Decisions from ${useCaseTitle}`}>
              <div className="mb-3 flex items-baseline justify-between gap-2 border-b border-border pb-2">
                <h2 className="text-sm font-semibold text-text-primary">
                  {useCaseTitle}
                </h2>
                <p className="text-xs text-text-muted">
                  {groupCards.length} decision{groupCards.length === 1 ? "" : "s"}
                </p>
              </div>
              <div className="space-y-4">
                {groupCards.map((card) => (
                  <DecisionCard
                    key={card.id}
                    card={card}
                    onApprove={approve}
                    onReject={reject}
                    onModify={modify}
                  />
                ))}
              </div>
            </section>
          ));
        })()}
      </div>

      {cards.length === 0 && (
        <div className="flex flex-col items-center py-16 text-center">
          <ClipboardCheck size={40} className="mb-3 text-[#d5d7da]" />
          <p className="text-base font-medium text-text-secondary">No Decision Cards pending</p>
          <p className="mt-1 text-sm text-text-muted">Cards will appear here after you activate a Co-Pilot use case.</p>
        </div>
      )}
    </div>
  );
}
