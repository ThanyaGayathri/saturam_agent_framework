// Persisted date/period filter for the Middleby chat. Applies to any hero use
// case answer so the AI's intro copy and data window match what the user picked.
// Supports the common presets plus a custom From→To range (capped at 2 years).

import { readLocalJson, writeLocalJson } from "./local-json";
import { normalizeDemoProfileId, DEFAULT_DEMO_PROFILE_ID } from "../domain/demo-profiles";
import { formatMiddlebyPeriodWindow, resolveMiddlebyPeriodSpan } from "../domain/middleby-dataset-info";

const BASE_KEY = "retailgpt:chat-filters:v1";

export const MAX_CUSTOM_RANGE_DAYS = 730; // 2 years

const PERIOD_IDS = ["week", "month", "quarter", "year", "two-years", "custom"];

/** Period options with concrete date labels and real calendar day counts. */
export function getPeriodOptions() {
  return PERIOD_IDS.map((id) => {
    if (id === "custom") return { id, days: null, label: "Custom range…" };
    const span = resolveMiddlebyPeriodSpan(id);
    return { id, days: span.days, label: span.label };
  });
}

/** @deprecated Prefer getPeriodOptions() so labels stay date-concrete. */
export const PERIOD_OPTIONS = getPeriodOptions();

export const DEFAULT_PERIOD = "week";

function keyForProfile(profileId) {
  const id = normalizeDemoProfileId(profileId);
  return id === DEFAULT_DEMO_PROFILE_ID ? BASE_KEY : `${BASE_KEY}:profile:${id}`;
}

function isValidDateString(s) {
  if (typeof s !== "string" || !s) return false;
  return !Number.isNaN(new Date(s).getTime());
}

export function readChatFilters(profileId) {
  const raw = readLocalJson(keyForProfile(profileId), { period: DEFAULT_PERIOD });
  const period = PERIOD_IDS.includes(raw?.period) ? raw.period : DEFAULT_PERIOD;
  return {
    period,
    fromDate: isValidDateString(raw?.fromDate) ? raw.fromDate : null,
    toDate: isValidDateString(raw?.toDate) ? raw.toDate : null,
  };
}

export function writeChatFilters(profileId, filters) {
  writeLocalJson(keyForProfile(profileId), {
    period: filters?.period || DEFAULT_PERIOD,
    fromDate: filters?.fromDate || null,
    toDate: filters?.toDate || null,
  });
}

export function periodLabel(periodId) {
  if (periodId === "custom") return "Custom range…";
  return formatMiddlebyPeriodWindow(periodId || DEFAULT_PERIOD);
}

/**
 * @returns {{ ok: boolean, error?: string, days?: number }}
 */
export function validateCustomRange(fromDate, toDate) {
  if (!isValidDateString(fromDate) || !isValidDateString(toDate)) {
    return { ok: false, error: "Pick both a From and a To date." };
  }
  const from = new Date(fromDate);
  const to = new Date(toDate);
  if (to < from) {
    return { ok: false, error: "The To date has to be on or after the From date." };
  }
  const days = Math.round((to - from) / (1000 * 60 * 60 * 24));
  if (days > MAX_CUSTOM_RANGE_DAYS) {
    return { ok: false, error: `Custom range is limited to ${MAX_CUSTOM_RANGE_DAYS} days.` };
  }
  return { ok: true, days };
}

export function describeCustomRange(fromDate, toDate) {
  return formatMiddlebyPeriodWindow("custom", fromDate, toDate);
}

export function describePresetRange(periodId) {
  if (!periodId || periodId === "custom") return null;
  return formatMiddlebyPeriodWindow(periodId);
}

// Concrete calendar label for the current filter (no relative wording).
// e.g. "Jul 3 to 9, 2026", "Jul 1 to 9, 2026", "Jul 1 to 9, 2026" (QTD).
export function displayFilterLabel(filters) {
  if (!filters) return formatMiddlebyPeriodWindow("week");
  if (filters.period === "custom") {
    return formatMiddlebyPeriodWindow("custom", filters.fromDate, filters.toDate);
  }
  return describePresetRange(filters.period) || periodLabel(filters.period);
}
