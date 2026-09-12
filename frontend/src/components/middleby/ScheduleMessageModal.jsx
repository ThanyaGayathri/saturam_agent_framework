// "Run this question on a schedule" modal. Client-only simulation for demo  - 
// shows the shape of scheduled reporting (cadence, delivery destination) so
// stakeholders can see how it would work in production.

import { useState } from "react";
import { X, Calendar, Mail, Bell, MessageSquare } from "lucide-react";
import { CADENCES, DELIVERY_CHANNELS } from "../../storage/scheduled-runs";

const DAYS = [
  { value: 1, label: "Monday" },
  { value: 2, label: "Tuesday" },
  { value: 3, label: "Wednesday" },
  { value: 4, label: "Thursday" },
  { value: 5, label: "Friday" },
];

const HOURS = [7, 8, 9, 10, 11, 13, 15, 17];

function hourLabel(h) {
  if (h === 12) return "12 PM";
  if (h === 0) return "12 AM";
  if (h < 12) return `${h} AM`;
  return `${h - 12} PM`;
}

export default function ScheduleMessageModal({ open, promptText, onClose, onSchedule }) {
  const [cadence, setCadence] = useState(CADENCES.WEEKLY);
  const [dayOfWeek, setDayOfWeek] = useState(1);
  const [hourOfDay, setHourOfDay] = useState(9);
  const [delivery, setDelivery] = useState(DELIVERY_CHANNELS.IN_APP);
  const [recipient, setRecipient] = useState("");
  const [label, setLabel] = useState("");

  if (!open) return null;

  const handleSubmit = (e) => {
    e.preventDefault();
    onSchedule?.({
      prompt: promptText,
      label,
      cadence,
      dayOfWeek,
      hourOfDay,
      delivery,
      recipient,
    });
    // reset for next open
    setLabel("");
    setRecipient("");
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" role="dialog" aria-modal>
      <button
        type="button"
        onClick={onClose}
        aria-label="Close"
        className="absolute inset-0 bg-black/40"
      />
      <form
        onSubmit={handleSubmit}
        className="relative z-10 w-full max-w-lg rounded-2xl border border-border bg-surface-default shadow-2xl"
      >
        <header className="flex items-start justify-between border-b border-border px-4 py-3">
          <div>
            <h3 className="flex items-center gap-2 text-sm font-semibold text-text-primary">
              <Calendar size={16} />
              Send this to me on a schedule
            </h3>
            <p className="mt-0.5 text-xs text-text-secondary">
              Pick a cadence and where it should land. In production this hooks up to email and Slack;
              in this demo we save the schedule locally so you can see what the workflow looks like.
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

        <div className="space-y-4 p-4">
          <div className="rounded-lg border border-border bg-surface-secondary/40 p-3">
            <p className="text-[11px] font-medium uppercase tracking-wide text-text-muted">The question</p>
            <p className="mt-1 text-sm text-text-primary">{promptText || "(no question)"}</p>
          </div>

          <div>
            <label className="block text-xs font-medium text-text-secondary">Give it a name (optional)</label>
            <input
              type="text"
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              placeholder="Weekly RFQ funnel check-in"
              className="mt-1 w-full rounded-lg border border-border bg-surface-default px-3 py-2 text-sm text-text-primary focus:border-primary focus:outline-none"
            />
          </div>

          <div>
            <p className="text-xs font-medium text-text-secondary">How often</p>
            <div className="mt-1 flex gap-2">
              {Object.values(CADENCES).map((c) => (
                <button
                  key={c}
                  type="button"
                  onClick={() => setCadence(c)}
                  className={`rounded-full border px-3 py-1 text-xs font-medium transition ${
                    cadence === c
                      ? "border-primary bg-primary text-white"
                      : "border-border bg-surface-default text-text-secondary hover:border-primary/40"
                  }`}
                >
                  {c === CADENCES.DAILY ? "Every day" : c === CADENCES.WEEKLY ? "Every week" : "Every month"}
                </button>
              ))}
            </div>
          </div>

          {cadence === CADENCES.WEEKLY ? (
            <div>
              <label className="block text-xs font-medium text-text-secondary">Which day</label>
              <select
                value={dayOfWeek}
                onChange={(e) => setDayOfWeek(Number(e.target.value))}
                className="mt-1 w-full rounded-lg border border-border bg-surface-default px-3 py-2 text-sm text-text-primary focus:border-primary focus:outline-none"
              >
                {DAYS.map((d) => (
                  <option key={d.value} value={d.value}>{d.label}</option>
                ))}
              </select>
            </div>
          ) : null}

          <div>
            <label className="block text-xs font-medium text-text-secondary">What time</label>
            <select
              value={hourOfDay}
              onChange={(e) => setHourOfDay(Number(e.target.value))}
              className="mt-1 w-full rounded-lg border border-border bg-surface-default px-3 py-2 text-sm text-text-primary focus:border-primary focus:outline-none"
            >
              {HOURS.map((h) => (
                <option key={h} value={h}>{hourLabel(h)}</option>
              ))}
            </select>
          </div>

          <div>
            <p className="text-xs font-medium text-text-secondary">Where to send it</p>
            <div className="mt-1 grid grid-cols-3 gap-2">
              <button
                type="button"
                onClick={() => setDelivery(DELIVERY_CHANNELS.IN_APP)}
                className={`flex flex-col items-center gap-1 rounded-lg border p-3 text-xs font-medium transition ${
                  delivery === DELIVERY_CHANNELS.IN_APP
                    ? "border-primary bg-primary-light/40 text-primary"
                    : "border-border bg-surface-default text-text-secondary hover:border-primary/40"
                }`}
              >
                <Bell size={14} />
                Show in Enterprise GPT
              </button>
              <button
                type="button"
                onClick={() => setDelivery(DELIVERY_CHANNELS.EMAIL)}
                className={`flex flex-col items-center gap-1 rounded-lg border p-3 text-xs font-medium transition ${
                  delivery === DELIVERY_CHANNELS.EMAIL
                    ? "border-primary bg-primary-light/40 text-primary"
                    : "border-border bg-surface-default text-text-secondary hover:border-primary/40"
                }`}
              >
                <Mail size={14} />
                Email
              </button>
              <button
                type="button"
                onClick={() => setDelivery(DELIVERY_CHANNELS.SLACK)}
                className={`flex flex-col items-center gap-1 rounded-lg border p-3 text-xs font-medium transition ${
                  delivery === DELIVERY_CHANNELS.SLACK
                    ? "border-primary bg-primary-light/40 text-primary"
                    : "border-border bg-surface-default text-text-secondary hover:border-primary/40"
                }`}
              >
                <MessageSquare size={14} />
                Slack
              </button>
            </div>
            {delivery !== DELIVERY_CHANNELS.IN_APP ? (
              <input
                type="text"
                value={recipient}
                onChange={(e) => setRecipient(e.target.value)}
                placeholder={delivery === DELIVERY_CHANNELS.EMAIL ? "name@middleby.com" : "#channel-name"}
                className="mt-2 w-full rounded-lg border border-border bg-surface-default px-3 py-2 text-sm text-text-primary focus:border-primary focus:outline-none"
              />
            ) : null}
          </div>
        </div>

        <footer className="flex items-center justify-end gap-2 border-t border-border px-4 py-3">
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg border border-border px-3 py-1.5 text-xs font-medium text-text-secondary transition hover:bg-surface-secondary"
          >
            Cancel
          </button>
          <button
            type="submit"
            className="rounded-lg bg-primary px-3 py-1.5 text-xs font-medium text-white transition hover:bg-primary/90"
          >
            Turn on schedule
          </button>
        </footer>
      </form>
    </div>
  );
}
