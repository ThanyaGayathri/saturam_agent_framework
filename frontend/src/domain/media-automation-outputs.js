/**
 * Default Media profile automation outputs for M1 and M2.
 * Shape mirrors gfi-automation-outputs.js. Uses the shared branded PDF /
 * CSV helpers so download buttons produce a real-looking media report
 * with actual data rows instead of an empty template.
 */

import {
  buildStandardPdfBrief,
  csvBlob,
  STANDARD_FOOTER,
  WEEK_LABEL,
} from "./automation-output-helpers";

// ==========================================================================
// M1 — Weekly trend intelligence digest
// ==========================================================================

const M1_TREND_ROWS = [
  {
    topic: "Mythological drama reboots",
    audience: "Hindi · Tier-2",
    weeks: 4,
    momentum: 78,
    sentiment: "+0.42",
    signal: "Sustained",
    sources: "YouTube shorts · Instagram Reels · Trade press · Twitter",
  },
  {
    topic: "Competitive cooking formats",
    audience: "25-40 urban female",
    weeks: 24,
    momentum: 71,
    sentiment: "+0.36",
    signal: "Sustained",
    sources: "Google Trends · Instagram · Trade press",
  },
  {
    topic: "Regional-language thrillers (Marathi)",
    audience: "Maharashtra · 18-34",
    weeks: 6,
    momentum: 68,
    sentiment: "+0.28",
    signal: "Sustained",
    sources: "YouTube · Regional press · Google Trends",
  },
  {
    topic: "Women-led courtroom drama",
    audience: "Hindi + regional · 25-45 F",
    weeks: 5,
    momentum: 64,
    sentiment: "+0.19",
    signal: "Sustained",
    sources: "Twitter · Trade press · YouTube",
  },
  {
    topic: "AI-generated content debates",
    audience: "General · English + Hindi",
    weeks: 3,
    momentum: 61,
    sentiment: "-0.08",
    signal: "Emerging",
    sources: "Twitter · Trade press · Reddit-adjacent forums",
  },
  {
    topic: "Cricket documentary series",
    audience: "18-45 M · pan-India",
    weeks: 8,
    momentum: 58,
    sentiment: "+0.44",
    signal: "Sustained",
    sources: "YouTube · Twitter · Trade press",
  },
  {
    topic: "Viral dance-format cameos (movie promos)",
    audience: "Gen Z · pan-India",
    weeks: 1,
    momentum: 84,
    sentiment: "+0.51",
    signal: "Spike (not sustained)",
    sources: "Instagram Reels · YouTube shorts",
  },
];

const M1_SLACK = {
  channel: "#content-strategy",
  author: "Enterprise GPT",
  timestamp: "Mon 7:02 AM",
  body: [
    "Weekly trend intelligence digest is ready.",
    "",
    "6 sustained trends this week (above 4-week momentum threshold) across 3.4M open-web + social signals.",
    "",
    "• Mythological drama reboots, Hindi Tier-2, 4-week momentum 78/100",
    "• Competitive cooking formats, 25-40 urban female, 6-month sustained",
    "• Regional-language thrillers (Marathi), 18-34 Maharashtra",
    "",
    "1 spike flagged (viral dance cameos) but not sustained, hold before commissioning.",
    "",
    "Full digest attached to the content-team email distribution.",
  ].join("\n"),
};

const M1_EMAIL = {
  from: "Enterprise GPT <no-reply@enterprisegpt.media.co>",
  to: "content-strategy@media.co",
  cc: "programming@media.co, content-development@media.co, consumer-insights@media.co",
  subject: `Weekly trend intelligence digest · ${WEEK_LABEL}`,
  sentAt: "Mon 7:00 AM",
  body: [
    "Content teams,",
    "",
    "This week's trend intelligence digest is attached. 3.4M audience signals scanned across 8 source families; 6 sustained themes cleared the 4-week momentum threshold.",
    "",
    "Highlights:",
    "  • Mythological drama reboots, 4-week sustained; strongest Hindi Tier-2 signal",
    "  • Competitive cooking formats, 6-month sustained; 25-40 urban female audience",
    "  • Regional-language thrillers (Marathi), new sustained signal worth deeper review",
    "",
    "One high-momentum topic (viral dance cameos) is flagged as a spike, not a sustained trend, retain visibility but hold before committing to commissioning research.",
    "",
    "Full topic table with source citations, momentum scores, and audience segments is in the attached PDF and CSV.",
    "",
    "Enterprise GPT",
  ].join("\n"),
  attachment: "weekly-trend-intelligence-digest.pdf",
};

const M1_CSV_HEADERS = [
  { key: "topic", label: "Topic / theme" },
  { key: "audience", label: "Audience segment" },
  { key: "weeks", label: "Weeks sustained" },
  { key: "momentum", label: "Momentum score" },
  { key: "sentiment", label: "Sentiment" },
  { key: "signal", label: "Signal type" },
  { key: "sources", label: "Evidence sources" },
];

