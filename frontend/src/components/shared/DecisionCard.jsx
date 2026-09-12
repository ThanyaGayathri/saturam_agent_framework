import { useState } from "react";
import { CheckCircle2, XCircle, ClipboardCheck, Edit3, Check, X } from "lucide-react";

/**
 * Single Co-Pilot decision card with approve / reject / modify affordances.
 *
 * Presentation-only. State (approve/reject/modify) is managed by the parent
 * via callbacks so the card can be reused in multiple contexts (dedicated
 * Decision Feed page, Step 9 inline, etc.).
 */

function StatusBadge({ status }) {
  if (status === "approved") {
    return <span className="rounded-full bg-[#ecfdf3] px-2.5 py-0.5 text-[11px] font-semibold text-[#067647]">Approved</span>;
  }
  if (status === "rejected") {
    return <span className="rounded-full bg-[#fef3f2] px-2.5 py-0.5 text-[11px] font-semibold text-[#b42318]">Rejected</span>;
  }
  return <span className="rounded-full bg-[#fffaeb] px-2.5 py-0.5 text-[11px] font-semibold text-[#b54708]">Pending</span>;
}

// Actions like "Raise PO: 120 units", "Apply 15% markdown", "Transfer 240 lbs",
// "Approve 8 hours overtime", "Add 12 associate-hours ..." each carry a
// modifiable numeric quantity. Detect the first matching pattern so the
// Modify affordance can edit the right unit type. Order matters:
// - % is checked before "hours" so "15% markdown for 48 hours" edits the %.
// - "associate-hours" is checked before "hours" so it wins over the generic.
const MODIFY_PATTERNS = [
  { key: "percent", regex: /(\d+(?:\.\d+)?)\s*%/, format: (n) => `${n}%`, label: "%", step: 1 },
  { key: "assocHours", regex: /(\d+)\s*associate-hours/, format: (n) => `${n} associate-hours`, label: "assoc-hrs", step: 1 },
  { key: "lbs", regex: /(\d+)\s*lbs/, format: (n) => `${n} lbs`, label: "lbs", step: 10 },
  { key: "units", regex: /(\d+)\s*units/, format: (n) => `${n} units`, label: "units", step: 5 },
  { key: "hours", regex: /(\d+)\s*hours/, format: (n) => `${n} hours`, label: "hours", step: 1 },
];

function extractModifiable(action) {
  if (!action) return null;
  for (const p of MODIFY_PATTERNS) {
    const m = action.match(p.regex);
    if (m) return { ...p, value: parseFloat(m[1]) };
  }
  return null;
}

