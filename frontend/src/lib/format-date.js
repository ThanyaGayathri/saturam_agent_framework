/**
 * A short, locale-aware date+time label for a UI line -- "17 Sep, 01:08" (`includeYear: true`
 * -> "17 Sep 2026, 01:08"). Shared by `KpiDetailPanel.jsx`, `JourneyBlock.jsx` and
 * `ScheduledRunsPanel.jsx`, which all formatted this same shape independently before.
 *
 * Assumes `date` already parses to a valid `Date` -- callers own their own missing/invalid-value
 * fallback (`null`, `""`, a truncated ISO string), which differs enough across the three that
 * folding it in here would silently change behavior at one of them. `UseCaseLaunchSummary.jsx`'s
 * "Completed ..." line uses `dateStyle`/`timeStyle` instead -- a different, locale-full format,
 * not a fourth copy of this one -- so it isn't routed through here.
 */
export function formatShortDateTime(date, { includeYear = false, hourFormat = "2-digit" } = {}) {
  return date.toLocaleString(undefined, {
    day: "numeric",
    month: "short",
    ...(includeYear ? { year: "numeric" } : {}),
    hour: hourFormat,
    minute: "2-digit",
  });
}
