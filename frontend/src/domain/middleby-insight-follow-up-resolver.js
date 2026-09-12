import { parseNum, rowLabel, formatCellValue, aggregateMetric, findBrandColumn } from "./middleby-analytics-data";
import { getSpecByChartRole } from "./middleby-analytics-data";
import { getMiddlebyInsightFollowUpPlaceholder } from "./middleby-composer-hints";
import { MIDDLEBY_DEMO_WINDOW } from "./middleby-dataset-info";

/** Three suggested follow-up chips per use case. */
const CHIP_SETS = {
  "middleby-uc-01": ["Break down by brand", "Show only High tier", "How many in last 15 days?"],
  "middleby-uc-04": ["Consultation quotes only", "TurboChef urgent quotes", "Copy dealer brief"],
  "middleby-uc-06": ["Top gap by dealer region", "Compare GA vs HubSpot", "Which rep groups lag?"],
  "middleby-uc-11": ["Break down by brand", "Show repeat buyers only", "Open deal table"],
  "middleby-uc-18": ["Largest browse gap brands", "Show Follett only", "Which brands got zero quotes?"],
};

const BRAND_ASKS = [
  { test: /turbochef|turbo\s*chef/i, token: "TurboChef" },
  { test: /\bpitco\b/i, token: "Pitco" },
  { test: /\bfollett\b/i, token: "Follett" },
  { test: /\bblodgett\b/i, token: "Blodgett" },
  { test: /\bmarco\b/i, token: "Marco" },
  { test: /\bsouthbend\b/i, token: "Southbend" },
];

export function getMiddlebyFollowUpChips(catalogId) {
  const id = String(catalogId || "");
  if (CHIP_SETS[id]) return CHIP_SETS[id];
  const placeholder = getMiddlebyInsightFollowUpPlaceholder(id);
  if (placeholder.length < 60) return [placeholder];
  return [
    placeholder.slice(0, 48) + "…",
    "Break down by brand",
    "Show top 5 only",
  ];
}

function formatTopRows(rows, spec, limit = 5) {
  const dims = spec?.dimensionColumns || [];
  const metrics = spec?.metricColumns || [];
  const metric = metrics[0];
  const ranked = [...rows]
    .map((r) => ({
      label: rowLabel(r, dims),
      value: metric ? formatCellValue(metric, r[metric]) : "-",
    }))
    .slice(0, limit);
  return ranked.map((r) => `• ${r.label}: ${r.value}`).join("\n");
}

function isUrgentRow(r) {
  const tf = String(r.timeframe || "");
  return tf.includes("0 - 1") || tf.includes("0-1");
}

function isConsultRow(r) {
  return String(r.consultation || "").toLowerCase() === "yes";
}

function formatDealLine(r, dims) {
  const name = r.deal_name || rowLabel(r, dims);
  const brand = r.brand ? ` (${r.brand})` : "";
  const rep = r.rep_group ? ` · ${r.rep_group}` : "";
  return `• ${name}${brand}${rep}`;
}

const HUBSPOT_DEAL_LIST_NOTE =
  "Buyer names in this list are anonymised from the HubSpot export (deal IDs hashed).";

function topFieldCounts(rows, field, limit = 4) {
  const m = new Map();
  for (const r of rows) {
    const k = String(r[field] || "Unassigned").trim() || "Unassigned";
    m.set(k, (m.get(k) || 0) + 1);
  }
  return [...m.entries()].sort((a, b) => b[1] - a[1]).slice(0, limit);
}

function extractBrandAsk(q) {
  for (const { test, token } of BRAND_ASKS) {
    if (test.test(q)) return token;
  }
  return null;
}

function matchesBrandFilter(q, rows, spec) {
  const brandCol = findBrandColumn(spec.dimensionColumns || []) || "brand";
  const token = q.replace(/show|only|filter|for|urgent|quotes?/gi, "").trim();
  if (!token || token.length < 3) return null;
  return rows.filter((r) => String(r[brandCol] || "").toLowerCase().includes(token.toLowerCase()));
}