function buildM1DigestPdf() {
  return buildStandardPdfBrief({
    title: "Weekly trend intelligence digest",
    subtitle: `${WEEK_LABEL}  ·  Prepared for content strategy & programming`,
    headline:
      "3.4M audience signals scanned this week across open-web, social, and search. 6 themes cleared the 4-week sustained-momentum threshold. Explainability layer separates spikes from sustained trends and links source evidence per topic.",
    sections: [
      { type: "kpis", heading: "This week", items: [
        { label: "Signals ingested", value: "3.4M", sub: "8 source families" },
        { label: "Sustained trends", value: "6", sub: "Above 4-week threshold" },
        { label: "Spikes flagged", value: "1", sub: "Not sustained; hold" },
      ]},
      { type: "table", heading: "Trending themes this week",
        head: ["Topic / theme", "Audience", "Weeks", "Momentum", "Sentiment", "Signal", "Evidence"],
        body: M1_TREND_ROWS.map((r) => [r.topic, r.audience, String(r.weeks), String(r.momentum), r.sentiment, r.signal, r.sources]) },
      { type: "bullets", heading: "Recommended next steps",
        items: [
          "Mythological drama reboots: brief content-development on a mythology-adjacent slate exploration (open scoping session).",
          "Competitive cooking formats: commission consumer-insights research on 25-40 urban female audience appetite.",
          "Regional-language thrillers (Marathi): validate viewer intent against our current Marathi catalog gaps.",
          "Viral dance cameos: retain in the monitor but do NOT commission, this is a spike, not a sustained trend.",
        ]},
    ],
    footer: STANDARD_FOOTER,
  });
}

// ==========================================================================
// M2 — Content opportunity brief (approval-gated)
// ==========================================================================

const M2_OPPORTUNITY_ROWS = [
  {
    theme: "Regional mythology (Marathi + Tamil)",
    momentum: 74,
    trajectory3m: "Rising",
    historicalParallel: "Legacy mythology slate over-indexed on similar audiences",
    slateGap: "Absent in current commissioning pipeline",
    label: "Recommendation",
  },
  {
    theme: "Women-led mythology reboots",
    momentum: 69,
    trajectory3m: "Rising",
    historicalParallel: "Family-drama success on 25-45 F audience",
    slateGap: "One title in development, none in market",
    label: "Recommendation",
  },
  {
    theme: "Competitive cooking (regional celebrity host)",
    momentum: 71,
    trajectory3m: "Stable",
    historicalParallel: "Prior cooking formats over-indexed on 25-40 urban female",
    slateGap: "Format thin in current slate",
    label: "Inferred trend",
  },
  {
    theme: "Youth-led courtroom drama",
    momentum: 64,
    trajectory3m: "Rising",
    historicalParallel: "Adjacent legal-drama catalog over-indexed on 25-45 F",
    slateGap: "Absent in current slate",
    label: "Inferred trend",
  },
  {
    theme: "Cricket documentary series (regional players)",
    momentum: 58,
    trajectory3m: "Stable",
    historicalParallel: "Cricket-adjacent programming reliably delivers pan-India reach",
    slateGap: "Format underweighted",
    label: "Fact",
  },
];

const M2_SLACK = {
  channel: "#content-strategy",
  author: "Enterprise GPT",
  timestamp: "Today 3:12 PM",
  body: [
    "Content opportunity brief prepared for approval.",
    "",
    "2 under-represented themes cleared the sustained-momentum threshold AND match historical hit patterns:",
    "",
    "• Regional mythology (Marathi + Tamil), momentum 74/100",
    "• Women-led mythology reboots, momentum 69/100",
    "",
    "Awaiting Head of Content Strategy approval before content-development handoff.",
  ].join("\n"),
};

const M2_EMAIL = {
  from: "Enterprise GPT <no-reply@enterprisegpt.media.co>",
  to: "head-content-strategy@media.co",
  cc: "content-development@media.co, programming@media.co",
  subject: "Approval requested: mythology-adjacent scoping shortlist",
  sentAt: "Today 3:15 PM",
  body: [
    "Head of Content Strategy,",
    "",
    "Two under-represented themes cleared the sustained-momentum threshold this week AND match historical hit patterns in our own catalog:",
    "",
    "  • Regional mythology (Marathi + Tamil), 4-week momentum 74/100; trajectory rising",
    "  • Women-led mythology reboots, 4-week momentum 69/100; trajectory rising",
    "",
    "The full brief separates:",
    "  • Fact: what we observe in signals today (with source citations)",
    "  • Inferred trend: where trajectory suggests interest is heading over 3-6 months",
    "  • Recommendation: themes worth deeper commissioning research",
    "",
    "The commissioning shortlist is held in draft until you approve or reject in the Automate panel.",
    "",
    "Enterprise GPT",
  ].join("\n"),
  attachment: "content-opportunity-brief.pdf",
};

