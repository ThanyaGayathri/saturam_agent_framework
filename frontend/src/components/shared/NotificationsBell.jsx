import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Bell, Check, ArrowRight } from "lucide-react";
import { useAlerts } from "../../hooks/useAlerts";
import {
  SEVERITY_META,
  getBusinessAreaLabel,
  relativeTimeFromNow,
  sortAlertsForDisplay,
} from "../../domain/alerts-view";

const DROPDOWN_PREVIEW_LIMIT = 6;

export default function NotificationsBell() {
  const navigate = useNavigate();
  const { alerts, unreadCount, criticalUnreadCount, markAllRead } = useAlerts();
  const [open, setOpen] = useState(false);
  const wrapperRef = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => {
      if (e.key === "Escape") setOpen(false);
    };
    const onClick = (e) => {
      if (!wrapperRef.current) return;
      if (!wrapperRef.current.contains(e.target)) setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("mousedown", onClick);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("mousedown", onClick);
    };
  }, [open]);

  const unresolvedSorted = sortAlertsForDisplay(
    alerts.filter((a) => a.status !== "resolved")
  );
  const preview = unresolvedSorted.slice(0, DROPDOWN_PREVIEW_LIMIT);
  const moreCount = Math.max(0, unresolvedSorted.length - preview.length);

  const badge = unreadCount > 0 ? (unreadCount > 9 ? "9+" : String(unreadCount)) : null;
  const badgeTone =
    criticalUnreadCount > 0 ? "bg-[#d92d20]" : "bg-[#dc6803]";

  const handleRowClick = (alertId) => {
    setOpen(false);
    navigate(`/alerts?alert=${encodeURIComponent(alertId)}`);
  };

  const handleViewAll = () => {
    setOpen(false);
    navigate("/alerts");
  };

  const handleMarkAllRead = () => {
    markAllRead();
  };

  return (
    <div ref={wrapperRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="relative rounded-lg p-2 text-text-secondary hover:bg-surface-secondary"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={unreadCount > 0 ? `Notifications, ${unreadCount} unread` : "Notifications"}
        title="Notifications"
      >
        <Bell className="h-5 w-5" strokeWidth={2} aria-hidden />
        {badge ? (
          <span
            className={`absolute -right-0.5 -top-0.5 inline-flex min-w-[18px] items-center justify-center rounded-full px-1 text-[10px] font-semibold leading-[18px] text-white ring-2 ring-white ${badgeTone}`}
            aria-hidden
          >
            {badge}
          </span>
        ) : null}
      </button>

      {open ? (
        <div
          role="menu"
          className="absolute right-0 top-full z-50 mt-2 w-[380px] overflow-hidden rounded-xl border border-border bg-surface shadow-[0_12px_24px_rgba(16,24,40,0.12)]"
        >
          <header className="flex items-center justify-between border-b border-border px-4 py-3">
            <div className="flex items-center gap-2">
              <h3 className="text-sm font-semibold text-text-primary">Notifications</h3>
              {unreadCount > 0 ? (
                <span className="inline-flex h-5 min-w-[20px] items-center justify-center rounded-full bg-[#fef3f2] px-1.5 text-[11px] font-semibold text-[#b42318]">
                  {unreadCount}
                </span>
              ) : null}
            </div>
            {unreadCount > 0 ? (
              <button
                type="button"
                onClick={handleMarkAllRead}
                className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-semibold text-text-tertiary hover:bg-surface-secondary"
              >
                <Check className="h-3.5 w-3.5" aria-hidden />
                Mark all read
              </button>
            ) : null}
          </header>

          <ul className="max-h-[360px] overflow-y-auto">
            {preview.length === 0 ? (
              <li className="px-4 py-10 text-center text-sm text-text-secondary">
                You are all caught up.
              </li>
            ) : (
              preview.map((a) => {
                const sev = SEVERITY_META[a.severity] || SEVERITY_META.info;
                return (
                  <li key={a.id}>
                    <button
                      type="button"
                      onClick={() => handleRowClick(a.id)}
                      className="flex w-full items-start gap-3 px-4 py-3 text-left transition-colors hover:bg-surface-secondary"
                    >
                      <span
                        className={`mt-1.5 inline-block h-2 w-2 shrink-0 rounded-full ${sev.dot}`}
                        aria-hidden
                      />
                      <span className="min-w-0 flex-1">
                        <span className="flex items-center gap-2">
                          <span
                            className={`inline-flex items-center rounded-full px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${sev.chipClass}`}
                          >
                            {sev.label}
                          </span>
                          {a.businessAreaId ? (
                            <span className="inline-flex items-center rounded-full bg-primary-light dark:bg-primary/20 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-primary">
                              {getBusinessAreaLabel(a.businessAreaId)}
                            </span>
                          ) : null}
                          {a.status === "new" ? (
                            <span className="inline-block h-1.5 w-1.5 rounded-full bg-[#d92d20]" aria-label="Unread" />
                          ) : null}
                        </span>
                        <span className="mt-1 block text-sm font-medium text-text-primary line-clamp-2">
                          {a.title}
                        </span>
                        <span className="mt-1 block text-xs text-text-muted">
                          {relativeTimeFromNow(a.createdAt)}
                        </span>
                      </span>
                    </button>
                  </li>
                );
              })
            )}
          </ul>

          <footer className="flex items-center justify-between border-t border-border bg-surface-secondary px-4 py-2.5">
            <span className="text-xs text-text-muted">
              {moreCount > 0 ? `${moreCount} more in inbox` : `${unresolvedSorted.length} open`}
            </span>
            <button
              type="button"
              onClick={handleViewAll}
              className="inline-flex items-center gap-1 text-xs font-semibold text-primary hover:text-[#53389e]"
            >
              View all alerts
              <ArrowRight className="h-3 w-3" strokeWidth={2.5} aria-hidden />
            </button>
          </footer>
        </div>
      ) : null}
    </div>
  );
}
