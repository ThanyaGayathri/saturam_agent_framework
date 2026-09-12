// Persisted list of "run this question on a schedule" entries. Client-only for
// the demo — the demo shows the shape of scheduled reporting without needing a
// real backend cron.

import { readLocalJson, writeLocalJson } from "./local-json";
import { normalizeDemoProfileId, DEFAULT_DEMO_PROFILE_ID } from "../domain/demo-profiles";

const BASE_KEY = "retailgpt:scheduled-runs:v1";

function keyForProfile(profileId) {
  const id = normalizeDemoProfileId(profileId);
  return id === DEFAULT_DEMO_PROFILE_ID ? BASE_KEY : `${BASE_KEY}:profile:${id}`;
}

function makeId() {
  return `sr-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

export const CADENCES = Object.freeze({
  DAILY: "daily",
  WEEKLY: "weekly",
  MONTHLY: "monthly",
});

export const DELIVERY_CHANNELS = Object.freeze({
  IN_APP: "in-app",
  EMAIL: "email",
  SLACK: "slack",
});

export function readScheduledRuns(profileId) {
  const raw = readLocalJson(keyForProfile(profileId), []);
  if (!Array.isArray(raw)) return [];
  return raw.filter((r) => r && r.id && r.prompt);
}

export function writeScheduledRuns(profileId, runs) {
  writeLocalJson(keyForProfile(profileId), runs || []);
}

export function addScheduledRun(profileId, { prompt, cadence, dayOfWeek, hourOfDay, delivery, recipient, label }) {
  const clean = String(prompt || "").trim();
  if (!clean) return null;
  const entry = {
    id: makeId(),
    prompt: clean,
    label: (label || "").trim(),
    cadence: cadence || CADENCES.WEEKLY,
    dayOfWeek: dayOfWeek ?? 1,
    hourOfDay: hourOfDay ?? 9,
    delivery: delivery || DELIVERY_CHANNELS.IN_APP,
    recipient: (recipient || "").trim(),
    createdAt: Date.now(),
    paused: false,
    lastRunAt: null,
    nextRunAt: null,
  };
  const current = readScheduledRuns(profileId);
  writeScheduledRuns(profileId, [entry, ...current]);
  return entry;
}

export function removeScheduledRun(profileId, id) {
  writeScheduledRuns(
    profileId,
    readScheduledRuns(profileId).filter((r) => r.id !== id)
  );
}

export function pauseScheduledRun(profileId, id, paused) {
  writeScheduledRuns(
    profileId,
    readScheduledRuns(profileId).map((r) => (r.id === id ? { ...r, paused: !!paused } : r))
  );
}

export function describeSchedule(entry) {
  if (!entry) return "";
  const days = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
  const hour = entry.hourOfDay || 9;
  const hourLabel = hour === 12 ? "12 PM" : hour === 0 ? "12 AM" : hour < 12 ? `${hour} AM` : `${hour - 12} PM`;

  if (entry.cadence === CADENCES.DAILY) return `Every day at ${hourLabel}`;
  if (entry.cadence === CADENCES.MONTHLY) return `Monthly at ${hourLabel}`;
  return `Every ${days[entry.dayOfWeek ?? 1]} at ${hourLabel}`;
}

export function describeDelivery(entry) {
  if (!entry) return "";
  if (entry.delivery === DELIVERY_CHANNELS.EMAIL) return entry.recipient ? `Email ${entry.recipient}` : "Email";
  if (entry.delivery === DELIVERY_CHANNELS.SLACK) return entry.recipient ? `Slack ${entry.recipient}` : "Slack";
  return "Show in Enterprise GPT";
}