const M2_CSV_HEADERS = [
  { key: "theme", label: "Theme" },
  { key: "momentum", label: "Momentum score" },
  { key: "trajectory3m", label: "3-month trajectory" },
  { key: "historicalParallel", label: "Historical parallel" },
  { key: "slateGap", label: "Slate gap" },
  { key: "label", label: "Fact / Inferred / Recommendation" },
];

function buildM2BriefPdf() {
  return buildStandardPdfBrief({
    title: "Content opportunity brief",
    subtitle: `${WEEK_LABEL}  ·  Prepared for Head of Content Strategy`,
    headline:
      "38 sustained themes evaluated against our historical hit patterns and competitor slate this week. 3 themes are under-represented in our current slate AND match audience appetite. Two are recommended for immediate scoping research. Fact vs. inferred trend vs. recommendation is labelled per theme.",
    sections: [
      { type: "kpis", heading: "Snapshot", items: [
        { label: "Themes evaluated", value: "38", sub: "Sustained-momentum catchment" },
        { label: "Under-represented", value: "3", sub: "In our slate" },
        { label: "Recommendations", value: "2", sub: "Ready for scoping" },
      ]},
      { type: "table", heading: "Content opportunities",
        head: ["Theme", "Momentum", "3-mo trajectory", "Historical parallel", "Slate gap", "Label"],
        body: M2_OPPORTUNITY_ROWS.map((r) => [
          r.theme, String(r.momentum), r.trajectory3m, r.historicalParallel, r.slateGap, r.label,
        ]) },
      { type: "bullets", heading: "Explainability & guardrails",
        items: [
          "Every row links back to source evidence per topic (open-web, social, and search signals).",
          "Trajectories are model projections with confidence bands; do not read as forecasts.",
          "Recommendations require Head of Content Strategy approval before content-development handoff.",
        ]},
    ],
    footer: STANDARD_FOOTER,
  });
}

// ==========================================================================
// Registry
// ==========================================================================

export const MEDIA_AUTOMATION_OUTPUTS = {
  M1: {
    preparingMessage:
      "Ingesting open-web and social signals, scoring 4-week momentum, and drafting the weekly trend digest...",
    reviewSteps: [
      { title: "Ingested 3.4M audience signals", description: "Open-web, social, and search across 8 tracked source families." },
      { title: "Extracted topics & entities", description: "NLP entity and topic extraction across the ingested corpus." },
      { title: "Scored momentum, sentiment, spread", description: "4-week growth curves separate sustained trends from spikes." },
      { title: "Composed weekly digest", description: "Explainability layer: what / why / who / momentum / evidence." },
      { title: "Scheduled Monday 7 AM distribution", description: "Content Strategy, Programming, Development, and Consumer Insights lists." },
    ],
    slack: M1_SLACK,
    email: M1_EMAIL,
    reports: [
      {
        id: "M1-digest-pdf",
        title: "Weekly trend intelligence digest",
        description: "One-page digest with sustained-trend momentum scores, audience segments, and source evidence.",
        format: "pdf",
        filename: "weekly-trend-intelligence-digest.pdf",
        build: () => buildM1DigestPdf(),
      },
      {
        id: "M1-trends-csv",
        title: "Trending themes detail",
        description: "All sustained + emerging themes this week with momentum scores, sentiment, and evidence sources.",
        format: "csv",
        filename: "weekly-trend-intelligence-detail.csv",
        build: () => csvBlob(M1_CSV_HEADERS, M1_TREND_ROWS),
      },
    ],
  },
  M2: {
    preparingMessage:
      "Extrapolating sustained trends, comparing with historical hits, and drafting the commissioning shortlist for Head of Content Strategy approval...",
    reviewSteps: [
      { title: "Extrapolated 38 sustained themes", description: "3-6 month trajectories with confidence bands per theme." },
      { title: "Matched against historical hits", description: "Compared trajectories with our own successful shows and competitor hits." },
      { title: "Flagged 3 under-represented themes", description: "Themes absent or thin in the current slate that cleared momentum bar." },
      { title: "Drafted commissioning shortlist", description: "Fact / inferred trend / recommendation labels applied per theme." },
      { title: "Staged brief for approval", description: "Held until Head of Content Strategy approves or rejects." },
    ],
    slack: M2_SLACK,
    email: M2_EMAIL,
    reports: [
      {
        id: "M2-brief-pdf",
        title: "Content opportunity brief",
        description: "Commissioning-shortlist brief with fact / inferred trend / recommendation labels per theme.",
        format: "pdf",
        filename: "content-opportunity-brief.pdf",
        build: () => buildM2BriefPdf(),
      },
      {
        id: "M2-opportunities-csv",
        title: "Content opportunity detail",
        description: "All evaluated themes with momentum, trajectory, historical parallels, and slate-gap flags.",
        format: "csv",
        filename: "content-opportunity-detail.csv",
        build: () => csvBlob(M2_CSV_HEADERS, M2_OPPORTUNITY_ROWS),
      },
    ],
  },
};
