/** Canonical demo week end (all sources aligned to this week). */
export const MIDDLEBY_DEMO_WEEK_END = "2026-06-07";

const MONTH_SHORT = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function localTodayIso() {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function parseISODate(iso) {
  if (!iso || typeof iso !== "string") return null;
  const d = new Date(`${iso.slice(0, 10)}T12:00:00`);
  return Number.isNaN(d.getTime()) ? null : d;
}

function formatDayMonthYear(d) {
  return `${MONTH_SHORT[d.getMonth()]} ${d.getDate()}, ${d.getFullYear()}`;
}

function formatMonthYear(d) {
  return `${MONTH_SHORT[d.getMonth()]} ${d.getFullYear()}`;
}

function toIsoLocal(d) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function daysBetweenInclusive(from, to) {
  const ms = to.getTime() - from.getTime();
  return Math.max(1, Math.round(ms / 86400000) + 1);
}

function formatInclusiveRange(from, to) {
  if (from.getFullYear() === to.getFullYear() && from.getMonth() === to.getMonth()) {
    if (from.getDate() === to.getDate()) {
      return formatDayMonthYear(to);
    }
    return `${MONTH_SHORT[from.getMonth()]} ${from.getDate()} to ${to.getDate()}, ${to.getFullYear()}`;
  }
  if (from.getFullYear() === to.getFullYear()) {
    return `${MONTH_SHORT[from.getMonth()]} ${from.getDate()} to ${MONTH_SHORT[to.getMonth()]} ${to.getDate()}, ${to.getFullYear()}`;
  }
  return `${formatDayMonthYear(from)} to ${formatDayMonthYear(to)}`;
}

/**
 * Resolve the real calendar span for a period filter.
 * Presets use elapsed days to date (week trailing 7, month MTD, quarter QTD,
 * year YTD) so shorter ranges never outscale longer ones.
 * Base sample data is one week, so factor = days / 7.
 */
export function resolveMiddlebyPeriodSpan(periodId, fromDate, toDate) {
  const todayIso = localTodayIso();
  const end = parseISODate(toDate || todayIso) || parseISODate(todayIso);

  if (periodId === "custom" && fromDate && toDate) {
    const from = parseISODate(fromDate);
    const to = parseISODate(toDate);
    if (!from || !to || to < from) {
      return resolveMiddlebyPeriodSpan("week");
    }
    const days = daysBetweenInclusive(from, to);
    return {
      id: "custom",
      days,
      factor: days / 7,
      fromISO: toIsoLocal(from),
      toISO: toIsoLocal(to),
      label: formatInclusiveRange(from, to),
    };
  }

  if (periodId === "month") {
    const from = new Date(end.getFullYear(), end.getMonth(), 1);
    const days = daysBetweenInclusive(from, end);
    return {
      id: "month",
      days,
      factor: days / 7,
      fromISO: toIsoLocal(from),
      toISO: toIsoLocal(end),
      // Partial month: show the exact days so Jul 1 to 9 is obvious vs a 3-day custom.
      label:
        end.getDate() === 1
          ? formatMonthYear(end)
          : formatInclusiveRange(from, end),
    };
  }

  if (periodId === "quarter") {
    const qStartMonth = Math.floor(end.getMonth() / 3) * 3;
    const from = new Date(end.getFullYear(), qStartMonth, 1);
    const qEnd = new Date(end.getFullYear(), qStartMonth + 3, 0);
    const days = daysBetweenInclusive(from, end);
    const complete = end.getTime() >= qEnd.getTime();
    return {
      id: "quarter",
      days,
      factor: days / 7,
      fromISO: toIsoLocal(from),
      toISO: toIsoLocal(end),
      label: complete
        ? `${MONTH_SHORT[qStartMonth]} to ${MONTH_SHORT[qStartMonth + 2]} ${end.getFullYear()}`
        : formatInclusiveRange(from, end),
    };
  }

  if (periodId === "year") {
    const from = new Date(end.getFullYear(), 0, 1);
    const days = daysBetweenInclusive(from, end);
    const complete = end.getMonth() === 11 && end.getDate() >= 31;
    return {
      id: "year",
      days,
      factor: days / 7,
      fromISO: toIsoLocal(from),
      toISO: toIsoLocal(end),
      label: complete ? String(end.getFullYear()) : formatInclusiveRange(from, end),
    };
  }

  if (periodId === "two-years") {
    const from = new Date(end.getFullYear() - 2, end.getMonth(), end.getDate());
    const days = daysBetweenInclusive(from, end);
    return {
      id: "two-years",
      days,
      factor: days / 7,
      fromISO: toIsoLocal(from),
      toISO: toIsoLocal(end),
      label: `${formatMonthYear(from)} to ${formatMonthYear(end)}`,
    };
  }

  // Default: trailing 7 days ending today (or provided end).
  const from = new Date(end);
  from.setDate(from.getDate() - 6);
  const days = daysBetweenInclusive(from, end);
  return {
    id: "week",
    days,
    factor: days / 7,
    fromISO: toIsoLocal(from),
    toISO: toIsoLocal(end),
    label: formatInclusiveRange(from, end),
  };
}

/**
 * Human-readable period label for the selected filter.
 * Presets anchor to local today. Labels are concrete dates/months only.
 */
const WEEK_FALLBACK = "Jun 1 to 7, 2026";

export function formatMiddlebyPeriodWindow(periodId, fromDate, toDate) {
  return resolveMiddlebyPeriodSpan(periodId, fromDate, toDate).label || WEEK_FALLBACK;
}

/** Canonical demo sample window (data files). Not the period-filter chip label. */
export const MIDDLEBY_DEMO_WINDOW = `Week of ${formatMiddlebyPeriodWindow("week", null, MIDDLEBY_DEMO_WEEK_END)}`;

/** Shared demo window labels (Insights home + insight session honesty footer). */
export const MIDDLEBY_DEMO_WINDOW_LABELS = {
  ga4: "GA4 sample",
  hubspot: "HubSpot deals sample",
  logrocket: "LogRocket sample",
  shopCatalog: "Middleby shop catalog reference",
  crossSource: "GA4 + HubSpot + LogRocket for the selected window",
  illustrative: "Sample for the selected window",
  blended: "GA4 + HubSpot + LogRocket for the selected window",
};

/** One-line summary for the About dataset panel. */
export const MIDDLEBY_REAL_EXPORT_WINDOWS_LINE =
  "Combines GA4, HubSpot, and LogRocket for the selected time window.";

export const MIDDLEBY_ILLUSTRATIVE_WINDOWS_LINE =
  "Values are shaped like production data for the selected time window.";

/** How multi-source charts combine data (join vs side-by-side KPI panel). */
export const MIDDLEBY_CROSS_SOURCE_KIND = {
  "middleby-uc-18": "join",
  "middleby-uc-07": "summary",
  "middleby-uc-16": "summary",
  "middleby-uc-23": "summary",
  "middleby-ex-06": "summary",
};

export const MIDDLEBY_CROSS_SOURCE_KIND_META = {
  join: {
    pill: "Joined on brand",
    caption: "Joined on brand name for the selected week.",
    stripTitle: "Joined views",
    stripDescription:
      "One row per brand with GA sessions and HubSpot quote counts for the same week.",
  },
  summary: {
    pill: "KPI panel",
    caption: "KPI cards from each source for the same week.",
    stripTitle: "Cross-source summaries",
    stripDescription:
      "Headline metrics from GA4, HubSpot, and LogRocket for the same week, shown side by side.",
  },
};

export function getMiddlebyCrossSourceKind(catalogId) {
  return MIDDLEBY_CROSS_SOURCE_KIND[catalogId] || null;
}

export function getMiddlebyCrossSourceMeta(kind) {
  return kind ? MIDDLEBY_CROSS_SOURCE_KIND_META[kind] || null : null;
}

/**
 * Per-source sample periods for the Insights home trust panel.
 * @type {Array<{ id: string, source: string, period: string, examples: string, kind: string }>}
 */
export const MIDDLEBY_SOURCE_WINDOWS = [
  {
    id: "ga4",
    source: "GA4",
    period: MIDDLEBY_DEMO_WINDOW,
    examples: "Brand sessions, product views, funnel events",
    kind: "Analytics",
  },
  {
    id: "hubspot",
    source: "HubSpot",
    period: MIDDLEBY_DEMO_WINDOW,
    examples: "Dealer worklist, quote pipeline, buyer intent mix",
    kind: "CRM (anonymised)",
  },
  {
    id: "logrocket",
    source: "LogRocket",
    period: MIDDLEBY_DEMO_WINDOW,
    examples: "Session errors, funnel drop-offs, UX impact",
    kind: "Experience monitoring",
  },
  {
    id: "catalog",
    source: "Shop catalog",
    period: "Reference data",
    examples: "Brand directory, SKU lookup",
    kind: "Catalog reference",
  },
];

/** How cross-source charts join data (Insights home trust panel). */
export const MIDDLEBY_CROSS_SOURCE_EXPLANATION = {
  lead: "All sources use the same week.",
  detail:
    "GA4, HubSpot, and LogRocket are aligned to one shared week so the numbers compare like for like.",
  join:
    "Cross-source charts join rows by brand or funnel step within that same week.",
};

/** Static Middleby reference data counts for the Insights home trust panel. */
export const MIDDLEBY_DATASET_SUMMARY = {
  headline: "About this analytics sample",
  intro:
    "These insights combine GA4, HubSpot, and LogRocket for one shared week.",
  footnote:
    "Open a use case to see source and period on that chart.",
  stats: [
    { label: "Brands tracked", value: "53" },
    { label: "Sample SKUs", value: "139" },
    { label: "HubSpot quotes", value: "351" },
    { label: "Analytics use cases", value: "33" },
  ],
};
