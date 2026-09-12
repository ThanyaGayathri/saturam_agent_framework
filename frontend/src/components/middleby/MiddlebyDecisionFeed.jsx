import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { CheckCircle2, ClipboardCheck, XCircle } from "lucide-react";
import Breadcrumb from "../shared/Breadcrumb";
import {
  getMiddlebyDecisionSeedCards,
  getMiddlebyDecisionSeedCardsForPattern,
  MIDDLEBY_AUTOMATION_PATTERNS,
  MIDDLEBY_DECISIONS_KEY,
} from "../../domain/middleby-decision-seeds";

function StatusBadge({ status }) {
  if (status === "approved")
    return (
      <span className="rounded-full bg-status-success-light px-2.5 py-0.5 text-[11px] font-semibold text-status-success">
        Approved
      </span>
    );
  if (status === "rejected")
    return (
      <span className="rounded-full bg-status-error-light px-2.5 py-0.5 text-[11px] font-semibold text-status-error">
        Rejected
      </span>
    );
  return (
    <span className="rounded-full bg-amber-100 px-2.5 py-0.5 text-[11px] font-semibold text-amber-800 dark:bg-amber-500/20 dark:text-amber-100">
      Pending
    </span>
  );
}

function DecisionCard({ card, onApprove, onReject, highlighted, cardRef }) {
  const isPending = card.status === "pending";

  return (
    <div
      ref={cardRef}
      className={`rounded-2xl border bg-surface-default shadow-sm transition-all ${
        highlighted ? "border-primary ring-2 ring-primary/30" : ""
      } ${
        card.status === "approved"
          ? "border-status-success/40"
          : card.status === "rejected"
            ? "border-status-error/40 opacity-70"
            : highlighted
              ? ""
              : "border-border"
      }`}
    >
      <div className="flex items-start justify-between gap-4 border-b border-border px-5 py-4">
        <div className="flex min-w-0 items-start gap-3">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-primary-light dark:bg-primary/15">
            <ClipboardCheck size={18} className="text-primary" />
          </div>
          <div className="min-w-0">
            <p className="text-sm font-semibold text-text-primary">{card.title}</p>
            <p className="mt-0.5 text-xs text-text-muted">
              Pattern {card.patternId} · Owner: {card.dealer}
            </p>
          </div>
        </div>
        <StatusBadge status={card.status} />
      </div>

      <div className="space-y-3 px-5 py-4">
        <div className="rounded-lg bg-surface-secondary px-3 py-2.5">
          <p className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-text-muted">
            AI reasoning
          </p>
          <p className="text-sm leading-relaxed text-text-secondary">{card.reasoning}</p>
        </div>
        <div className="rounded-lg border border-border bg-surface-default px-3 py-2.5">
          <p className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-text-muted">
            Recommended action
          </p>
          <p className="text-sm font-medium text-text-primary">{card.action}</p>
        </div>
      </div>

      {isPending ? (
        <div className="flex items-center gap-3 border-t border-border px-5 py-4">
          <button
            type="button"
            onClick={() => onApprove(card.id)}
            className="flex flex-1 items-center justify-center gap-2 rounded-lg bg-status-success px-4 py-2.5 text-sm font-semibold text-white transition hover:opacity-90"
          >
            <CheckCircle2 size={15} />
            Approve
          </button>
          <button
            type="button"
            onClick={() => onReject(card.id)}
            className="flex flex-1 items-center justify-center gap-2 rounded-lg border border-border px-4 py-2.5 text-sm font-semibold text-status-error transition hover:bg-status-error-light"
          >
            <XCircle size={15} />
            Reject
          </button>
        </div>
      ) : null}

      {card.status === "approved" ? (
        <div className="flex items-center gap-2 border-t border-status-success/30 px-5 py-3">
          <CheckCircle2 size={14} className="text-status-success" />
          <p className="text-sm text-status-success">Approved. Action queued for the owning team.</p>
        </div>
      ) : null}
      {card.status === "rejected" ? (
        <div className="flex items-center gap-2 border-t border-status-error/30 px-5 py-3">
          <XCircle size={14} className="text-status-error" />
          <p className="text-sm text-status-error">Rejected. No action was taken.</p>
        </div>
      ) : null}
    </div>
  );
}

