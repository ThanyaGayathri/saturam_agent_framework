// Compact list of scheduled runs. Each run can be paused, deleted, or fired
// immediately via "Send now"  -  which uses the real delivery adapters (mailto
// for email, Slack incoming webhook for Slack, or the in-app inbox). Shows
// last-delivered status so users can see it actually worked.

import { useEffect, useState } from "react";
import { X, Calendar, Pause, Play, Trash2, Send, Check, Loader2, Inbox } from "lucide-react";
import { describeSchedule, describeDelivery } from "../../storage/scheduled-runs";
import { deliverScheduledRun, readDeliveries } from "../../services/schedule-delivery";

function formatWhen(ts) {
  if (!ts) return null;
  const d = new Date(ts);
  return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

function InAppInbox({ deliveries }) {
  const inApp = deliveries.filter((d) => d.channel === "in-app");
  if (!inApp.length) return null;
  return (
    <div className="mb-3 rounded-xl border border-primary/25 bg-primary-light/25 p-3 dark:bg-primary/10">
      <p className="flex items-center gap-1.5 text-xs font-semibold text-primary">
        <Inbox size={12} />
        Your Enterprise GPT inbox
      </p>
      <ul className="mt-2 space-y-1">
        {inApp.slice(0, 5).map((d) => (
          <li key={d.id} className="rounded bg-surface-default p-2 text-xs">
            <p className="font-medium text-text-primary">{d.subject || "Scheduled answer"}</p>
            <p className="mt-0.5 text-[11px] text-text-muted">Delivered {formatWhen(d.sentAt)}</p>
            {d.preview ? (
              <p className="mt-1 line-clamp-2 text-[11px] text-text-secondary">{d.preview}</p>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
}

export default function ScheduledRunsPanel({ open, onClose, runs, onPause, onRemove, profileId }) {
  const [deliveries, setDeliveries] = useState([]);
  const [busy, setBusy] = useState(null);
  const [flash, setFlash] = useState(null);

  useEffect(() => {
    if (open) setDeliveries(readDeliveries(profileId));
  }, [open, profileId]);

  const handleSendNow = async (run) => {
    setBusy(run.id);
    setFlash(null);
    try {
      const bodyText = `Answer to: ${run.prompt}\n\n(A live run would attach the current answer body.)`;
      const result = await deliverScheduledRun(profileId, run, bodyText);
      setDeliveries(readDeliveries(profileId));
      setFlash({ runId: run.id, ok: result.ok, note: result.note });
      setTimeout(() => setFlash(null), 3500);
    } finally {
      setBusy(null);
    }
  };

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-40" role="dialog" aria-label="Your scheduled questions">
      <button
        type="button"
        onClick={onClose}
        aria-label="Close"
        className="absolute inset-0 bg-black/30"
      />
      <aside className="absolute right-0 top-0 flex h-full w-full max-w-md flex-col bg-surface-default shadow-2xl">
        <header className="flex items-center justify-between border-b border-border px-4 py-3">
          <div>
            <h3 className="flex items-center gap-2 text-sm font-semibold text-text-primary">
              <Calendar size={16} />
              Your scheduled answers
            </h3>
            <p className="text-xs text-text-muted">
              Questions that run on a cadence. Click <strong>Send now</strong> on any to trigger delivery immediately.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="rounded p-1.5 text-text-muted transition hover:bg-surface-secondary"
          >
            <X size={16} />
          </button>
        </header>

        <div className="flex-1 overflow-y-auto p-4">
          <InAppInbox deliveries={deliveries} />
          {!runs?.length ? (
            <div className="rounded-lg border border-dashed border-border bg-surface-secondary/50 p-6 text-center">
              <p className="text-sm font-medium text-text-primary">Nothing scheduled yet</p>
              <p className="mt-1 text-xs text-text-secondary">
                In any chat answer, click the calendar icon to schedule a question  -  pick email, Slack, or in-app delivery.
              </p>
            </div>
          ) : (
            <ul className="space-y-2">
              {runs.map((r) => {
                const lastDelivery = deliveries.find((d) => d.runId === r.id);
                return (
                  <li
                    key={r.id}
                    className={`rounded-xl border border-border bg-surface-default p-3 ${
                      r.paused ? "opacity-60" : ""
                    }`}
                  >
                    {r.label ? (
                      <p className="text-xs font-semibold text-text-primary">{r.label}</p>
                    ) : null}
                    <p className={`text-sm text-text-primary ${r.label ? "mt-0.5" : ""}`}>{r.prompt}</p>
                    <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px] text-text-secondary">
                      <span className="rounded-full bg-surface-secondary px-2 py-0.5">
                        {describeSchedule(r)}
                      </span>
                      <span className="rounded-full bg-surface-secondary px-2 py-0.5">
                        {describeDelivery(r)}
                      </span>
                      {r.paused ? (
                        <span className="rounded-full bg-status-warning/15 px-2 py-0.5 text-status-warning">
                          Paused
                        </span>
                      ) : null}
                      {lastDelivery ? (
                        <span className="rounded-full bg-status-success/15 px-2 py-0.5 text-status-success">
                          Last delivered {formatWhen(lastDelivery.sentAt)}
                        </span>
                      ) : null}
                    </div>
                    <div className="mt-2 flex items-center gap-1">
                      <button
                        type="button"
                        onClick={() => handleSendNow(r)}
                        disabled={busy === r.id || r.paused}
                        className="inline-flex items-center gap-1 rounded-full bg-primary px-3 py-0.5 text-[11px] font-medium text-white hover:bg-primary/90 disabled:opacity-50"
                        title="Deliver this now via the chosen channel"
                      >
                        {busy === r.id ? <Loader2 size={11} className="animate-spin" /> : <Send size={11} />}
                        Send now
                      </button>
                      <button
                        type="button"
                        onClick={() => onPause?.(r.id, !r.paused)}
                        className="inline-flex items-center gap-1 rounded-full border border-border px-2 py-0.5 text-[11px] font-medium text-text-secondary hover:border-primary/40 hover:text-primary"
                        title={r.paused ? "Turn back on" : "Pause"}
                      >
                        {r.paused ? <Play size={11} /> : <Pause size={11} />}
                        {r.paused ? "Resume" : "Pause"}
                      </button>
                      <button
                        type="button"
                        onClick={() => onRemove?.(r.id)}
                        aria-label="Delete"
                        title="Delete"
                        className="rounded p-1 text-text-muted hover:bg-status-critical/10 hover:text-status-critical"
                      >
                        <Trash2 size={11} />
                      </button>
                    </div>
                    {flash && flash.runId === r.id ? (
                      <p
                        className={`mt-2 rounded-md px-2 py-1 text-[11px] ${
                          flash.ok
                            ? "bg-status-success/10 text-status-success"
                            : "bg-status-warning/10 text-status-warning"
                        }`}
                      >
                        {flash.ok ? <Check size={11} className="mr-1 inline" /> : null}
                        {flash.note}
                      </p>
                    ) : null}
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </aside>
    </div>
  );
}
