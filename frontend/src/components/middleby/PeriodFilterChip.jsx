// The period-filter chip. Dropdown options show concrete date ranges
// (e.g. "Jul 2026", "Jul to Sep 2026") so the window is obvious at a glance.

import { useEffect, useRef, useState } from "react";
import { CalendarRange, ChevronDown, X } from "lucide-react";
import { MAX_CUSTOM_RANGE_DAYS, validateCustomRange } from "../../storage/chat-filters";

function todayISO() {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function daysAgoISO(days) {
  const d = new Date();
  d.setDate(d.getDate() - days);
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

export default function PeriodFilterChip({ chatFilters }) {
  const [open, setOpen] = useState(false);
  const [draftFrom, setDraftFrom] = useState(chatFilters.fromDate || daysAgoISO(30));
  const [draftTo, setDraftTo] = useState(chatFilters.toDate || todayISO());
  const [error, setError] = useState(null);
  const popoverRef = useRef(null);

  useEffect(() => {
    if (open) {
      setDraftFrom(chatFilters.fromDate || daysAgoISO(30));
      setDraftTo(chatFilters.toDate || todayISO());
      setError(null);
    }
  }, [open, chatFilters.fromDate, chatFilters.toDate]);

  useEffect(() => {
    if (!open) return;
    const handleClickOutside = (e) => {
      if (popoverRef.current && !popoverRef.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [open]);

  const currentLabel = () => chatFilters.displayLabel || chatFilters.periods?.[0]?.label || "Period";

  const handleSelect = (e) => {
    const val = e.target.value;
    if (val === "custom") {
      setOpen(true);
      return;
    }
    chatFilters.setPeriod(val);
  };

  const commitCustom = () => {
    const validation = validateCustomRange(draftFrom, draftTo);
    if (!validation.ok) {
      setError(validation.error);
      return;
    }
    const result = chatFilters.setCustomRange(draftFrom, draftTo);
    if (!result?.ok) {
      setError(result?.error || "Couldn't apply that range.");
      return;
    }
    setError(null);
    setOpen(false);
  };

  return (
    <div className="relative">
      <div className="inline-flex items-center gap-1 rounded-full border border-border bg-surface-default pl-2 pr-1 py-0.5 text-xs">
        <CalendarRange size={12} className="text-text-muted" />
        <span className="text-text-secondary">Period:</span>
        <button
          type="button"
          onClick={() => (chatFilters.period === "custom" ? setOpen((o) => !o) : null)}
          className="inline-flex items-center gap-0.5 py-0.5 text-xs font-semibold text-primary hover:underline"
          title={chatFilters.period === "custom" ? "Change custom range" : "Current period"}
        >
          {currentLabel()}
          {chatFilters.period === "custom" ? <ChevronDown size={10} /> : null}
        </button>
        {/* The select is the control, but its selected option repeats the label above, so it
            is collapsed to a chevron and the native menu is opened through it. */}
        <span className="relative ml-0.5 inline-flex h-4 w-4 items-center justify-center">
          <ChevronDown size={12} className="pointer-events-none text-text-muted" aria-hidden />
        <select
          value={chatFilters.period}
          onChange={handleSelect}
          aria-label="Time period for answers"
          className="absolute inset-0 cursor-pointer opacity-0"
          title="Pick a date range"
        >
          {chatFilters.periods.map((p) => (
            <option key={p.id} value={p.id}>
              {p.label}
            </option>
          ))}
        </select>
        </span>
      </div>

      {open ? (
        <div
          ref={popoverRef}
          className="absolute left-1/2 top-full z-[60] mt-2 w-72 -translate-x-1/2 rounded-xl border border-border bg-surface-default p-3 shadow-xl"
        >
          <div className="mb-2 flex items-start justify-between">
            <p className="text-xs font-semibold text-text-primary">Custom range</p>
            <button
              type="button"
              onClick={() => setOpen(false)}
              aria-label="Close"
              className="rounded p-0.5 text-text-muted hover:bg-surface-secondary"
            >
              <X size={12} />
            </button>
          </div>
          <p className="mb-2 text-[11px] text-text-muted">Pick a start and end date. Range can't be longer than {MAX_CUSTOM_RANGE_DAYS} days.</p>
          <div className="grid grid-cols-2 gap-2">
            <label className="block">
              <span className="block text-[10px] font-medium uppercase text-text-muted">From</span>
              <input
                type="date"
                value={draftFrom}
                max={todayISO()}
                onChange={(e) => setDraftFrom(e.target.value)}
                className="mt-1 w-full rounded border border-border bg-surface-default px-2 py-1 text-xs text-text-primary focus:border-primary focus:outline-none"
              />
            </label>
            <label className="block">
              <span className="block text-[10px] font-medium uppercase text-text-muted">To</span>
              <input
                type="date"
                value={draftTo}
                max={todayISO()}
                onChange={(e) => setDraftTo(e.target.value)}
                className="mt-1 w-full rounded border border-border bg-surface-default px-2 py-1 text-xs text-text-primary focus:border-primary focus:outline-none"
              />
            </label>
          </div>
          {error ? (
            <p className="mt-2 rounded bg-status-warning/10 px-2 py-1 text-[11px] text-status-warning">
              {error}
            </p>
          ) : null}
          <div className="mt-3 flex justify-end gap-2">
            <button
              type="button"
              onClick={() => setOpen(false)}
              className="rounded border border-border px-2 py-1 text-xs text-text-secondary hover:bg-surface-secondary"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={commitCustom}
              className="rounded bg-primary px-3 py-1 text-xs font-medium text-white hover:bg-primary/90"
            >
              Apply
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