/** Middleby-only automation feed (separate storage from retail Co-Pilot). */
export default function MiddlebyDecisionFeed() {
  const [searchParams] = useSearchParams();
  const patternFilter = searchParams.get("pattern");
  const cardFocus = searchParams.get("card");
  const [cards, setCards] = useState([]);
  const focusRef = useRef(null);

  const patternMeta = useMemo(
    () => MIDDLEBY_AUTOMATION_PATTERNS.find((p) => p.id === patternFilter) ?? null,
    [patternFilter]
  );

  useEffect(() => {
    try {
      const raw = sessionStorage.getItem(MIDDLEBY_DECISIONS_KEY);
      const parsed = raw ? JSON.parse(raw) : null;
      const seed = parsed?.length ? parsed : getMiddlebyDecisionSeedCards();
      setCards(seed);
      if (!parsed?.length) {
        sessionStorage.setItem(MIDDLEBY_DECISIONS_KEY, JSON.stringify(seed));
      }
    } catch {
      setCards(getMiddlebyDecisionSeedCards());
    }
  }, []);

  const visibleCards = useMemo(() => {
    if (!patternFilter) return cards;
    const forPattern = getMiddlebyDecisionSeedCardsForPattern(patternFilter);
    if (!forPattern.length) return cards.filter((c) => c.patternId === patternFilter);
    const ids = new Set(forPattern.map((c) => c.id));
    const merged = cards.filter((c) => ids.has(c.id) || c.patternId === patternFilter);
    return merged.length ? merged : forPattern;
  }, [cards, patternFilter]);

  useEffect(() => {
    if (!cardFocus || !focusRef.current) return;
    const t = window.setTimeout(() => {
      focusRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
    }, 120);
    return () => window.clearTimeout(t);
  }, [cardFocus, visibleCards]);

  const updateCard = (id, patch) => {
    setCards((prev) => {
      const next = prev.map((c) => (c.id === id ? { ...c, ...patch } : c));
      try {
        sessionStorage.setItem(MIDDLEBY_DECISIONS_KEY, JSON.stringify(next));
      } catch {
        /* ignore */
      }
      return next;
    });
  };

  const pending = visibleCards.filter((c) => c.status === "pending").length;
  const approved = visibleCards.filter((c) => c.status === "approved").length;

  return (
    <div className="mx-auto max-w-3xl px-6 py-10">
      <Breadcrumb
        items={[
          { label: "Home", to: "/playground" },
          { label: "Automation", to: "/analytics/automation" },
          { label: "Approval queue" },
        ]}
      />

      <div className="mb-8 mt-4">
        <div className="inline-flex items-center gap-2 rounded-full bg-primary-light px-3 py-1 text-xs font-semibold uppercase tracking-wide text-primary dark:bg-primary/15">
          <ClipboardCheck size={13} />
          Approval queue
        </div>
        <h1 className="mt-3 text-2xl font-semibold tracking-tight text-text-primary">
          Pending approvals
        </h1>
        <p className="mt-1 text-sm text-text-secondary">
          Quick review of all automation cards. For the full walkthrough, open a workflow from the
          automation hub.
        </p>
        <Link
          to="/analytics/automation"
          className="mt-3 inline-flex text-sm font-semibold text-primary hover:underline"
        >
          Back to automation workflows
        </Link>
        {patternMeta ? (
          <p className="mt-3 rounded-lg border border-primary/25 bg-primary-light/50 px-3 py-2 text-sm text-text-secondary dark:bg-primary/10">
            Filtered to <span className="font-semibold text-text-primary">{patternMeta.label}</span>.{" "}
            <Link
              to={`/analytics/automation/${encodeURIComponent(patternMeta.id)}`}
              className="font-semibold text-primary hover:underline"
            >
              Open full workflow
            </Link>
          </p>
        ) : null}
        {cardFocus ? (
          <p className="mt-2 text-xs text-text-muted">
            Highlighting card from the use case workflow tab. Approve or reject below.
          </p>
        ) : null}
      </div>

      <div className="mb-6 grid grid-cols-2 gap-4">
        <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-center dark:border-amber-500/30 dark:bg-amber-500/10">
          <p className="text-2xl font-semibold text-amber-800 dark:text-amber-100">{pending}</p>
          <p className="mt-0.5 text-xs font-medium text-amber-900/80 dark:text-amber-200/80">Pending</p>
        </div>
        <div className="rounded-xl border border-status-success/30 bg-status-success-light p-4 text-center">
          <p className="text-2xl font-semibold text-status-success">{approved}</p>
          <p className="mt-0.5 text-xs font-medium text-status-success">Approved</p>
        </div>
      </div>

      <div className="space-y-6">
        {visibleCards.map((card) => (
          <DecisionCard
            key={card.id}
            card={card}
            highlighted={card.id === cardFocus}
            cardRef={card.id === cardFocus ? focusRef : null}
            onApprove={(id) => updateCard(id, { status: "approved" })}
            onReject={(id) => updateCard(id, { status: "rejected" })}
          />
        ))}
      </div>

      {visibleCards.length === 0 ? (
        <div className="flex flex-col items-center py-16 text-center">
          <ClipboardCheck size={40} className="mb-3 text-text-muted" />
          <p className="text-base font-medium text-text-secondary">No decision cards for this filter</p>
          <Link to="/analytics/automation" className="mt-3 text-sm font-semibold text-primary hover:underline">
            Back to automation hub
          </Link>
        </div>
      ) : null}
    </div>
  );
}