export default function DecisionCard({ card, onApprove, onReject, onModify }) {
  const [editing, setEditing] = useState(false);
  const modifiable = extractModifiable(card.action);
  const canModify = Boolean(modifiable);
  const [qty, setQty] = useState(() => modifiable?.value ?? 0);

  const isPending = card.status === "pending";

  return (
    <div
      className={`rounded-2xl border bg-surface shadow-sm transition-all ${
        card.status === "approved"
          ? "border-[#a7f3d0]"
          : card.status === "rejected"
          ? "border-[#fca5a5] opacity-70"
          : "border-border"
      }`}
    >
      <div className="flex items-start justify-between gap-4 border-b border-[#f2f2f5] px-5 py-4">
        <div className="flex items-start gap-3 min-w-0">
          <div
            className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-full ${
              card.status === "approved"
                ? "bg-[#ecfdf3]"
                : card.status === "rejected"
                ? "bg-[#fef3f2]"
                : "bg-[#fffaeb]"
            }`}
          >
            <ClipboardCheck
              size={18}
              className={
                card.status === "approved"
                  ? "text-[#067647]"
                  : card.status === "rejected"
                  ? "text-[#b42318]"
                  : "text-[#b54708]"
              }
            />
          </div>
          <div className="min-w-0">
            {card.useCaseTitle ? (
              <p className="text-[10px] font-semibold uppercase tracking-wide text-primary">
                From: {card.useCaseTitle}
              </p>
            ) : null}
            <p className="truncate text-sm font-semibold text-text-primary">{card.title}</p>
            {card.supplier ? (
              <p className="mt-0.5 text-xs text-text-muted">Supplier: {card.supplier}</p>
            ) : null}
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

        <div className="rounded-lg border border-border bg-surface px-3 py-2.5">
          <p className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-text-muted">
            Recommended action
          </p>
          {editing && modifiable ? (
            <div className="flex flex-wrap items-center gap-2">
              <p className="min-w-0 flex-1 text-sm text-text-primary">
                {card.action.replace(modifiable.regex, modifiable.format(qty))}
              </p>
              <div className="flex items-center gap-1">
                <input
                  type="number"
                  value={qty}
                  step={modifiable.step}
                  min={0}
                  onChange={(e) => setQty(Number(e.target.value))}
                  className="w-20 rounded-md border border-border-light px-2 py-1 text-sm focus:border-primary focus:outline-none"
                />
                <span className="text-xs text-text-secondary">{modifiable.label}</span>
                <button
                  type="button"
                  onClick={() => {
                    const nextAction = card.action.replace(modifiable.regex, modifiable.format(qty));
                    const ratio = modifiable.value > 0 ? qty / modifiable.value : 0;
                    const nextValue = ratio > 0 ? Math.round((card.value ?? 0) * ratio) : (card.value ?? 0);
                    onModify?.(card.id, { action: nextAction, value: nextValue, qty });
                    setEditing(false);
                  }}
                  className="rounded-md bg-primary p-1 text-white hover:bg-primary-hover"
                  aria-label="Apply change"
                >
                  <Check size={13} />
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setQty(modifiable.value);
                    setEditing(false);
                  }}
                  className="rounded-md border border-border-light p-1 text-text-secondary hover:bg-surface-secondary"
                  aria-label="Cancel"
                >
                  <X size={13} />
                </button>
              </div>
            </div>
          ) : (
            <div className="flex items-center justify-between gap-2">
              <p className="text-sm font-medium text-text-primary">{card.action}</p>
              {isPending && onModify && canModify ? (
                <button
                  type="button"
                  onClick={() => setEditing(true)}
                  className="flex shrink-0 items-center gap-1 text-xs text-primary hover:underline"
                >
                  <Edit3 size={11} />
                  Modify {modifiable.label}
                </button>
              ) : null}
            </div>
          )}
        </div>

        {typeof card.value === "number" ? (
          <div className="flex items-center justify-between rounded-lg border border-border bg-surface px-3 py-2">
            <p className="text-sm text-text-secondary">Estimated value</p>
            <p className="text-sm font-semibold text-text-primary">
              ${(card.value ?? 0).toLocaleString()}
            </p>
          </div>
        ) : null}
      </div>

      {isPending ? (
        <div className="flex items-center gap-3 border-t border-[#f2f2f5] px-5 py-4">
          <button
            type="button"
            onClick={() => onApprove?.(card.id)}
            className="flex flex-1 items-center justify-center gap-2 rounded-lg bg-[#067647] px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-[#055c39]"
          >
            <CheckCircle2 size={15} />
            Approve
          </button>
          <button
            type="button"
            onClick={() => onReject?.(card.id)}
            className="flex flex-1 items-center justify-center gap-2 rounded-lg border border-border-light px-4 py-2.5 text-sm font-semibold text-[#b42318] transition hover:bg-[#fef3f2]"
          >
            <XCircle size={15} />
            Reject
          </button>
        </div>
      ) : null}

      {card.status === "approved" ? (
        <div className="flex items-center gap-2 border-t border-[#a7f3d0] px-5 py-3">
          <CheckCircle2 size={14} className="text-[#067647]" />
          <p className="text-sm text-[#067647]">Approved. Action queued for execution.</p>
        </div>
      ) : null}
      {card.status === "rejected" ? (
        <div className="flex items-center gap-2 border-t border-[#fca5a5] px-5 py-3">
          <XCircle size={14} className="text-[#b42318]" />
          <p className="text-sm text-[#b42318]">Rejected. No action was taken.</p>
        </div>
      ) : null}
    </div>
  );
}