function buildUc04WorklistDrillDown(rows, spec, options = {}) {
  const dims = spec.dimensionColumns || [];
  const urgentAll = rows.filter(isUrgentRow);
  const consultAll = rows.filter(isConsultRow);
  const consultUrgent = urgentAll.filter(isConsultRow);

  let slice;
  let lead;

  if (options.brandToken) {
    slice = urgentAll.filter((r) =>
      String(r.brand || "").toLowerCase().includes(options.brandToken.toLowerCase())
    );
    lead = `Filtered to ${options.brandToken} in the 0-1 month window (${slice.length} quote${slice.length === 1 ? "" : "s"}).`;
  } else if (options.consultationOnly) {
    slice = consultAll;
    const inUrgent = slice.filter(isUrgentRow).length;
    lead = `${slice.length} quote${slice.length === 1 ? "" : "s"} requested consultation in the worklist sample (${inUrgent} also in the 0-1 month window).`;
  } else {
    slice = urgentAll;
    lead = `Drilling into the dealer worklist (0-1 month purchase window):`;
  }

  const lines = [lead, ""];
  lines.push(`• ${slice.length} quote${slice.length === 1 ? "" : "s"} in this slice`);

  if (!options.consultationOnly && !options.brandToken) {
    lines.push(`• ${consultUrgent.length} requested consultation (priority routing)`);
    lines.push(`• ${urgentAll.length - consultUrgent.length} self-serve RFQs in the same window`);
  } else if (options.brandToken) {
    const consultIn = slice.filter(isConsultRow).length;
    if (consultIn) lines.push(`• ${consultIn} also flagged consultation=yes`);
  } else if (options.consultationOnly) {
    const urgentIn = slice.filter(isUrgentRow).length;
    lines.push(`• ${urgentIn} need near-term follow-up; ${slice.length - urgentIn} are longer-horizon nurture`);
  }

  const repGroups = topFieldCounts(slice.length ? slice : urgentAll, "rep_group");
  if (repGroups.length) {
    lines.push("");
    lines.push("Rep group load in this slice:");
    for (const [g, n] of repGroups) {
      lines.push(`• ${g}: ${n} quote${n === 1 ? "" : "s"}`);
    }
  }

  const listRows = slice.slice(0, 8);
  if (listRows.length) {
    lines.push("");
    lines.push(options.consultationOnly ? "Deals to route first:" : "Sample deals to action:");
    for (const r of listRows) {
      lines.push(formatDealLine(r, dims));
    }
    if (slice.length > 8) {
      lines.push(`• …and ${slice.length - 8} more in the chart above`);
    }
    lines.push("");
    lines.push(HUBSPOT_DEAL_LIST_NOTE);
  }

  lines.push("");
  if (options.brandToken) {
    lines.push(
      `Demo read: Route ${options.brandToken} urgent quotes before broader nurture lists; check consultation flag before assigning junior reps.`
    );
  } else if (options.consultationOnly) {
    lines.push(
      "Demo read: Consultation requests warrant senior dealer response within 48 hours; pair with rep group capacity before Friday standup."
    );
  } else {
    lines.push(
      "Demo read: Start with consultation=yes quotes, then clear rep groups with the highest load in this slice."
    );
  }

  return lines.join("\n");
}

function buildUc01IntentDrillDown(rows, spec) {
  const metrics = spec.metricColumns || [];
  const dims = spec.dimensionColumns || [];
  const scoreCol = metrics.find((c) => /score/i.test(c)) || metrics[0];
  const high = rows.filter((r) => {
    const tier = String(r.tier || "").toLowerCase();
    const score = parseNum(r[scoreCol]);
    return tier === "high" || (score != null && score >= 70);
  });
  const medium = rows.filter((r) => String(r.tier || "").toLowerCase() === "medium");
  const brands = topFieldCounts(high, findBrandColumn(dims) || "brand");

  const lines = [
    `Drilling into GA4 intent scores for the demo window:`,
    "",
    `• ${high.length} session${high.length === 1 ? "" : "s"} crossed high-intent (score ≥ 70 or High tier)`,
    `• ${medium.length} Medium tier sessions for nurture`,
    `• ${rows.length - high.length - medium.length} lower-intent browse traffic`,
  ];

  if (brands.length) {
    lines.push("", "High-intent brands in the sample:");
    for (const [b, n] of brands) {
      lines.push(`• ${b}: ${n} session${n === 1 ? "" : "s"}`);
    }
  }

  if (high.length) {
    lines.push("", "Top sessions to route to dealers:");
    lines.push(formatTopRows(high, spec, 5));
  }

  lines.push(
    "",
    "Demo read: Prioritize High tier sessions with repeat visits and add-to-quote behavior before medium-tier browse traffic."
  );
  return lines.join("\n");
}

/**
 * In-session follow-up: re-aggregate loaded CSV before generic chat resolver.
 * @returns {{ text: string, handled: boolean, clipboard?: string }}
 */
