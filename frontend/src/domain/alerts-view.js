/**
 * Presentation helpers for alerts. Pure functions and static style maps,
 * shared by the bell dropdown, the /alerts page, and /monitor. Keeping the
 * visual language in one place stops the three surfaces from drifting.
 *
 * Severity colors: critical, warning, and info use `semanticStatus` tokens from
 * the active theme (`domain/themes.js` → `ThemeContext.applyTheme` → CSS vars).
 */
import { BUSINESS_AREAS as SEED_BUSINESS_AREAS } from "./business-areas";
import { getBusinessAreasSync } from "../storage/business-areas";
import { storageKeysForProfile } from "../storage/keys";
import { readActiveDemoProfileId } from "../storage/demo-profile-preference";

/** @typedef {"critical" | "warning" | "info"} AlertSeverity */
/** @typedef {"new" | "acknowledged" | "snoozed" | "resolved"} AlertStatus */

export const SEVERITY_META = {
  critical: {
    label: "Critical",
    dot: "bg-status-critical-dot",
    chipClass:
      "bg-status-critical-light text-status-critical ring-1 ring-status-critical-border dark:bg-status-critical/15 dark:ring-status-critical-border/55",
    rowAccent: "border-l-4 border-status-critical-accent",
    iconCls: "text-status-critical",
    railHeadingCls: "font-medium text-status-critical",
    icon: "AlertTriangle",
    order: 0,
  },
  warning: {
    label: "Warning",
    dot: "bg-status-warning-dot",
    chipClass:
      "bg-status-warning-light text-status-warning ring-1 ring-status-warning-border dark:bg-status-warning/15 dark:ring-status-warning-border/55",
    rowAccent: "border-l-4 border-status-warning-accent",
    iconCls: "text-status-warning",
    railHeadingCls: "font-medium text-status-warning",
    icon: "AlertCircle",
    order: 1,
  },
  info: {
    label: "Info",
    dot: "border border-[color:var(--theme-status-info-border,#93c5fd)] bg-[color:var(--theme-status-info-dot,#3b82f6)]",
    chipClass:
      "border border-transparent bg-[color:var(--theme-status-info-light,#eff6ff)] text-[color:var(--theme-status-info,#2563eb)] ring-1 ring-[color:var(--theme-status-info-border,#93c5fd)]",
    rowAccent:
      "border-l-4 border-[color:var(--theme-status-info-accent,#3b82f6)]",
    iconCls: "text-[color:var(--theme-status-info,#2563eb)]",
    railHeadingCls: "font-medium text-[color:var(--theme-status-info,#2563eb)]",
    icon: "Info",
    order: 2,
  },
};

export const STATUS_META = {
  new: {
    label: "New",
    chipClass:
      "bg-status-critical-light text-status-critical ring-1 ring-status-critical-border dark:bg-status-critical/15 dark:ring-status-critical-border/55",
    dot: "bg-status-critical-dot",
  },
  acknowledged: {
    label: "Acknowledged",
    chipClass:
      "bg-status-warning-light text-status-warning ring-1 ring-status-warning-border dark:bg-status-warning/15 dark:ring-status-warning-border/55",
    dot: "bg-status-warning-dot",
  },
  snoozed: {
    label: "Snoozed",
    chipClass:
      "bg-surface-tertiary text-text-tertiary ring-1 ring-border dark:bg-surface-secondary dark:ring-border-light",
    dot: "bg-text-muted",
  },
  resolved: {
    label: "Resolved",
    chipClass:
      "bg-status-success-light text-status-success ring-1 ring-status-success-border dark:bg-status-success/15 dark:ring-status-success-border/55",
    dot: "bg-status-success-dot",
  },
};

/**
 * Compact relative timestamp ("just now", "5 min ago", "2h ago", "3d ago").
 * Keeps the bell and inbox rows scanable without a full date library.
 */
export function relativeTimeFromNow(ts, nowMs = Date.now()) {
  if (!ts || typeof ts !== "number") return "";
  const diff = Math.max(0, nowMs - ts);
  const sec = Math.floor(diff / 1000);
  if (sec < 45) return "just now";
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min} min ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.floor(hr / 24);
  if (day < 7) return `${day}d ago`;
  const week = Math.floor(day / 7);
  if (week < 5) return `${week}w ago`;
  const mon = Math.floor(day / 30);
  return `${mon}mo ago`;
}

/**
 * KPI delta color on alerts: "bad" direction picks up critical / warning / info
 * text tokens from the theme; favourable movement uses success green.
 */
export function alertKpiDeltaTextClass(alert) {
  const d = alert.kpi?.delta;
  if (typeof d !== "string" || !d) return "";
  const isBad =
    (d.startsWith("+") && alert.severity !== "info") ||
    (d.startsWith("-") && alert.severity === "info");
  if (!isBad) return "text-status-success";
  if (alert.severity === "warning") return "text-status-warning";
  if (alert.severity === "info") return "text-[color:var(--theme-status-info,#2563eb)]";
  return "text-status-critical";
}

/** Sort alerts for inbox-style display: unresolved first, then critical→warning→info, then newest first. */
export function sortAlertsForDisplay(alerts) {
  const arr = Array.isArray(alerts) ? [...alerts] : [];
  arr.sort((a, b) => {
    const ar = a.status === "resolved" ? 1 : 0;
    const br = b.status === "resolved" ? 1 : 0;
    if (ar !== br) return ar - br;
    const as = SEVERITY_META[a.severity]?.order ?? 99;
    const bs = SEVERITY_META[b.severity]?.order ?? 99;
    if (as !== bs) return as - bs;
    return (b.createdAt || 0) - (a.createdAt || 0);
  });
  return arr;
}

export function getBusinessAreaLabel(id) {
  // Look up in the persisted business hierarchy first so user renames win,
  // then fall back to the seed const if the store is empty.
  try {
    const profileId = readActiveDemoProfileId();
    const key = storageKeysForProfile(profileId).BUSINESS_AREAS;
    const areas = getBusinessAreasSync(key, profileId);
    const ba = areas.find((b) => b.id === id);
    if (ba) return ba.label;
  } catch {
    /* fall through to seed */
  }
  const seed = SEED_BUSINESS_AREAS.find((b) => b.id === id);
  return seed ? seed.label : id ?? "";
}

const MIN = 60 * 1000;
const HOUR = 60 * MIN;

export const SNOOZE_OPTIONS = [
  { label: "1 hour", ms: 1 * HOUR },
  { label: "4 hours", ms: 4 * HOUR },
  { label: "Tomorrow morning", ms: 16 * HOUR },
];
