/**
 * Sample outputs produced by Middleby automation playbooks A1..A7.
 *
 * Each entry maps a playbook id to:
 *   preparingMessage : line shown during the Prepare phase
 *   reviewSteps      : numbered list of what the AI prepared (Review phase)
 *   preflightChecks  : pass/warn/fail rows the human sees before approving
 *   channels         : inline previews of what will be delivered where
 *                      (Slack messages, email drafts, HubSpot task rows)
 *   reports          : downloadable / openable artifacts triggered from buttons
 *                      (PDF briefs, CSV exports)
 *
 * jsPDF and jspdf-autotable are dynamically imported inside build functions so
 * the main bundle stays lean; the libs only load when a viewer clicks a report.
 */

// ==========================================================================
// Shared helpers
// ==========================================================================

function csvEscape(cell) {
  const s = cell == null ? "" : String(cell);
  if (/[",\n\r]/.test(s)) return `"${s.replace(/"/g, '""')}"`;
  return s;
}

function rowsToCsv(headers, rows) {
  const headerLine = headers.map((h) => csvEscape(h.label)).join(",");
  const bodyLines = rows.map((r) => headers.map((h) => csvEscape(r[h.key])).join(","));
  return [headerLine, ...bodyLines].join("\r\n");
}

function csvBlob(headers, rows) {
  const csv = rowsToCsv(headers, rows);
  return new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8" });
}

/**
 * Shared branded PDF brief renderer. Keeps per-playbook builders declarative.
 *
 * @param {{
 *   eyebrow?: string,
 *   title: string,
 *   subtitle?: string,
 *   headline?: string,
 *   sections?: Array<
 *     | { type: "text", heading?: string, body: string | string[] }
 *     | { type: "table", heading?: string, head: string[], body: (string|number)[][], colWidths?: number[] }
 *     | { type: "kpis", heading?: string, items: Array<{ label: string, value: string, sub?: string }> }
 *     | { type: "bullets", heading?: string, items: string[] }
 *   >,
 *   footer?: string,
 * }} spec
 */
async function buildStandardPdfBrief(spec) {
  const [{ jsPDF }, { default: autoTable }] = await Promise.all([
    import("jspdf"),
    import("jspdf-autotable"),
  ]);
  const doc = new jsPDF({ unit: "pt", format: "a4" });
  const pageWidth = doc.internal.pageSize.getWidth();
  const pageHeight = doc.internal.pageSize.getHeight();
  const margin = 48;
  let y = margin;

  const ensureRoom = (need) => {
    if (y + need > pageHeight - margin) {
      doc.addPage();
      y = margin;
    }
  };

  doc.setFontSize(10);
  doc.setFont(undefined, "bold");
  doc.setTextColor(105, 65, 198);
  doc.text(spec.eyebrow || "ENTERPRISE GPT · MIDDLEBY", margin, y);
  y += 22;

  doc.setFontSize(22);
  doc.setTextColor(29, 41, 57);
  doc.text(spec.title, margin, y);
  y += 24;

  if (spec.subtitle) {
    doc.setFontSize(11);
    doc.setFont(undefined, "normal");
    doc.setTextColor(102, 112, 133);
    const subLines = doc.splitTextToSize(spec.subtitle, pageWidth - margin * 2);
    doc.text(subLines, margin, y);
    y += subLines.length * 14 + 14;
  }

  if (spec.headline) {
    doc.setFontSize(13);
    doc.setFont(undefined, "bold");
    doc.setTextColor(29, 41, 57);
    doc.text("Headline", margin, y);
    y += 16;
    doc.setFontSize(11);
    doc.setFont(undefined, "normal");
    doc.setTextColor(52, 64, 84);
    const lines = doc.splitTextToSize(spec.headline, pageWidth - margin * 2);
    ensureRoom(lines.length * 14 + 8);
    doc.text(lines, margin, y);
    y += lines.length * 14 + 18;
  }

  for (const section of spec.sections || []) {
    if (section.heading) {
      ensureRoom(30);
      doc.setFontSize(13);
      doc.setFont(undefined, "bold");
      doc.setTextColor(29, 41, 57);
      doc.text(section.heading, margin, y);
      y += 6;
    }

    if (section.type === "text") {
      const body = Array.isArray(section.body) ? section.body.join("\n") : section.body;
      doc.setFontSize(11);
      doc.setFont(undefined, "normal");
      doc.setTextColor(52, 64, 84);
      const lines = doc.splitTextToSize(body, pageWidth - margin * 2);
      ensureRoom(lines.length * 14 + 8);
      doc.text(lines, margin, y + 8);
      y += lines.length * 14 + 20;
    } else if (section.type === "bullets") {
      doc.setFontSize(11);
      doc.setFont(undefined, "normal");
      doc.setTextColor(52, 64, 84);
      for (const item of section.items) {
        const lines = doc.splitTextToSize(`•  ${item}`, pageWidth - margin * 2 - 12);
        ensureRoom(lines.length * 14 + 4);
        doc.text(lines, margin + 8, y + 12);
        y += lines.length * 14 + 4;
      }
      y += 12;
    } else if (section.type === "table") {
      autoTable(doc, {
        startY: y + 8,
        head: [section.head],
        body: section.body,
        margin: { left: margin, right: margin },
        styles: { fontSize: 9, cellPadding: 5 },
        headStyles: {
          fillColor: [242, 244, 247],
          textColor: [52, 64, 84],
          fontStyle: "bold",
        },
        columnStyles: section.colWidths
          ? Object.fromEntries(section.colWidths.map((w, i) => [i, { cellWidth: w }]))
          : undefined,
      });
      y = doc.lastAutoTable.finalY + 20;
    } else if (section.type === "kpis") {
      const cellW = (pageWidth - margin * 2) / Math.max(1, section.items.length);
      const boxH = 56;
      ensureRoom(boxH + 10);
      const startY = y + 4;
      section.items.forEach((kpi, i) => {
        const x = margin + i * cellW;
        doc.setDrawColor(228, 231, 236);
        doc.roundedRect(x + 4, startY, cellW - 8, boxH, 6, 6);
        doc.setFontSize(8);
        doc.setFont(undefined, "bold");
        doc.setTextColor(102, 112, 133);
        doc.text(kpi.label.toUpperCase(), x + 12, startY + 16);
        doc.setFontSize(16);
        doc.setFont(undefined, "bold");
        doc.setTextColor(29, 41, 57);
        doc.text(kpi.value, x + 12, startY + 36);
        if (kpi.sub) {
          doc.setFontSize(8);
          doc.setFont(undefined, "normal");
          doc.setTextColor(102, 112, 133);
          doc.text(kpi.sub, x + 12, startY + 50);
        }
      });
      y = startY + boxH + 18;
    }
  }

  if (spec.footer) {
    ensureRoom(20);
    doc.setFontSize(9);
    doc.setFont(undefined, "italic");
    doc.setTextColor(152, 162, 179);
    const lines = doc.splitTextToSize(spec.footer, pageWidth - margin * 2);
    doc.text(lines, margin, pageHeight - margin);
  }

  return doc.output("blob");
}

const STANDARD_FOOTER =
  "Generated by Enterprise GPT. Sample data. In production, artifacts include full source citations and version history.";

const WEEK_LABEL = `Week of ${new Date().toLocaleDateString(undefined, {
  month: "long",
  day: "numeric",
  year: "numeric",
})}`;

// ==========================================================================
// A1 - Weekly dealer worklist refresh
// ==========================================================================

const A1_WORKLIST_ROWS = [
  { deal: "MB-24817", buyer: "Highland Foods Group", brand: "TurboChef", rep: "Northeast dealer team", value: "$142,500", daysOpen: 26, consultation: "Yes", priority: 1, dueDate: "Jul 22" },
  { deal: "MB-24802", buyer: "Coastal Restaurant Partners", brand: "Blodgett", rep: "Southeast dealer team", value: "$88,300", daysOpen: 24, consultation: "Yes", priority: 2, dueDate: "Jul 22" },
  { deal: "MB-24793", buyer: "Metro Cafeterias LLC", brand: "Pitco", rep: "Central dealer team", value: "$61,200", daysOpen: 21, consultation: "Yes", priority: 3, dueDate: "Jul 23" },
  { deal: "MB-24778", buyer: "Sunbelt Hospitality", brand: "Southbend", rep: "Southeast dealer team", value: "$210,000", daysOpen: 19, consultation: "Yes", priority: 4, dueDate: "Jul 23" },
  { deal: "MB-24771", buyer: "Northland Convenience", brand: "TurboChef", rep: "Central dealer team", value: "$47,800", daysOpen: 18, consultation: "Yes", priority: 5, dueDate: "Jul 23" },
  { deal: "MB-24759", buyer: "Pacific Bakers Guild", brand: "Bakers Pride", rep: "West coast dealer team", value: "$56,400", daysOpen: 17, consultation: "Yes", priority: 6, dueDate: "Jul 24" },
  { deal: "MB-24744", buyer: "Riverside School District", brand: "Wells", rep: "Central dealer team", value: "$38,900", daysOpen: 16, consultation: "No", priority: 7, dueDate: "Jul 24" },
  { deal: "MB-24738", buyer: "Grand Plateau Resorts", brand: "Middleby Marshall", rep: "West coast dealer team", value: "$174,600", daysOpen: 15, consultation: "No", priority: 8, dueDate: "Jul 25" },
  { deal: "MB-24722", buyer: "Cornerstone Diners Co", brand: "Star", rep: "Northeast dealer team", value: "$29,750", daysOpen: 14, consultation: "No", priority: 9, dueDate: "Jul 25" },
  { deal: "MB-24717", buyer: "Southern Grill Partners", brand: "Southbend", rep: "Southeast dealer team", value: "$97,200", daysOpen: 13, consultation: "No", priority: 10, dueDate: "Jul 25" },
  { deal: "MB-24705", buyer: "Alpine Ski Resorts", brand: "Blodgett", rep: "West coast dealer team", value: "$52,300", daysOpen: 12, consultation: "No", priority: 11, dueDate: "Jul 26" },
  { deal: "MB-24699", buyer: "Twin Cities Grocers", brand: "Nu-Vu", rep: "Central dealer team", value: "$41,100", daysOpen: 11, consultation: "No", priority: 12, dueDate: "Jul 26" },
  { deal: "MB-24686", buyer: "Coastal Bistros", brand: "Jade Range", rep: "West coast dealer team", value: "$63,400", daysOpen: 10, consultation: "No", priority: 13, dueDate: "Jul 26" },
  { deal: "MB-24680", buyer: "Interstate Truck Stops", brand: "Toastmaster", rep: "Central dealer team", value: "$34,200", daysOpen: 9, consultation: "No", priority: 14, dueDate: "Jul 27" },
  { deal: "MB-24673", buyer: "Beacon Hospitality Group", brand: "TurboChef", rep: "Northeast dealer team", value: "$118,900", daysOpen: 8, consultation: "No", priority: 15, dueDate: "Jul 27" },
  { deal: "MB-24665", buyer: "Delta Regional Hospitals", brand: "Blodgett", rep: "Southeast dealer team", value: "$79,600", daysOpen: 7, consultation: "No", priority: 16, dueDate: "Jul 27" },
  { deal: "MB-24658", buyer: "Prairie Catering Co", brand: "Pitco", rep: "Central dealer team", value: "$45,300", daysOpen: 6, consultation: "No", priority: 17, dueDate: "Jul 28" },
  { deal: "MB-24651", buyer: "Harbor View Hotels", brand: "Middleby Marshall", rep: "West coast dealer team", value: "$91,700", daysOpen: 5, consultation: "No", priority: 18, dueDate: "Jul 28" },
];

const A1_TASK_PREVIEW = A1_WORKLIST_ROWS.slice(0, 6).map((r) => ({
  taskName: `Follow up on ${r.deal} (${r.brand})`,
  assignee: r.rep,
  dueDate: r.dueDate,
  priority: r.priority <= 6 ? "High" : "Normal",
  linkedDeal: r.deal,
}));

const A1_SLACK = {
  channel: "#dealer-ops-monday",
  author: "Enterprise GPT",
  timestamp: "Mon 7:02 AM",
  body: [
    "Monday follow-up worklist is ready.",
    "",
    "18 quotes prioritized for near-term outreach. 6 are flagged for consultation and routed to senior reps.",
    "",
    "• Top 3 by value: MB-24778 (Sunbelt Hospitality, $210K), MB-24738 (Grand Plateau Resorts, $175K), MB-24817 (Highland Foods Group, $143K)",
    "• Rep groups covered: Northeast, Southeast, Central, West coast",
    "• Target response lift vs prior Monday cadence: +12%",
  ].join("\n"),
  actionLabel: "Open worklist",
};

const A1_EMAIL = {
  from: "Enterprise GPT <no-reply@enterprisegpt.middleby.com>",
  to: "dealer-ops@middleby.com",
  cc: "sales-leadership@middleby.com",
  subject: `Weekly dealer follow-up brief · ${WEEK_LABEL}`,
  sentAt: "Mon 7:04 AM",
  body: [
    "Dealer ops team,",
    "",
    "Your Monday worklist for this week is attached. 18 quotes in the 0-1 month window are prioritized by consultation flag and rep group workload.",
    "",
    "Highlights:",
    "  • 6 consultation-flagged deals routed to senior reps",
    "  • $1.5M in aggregate pipeline value across the worklist",
    "  • 4 rep groups covered; Central carries the heaviest load (6 deals)",
    "",
    "The full brief includes rep-by-rep breakdowns and suggested talking points. HubSpot tasks are queued for creation once dealer ops approves the list.",
    "",
    "Enterprise GPT",
  ].join("\n"),
  attachment: "monday-dealer-follow-up-brief.pdf",
};

const A1_WORKLIST_CSV_HEADERS = [
  { key: "priority", label: "Priority" },
  { key: "deal", label: "Deal ID" },
  { key: "buyer", label: "Buyer" },
  { key: "brand", label: "Brand" },
  { key: "value", label: "Deal value" },
  { key: "rep", label: "Rep group" },
  { key: "daysOpen", label: "Days open" },
  { key: "consultation", label: "Consultation flag" },
  { key: "dueDate", label: "Follow-up due" },
];

function buildA1BriefPdf() {
  const groupLoad = A1_WORKLIST_ROWS.reduce((acc, r) => {
    acc[r.rep] = (acc[r.rep] || 0) + 1;
    return acc;
  }, {});
  const groupRows = Object.entries(groupLoad)
    .sort((a, b) => b[1] - a[1])
    .map(([g, c]) => [g, String(c), `${Math.round((c / A1_WORKLIST_ROWS.length) * 100)}%`]);
  const topRows = A1_WORKLIST_ROWS.slice(0, 6).map((r) => [
    String(r.priority), r.deal, r.buyer, r.brand, r.value, r.rep, r.consultation, r.dueDate,
  ]);
  return buildStandardPdfBrief({
    title: "Monday dealer follow-up brief",
    subtitle: `${WEEK_LABEL}  ·  Prepared for dealer ops`,
    headline:
      "18 open quotes in the 0-1 month window need near-term follow-up. 6 are consultation-flagged and have been routed to senior reps. Aggregate pipeline value in the worklist is $1.5M.",
    sections: [
      { type: "table", heading: "Top priorities this week",
        head: ["#", "Deal", "Buyer", "Brand", "Value", "Rep group", "Consult", "Due"], body: topRows },
      { type: "table", heading: "Rep group load",
        head: ["Rep group", "Deals", "Share"], body: groupRows },
    ],
    footer: STANDARD_FOOTER,
  });
}

// ==========================================================================
// A2 - Dealer SLA escalation
// ==========================================================================

const A2_BREACH_ROWS = [
  { deal: "MB-24589", buyer: "Sovereign Steakhouses", brand: "TurboChef", rep: "Northeast dealer team", value: "$185,000", daysOpen: 34, breach: 20, severity: "Critical" },
  { deal: "MB-24567", buyer: "Meridian Hotels & Resorts", brand: "Southbend", rep: "West coast dealer team", value: "$312,400", daysOpen: 32, breach: 18, severity: "Critical" },
  { deal: "MB-24544", buyer: "Golden Palate Restaurants", brand: "Blodgett", rep: "Central dealer team", value: "$76,300", daysOpen: 29, breach: 15, severity: "High" },
  { deal: "MB-24521", buyer: "Ridgeline Truck Stops", brand: "Pitco", rep: "Central dealer team", value: "$54,100", daysOpen: 27, breach: 13, severity: "High" },
  { deal: "MB-24487", buyer: "Silver Fork Group", brand: "Middleby Marshall", rep: "Southeast dealer team", value: "$128,700", daysOpen: 25, breach: 11, severity: "High" },
  { deal: "MB-24473", buyer: "Frontier Convenience", brand: "Wells", rep: "West coast dealer team", value: "$41,200", daysOpen: 24, breach: 10, severity: "Medium" },
  { deal: "MB-24461", buyer: "Skyline University Foods", brand: "Nu-Vu", rep: "Central dealer team", value: "$88,900", daysOpen: 23, breach: 9, severity: "Medium" },
  { deal: "MB-24448", buyer: "Pinnacle Regional Care", brand: "Jade Range", rep: "Southeast dealer team", value: "$67,400", daysOpen: 22, breach: 8, severity: "Medium" },
  { deal: "MB-24432", buyer: "Coastal Convention Centers", brand: "TurboChef", rep: "Northeast dealer team", value: "$543,000", daysOpen: 20, breach: 6, severity: "Medium" },
  { deal: "MB-24418", buyer: "Northgate Restaurants", brand: "Bakers Pride", rep: "Central dealer team", value: "$32,600", daysOpen: 18, breach: 4, severity: "Low" },
  { deal: "MB-24403", buyer: "Highway Diners", brand: "Star", rep: "Southeast dealer team", value: "$28,900", daysOpen: 17, breach: 3, severity: "Low" },
  { deal: "MB-24389", buyer: "Prairie Grocery Chain", brand: "Toastmaster", rep: "West coast dealer team", value: "$59,700", daysOpen: 16, breach: 2, severity: "Low" },
];

const A2_SLACK = {
  channel: "#sales-leadership",
  author: "Enterprise GPT",
  timestamp: "Mon 8:11 AM",
  body: [
    "Overdue quote escalation for the week.",
    "",
    "12 deals past the 14-day SLA policy. 2 are Critical (>30 days open).",
    "",
    "• At-risk pipeline value: $1.72M",
    "• Largest exposure: MB-24432 (Coastal Convention Centers, $543K, +6 days over)",
    "• Rep groups on the escalation list: Central (4), West coast (3), Southeast (3), Northeast (2)",
  ].join("\n"),
  actionLabel: "Open escalation digest",
};

const A2_EMAIL = {
  from: "Enterprise GPT <no-reply@enterprisegpt.middleby.com>",
  to: "sales-leadership@middleby.com",
  cc: "dealer-ops@middleby.com",
  subject: `SLA escalation package · ${WEEK_LABEL}`,
  sentAt: "Mon 8:12 AM",
  body: [
    "Sales leadership,",
    "",
    "12 open quotes have crossed the 14-day follow-up SLA. Full escalation package attached.",
    "",
    "Summary:",
    "  • 2 Critical (over 30 days open): MB-24589 ($185K), MB-24567 ($312K)",
    "  • 3 High, 4 Medium, 3 Low severity",
    "  • 1 deal above the $500K senior-review threshold flagged for VP visibility",
    "",
    "Rep notifications are queued in HubSpot and held until leadership approves the recipient list.",
    "",
    "Enterprise GPT",
  ].join("\n"),
  attachment: "sla-escalation-package.pdf",
};

const A2_TASK_PREVIEW = A2_BREACH_ROWS.slice(0, 6).map((r) => ({
  taskName: `Escalate ${r.deal} - ${r.severity}`,
  assignee: r.rep,
  dueDate: "Today",
  priority: r.severity === "Critical" ? "High" : r.severity === "High" ? "High" : "Normal",
  linkedDeal: r.deal,
}));

const A2_CSV_HEADERS = [
  { key: "deal", label: "Deal ID" },
  { key: "buyer", label: "Buyer" },
  { key: "brand", label: "Brand" },
  { key: "value", label: "Deal value" },
  { key: "rep", label: "Rep group" },
  { key: "daysOpen", label: "Days open" },
  { key: "breach", label: "Days past SLA" },
  { key: "severity", label: "Severity" },
];

function buildA2EscalationPdf() {
  const rows = A2_BREACH_ROWS.slice(0, 8).map((r) => [
    r.deal, r.buyer, r.brand, r.value, r.rep, String(r.daysOpen), `+${r.breach}`, r.severity,
  ]);
  const bySeverity = A2_BREACH_ROWS.reduce((acc, r) => {
    acc[r.severity] = (acc[r.severity] || 0) + 1;
    return acc;
  }, {});
  const severityRows = ["Critical", "High", "Medium", "Low"]
    .filter((k) => bySeverity[k])
    .map((k) => [k, String(bySeverity[k])]);
  return buildStandardPdfBrief({
    title: "Overdue quote escalation package",
    subtitle: `${WEEK_LABEL}  ·  Prepared for sales leadership`,
    headline:
      "12 open quotes have crossed the 14-day follow-up SLA. Aggregate at-risk pipeline value is $1.72M. Two deals are Critical (over 30 days open) and one exceeds the $500K senior-review threshold.",
    sections: [
      { type: "kpis", heading: "Escalation summary", items: [
        { label: "Breaches", value: "12", sub: "Over 14-day SLA" },
        { label: "At-risk value", value: "$1.72M", sub: "Aggregate" },
        { label: "Critical", value: "2", sub: "Over 30 days" },
      ]},
      { type: "table", heading: "By severity", head: ["Severity", "Deals"], body: severityRows },
      { type: "table", heading: "Top breaches",
        head: ["Deal", "Buyer", "Brand", "Value", "Rep group", "Days open", "Past SLA", "Severity"],
        body: rows },
    ],
    footer: STANDARD_FOOTER,
  });
}

// ==========================================================================
// A3 - Friday executive briefing (no approval gate)
// ==========================================================================

const A3_SIGNALS = [
  { area: "Traffic", metric: "Site sessions", value: "42,318", change: "+12%", source: "GA", direction: "up" },
  { area: "Pipeline", metric: "New quotes (HubSpot)", value: "184", change: "-4%", source: "HubSpot", direction: "down" },
  { area: "UX", metric: "Bounce rate", value: "38.4%", change: "+1.7pt", source: "GA", direction: "down" },
  { area: "Conversion", metric: "Homepage to RFQ", value: "1.9%", change: "-0.3pt", source: "GA", direction: "down" },
  { area: "Ops", metric: "Quote SLA compliance", value: "96%", change: "+2pt", source: "HubSpot", direction: "up" },
  { area: "UX", metric: "Post-deploy error rate", value: "0.42%", change: "+0.18pt", source: "LogRocket", direction: "down" },
  { area: "Demand", metric: "Widest brand gap", value: "Globe 745:1", change: "New", source: "GA · HubSpot", direction: "flat" },
  { area: "Dealer", metric: "Consultation queue", value: "28", change: "+6", source: "HubSpot", direction: "flat" },
];

const A3_SLACK = {
  channel: "#exec-briefing",
  author: "Enterprise GPT",
  timestamp: "Fri 7:02 AM",
  body: [
    "Friday executive briefing is live.",
    "",
    "This week: traffic up +12% but quote volume down -4%. Post-deploy error rate is up +0.18pt (RequireJS spike, tracked in A7).",
    "",
    "Top three signals worth a leadership conversation:",
    "  1. Widening brand gap on Globe (2,236 sessions, 3 quotes)",
    "  2. Post-release UX regression (see A7 alert)",
    "  3. Consultation queue up +6 to 28",
  ].join("\n"),
  actionLabel: "Read full brief",
};

const A3_EMAIL = {
  from: "Enterprise GPT <no-reply@enterprisegpt.middleby.com>",
  to: "leadership@middleby.com",
  cc: "",
  subject: `Friday executive briefing · ${WEEK_LABEL}`,
  sentAt: "Fri 7:00 AM",
  body: [
    "Leadership team,",
    "",
    "This week's briefing is attached. 8 signals across traffic, pipeline, UX, ops, demand, and dealer motion.",
    "",
    "Highlights:",
    "  • Traffic +12% WoW; quote volume -4% WoW - conversion is the story to explore",
    "  • Post-deploy error rate up (see the A7 regression alert for detail)",
    "  • Globe brand shows the widest browse-to-quote gap in the sample",
    "",
    "Full monitor screenshots and source citations are embedded in the PDF.",
    "",
    "Enterprise GPT",
  ].join("\n"),
  attachment: "friday-executive-briefing.pdf",
};

const A3_CSV_HEADERS = [
  { key: "area", label: "Area" },
  { key: "metric", label: "Metric" },
  { key: "value", label: "This week" },
  { key: "change", label: "Change WoW" },
  { key: "source", label: "Source" },
];

function buildA3BriefingPdf() {
  return buildStandardPdfBrief({
    title: "Friday executive briefing",
    subtitle: `${WEEK_LABEL}  ·  Prepared for leadership`,
    headline:
      "This week: 8 tracked signals across traffic, pipeline, UX, and demand. Traffic and SLA compliance are trending up. Conversion and post-deploy UX are trending down. Widest browse-to-quote gap continues to sit on the Globe brand.",
    sections: [
      { type: "table", heading: "Signals this week",
        head: ["Area", "Metric", "This week", "Change WoW", "Source"],
        body: A3_SIGNALS.map((s) => [s.area, s.metric, s.value, s.change, s.source]) },
      { type: "bullets", heading: "Worth a leadership conversation",
        items: [
          "Traffic +12% WoW but quotes -4% WoW. The gap is a conversion story, not a demand story. See section 2.",
          "Post-release UX regression (A7 alert live). Commerce team is triaging the RequireJS spike; recovery ETA is 24-48h.",
          "Globe brand ratio of 745 sessions per quote is the widest in the sample. Marketing nudge (A4) is queued.",
        ]},
    ],
    footer: STANDARD_FOOTER,
  });
}

// ==========================================================================
// A4 - Brand gap nudge to marketing
// ==========================================================================

const A4_GAP_ROWS = [
  { brand: "Globe", sessions: 2236, quotes: 3, ratio: "745:1", rank: 1 },
  { brand: "Aspire", sessions: 1432, quotes: 8, ratio: "179:1", rank: 2 },
  { brand: "Anets", sessions: 987, quotes: 6, ratio: "165:1", rank: 3 },
  { brand: "Jade Range", sessions: 812, quotes: 6, ratio: "135:1", rank: 4 },
  { brand: "Toastmaster", sessions: 743, quotes: 7, ratio: "106:1", rank: 5 },
  { brand: "Nu-Vu", sessions: 921, quotes: 12, ratio: "77:1", rank: 6 },
];

const A4_LANDING_PAGES = [
  { path: "/brands/globe/glidegrill-series", sessions: 743, quotes: 0 },
  { path: "/brands/globe/product-catalog", sessions: 612, quotes: 1 },
  { path: "/brands/globe/tabletop-solutions", sessions: 398, quotes: 0 },
  { path: "/brands/globe/dealer-locator", sessions: 287, quotes: 1 },
  { path: "/brands/globe/spec-sheets", sessions: 196, quotes: 1 },
];

const A4_ASANA_TASK = [
  { taskName: "Review Globe RFQ content and dealer handoff", assignee: "Marketing lead", dueDate: "Jul 29", priority: "High", linkedDeal: "-" },
  { taskName: "Audit /brands/globe/glidegrill-series landing", assignee: "Content team", dueDate: "Jul 29", priority: "High", linkedDeal: "-" },
  { taskName: "Confirm dealer coverage for Globe accounts", assignee: "Dealer ops", dueDate: "Jul 30", priority: "Normal", linkedDeal: "-" },
];

const A4_SLACK = {
  channel: "#brand-marketing",
  author: "Enterprise GPT",
  timestamp: "Tue 9:14 AM",
  body: [
    "Brand gap nudge: Globe is the widest browse-to-quote gap this week.",
    "",
    "• 2,236 sessions, 3 quotes (ratio 745:1)",
    "• Top landing: /brands/globe/glidegrill-series - 743 sessions, 0 quotes",
    "• Nearest peer (Aspire) is 4x tighter at 179:1",
    "",
    "Suggested owners: Marketing lead (RFQ content), Content team (landing audit), Dealer ops (coverage).",
  ].join("\n"),
  actionLabel: "Open nudge brief",
};

const A4_EMAIL = {
  from: "Enterprise GPT <no-reply@enterprisegpt.middleby.com>",
  to: "marketing-lead@middleby.com",
  cc: "",
  subject: "Brand gap opportunity · Globe (Week of Jul 22)",
  sentAt: "Tue 9:15 AM",
  body: [
    "Hi marketing lead,",
    "",
    "Brand gap alert on Globe. Full opportunity brief attached.",
    "",
    "Snapshot:",
    "  • 2,236 site sessions vs 3 HubSpot quotes for the week",
    "  • Top landing page /brands/globe/glidegrill-series drove 743 sessions and 0 quotes",
    "  • Data window caveat: GA sessions are weekly, HubSpot quotes are a snapshot at export time",
    "",
    "An Asana review task has been drafted (held until you approve outreach copy).",
    "",
    "Enterprise GPT",
  ].join("\n"),
  attachment: "brand-gap-opportunity-globe.pdf",
};

const A4_CSV_HEADERS = [
  { key: "rank", label: "Rank" },
  { key: "brand", label: "Brand" },
  { key: "sessions", label: "Sessions (weekly)" },
  { key: "quotes", label: "Quotes (snapshot)" },
  { key: "ratio", label: "Sessions per quote" },
];

function buildA4NudgePdf() {
  return buildStandardPdfBrief({
    title: "Brand gap opportunity - Globe",
    subtitle: `${WEEK_LABEL}  ·  Prepared for marketing`,
    headline:
      "Globe is the widest browse-to-quote gap this week: 2,236 sessions produced 3 quotes (ratio 745:1). The nearest peer, Aspire, is 4x tighter. The top landing page produced zero quotes.",
    sections: [
      { type: "table", heading: "Top brand gaps",
        head: ["#", "Brand", "Sessions", "Quotes", "Ratio"],
        body: A4_GAP_ROWS.map((r) => [String(r.rank), r.brand, String(r.sessions), String(r.quotes), r.ratio]) },
      { type: "table", heading: "Landing pages driving Globe browse",
        head: ["Landing page", "Sessions", "Quotes"],
        body: A4_LANDING_PAGES.map((p) => [p.path, String(p.sessions), String(p.quotes)]) },
      { type: "bullets", heading: "Suggested next steps",
        items: [
          "Audit /brands/globe/glidegrill-series - highest browse, zero conversion.",
          "Confirm dealer coverage for Globe accounts in the top browsing regions.",
          "Test a targeted RFQ prompt on the Globe product catalog page.",
        ]},
      { type: "text", heading: "Data window caveat",
        body: "GA sessions are weekly totals. HubSpot quotes are a snapshot at export time. The ratio is directional, not audit-grade." },
    ],
    footer: STANDARD_FOOTER,
  });
}

// ==========================================================================
// A5 - SKU watchlist export (no approval gate)
// ==========================================================================

const A5_WATCHLIST_ROWS = [
  { sku: "SKU-TC-3009", name: "TurboChef Sota Countertop Oven", category: "High-Speed Ovens", price: "$8,942", intent: 92, watchReason: "Browse +38% WoW" },
  { sku: "SKU-BL-4412", name: "Blodgett Combi Oven BCX-14E", category: "Combi Ovens", price: "$14,750", intent: 88, watchReason: "3 quote drafts, none submitted" },
  { sku: "SKU-PI-2211", name: "Pitco 40D Gas Fryer", category: "Fryers", price: "$3,890", intent: 84, watchReason: "Consultation clicks +12" },
  { sku: "SKU-SB-5518", name: "Southbend Marathoner Range", category: "Ranges", price: "$5,240", intent: 82, watchReason: "Repeat visits from 6 accounts" },
  { sku: "SKU-MM-9902", name: "Middleby Marshall PS740E Conveyor", category: "Conveyor Ovens", price: "$18,300", intent: 80, watchReason: "Cart abandoned x4" },
  { sku: "SKU-BP-7714", name: "Bakers Pride Y-800BL Deck Oven", category: "Deck Ovens", price: "$7,120", intent: 78, watchReason: "Browse +22% WoW" },
  { sku: "SKU-WL-3308", name: "Wells WVAE-30F Fryer", category: "Fryers", price: "$4,410", intent: 76, watchReason: "Spec-sheet downloads x18" },
  { sku: "SKU-JR-6620", name: "Jade Range JRTB-24 Charbroiler", category: "Charbroilers", price: "$3,680", intent: 74, watchReason: "New browse from 4 dealers" },
  { sku: "SKU-NV-1102", name: "Nu-Vu QB-3/9 Proofer/Oven", category: "Proofers", price: "$9,850", intent: 72, watchReason: "Comparison-tool clicks +9" },
  { sku: "SKU-ST-4499", name: "Star 8G-CBA Griddle", category: "Griddles", price: "$1,940", intent: 70, watchReason: "Steady weekly demand" },
  { sku: "SKU-TM-2020", name: "Toastmaster TP24 Toaster", category: "Toasters", price: "$1,120", intent: 68, watchReason: "Attach-rate opportunity" },
  { sku: "SKU-TC-1188", name: "TurboChef Bullet Rapid-Cook", category: "High-Speed Ovens", price: "$10,420", intent: 66, watchReason: "Coming off stock alert" },
  { sku: "SKU-BL-8811", name: "Blodgett DFG-100 Double Deck", category: "Deck Ovens", price: "$11,600", intent: 64, watchReason: "Repeat dealer inquiries" },
  { sku: "SKU-SB-3312", name: "Southbend PE22-4 Convection Oven", category: "Convection", price: "$6,780", intent: 62, watchReason: "Category up +14% WoW" },
];

const A5_SLACK = {
  channel: "#merchandising",
  author: "Enterprise GPT",
  timestamp: "Wed 6:03 PM",
  body: [
    "SKU watchlist for the week is ready.",
    "",
    "14 SKUs above intent threshold. High-Speed Ovens and Combi Ovens lead the list.",
    "",
    "• Top signal: SKU-TC-3009 (TurboChef Sota) - browse +38% WoW",
    "• Highest cart-abandonment: SKU-MM-9902 (Middleby Marshall PS740E) - 4x this week",
    "• Categories covered: 6",
    "",
    "Dealer-ready CSV attached to the merchandising email.",
  ].join("\n"),
  actionLabel: "Open watchlist",
};

const A5_EMAIL = {
  from: "Enterprise GPT <no-reply@enterprisegpt.middleby.com>",
  to: "merchandising@middleby.com",
  cc: "dealer-ops@middleby.com",
  subject: `SKU watchlist export · ${WEEK_LABEL}`,
  sentAt: "Wed 6:04 PM",
  body: [
    "Merchandising team,",
    "",
    "Weekly SKU watchlist is attached (CSV). 14 SKUs are above the intent threshold this week.",
    "",
    "  • Top intent: SKU-TC-3009 (TurboChef Sota) at 92",
    "  • Highest cart-abandonment signal: SKU-MM-9902 (Middleby Marshall PS740E)",
    "  • Rationale brief is attached alongside the CSV",
    "",
    "Enterprise GPT",
  ].join("\n"),
  attachment: "sku-watchlist.csv",
};

const A5_CSV_HEADERS = [
  { key: "sku", label: "SKU" },
  { key: "name", label: "Product" },
  { key: "category", label: "Category" },
  { key: "price", label: "List price" },
  { key: "intent", label: "Intent score" },
  { key: "watchReason", label: "Watch reason" },
];

function buildA5RationalePdf() {
  const byCat = A5_WATCHLIST_ROWS.reduce((acc, r) => {
    acc[r.category] = (acc[r.category] || 0) + 1;
    return acc;
  }, {});
  const catRows = Object.entries(byCat)
    .sort((a, b) => b[1] - a[1])
    .map(([c, n]) => [c, String(n)]);
  return buildStandardPdfBrief({
    title: "SKU watchlist rationale",
    subtitle: `${WEEK_LABEL}  ·  Prepared for merchandising`,
    headline:
      "14 SKUs are above the intent threshold this week. Signals are a mix of browse volume, cart abandonment, spec-sheet downloads, and comparison-tool activity. All 14 have been enriched with catalog data from Akeneo.",
    sections: [
      { type: "kpis", heading: "This week", items: [
        { label: "SKUs on watchlist", value: "14", sub: "Above threshold" },
        { label: "Categories", value: String(catRows.length), sub: "L1 in sample" },
        { label: "Format", value: "CSV", sub: "Dealer-ready" },
      ]},
      { type: "table", heading: "By category", head: ["Category", "SKUs"], body: catRows },
      { type: "table", heading: "Top 6 by intent",
        head: ["SKU", "Product", "Category", "Intent"],
        body: A5_WATCHLIST_ROWS.slice(0, 6).map((r) => [r.sku, r.name, r.category, String(r.intent)]) },
    ],
    footer: STANDARD_FOOTER,
  });
}

// ==========================================================================
// A6 - Consultation-yes priority routing
// ==========================================================================

const A6_CONSULT_ROWS = [
  { deal: "MB-24812", buyer: "Sunbelt Hospitality", region: "Southeast", seniorRep: "R. Alvarez", intent: 94, priority: 1 },
  { deal: "MB-24801", buyer: "Meridian Hotels", region: "West coast", seniorRep: "J. Chen", intent: 92, priority: 2 },
  { deal: "MB-24794", buyer: "Beacon Hospitality Group", region: "Northeast", seniorRep: "S. Patel", intent: 89, priority: 3 },
  { deal: "MB-24786", buyer: "Coastal Convention Centers", region: "Northeast", seniorRep: "S. Patel", intent: 87, priority: 4 },
  { deal: "MB-24771", buyer: "Northland Convenience", region: "Central", seniorRep: "M. Rivera", intent: 85, priority: 5 },
  { deal: "MB-24762", buyer: "Grand Plateau Resorts", region: "West coast", seniorRep: "J. Chen", intent: 84, priority: 6 },
  { deal: "MB-24754", buyer: "Golden Palate Restaurants", region: "Central", seniorRep: "M. Rivera", intent: 82, priority: 7 },
  { deal: "MB-24741", buyer: "Silver Fork Group", region: "Southeast", seniorRep: "R. Alvarez", intent: 80, priority: 8 },
];

const A6_TASK_PREVIEW = A6_CONSULT_ROWS.slice(0, 6).map((r) => ({
  taskName: `Consultation call: ${r.buyer} (${r.deal})`,
  assignee: r.seniorRep,
  dueDate: "Within 24h",
  priority: r.priority <= 3 ? "High" : "Normal",
  linkedDeal: r.deal,
}));

const A6_SLACK = {
  channel: "#dealer-ops",
  author: "Enterprise GPT",
  timestamp: "Thu 8:22 AM",
  body: [
    "Consultation priority queue is ready.",
    "",
    "28 consultation-yes quotes routed to 12 senior reps by region.",
    "",
    "• Central: 8 quotes to M. Rivera + 3 others",
    "• West coast: 7 quotes to J. Chen + 2 others",
    "• Northeast: 6 quotes to S. Patel + 2 others",
    "• Southeast: 7 quotes to R. Alvarez + 3 others",
    "",
    "First-touch SLA is 24 hours from ops approval.",
  ].join("\n"),
  actionLabel: "Open queue",
};

const A6_EMAIL = {
  from: "Enterprise GPT <no-reply@enterprisegpt.middleby.com>",
  to: "dealer-ops@middleby.com",
  cc: "senior-reps@middleby.com",
  subject: `Consultation priority routing · ${WEEK_LABEL}`,
  sentAt: "Thu 8:24 AM",
  body: [
    "Dealer ops team,",
    "",
    "28 consultation-yes quotes have been routed to senior reps by region. Full priority queue attached.",
    "",
    "Highlights:",
    "  • Top 3 by intent: MB-24812 (Sunbelt Hospitality), MB-24801 (Meridian Hotels), MB-24794 (Beacon Hospitality)",
    "  • Rep capacity balanced across 4 regions",
    "  • HubSpot ownership updates staged, held until ops confirms",
    "",
    "Enterprise GPT",
  ].join("\n"),
  attachment: "consultation-priority-routing.pdf",
};

const A6_CSV_HEADERS = [
  { key: "priority", label: "Priority" },
  { key: "deal", label: "Deal ID" },
  { key: "buyer", label: "Buyer" },
  { key: "region", label: "Region" },
  { key: "seniorRep", label: "Senior rep" },
  { key: "intent", label: "Intent score" },
];

function buildA6RoutingPdf() {
  const byRegion = A6_CONSULT_ROWS.reduce((acc, r) => {
    acc[r.region] = (acc[r.region] || 0) + 1;
    return acc;
  }, {});
  const regionRows = Object.entries(byRegion)
    .sort((a, b) => b[1] - a[1])
    .map(([reg, n]) => [reg, String(n), "24h"]);
  return buildStandardPdfBrief({
    title: "Consultation priority routing",
    subtitle: `${WEEK_LABEL}  ·  Prepared for dealer ops`,
    headline:
      "28 consultation-yes quotes have been routed to 12 senior reps across 4 regions. Priority is based on intent score and time since consultation flag. Ownership updates are staged in HubSpot and held until dealer ops approves rep assignments.",
    sections: [
      { type: "table", heading: "Regional load", head: ["Region", "Consult quotes", "First-touch SLA"], body: regionRows },
      { type: "table", heading: "Top 8 by intent",
        head: ["#", "Deal", "Buyer", "Region", "Senior rep", "Intent"],
        body: A6_CONSULT_ROWS.map((r) => [String(r.priority), r.deal, r.buyer, r.region, r.seniorRep, String(r.intent)]) },
    ],
    footer: STANDARD_FOOTER,
  });
}

// ==========================================================================
// A7 - Post-deploy regression alert
// ==========================================================================

const A7_ERROR_ROWS = [
  { hour: "Mar 22 - 00:00 (pre)", sessions: 892, errors: 3, rate: "0.34%" },
  { hour: "Mar 22 - 08:00 (deploy)", sessions: 1104, errors: 7, rate: "0.63%" },
  { hour: "Mar 22 - 10:00", sessions: 1287, errors: 21, rate: "1.63%" },
  { hour: "Mar 22 - 12:00", sessions: 1419, errors: 34, rate: "2.40%" },
  { hour: "Mar 22 - 14:00", sessions: 1502, errors: 42, rate: "2.79%" },
  { hour: "Mar 22 - 16:00", sessions: 1361, errors: 38, rate: "2.79%" },
  { hour: "Mar 22 - 18:00", sessions: 1088, errors: 26, rate: "2.39%" },
  { hour: "Mar 22 - 20:00", sessions: 863, errors: 19, rate: "2.20%" },
];

const A7_ERROR_BREAKDOWN = [
  { errorType: "RequireJS module load failure", sessions: 2874, percent: "68%" },
  { errorType: "Magento checkout script timeout", sessions: 892, percent: "21%" },
  { errorType: "PDP image asset 404", sessions: 341, percent: "8%" },
  { errorType: "Cart hydration mismatch", sessions: 125, percent: "3%" },
];

const A7_SLACK = {
  channel: "#commerce-incidents",
  author: "Enterprise GPT",
  timestamp: "Mar 22 - 3:12 PM",
  body: [
    "⚠  Post-deploy regression alert",
    "",
    "Error rate spiked from 0.34% (pre-deploy) to 2.79% at peak. RequireJS module load failure is the dominant signal (68% of affected sessions).",
    "",
    "• Sessions affected: 4,232",
    "• Estimated quotes at risk: 38",
    "• Deploy marker: Mar 22, 08:00",
    "• First detected: 10:00, 2h after release",
    "",
    "Digital team notified. Recovery is being tracked; error rate trending down since 14:00.",
  ].join("\n"),
  actionLabel: "View incident",
};

const A7_EMAIL = {
  from: "Enterprise GPT <no-reply@enterprisegpt.middleby.com>",
  to: "digital-team@middleby.com",
  cc: "commerce-leadership@middleby.com",
  subject: "Post-deploy regression detected · Mar 22 release",
  sentAt: "Mar 22 - 3:13 PM",
  body: [
    "Digital team,",
    "",
    "A UX regression has been detected after the Mar 22 site release. Full incident brief attached.",
    "",
    "Snapshot:",
    "  • Error rate: 0.34% pre-deploy, peaked at 2.79% at 14:00",
    "  • Dominant signal: RequireJS module load failure (68% of affected sessions)",
    "  • Estimated quotes at risk: 38",
    "  • Recovery: error rate trending down since 14:00 but not yet at baseline",
    "",
    "Jira ticket has been drafted for the commerce backlog (held for commerce lead review).",
    "",
    "Enterprise GPT",
  ].join("\n"),
  attachment: "post-deploy-regression-brief.pdf",
};

const A7_CSV_HEADERS = [
  { key: "hour", label: "Hour" },
  { key: "sessions", label: "Sessions" },
  { key: "errors", label: "Error events" },
  { key: "rate", label: "Error rate" },
];

function buildA7IncidentPdf() {
  return buildStandardPdfBrief({
    title: "Post-deploy regression alert",
    subtitle: "Mar 22 site release  ·  Prepared for the digital team",
    headline:
      "Error rate spiked from 0.34% pre-deploy to 2.79% at peak (14:00). RequireJS module load failure is the dominant signal, affecting 68% of impacted sessions. Estimated quotes at risk: 38. Error rate is trending down since 14:00 but has not yet returned to baseline.",
    sections: [
      { type: "kpis", heading: "Impact snapshot", items: [
        { label: "Sessions affected", value: "4,232", sub: "24h window" },
        { label: "Peak error rate", value: "2.79%", sub: "vs 0.34% baseline" },
        { label: "Quotes at risk", value: "38", sub: "Estimated" },
      ]},
      { type: "table", heading: "Error signal timeline",
        head: ["Hour", "Sessions", "Error events", "Rate"],
        body: A7_ERROR_ROWS.map((r) => [r.hour, String(r.sessions), String(r.errors), r.rate]) },
      { type: "table", heading: "Affected sessions by error type",
        head: ["Error type", "Sessions", "Share"],
        body: A7_ERROR_BREAKDOWN.map((r) => [r.errorType, String(r.sessions), r.percent]) },
      { type: "bullets", heading: "Recommended next steps",
        items: [
          "Confirm the RequireJS module list changed in the Mar 22 release. If yes, verify shim config.",
          "Track error rate hourly until it returns to the 0.34% baseline.",
          "File the Jira ticket held in Draft once the commerce lead reviews.",
        ]},
    ],
    footer: STANDARD_FOOTER,
  });
}

// ==========================================================================
// Registry
// ==========================================================================

/**
 * @typedef {{
 *   id: string,
 *   title: string,
 *   description?: string,
 *   format: "pdf" | "csv" | "html",
 *   filename: string,
 *   build: () => (Blob | { html: string } | Promise<Blob | { html: string }>)
 * }} AutomationReport
 *
 * @typedef {{ id: string, title: string, status: "pass" | "warn" | "fail", detail: string }} PreflightCheck
 *
 * @typedef {{
 *   preparingMessage: string,
 *   reviewSteps: Array<{ title: string, description?: string }>,
 *   preflightChecks: PreflightCheck[],
 *   channels: Array<
 *     | { kind: "slack", title: string, message: object }
 *     | { kind: "email", title: string, message: object }
 *     | { kind: "hubspot-tasks", title: string, rows: object[] }
 *   >,
 *   reports: AutomationReport[],
 * }} AutomationOutputs
 *
 * @type {Record<string, AutomationOutputs>}
 */
export const MIDDLEBY_AUTOMATION_OUTPUTS = {
  A1: {
    preparingMessage:
      "Pulling open quotes from HubSpot, ranking by consultation flag and rep workload, and drafting the Monday brief...",
    reviewSteps: [
      { title: "Selected 18 quotes in the 0-1 month follow-up window",
        description: "Filtered HubSpot deals whose last activity is 5 to 26 days ago." },
      { title: "Ranked by consultation flag and rep group workload",
        description: "6 consultation-flagged deals moved to the top of the list." },
      { title: "Drafted the Monday standup brief",
        description: "One-page summary with headline, top priorities, and rep group load." },
      { title: "Drafted the Slack post for #dealer-ops-monday",
        description: "Short summary with top 3 deals by value and rep coverage." },
      { title: "Prepared 6 HubSpot tasks for creation",
        description: "One task per top-priority deal, assigned to the owning rep group." },
    ],
    preflightChecks: [
      { id: "workload", title: "Rep group workloads balanced", status: "pass",
        detail: "Deals distributed across 4 rep groups. Central and West coast carry 6 each; Northeast and Southeast carry 3 each. No single rep exceeds 3 assigned follow-ups." },
      { id: "approval", title: "Approval limits ok", status: "pass",
        detail: "All queued HubSpot tasks are within rep-owner authority. No deals require sales leadership approval to progress at this stage." },
      { id: "sla", title: "SLA policy respected", status: "pass",
        detail: "No deals in the worklist have crossed the 14-day SLA threshold. Escalation flow (A2) handles those separately." },
      { id: "senior-review", title: "2 deals above $100K flagged for senior rep confirmation", status: "warn",
        detail: "MB-24778 (Sunbelt Hospitality, $210K) and MB-24738 (Grand Plateau Resorts, $175K) are above the $100K senior-review threshold. The tasks will still be created, but the assigned senior rep should confirm outreach before contact." },
    ],
    channels: [
      { kind: "slack", title: "Slack · #dealer-ops-monday", message: A1_SLACK },
      { kind: "email", title: "Email · dealer-ops distribution", message: A1_EMAIL },
      { kind: "hubspot-tasks", title: "HubSpot · tasks queued", rows: A1_TASK_PREVIEW },
    ],
    reports: [
      { id: "A1-brief-pdf", title: "Monday dealer follow-up brief",
        description: "One-page leadership summary for the Monday standup.",
        format: "pdf", filename: "monday-dealer-follow-up-brief.pdf",
        build: () => buildA1BriefPdf() },
      { id: "A1-worklist-csv", title: "Weekly worklist (18 quotes)",
        description: "Prioritized list of quotes for dealer follow-up this week.",
        format: "csv", filename: "weekly-dealer-worklist.csv",
        build: () => csvBlob(A1_WORKLIST_CSV_HEADERS, A1_WORKLIST_ROWS) },
    ],
  },

  A2: {
    preparingMessage:
      "Finding overdue quotes past the 14-day SLA, attaching rep and brand context, and assembling the leadership escalation package...",
    reviewSteps: [
      { title: "Detected 12 quotes past the 14-day SLA",
        description: "Filtered HubSpot open deals with last activity older than the policy threshold." },
      { title: "Enriched with rep group, brand, and deal value",
        description: "Every breach carries the ownership and pipeline context leadership needs." },
      { title: "Built the escalation digest for sales leadership",
        description: "PDF summary with severity split, top breaches, and rep group load." },
      { title: "Drafted the Slack post for #sales-leadership",
        description: "Short summary with critical breaches and largest exposure." },
      { title: "Queued 12 HubSpot escalation tasks",
        description: "One per rep group, held until leadership approves the recipient list." },
    ],
    preflightChecks: [
      { id: "policy", title: "SLA policy match", status: "pass",
        detail: "All 12 breaches meet the current 14-day escalation policy. No policy conflicts detected." },
      { id: "ownership", title: "Rep group ownership resolved", status: "pass",
        detail: "Every breach has an assigned rep group. No orphaned deals in the escalation list." },
      { id: "distro", title: "Leadership recipient list current", status: "pass",
        detail: "Sales leadership distribution list synced within the last 24 hours." },
      { id: "vp", title: "1 deal above $500K flagged for VP review", status: "warn",
        detail: "MB-24432 (Coastal Convention Centers, $543K) is above the $500K senior-review threshold. The escalation will include VP visibility as a Cc." },
    ],
    channels: [
      { kind: "slack", title: "Slack · #sales-leadership", message: A2_SLACK },
      { kind: "email", title: "Email · sales leadership", message: A2_EMAIL },
      { kind: "hubspot-tasks", title: "HubSpot · escalation tasks", rows: A2_TASK_PREVIEW },
    ],
    reports: [
      { id: "A2-package-pdf", title: "Overdue quote escalation package",
        description: "Leadership brief with severity split, top breaches, and rep group load.",
        format: "pdf", filename: "sla-escalation-package.pdf",
        build: () => buildA2EscalationPdf() },
      { id: "A2-breach-csv", title: "SLA breach list (12 deals)",
        description: "Full list of overdue quotes with rep, value, days open, and severity.",
        format: "csv", filename: "sla-breach-list.csv",
        build: () => csvBlob(A2_CSV_HEADERS, A2_BREACH_ROWS) },
    ],
  },

  A3: {
    preparingMessage:
      "Pulling weekly deltas from GA, HubSpot, and LogRocket, and merging the Friday executive one-pager...",
    reviewSteps: [
      { title: "Pulled 8 weekly signals across GA, HubSpot, and LogRocket",
        description: "Traffic, pipeline, UX, ops, demand, and dealer motion signals for the week." },
      { title: "Compared each signal week-over-week",
        description: "Direction of change surfaced alongside the source system." },
      { title: "Drafted the executive one-pager",
        description: "Headline plus 8 signals table plus 3 leadership talking points." },
      { title: "Drafted the Slack post for #exec-briefing",
        description: "Short summary with the top 3 signals worth a leadership conversation." },
      { title: "Scheduled the Friday 7am distribution",
        description: "PDF attached to the briefing email; distribution held until send." },
    ],
    preflightChecks: [
      { id: "freshness", title: "All source systems fresh within 4 hours", status: "pass",
        detail: "GA, HubSpot, and LogRocket exports were refreshed within the last 4 hours. No stale-data warnings." },
      { id: "recipients", title: "Leadership distribution list current", status: "pass",
        detail: "Leadership list matches the org chart snapshot from Monday's HRIS sync." },
      { id: "content", title: "No content policy violations detected", status: "pass",
        detail: "No customer PII, restricted financial figures, or unreleased-product mentions present in the brief." },
    ],
    channels: [
      { kind: "slack", title: "Slack · #exec-briefing", message: A3_SLACK },
      { kind: "email", title: "Email · leadership distribution", message: A3_EMAIL },
    ],
    reports: [
      { id: "A3-briefing-pdf", title: "Friday executive briefing",
        description: "Headline, 8 signals table, and 3 leadership talking points.",
        format: "pdf", filename: "friday-executive-briefing.pdf",
        build: () => buildA3BriefingPdf() },
      { id: "A3-signals-csv", title: "Weekly signals (8 rows)",
        description: "Every signal in the brief with value, week-over-week change, and source.",
        format: "csv", filename: "weekly-signals.csv",
        build: () => csvBlob(A3_CSV_HEADERS, A3_SIGNALS) },
    ],
  },

  A4: {
    preparingMessage:
      "Calculating browse-to-quote gap by brand and drafting the marketing nudge for the widest gap...",
    reviewSteps: [
      { title: "Ranked 6 brands by browse-to-quote gap",
        description: "GA sessions divided by HubSpot quotes; higher ratio means more browse without conversion." },
      { title: "Identified Globe as the widest gap (745:1)",
        description: "Nearest peer Aspire is 4x tighter at 179:1." },
      { title: "Attached the top 5 landing pages driving Globe browse",
        description: "Highest-browse page /brands/globe/glidegrill-series produced zero quotes." },
      { title: "Drafted the marketing nudge",
        description: "Email plus Slack summary plus suggested next-step owners." },
      { title: "Prepared 3 Asana tasks for the marketing team",
        description: "Held until marketing lead approves outreach copy." },
    ],
    preflightChecks: [
      { id: "window", title: "Data windows aligned within tolerance", status: "warn",
        detail: "GA sessions are a weekly total; HubSpot quotes are a snapshot at export time. Ratios are directional, not audit-grade. Confirm before public communication." },
      { id: "attribution", title: "Landing page attribution complete", status: "pass",
        detail: "All 5 landing pages resolved to a single brand. No cross-brand contamination detected." },
      { id: "owner", title: "Marketing owner identified", status: "pass",
        detail: "The Globe brand is owned by the marketing lead per the brand ownership registry." },
    ],
    channels: [
      { kind: "slack", title: "Slack · #brand-marketing", message: A4_SLACK },
      { kind: "email", title: "Email · marketing lead", message: A4_EMAIL },
      { kind: "hubspot-tasks", title: "Asana · tasks queued", rows: A4_ASANA_TASK },
    ],
    reports: [
      { id: "A4-nudge-pdf", title: "Brand gap opportunity brief - Globe",
        description: "Ranked brand gaps, top landing pages, and suggested next steps.",
        format: "pdf", filename: "brand-gap-opportunity-globe.pdf",
        build: () => buildA4NudgePdf() },
      { id: "A4-gap-csv", title: "Browse-to-quote by brand (6 rows)",
        description: "Full brand gap ranking with sessions, quotes, and ratio.",
        format: "csv", filename: "brand-gap-ranking.csv",
        build: () => csvBlob(A4_CSV_HEADERS, A4_GAP_ROWS) },
    ],
  },

  A5: {
    preparingMessage:
      "Scoring SKUs by intent, enriching from Akeneo, and generating the merchandising watchlist export...",
    reviewSteps: [
      { title: "Scored 14 SKUs above the intent threshold",
        description: "Browse and quote signals combined into a per-SKU intent score." },
      { title: "Enriched every SKU from Akeneo",
        description: "Category, list price, and product attributes attached to each row." },
      { title: "Generated the dealer-ready CSV export",
        description: "14-row watchlist ready to hand to merchandising and dealer teams." },
      { title: "Drafted the rationale brief",
        description: "PDF summary explaining why each SKU is on the watchlist." },
      { title: "Prepared the distribution email",
        description: "Sent to the merchandising distribution list with CSV plus rationale attached." },
    ],
    preflightChecks: [
      { id: "enrichment", title: "All 14 SKUs enriched successfully", status: "pass",
        detail: "Every SKU resolved to catalog data in Akeneo. No missing attributes." },
      { id: "thresholds", title: "Watch thresholds within calibration window", status: "pass",
        detail: "Intent thresholds were last recalibrated 6 days ago (inside the 14-day window). No calibration drift detected." },
      { id: "distro", title: "Merchandising distribution list current", status: "pass",
        detail: "Distribution list synced with the ops team on Monday. No stale addresses." },
    ],
    channels: [
      { kind: "slack", title: "Slack · #merchandising", message: A5_SLACK },
      { kind: "email", title: "Email · merchandising distribution", message: A5_EMAIL },
    ],
    reports: [
      { id: "A5-watchlist-csv", title: "SKU watchlist (14 SKUs)",
        description: "Dealer-ready CSV export with SKU, product, category, price, intent, and watch reason.",
        format: "csv", filename: "sku-watchlist.csv",
        build: () => csvBlob(A5_CSV_HEADERS, A5_WATCHLIST_ROWS) },
      { id: "A5-rationale-pdf", title: "Watchlist rationale",
        description: "Category breakdown and top-6-by-intent detail for the watchlist.",
        format: "pdf", filename: "sku-watchlist-rationale.pdf",
        build: () => buildA5RationalePdf() },
    ],
  },

  A6: {
    preparingMessage:
      "Filtering consultation-yes quotes, matching to senior rep capacity by region, and staging HubSpot ownership updates...",
    reviewSteps: [
      { title: "Filtered 28 consultation-yes quotes",
        description: "Open HubSpot deals where the consultation field is set to Yes." },
      { title: "Identified 12 senior reps across 4 regions",
        description: "Central, West coast, Northeast, Southeast rep pools with current capacity." },
      { title: "Ranked the priority queue by intent score",
        description: "Higher intent moves to the top; ties broken by consultation flag age." },
      { title: "Drafted the Slack summary for #dealer-ops",
        description: "Regional load and top-3 by intent for ops visibility." },
      { title: "Staged 28 HubSpot ownership updates",
        description: "Held until dealer ops confirms rep assignments." },
    ],
    preflightChecks: [
      { id: "capacity", title: "Senior rep capacity balanced across regions", status: "pass",
        detail: "Central 8, West coast 7, Southeast 7, Northeast 6. No senior rep exceeds their weekly consultation cap." },
      { id: "recency", title: "No consultation quote is more than 30 days old", status: "pass",
        detail: "Oldest consultation flag in the queue is 22 days. Any 30+ day flag would route to escalation (A2) instead." },
      { id: "region", title: "2 consultation quotes have unclear region", status: "warn",
        detail: "MB-24671 and MB-24659 do not have a clear region tag in HubSpot. They have been assigned to the nearest senior rep by ZIP but should be confirmed by ops." },
    ],
    channels: [
      { kind: "slack", title: "Slack · #dealer-ops", message: A6_SLACK },
      { kind: "email", title: "Email · dealer ops", message: A6_EMAIL },
      { kind: "hubspot-tasks", title: "HubSpot · ownership updates", rows: A6_TASK_PREVIEW },
    ],
    reports: [
      { id: "A6-routing-pdf", title: "Priority routing summary",
        description: "Regional load and top-8-by-intent for the consultation queue.",
        format: "pdf", filename: "consultation-priority-routing.pdf",
        build: () => buildA6RoutingPdf() },
      { id: "A6-queue-csv", title: "Consultation queue (8 rows)",
        description: "Full priority queue with deal, buyer, region, senior rep, and intent.",
        format: "csv", filename: "consultation-queue.csv",
        build: () => csvBlob(A6_CSV_HEADERS, A6_CONSULT_ROWS) },
    ],
  },

  A7: {
    preparingMessage:
      "Comparing error rates around the Mar 22 release, attaching affected session detail, and drafting the incident brief...",
    reviewSteps: [
      { title: "Compared error rate 24h before and after the Mar 22 deploy",
        description: "Baseline 0.34%; peak after release 2.79% at 14:00 (roughly 6 hours post-deploy)." },
      { title: "Attached the top error type and affected sessions",
        description: "RequireJS module load failure is 68% of the signal; 4,232 sessions affected." },
      { title: "Drafted the incident summary for the digital team",
        description: "Brief with impact snapshot, timeline, error breakdown, and recommended next steps." },
      { title: "Drafted the Slack post for #commerce-incidents",
        description: "Short alert with impact and current recovery status." },
      { title: "Prepared a Jira ticket for the commerce backlog",
        description: "Held until commerce lead reviews the Slack post." },
    ],
    preflightChecks: [
      { id: "marker", title: "Deploy marker confirmed", status: "pass",
        detail: "Mar 22 08:00 release marker matches the CI/CD pipeline record. No missing release notes." },
      { id: "signal", title: "Error signal above alert threshold", status: "pass",
        detail: "Peak error rate 2.79% is over 8x the baseline, well above the 3x alert threshold. Signal is above noise." },
      { id: "pii", title: "Affected user PII redacted", status: "pass",
        detail: "The 4,232 affected sessions are referenced by session id only. No email, IP, or account name appears in the incident brief." },
    ],
    channels: [
      { kind: "slack", title: "Slack · #commerce-incidents", message: A7_SLACK },
      { kind: "email", title: "Email · digital team", message: A7_EMAIL },
    ],
    reports: [
      { id: "A7-incident-pdf", title: "Post-deploy regression brief",
        description: "Impact snapshot, hourly timeline, error breakdown, and recommended next steps.",
        format: "pdf", filename: "post-deploy-regression-brief.pdf",
        build: () => buildA7IncidentPdf() },
      { id: "A7-timeline-csv", title: "Error signal timeline (8 hours)",
        description: "Hourly session counts, error events, and error rate around the release.",
        format: "csv", filename: "error-signal-timeline.csv",
        build: () => csvBlob(A7_CSV_HEADERS, A7_ERROR_ROWS) },
    ],
  },
};

export function getMiddlebyAutomationOutputs(playbookId) {
  return MIDDLEBY_AUTOMATION_OUTPUTS[playbookId] ?? null;
}