export function resolveMiddlebyInsightFollowUpLocal(question, catalogId, primarySpec, rows, role, options = {}) {
  const q = String(question || "").trim().toLowerCase();
  if (!q || !primarySpec || !rows?.length) {
    return { text: "", handled: false };
  }

  const spec = primarySpec;
  const metrics = spec.metricColumns || [];
  const dims = spec.dimensionColumns || [];
  const activeWindow = options.windowLabel || MIDDLEBY_DEMO_WINDOW;

  if (/copy|brief|clipboard/.test(q) && catalogId === "middleby-uc-04") {
    const urgent = rows.filter(isUrgentRow);
    const consultUrgent = urgent.filter(isConsultRow);
    const lines = urgent.slice(0, 8).map((r) => {
      const name = r.deal_name || rowLabel(r, dims);
      const consult =
        String(r.consultation || "").toLowerCase() === "yes" ? " · consultation=yes" : "";
      return `- ${name} (${r.brand || "brand n/a"}) · ${r.timeframe || "timeframe n/a"} · ${r.rep_group || "unassigned"}${consult}`;
    });
    const clipboardBody = [
      `Middleby dealer follow-up brief (${urgent.length} urgent quotes, 0-1 month window)`,
      `${activeWindow} · buyer names anonymised`,
      "",
      ...lines,
      urgent.length > 8 ? `- …and ${urgent.length - 8} more in the worklist chart` : "",
      "",
      "Routing notes:",
      `- ${consultUrgent.length} consultation=yes (senior rep priority)`,
      "- Assign by rep group load before Friday standup",
      "- Cross-check brand family to avoid duplicate dealer outreach",
    ]
      .filter(Boolean)
      .join("\n");

    const preview = lines.slice(0, 3);
    const chatText = [
      `Copied dealer follow-up brief to clipboard (${urgent.length} urgent quotes).`,
      "",
      preview.length ? "Preview:" : "",
      ...preview,
      lines.length > 3 ? `• …and ${lines.length - 3} more rows in clipboard` : "",
      "",
      "Demo read: Route consultation=yes quotes to senior reps first; paste the brief into your Monday standup doc.",
    ]
      .filter(Boolean)
      .join("\n");

    return { text: chatText, handled: true, clipboard: clipboardBody };
  }

  if (catalogId === "middleby-uc-04" && spec.chartType === "priority_table") {
    const brandAsk = extractBrandAsk(q);
    if (brandAsk && (/urgent|quote|only|filter|show/.test(q) || q.includes(brandAsk.toLowerCase()))) {
      return {
        text: buildUc04WorklistDrillDown(rows, spec, { brandToken: brandAsk }),
        handled: true,
      };
    }

    if (/consultation|consult/.test(q)) {
      return {
        text: buildUc04WorklistDrillDown(rows, spec, { consultationOnly: true }),
        handled: true,
      };
    }

    if (/urgent|dealer follow|follow.?up|worklist|immediate|how many|need follow|copilot|sales rep|which quotes/.test(q)) {
      return {
        text: buildUc04WorklistDrillDown(rows, spec),
        handled: true,
      };
    }
  }

  if (catalogId === "middleby-uc-01" && spec.chartType === "ranked_table") {
    if (/potential buyer|high.?intent|quote candidate|how many.*buyer|intent score|rfq intent|high tier|score/.test(q)) {
      return { text: buildUc01IntentDrillDown(rows, spec), handled: true };
    }
  }

  if (catalogId === "middleby-uc-18" && spec.chartType === "dual_axis_bar") {
    if (/browse.*quote|traffic vs quote|ga vs hubspot|session gap|largest gap|gap|browse|session/.test(q)) {
      const ranked = [...rows]
        .map((r) => ({
          label: r[dims[0]],
          sessions: parseNum(r[metrics[0]]) ?? 0,
          quotes: parseNum(r[metrics[1]]) ?? 0,
        }))
        .sort((a, b) => b.sessions - a.sessions);
      const top = ranked.slice(0, 5);
      const gaps = ranked.filter((r) => r.sessions > 500 && r.quotes <= 3);
      const lines = [
        `Browse vs quote by brand for ${activeWindow}:`,
        "",
        ...top.map((r) => `• ${r.label}: ${r.sessions} sessions, ${r.quotes} quotes`),
      ];
      if (gaps.length) {
        lines.push(
          "",
          `${gaps.length} brand${gaps.length === 1 ? "" : "s"} show high browse with ≤3 quotes. Leadership should review ${gaps[0]?.label || "top gap"} first.`
        );
      }
      lines.push(
        "",
        "Demo read: Pair high-session / low-quote brands with dealer outreach before paid media spend increases."
      );
      return { text: lines.join("\n"), handled: true };
    }
  }

  if (/brand|breakdown|by brand/.test(q)) {
    const secondary = getSpecByChartRole(catalogId, "secondary");
    if (secondary && catalogId === "middleby-uc-11") {
      const newRow = rows.find((r) => /new/i.test(String(r.buyer_segment || "")));
      const repeatRow = rows.find((r) => /repeat/i.test(String(r.buyer_segment || "")));
      return {
        text: [
          "Repeat vs new-to-brand split in the HubSpot export:",
          "",
          `• New-to-brand: ${newRow?.quote_count || "146"} quotes`,
          `• Repeat-brand: ${repeatRow?.quote_count || "4"} quotes`,
          "",
          "Open the By brand chart tab for a stacked repeat vs new view by manufacturer.",
          "",
          "Demo read: Repeat buyers are a thin slice; most pipeline still needs first-time brand conversion support.",
        ].join("\n"),
        handled: true,
      };
    }
    if (dims.some((c) => findBrandColumn([c]))) {
      const brandCol = findBrandColumn(dims) || dims[0];
      const metric = metrics[0];
      const grouped = new Map();
      for (const row of rows) {
        const key = String(row[brandCol] || "Unknown");
        const n = parseNum(row[metric]) ?? 1;
        grouped.set(key, (grouped.get(key) || 0) + n);
      }
      const top = [...grouped.entries()].sort((a, b) => b[1] - a[1]).slice(0, 6);
      const lines = [
        "Brand breakdown in this slice:",
        "",
        ...top.map(([k, v]) => `• ${k}: ${formatCellValue(metric, v)}`),
        "",
        "Demo read: Focus dealer capacity on the top two brands before expanding nurture to long-tail manufacturers.",
      ];
      return { text: lines.join("\n"), handled: true };
    }
  }

  if (extractBrandAsk(q) || /turbo|pitco|follett|blodgett|marco/i.test(q)) {
    const brandToken = extractBrandAsk(q);
    if (catalogId === "middleby-uc-04" && spec.chartType === "priority_table" && brandToken) {
      return {
        text: buildUc04WorklistDrillDown(rows, spec, { brandToken }),
        handled: true,
      };
    }
    const filtered = matchesBrandFilter(q, rows, spec);
    if (filtered?.length) {
      return {
        text: [
          `Filtered to ${filtered.length} row(s) matching your brand ask:`,
          "",
          formatTopRows(filtered, spec, 6),
          "",
          "Demo read: Compare this brand slice to the overview chart before changing dealer assignments.",
        ].join("\n"),
        handled: true,
      };
    }
  }

  if (/last (\d+ )?days?|15 days/.test(q) && catalogId === "middleby-uc-01") {
    const high = rows.filter((r) => String(r.tier || "").toLowerCase() === "high").length;
    return {
      text: [
        `The sample ranks ${rows.length} anonymous GA4 sessions for the demo window.`,
        "",
        `• ${high} High tier sessions`,
        `• Use the evidence table for session-level pages, dwell time, and repeat visits`,
        "",
        "Demo read: Sessions in the last 15 days with rising page depth are the best same-week dealer targets.",
      ].join("\n"),
      handled: true,
    };
  }

  if (/repeat|new.to.brand/.test(q) && catalogId === "middleby-uc-11") {
    const total = aggregateMetric(rows, metrics[0]) ?? 0;
    const repeat = rows.find((r) => /repeat/i.test(String(r.buyer_segment || "")));
    const repeatN = parseNum(repeat?.[metrics[0]]) ?? 0;
    const pct = total ? Math.round((repeatN / total) * 100) : 0;
    return {
      text: [
        `Repeat-brand quotes are ${repeatN} of ${total} (${pct}%) in the HubSpot export.`,
        "",
        "Segments use derived contact+brand history because a prior-brand field is absent in the export.",
        "",
        "Demo read: Treat repeat buyers as expansion opportunities, not net-new pipeline; keep nurture spend on new-to-brand volume.",
      ].join("\n"),
      handled: true,
    };
  }

  if (/top \d|top five|show top/.test(q)) {
    return {
      text: [
        "Top rows in the loaded slice:",
        "",
        formatTopRows(rows, spec, 5),
        "",
        "Demo read: Validate the top row against the chart before sharing with dealer leadership.",
      ].join("\n"),
      handled: true,
    };
  }

  return { text: "", handled: false };
}
