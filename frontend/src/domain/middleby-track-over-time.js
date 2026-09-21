/**
 * Per-catalog "Track over time" content for the Middleby detail page.
 *
 * The Track-over-time tab used to render a shared "business monitor"
 * (Pipeline Health, Customer Experience, etc.) which showed KPIs and
 * hero charts for a group of use cases. That was confusing when a user
 * viewing the RFQ Funnel Intelligence use case saw Pipeline Health tiles
 * about consultation requests and dealer worklists — content not aligned
 * with the RFQ funnel question they were exploring.
 *
 * This module makes every use case's Track-over-time tab render content
 * SPECIFIC to that use case: a trend narrative that expands the answer
 * over the last 12 weeks, a metric-labelled weekly series, and 2-3
 * signals explaining what happened during the window.
 *
 * Content precedence:
 *   1. Hand-authored entry in TRACK_OVER_TIME_BY_ID
 *   2. Derived entry from the use case's insight-meta + primary spec
 *      (uses the use case's own title / question / metric name to build
 *      a plausible 12-week series so no use case falls back to a shared
 *      or generic view)
 */

import { getMiddlebyInsightMeta } from "./middleby-insight-meta";
import { getMiddlebyPrimarySpec } from "./middleby-screen-data";

// ── Layer 1: hand-authored entries (starts with uc-02 — the specific
//    use case the user flagged as showing wrong monitor content) ───────────
const TRACK_OVER_TIME_BY_ID = {
  "middleby-uc-02": {
    title: "RFQ Funnel Intelligence",
    question: "How has RFQ conversion changed over the last 12 weeks?",
    headline: "RFQ conversion has been sliding for 4 straight weeks - from 5.4% in early June to 4.0% for the week of Jul 17-23. The biggest single-week drop happened around Jun 21 when release r-2026-25 shipped.",
    metricLabel: "RFQ conversion rate (view-to-submit)",
    metricUnit: "%",
    weeklyPoints: [
      { week: "2026-05-01", value: 5.6 }, { week: "2026-05-08", value: 5.5 }, { week: "2026-05-15", value: 5.5 }, { week: "2026-05-22", value: 5.4 }, { week: "2026-05-29", value: 5.4 }, { week: "2026-06-05", value: 5.4 }, { week: "2026-06-12", value: 5.2 }, { week: "2026-06-19", value: 4.4 }, { week: "2026-06-26", value: 4.5 }, { week: "2026-07-03", value: 4.3 }, { week: "2026-07-10", value: 4.1 }, { week: "2026-07-17", value: 4.0 },
    ],
    signals: [
      { title: "Jun 19 to 26: -0.8 point drop", detail: "Coincides with the r-2026-25 release. LogRocket flagged an Add-to-Quote regression on iOS Safari the same day; top-of-funnel is unchanged but cart-to-form conversion is materially worse." },
      { title: "Recovery paused after week 4", detail: "The Safari hotfix landed Jun 30 but conversion did not fully rebound - suggests a second cause layered on top. See the Look-at-next chip 'UX errors on the drop-off steps' for LogRocket detail." },
      { title: "Traffic is flat, not the cause", detail: "GA sessions are within +/- 3% across all 12 weeks, so this is not a traffic-mix issue - it is a conversion issue at cart / form specifically." },
    ],
  },
  "middleby-uc-01": {
    title: "RFQ Intent Intelligence",
    question: "How has high-intent session volume trended over the last 12 weeks?",
    headline: "High-intent anonymous sessions have grown steadily from ~120/week in early May to ~185/week now, matching a rising interest signal even as RFQ conversion softened. Sales should follow up faster on the growing tier.",
    metricLabel: "High-intent sessions per week",
    metricUnit: "",
    weeklyPoints: [ { week: "2026-05-01", value: 118 }, { week: "2026-05-08", value: 122 }, { week: "2026-05-15", value: 131 }, { week: "2026-05-22", value: 138 }, { week: "2026-05-29", value: 144 }, { week: "2026-06-05", value: 152 }, { week: "2026-06-12", value: 158 }, { week: "2026-06-19", value: 162 }, { week: "2026-06-26", value: 170 }, { week: "2026-07-03", value: 176 }, { week: "2026-07-10", value: 181 }, { week: "2026-07-17", value: 185 } ],
    signals: [
      { title: "Interest is up while conversion is down", detail: "High-intent volume grew ~57% while the RFQ funnel conversion (uc-02) fell - meaning more qualified traffic is being lost at the cart / form stage than gained by intent alone." },
      { title: "Multi-brand browsing rose in weeks 6-10", detail: "The share of high-intent sessions that viewed 2+ brands climbed from 38% to 49% - a cross-sell signal worth activating in the ordering surface." },
    ],
  },
  "middleby-uc-03": {
    title: "Product Demand & Opportunity Discovery",
    question: "Which L1/L2 categories have gained or lost demand momentum in the last 12 weeks?",
    headline: "Refrigeration and Warewashing gained demand momentum (+22% and +14% respectively) while Cooking dipped 6%. Conversion is flat across categories, so demand shifts are trend drivers, not signal noise.",
    metricLabel: "Category demand index (portfolio = 100)",
    metricUnit: "",
    weeklyPoints: [ { week: "2026-05-01", value: 98 }, { week: "2026-05-08", value: 99 }, { week: "2026-05-15", value: 101 }, { week: "2026-05-22", value: 103 }, { week: "2026-05-29", value: 104 }, { week: "2026-06-05", value: 106 }, { week: "2026-06-12", value: 108 }, { week: "2026-06-19", value: 109 }, { week: "2026-06-26", value: 110 }, { week: "2026-07-03", value: 112 }, { week: "2026-07-10", value: 113 }, { week: "2026-07-17", value: 115 } ],
    signals: [
      { title: "Refrigeration accelerating", detail: "Refrigeration L1 grew from index 98 to 122 over the window, driven by True Manufacturing and Nor-Lake session share increases." },
      { title: "Cooking softening", detail: "Cooking L1 fell 6 points, concentrated in the Fryer L2 - which also has the lowest RFQ conversion in the portfolio." },
    ],
  },
  "middleby-uc-04": {
    title: "Sales Rep / Dealer Copilot",
    question: "How has urgent-quote volume moved for reps over the last 12 weeks?",
    headline: "Urgent-quote count climbed from 28 to 41 per week, with consultation-required cases growing faster than self-serve. Rep touch time is not keeping up.",
    metricLabel: "Urgent quotes (0-1 month timeframe)",
    metricUnit: "",
    weeklyPoints: [ { week: "2026-05-01", value: 28 }, { week: "2026-05-08", value: 29 }, { week: "2026-05-15", value: 31 }, { week: "2026-05-22", value: 32 }, { week: "2026-05-29", value: 34 }, { week: "2026-06-05", value: 35 }, { week: "2026-06-12", value: 36 }, { week: "2026-06-19", value: 37 }, { week: "2026-06-26", value: 38 }, { week: "2026-07-03", value: 39 }, { week: "2026-07-10", value: 40 }, { week: "2026-07-17", value: 41 } ],
    signals: [
      { title: "Consultation share rising", detail: "Consultation=yes flag grew from 8% to 15% of urgent quotes - premium buyers wanting a person, not a form." },
      { title: "Median hours-to-first-touch drifting up", detail: "First rep touch time climbed from 4.2 hours to 6.8 hours across the window - dropping below the internal 4-hour SLA." },
    ],
  },
  "middleby-uc-05": {
    title: "Customer Journey Visibility",
    question: "How has the browse-to-quote journey completion rate changed over 12 weeks?",
    headline: "Journey completion (view → add-to-quote → cart → form → submit) for the Pitco fryer family declined from 4.8% to 3.6%. The Add-to-Quote step is the biggest single weakener.",
    metricLabel: "Journey completion rate",
    metricUnit: "%",
    weeklyPoints: [ { week: "2026-05-01", value: 4.8 }, { week: "2026-05-08", value: 4.7 }, { week: "2026-05-15", value: 4.7 }, { week: "2026-05-22", value: 4.6 }, { week: "2026-05-29", value: 4.5 }, { week: "2026-06-05", value: 4.4 }, { week: "2026-06-12", value: 4.2 }, { week: "2026-06-19", value: 3.9 }, { week: "2026-06-26", value: 3.8 }, { week: "2026-07-03", value: 3.7 }, { week: "2026-07-10", value: 3.6 }, { week: "2026-07-17", value: 3.6 } ],
    signals: [
      { title: "Add-to-Quote step regression", detail: "Add-to-Quote click-through dropped from 34% to 26% - matches the LogRocket regression flagged in uc-08." },
      { title: "Product-family patterns diverge", detail: "Pitco is worst-hit; TurboChef held stable at 5.1% completion; Southbend actually improved by 8%." },
    ],
  },
  "middleby-uc-06": {
    title: "Dealer Performance & Revenue Leakage Analysis",
    question: "Which dealer regions have leaked the most pipeline over 12 weeks?",
    headline: "Total leaked pipeline grew from ~$180K to ~$310K per week. West-coast dealer regions account for 55% of the growth, driven by longer rep-follow-up gaps.",
    metricLabel: "Weekly leaked pipeline ($K)",
    metricUnit: "K",
    weeklyPoints: [ { week: "2026-05-01", value: 180 }, { week: "2026-05-08", value: 195 }, { week: "2026-05-15", value: 210 }, { week: "2026-05-22", value: 220 }, { week: "2026-05-29", value: 232 }, { week: "2026-06-05", value: 245 }, { week: "2026-06-12", value: 258 }, { week: "2026-06-19", value: 268 }, { week: "2026-06-26", value: 278 }, { week: "2026-07-03", value: 290 }, { week: "2026-07-10", value: 298 }, { week: "2026-07-17", value: 310 } ],
    signals: [
      { title: "West-coast leakage concentration", detail: "Regions 411 (LA) and 415 (SF) alone drove 55% of the week-over-week increase - both have vacant primary-rep seats." },
      { title: "Consultation cases most affected", detail: "70% of the leaked pipeline had consultation=yes flags - the highest-touch buyers are receiving the slowest response." },
    ],
  },
  "middleby-uc-07": {
    title: "Executive AI Business Analyst",
    question: "How have the executive KPIs (traffic, RFQs, conversion, pipeline) moved over 12 weeks?",
    headline: "Traffic +8%, RFQs -12%, conversion -18% - the classic executive concern: interest is up, output is down. Pipeline dollars kept climbing on deal-size mix, not quote count.",
    metricLabel: "Executive KPI composite (100 = baseline)",
    metricUnit: "",
    weeklyPoints: [ { week: "2026-05-01", value: 100 }, { week: "2026-05-08", value: 101 }, { week: "2026-05-15", value: 102 }, { week: "2026-05-22", value: 101 }, { week: "2026-05-29", value: 100 }, { week: "2026-06-05", value: 99 }, { week: "2026-06-12", value: 96 }, { week: "2026-06-19", value: 91 }, { week: "2026-06-26", value: 92 }, { week: "2026-07-03", value: 90 }, { week: "2026-07-10", value: 89 }, { week: "2026-07-17", value: 88 } ],
    signals: [
      { title: "Traffic-conversion divergence", detail: "This is the widest gap between sessions and RFQs in the last 12 months - and it opened on the same week r-2026-25 shipped." },
      { title: "Pipeline dollars still growing", detail: "Fewer quotes but bigger average deal size (+22%) keeps pipeline dollars up - masking the underlying conversion problem." },
    ],
  },
  "middleby-uc-08": {
    title: "UX / Experience Insights",
    question: "How have UX-driven error sessions trended over 12 weeks?",
    headline: "LogRocket-flagged error sessions grew from ~1,200/week in early May to ~4,100/week after the Jun 21 release. Add-to-Quote iOS Safari failure is the dominant issue.",
    metricLabel: "Error sessions per week",
    metricUnit: "",
    weeklyPoints: [ { week: "2026-05-01", value: 1220 }, { week: "2026-05-08", value: 1310 }, { week: "2026-05-15", value: 1380 }, { week: "2026-05-22", value: 1450 }, { week: "2026-05-29", value: 1550 }, { week: "2026-06-05", value: 1720 }, { week: "2026-06-12", value: 1980 }, { week: "2026-06-19", value: 3620 }, { week: "2026-06-26", value: 3980 }, { week: "2026-07-03", value: 4100 }, { week: "2026-07-10", value: 4080 }, { week: "2026-07-17", value: 4135 } ],
    signals: [
      { title: "Release-triggered spike", detail: "r-2026-25 on Jun 21 caused ~1,800 additional error sessions the same week - specifically the Add-to-Quote unresponsive issue on Safari." },
      { title: "Hotfix reduced but didn't eliminate", detail: "The Jun 30 patch cut ~600 sessions but the remaining ~3,500/week has plateaued - a second latent cause remains." },
    ],
  },
  "middleby-uc-09": {
    title: "Quote Form & Buyer Intent Mix",
    question: "How has the buyer intent-mix (0-1 mo vs consultation vs longer) shifted over 12 weeks?",
    headline: "Buy-now (0-1 month) share climbed from 34% to 42% of submitted quotes, and consultation requests rose from 7% to 12% - both premium signals - while long-horizon quotes softened.",
    metricLabel: "Share of quotes wanting 0-1 month delivery",
    metricUnit: "%",
    weeklyPoints: [ { week: "2026-05-01", value: 34 }, { week: "2026-05-08", value: 35 }, { week: "2026-05-15", value: 36 }, { week: "2026-05-22", value: 36 }, { week: "2026-05-29", value: 37 }, { week: "2026-06-05", value: 38 }, { week: "2026-06-12", value: 38 }, { week: "2026-06-19", value: 39 }, { week: "2026-06-26", value: 40 }, { week: "2026-07-03", value: 41 }, { week: "2026-07-10", value: 41 }, { week: "2026-07-17", value: 42 } ],
    signals: [
      { title: "Consultation demand rising", detail: "Consultation=yes flag climbed from 7% to 12% of submitted quotes - dealer team should expect more high-touch cases per rep this quarter." },
      { title: "Long-horizon share compressed", detail: "Quotes with 4-12 month timeframes fell from 41% to 32% - buyers are pulling forward decisions." },
    ],
  },
  "middleby-uc-10": {
    title: "Consultation-Led vs Self-Serve Pipeline",
    question: "How has consultation-led conversion moved vs self-serve over 12 weeks?",
    headline: "Consultation-led quotes convert at 38% vs 21% for self-serve - and the gap has widened from 12 points in May to 17 points now. Consultation is the highest-yield lead type.",
    metricLabel: "Consultation-led conversion rate",
    metricUnit: "%",
    weeklyPoints: [ { week: "2026-05-01", value: 34 }, { week: "2026-05-08", value: 34 }, { week: "2026-05-15", value: 35 }, { week: "2026-05-22", value: 35 }, { week: "2026-05-29", value: 36 }, { week: "2026-06-05", value: 36 }, { week: "2026-06-12", value: 36 }, { week: "2026-06-19", value: 37 }, { week: "2026-06-26", value: 37 }, { week: "2026-07-03", value: 37 }, { week: "2026-07-10", value: 38 }, { week: "2026-07-17", value: 38 } ],
    signals: [
      { title: "Consultation-lift widening", detail: "The consultation-vs-self-serve conversion gap grew from 12 to 17 percentage points - either consultation reps are getting better, or self-serve UX regressed." },
      { title: "Follow-up speed matters", detail: "Consultation cases with <2-hour response convert at 51% vs 26% for slower response - a coaching opportunity." },
    ],
  },
  "middleby-uc-11": {
    title: "Repeat vs New Buyer",
    question: "How has repeat-brand-buyer share moved over 12 weeks?",
    headline: "Repeat-brand buyers grew from 22% to 28% of submitted quotes - and their conversion is ~2.4x higher than first-time buyers. Loyalty is quietly compounding.",
    metricLabel: "Repeat-brand-buyer share",
    metricUnit: "%",
    weeklyPoints: [ { week: "2026-05-01", value: 22 }, { week: "2026-05-08", value: 23 }, { week: "2026-05-15", value: 24 }, { week: "2026-05-22", value: 24 }, { week: "2026-05-29", value: 25 }, { week: "2026-06-05", value: 25 }, { week: "2026-06-12", value: 26 }, { week: "2026-06-19", value: 26 }, { week: "2026-06-26", value: 27 }, { week: "2026-07-03", value: 27 }, { week: "2026-07-10", value: 27 }, { week: "2026-07-17", value: 28 } ],
    signals: [
      { title: "Conversion lift is real", detail: "Repeat buyers convert at 42% vs 18% for first-time - the return on repeat-buyer marketing is measurable and growing." },
      { title: "Portfolio distribution matters", detail: "Follett and TurboChef lead in repeat rate (~35%); Star and Wells lag (~15%) - a brand-tagging hygiene issue per uc-18." },
    ],
  },
  "middleby-uc-12": {
    title: "Install & Delivery Preferences by Region",
    question: "How has the install-heavy vs delivery-only preference split trended by region?",
    headline: "Install-heavy quote share is highest in the Southeast (31%) and Midwest (28%), and both regions grew ~5 points over the window. Install-partner capacity is the constraint to watch.",
    metricLabel: "Install-heavy quote share (national)",
    metricUnit: "%",
    weeklyPoints: [ { week: "2026-05-01", value: 22 }, { week: "2026-05-08", value: 22 }, { week: "2026-05-15", value: 23 }, { week: "2026-05-22", value: 23 }, { week: "2026-05-29", value: 24 }, { week: "2026-06-05", value: 24 }, { week: "2026-06-12", value: 25 }, { week: "2026-06-19", value: 25 }, { week: "2026-06-26", value: 26 }, { week: "2026-07-03", value: 26 }, { week: "2026-07-10", value: 27 }, { week: "2026-07-17", value: 27 } ],
    signals: [
      { title: "Southeast + Midwest concentration", detail: "These two regions drove ~70% of the install-share growth - typical for the fryer-heavy summer mix." },
      { title: "West-coast leaning delivery-only", detail: "California and Washington show <18% install preference - fits their smaller-format restaurant mix." },
    ],
  },
  "middleby-uc-13": {
    title: "Quote Pipeline by Brand, Region & Stage",
    question: "How has stalled pipeline moved over 12 weeks?",
    headline: "Stalled pipeline dollars grew from $520K to $840K, concentrated at the Proposal-Sent stage. Follett and Pitco account for 60% of the growth.",
    metricLabel: "Stalled pipeline dollars ($K)",
    metricUnit: "K",
    weeklyPoints: [ { week: "2026-05-01", value: 520 }, { week: "2026-05-08", value: 540 }, { week: "2026-05-15", value: 570 }, { week: "2026-05-22", value: 590 }, { week: "2026-05-29", value: 620 }, { week: "2026-06-05", value: 650 }, { week: "2026-06-12", value: 680 }, { week: "2026-06-19", value: 710 }, { week: "2026-06-26", value: 740 }, { week: "2026-07-03", value: 770 }, { week: "2026-07-10", value: 800 }, { week: "2026-07-17", value: 840 } ],
    signals: [
      { title: "Proposal-Sent stage stuck", detail: "Median days-in-stage grew from 9 to 14 - dealer teams are slower to move Proposal → Contract this quarter." },
      { title: "Follett and Pitco concentrated", detail: "These two brands account for 60% of the stalled dollars - both have known dealer-coverage gaps in the Southeast." },
    ],
  },
  "middleby-uc-14": {
    title: "Marketing Channel & Traffic Quality",
    question: "How has channel-quality shifted over 12 weeks?",
    headline: "Organic search grew its share of deep-engagement sessions from 42% to 51%. Paid social's share halved (18% → 9%) with the same spend - efficiency dropped.",
    metricLabel: "Organic share of high-engagement sessions",
    metricUnit: "%",
    weeklyPoints: [ { week: "2026-05-01", value: 42 }, { week: "2026-05-08", value: 43 }, { week: "2026-05-15", value: 44 }, { week: "2026-05-22", value: 44 }, { week: "2026-05-29", value: 45 }, { week: "2026-06-05", value: 46 }, { week: "2026-06-12", value: 47 }, { week: "2026-06-19", value: 48 }, { week: "2026-06-26", value: 49 }, { week: "2026-07-03", value: 50 }, { week: "2026-07-10", value: 50 }, { week: "2026-07-17", value: 51 } ],
    signals: [
      { title: "Paid social efficiency dropped", detail: "Same budget, half the deep-engagement sessions - creative fatigue on the top-3 campaigns is the leading hypothesis." },
      { title: "Referral rising quietly", detail: "Referral traffic share grew from 6% to 9% - a signal to double down on the dealer-referral program." },
    ],
  },
  "middleby-uc-15": {
    title: "Multi-Brand Portfolio Interest",
    question: "How has multi-brand session share moved over 12 weeks?",
    headline: "Multi-brand sessions rose from 24% to 31% of qualified sessions. TurboChef + Southbend and Follett + True are the most-frequent pairs.",
    metricLabel: "Sessions viewing 2+ brands (%)",
    metricUnit: "%",
    weeklyPoints: [ { week: "2026-05-01", value: 24 }, { week: "2026-05-08", value: 25 }, { week: "2026-05-15", value: 25 }, { week: "2026-05-22", value: 26 }, { week: "2026-05-29", value: 26 }, { week: "2026-06-05", value: 27 }, { week: "2026-06-12", value: 28 }, { week: "2026-06-19", value: 28 }, { week: "2026-06-26", value: 29 }, { week: "2026-07-03", value: 30 }, { week: "2026-07-10", value: 30 }, { week: "2026-07-17", value: 31 } ],
    signals: [
      { title: "Cross-brand cross-sell opportunity", detail: "TurboChef + Southbend co-visits alone represent ~$400K in quote pipeline potential per quarter." },
      { title: "Portfolio nav is under-used", detail: "Only 12% of multi-brand sessions use the site nav; most arrive at a second brand via search - a UX opportunity." },
    ],
  },
  "middleby-uc-16": {
    title: "Executive Summary / Weekly Insights",
    question: "How many briefing-worthy shifts have surfaced per week?",
    headline: "Briefing-worthy shifts (defined as >5% week-over-week move) rose from an average of 3 to 6 per week - reflecting the increased volatility around the Jun release + summer buying push.",
    metricLabel: "Briefing-worthy shifts per week",
    metricUnit: "",
    weeklyPoints: [ { week: "2026-05-01", value: 3 }, { week: "2026-05-08", value: 3 }, { week: "2026-05-15", value: 3 }, { week: "2026-05-22", value: 3 }, { week: "2026-05-29", value: 4 }, { week: "2026-06-05", value: 4 }, { week: "2026-06-12", value: 4 }, { week: "2026-06-19", value: 5 }, { week: "2026-06-26", value: 5 }, { week: "2026-07-03", value: 6 }, { week: "2026-07-10", value: 6 }, { week: "2026-07-17", value: 6 } ],
    signals: [
      { title: "Volatility clustering", detail: "The Jun 21 release week generated 8 briefing-worthy shifts in a single window - the widest since the platform launched." },
      { title: "Most shifts trace to conversion", detail: "5 of 6 shifts this week are conversion-related, not traffic - matching the pattern flagged by uc-07." },
    ],
  },
  "middleby-uc-17": {
    title: "Executive NL Prompt to Chart",
    question: "How many NL prompts have been answered per week?",
    headline: "NL prompt volume grew from ~8/week to ~22/week as leadership got comfortable with the surface. Roughly 78% of prompts resolve to a chart on first try.",
    metricLabel: "NL prompts answered per week",
    metricUnit: "",
    weeklyPoints: [ { week: "2026-05-01", value: 8 }, { week: "2026-05-08", value: 9 }, { week: "2026-05-15", value: 11 }, { week: "2026-05-22", value: 12 }, { week: "2026-05-29", value: 13 }, { week: "2026-06-05", value: 15 }, { week: "2026-06-12", value: 16 }, { week: "2026-06-19", value: 18 }, { week: "2026-06-26", value: 19 }, { week: "2026-07-03", value: 20 }, { week: "2026-07-10", value: 21 }, { week: "2026-07-17", value: 22 } ],
    signals: [
      { title: "First-try success rate", detail: "78% of prompts resolve to a chart on first try. Most misses ask about HubSpot fields not yet in the schema map." },
      { title: "Prompt topics shifting", detail: "Early prompts asked for traffic totals; now more prompts ask about brand cross-sell and dealer performance - deeper engagement." },
    ],
  },
  "middleby-uc-18": {
    title: "Product Interest vs RFQ Gap Analysis",
    question: "How has the browse-to-quote gap evolved by brand over 12 weeks?",
    headline: "The portfolio-weighted browse-to-quote gap widened from 0.6% to 1.1%, driven by growing traffic on brands with limited RFQ tagging. Globe and Star are the biggest widening pairs.",
    metricLabel: "Portfolio-weighted browse-to-quote gap",
    metricUnit: "%",
    weeklyPoints: [ { week: "2026-05-01", value: 0.6 }, { week: "2026-05-08", value: 0.6 }, { week: "2026-05-15", value: 0.7 }, { week: "2026-05-22", value: 0.7 }, { week: "2026-05-29", value: 0.8 }, { week: "2026-06-05", value: 0.8 }, { week: "2026-06-12", value: 0.9 }, { week: "2026-06-19", value: 0.9 }, { week: "2026-06-26", value: 1.0 }, { week: "2026-07-03", value: 1.0 }, { week: "2026-07-10", value: 1.1 }, { week: "2026-07-17", value: 1.1 } ],
    signals: [
      { title: "Globe and Star lag", detail: "Both brands have <60% deal-tagging completeness, so their observed quote counts undershoot true demand by ~10-15%." },
      { title: "Follett leads on hygiene", detail: "Follett tags 98% of its deals; used as the tagging benchmark for the portfolio hygiene sweep." },
    ],
  },
  "middleby-uc-19": {
    title: "Catalog / SKU Gap - High Traffic, Low Quotes",
    question: "How many high-browse / low-quote SKU outliers have appeared per week?",
    headline: "Outlier SKUs grew from 47 to 72 over the window - about 40% of them concentrate in the Fryer L2. PDP content quality is the leading hypothesis.",
    metricLabel: "High-browse / low-quote outlier SKUs",
    metricUnit: "",
    weeklyPoints: [ { week: "2026-05-01", value: 47 }, { week: "2026-05-08", value: 49 }, { week: "2026-05-15", value: 52 }, { week: "2026-05-22", value: 54 }, { week: "2026-05-29", value: 57 }, { week: "2026-06-05", value: 59 }, { week: "2026-06-12", value: 62 }, { week: "2026-06-19", value: 64 }, { week: "2026-06-26", value: 66 }, { week: "2026-07-03", value: 68 }, { week: "2026-07-10", value: 70 }, { week: "2026-07-17", value: 72 } ],
    signals: [
      { title: "Fryer L2 concentration", detail: "40% of outliers are Fryer SKUs - the same L2 where uc-21 flags PDP content quality issues." },
      { title: "Discontinued but indexed", detail: "12% of outliers are actually discontinued SKUs still ranking on organic search - an SEO cleanup task." },
    ],
  },
  "middleby-uc-20": {
    title: "Post-Release Regression Watch",
    question: "How have release-linked error rates trended over 12 weeks?",
    headline: "Release-linked error rate stayed near baseline through late May, then spiked from 2.1% to 5.8% following r-2026-25 on Jun 21. The Jun 30 hotfix reduced but did not fully clear it.",
    metricLabel: "Release-linked error rate",
    metricUnit: "%",
    weeklyPoints: [ { week: "2026-05-01", value: 2.0 }, { week: "2026-05-08", value: 2.1 }, { week: "2026-05-15", value: 2.0 }, { week: "2026-05-22", value: 2.1 }, { week: "2026-05-29", value: 2.1 }, { week: "2026-06-05", value: 2.2 }, { week: "2026-06-12", value: 2.4 }, { week: "2026-06-19", value: 5.8 }, { week: "2026-06-26", value: 5.2 }, { week: "2026-07-03", value: 4.6 }, { week: "2026-07-10", value: 4.5 }, { week: "2026-07-17", value: 4.4 } ],
    signals: [
      { title: "r-2026-25 impact", detail: "Add-to-Quote on iOS Safari - traced via LogRocket JS stack. The single deploy caused the largest release-linked error spike this year." },
      { title: "Hotfix incomplete", detail: "The Jun 30 patch cut ~1.2 percentage points, but the residual 4.4% has plateaued - a follow-on cause remains." },
    ],
  },
  "middleby-uc-21": {
    title: "Product Page Content Effectiveness",
    question: "How has PDP view-to-quote conversion moved over 12 weeks?",
    headline: "Fleet-average PDP conversion fell from 3.2% to 2.6% - concentrated in Fryer and Cooking-suite L2s. Content freshness on 40+ hero PDPs is the leading candidate.",
    metricLabel: "Fleet PDP view-to-quote conversion",
    metricUnit: "%",
    weeklyPoints: [ { week: "2026-05-01", value: 3.2 }, { week: "2026-05-08", value: 3.1 }, { week: "2026-05-15", value: 3.1 }, { week: "2026-05-22", value: 3.0 }, { week: "2026-05-29", value: 3.0 }, { week: "2026-06-05", value: 2.9 }, { week: "2026-06-12", value: 2.9 }, { week: "2026-06-19", value: 2.8 }, { week: "2026-06-26", value: 2.7 }, { week: "2026-07-03", value: 2.7 }, { week: "2026-07-10", value: 2.6 }, { week: "2026-07-17", value: 2.6 } ],
    signals: [
      { title: "40+ hero PDPs stale", detail: "Content was last refreshed >6 months ago for these pages - a systematic refresh cycle would recover ~$180K in quote pipeline." },
      { title: "Fryer L2 hit hardest", detail: "Same L2 also flagged by uc-19 for catalog gaps - the two views point at the same root-cause set." },
    ],
  },
  "middleby-uc-22": {
    title: "Anonymous High-Intent Session Segments",
    question: "How many hidden-intent sessions have we detected per week over 12 weeks?",
    headline: "Detected hidden-intent segments grew from ~280 sessions/week to ~460/week. Their downstream conversion (when re-engaged) is ~14% - comparable to known warm leads.",
    metricLabel: "Hidden-intent sessions detected per week",
    metricUnit: "",
    weeklyPoints: [ { week: "2026-05-01", value: 280 }, { week: "2026-05-08", value: 295 }, { week: "2026-05-15", value: 310 }, { week: "2026-05-22", value: 325 }, { week: "2026-05-29", value: 340 }, { week: "2026-06-05", value: 360 }, { week: "2026-06-12", value: 380 }, { week: "2026-06-19", value: 395 }, { week: "2026-06-26", value: 410 }, { week: "2026-07-03", value: 425 }, { week: "2026-07-10", value: 445 }, { week: "2026-07-17", value: 460 } ],
    signals: [
      { title: "Growing untapped pool", detail: "460 sessions/week × 14% re-engagement conversion × ~$3.4K average deal = ~$220K in weekly hidden pipeline." },
      { title: "Retargeting opt-in is the constraint", detail: "Only ~35% of sessions carry the retargeting-consent flag - activation is bottlenecked on consent capture." },
    ],
  },
  "middleby-uc-23": {
    title: "Unified Analytics vs Siloed Tools",
    question: "How many cross-tool divergences have appeared per week?",
    headline: "Cross-tool divergence events (GA / LogRocket / HubSpot pointing opposite ways) grew from ~4/week to ~11/week - proof that stitching matters more, not less, as volume grows.",
    metricLabel: "Cross-tool divergence events per week",
    metricUnit: "",
    weeklyPoints: [ { week: "2026-05-01", value: 4 }, { week: "2026-05-08", value: 4 }, { week: "2026-05-15", value: 5 }, { week: "2026-05-22", value: 6 }, { week: "2026-05-29", value: 6 }, { week: "2026-06-05", value: 7 }, { week: "2026-06-12", value: 8 }, { week: "2026-06-19", value: 9 }, { week: "2026-06-26", value: 9 }, { week: "2026-07-03", value: 10 }, { week: "2026-07-10", value: 11 }, { week: "2026-07-17", value: 11 } ],
    signals: [
      { title: "Divergence class shifting", detail: "Early-window divergences were mostly traffic vs errors; recent divergences are traffic vs conversion - more strategic than diagnostic." },
      { title: "Enterprise GPT reconciliation value", detail: "Each divergence saves ~90 minutes of executive analyst reconciliation time - approximate multiplier of the annual saving." },
    ],
  },
  "middleby-uc-24": {
    title: "Session Replay Linked to HubSpot Quote",
    question: "How has the join-coverage between GA / LogRocket / HubSpot moved over 12 weeks?",
    headline: "The share of active HubSpot deals with a complete four-system stitch grew from 12% to 19% as the id-mapping backfill rolled out. Full coverage requires a shared identifier written into each system.",
    metricLabel: "Deals with four-system stitch (%)",
    metricUnit: "%",
    weeklyPoints: [ { week: "2026-05-01", value: 12 }, { week: "2026-05-08", value: 12 }, { week: "2026-05-15", value: 13 }, { week: "2026-05-22", value: 13 }, { week: "2026-05-29", value: 14 }, { week: "2026-06-05", value: 14 }, { week: "2026-06-12", value: 15 }, { week: "2026-06-19", value: 16 }, { week: "2026-06-26", value: 17 }, { week: "2026-07-03", value: 17 }, { week: "2026-07-10", value: 18 }, { week: "2026-07-17", value: 19 } ],
    signals: [
      { title: "Backfill working", detail: "The email-hash backfill added ~1 point of coverage per week - steady progress; full coverage projected by end of Q3." },
      { title: "Salesforce join still weakest", detail: "The GA-HubSpot stitch is at 42%; the LogRocket-Salesforce stitch is at 19% - Salesforce is the coverage bottleneck." },
    ],
  },
  "middleby-uc-25": {
    title: "Multi-Product Quote Basket & Attach Rates",
    question: "How has the multi-line quote attach rate moved over 12 weeks?",
    headline: "Attach rate (share of RFQ carts with 2+ SKU lines) grew from 18% to 26% - a strong cross-sell signal. TurboChef + Southbend is the fastest-growing pair.",
    metricLabel: "Multi-line RFQ cart share",
    metricUnit: "%",
    weeklyPoints: [ { week: "2026-05-01", value: 18 }, { week: "2026-05-08", value: 19 }, { week: "2026-05-15", value: 20 }, { week: "2026-05-22", value: 20 }, { week: "2026-05-29", value: 21 }, { week: "2026-06-05", value: 22 }, { week: "2026-06-12", value: 22 }, { week: "2026-06-19", value: 23 }, { week: "2026-06-26", value: 24 }, { week: "2026-07-03", value: 24 }, { week: "2026-07-10", value: 25 }, { week: "2026-07-17", value: 26 } ],
    signals: [
      { title: "TurboChef + Southbend surging", detail: "This pair grew from ~8 co-quotes/week to ~22/week - large enough to warrant a dedicated cart-side recommendation." },
      { title: "Cart abandonment steady", detail: "Multi-line carts abandon at the same rate as single-line - no evidence that 'more products = more friction'." },
    ],
  },
  "middleby-uc-26": {
    title: "Guest vs Registered vs Sales-Rep Quote Paths",
    question: "How has the buyer-path mix moved over 12 weeks?",
    headline: "Guest quotes grew from 42% to 51% of submissions - a signal that ungated experiences are winning traffic. Sales-rep-originated share held steady at ~14%.",
    metricLabel: "Guest quote share (%)",
    metricUnit: "%",
    weeklyPoints: [ { week: "2026-05-01", value: 42 }, { week: "2026-05-08", value: 43 }, { week: "2026-05-15", value: 44 }, { week: "2026-05-22", value: 45 }, { week: "2026-05-29", value: 45 }, { week: "2026-06-05", value: 46 }, { week: "2026-06-12", value: 47 }, { week: "2026-06-19", value: 48 }, { week: "2026-06-26", value: 48 }, { week: "2026-07-03", value: 49 }, { week: "2026-07-10", value: 50 }, { week: "2026-07-17", value: 51 } ],
    signals: [
      { title: "Guest conversion lifted", detail: "Guest-quote conversion improved from 17% to 21% over the window - the streamlined RFQ form is paying off." },
      { title: "Registered share compressing", detail: "Registered buyers dropped from 44% to 35% of submissions - suggests the value of the account isn't being communicated." },
    ],
  },
  "middleby-uc-27": {
    title: "Cross-Sell Within Quote Cart",
    question: "How has cross-sell recommendation acceptance trended over 12 weeks?",
    headline: "Cart-side recommendation acceptance rose from ~4% to ~11% as the recommendation-model tuning matured. Attach revenue impact: ~$80K/week in incremental pipeline.",
    metricLabel: "Cart recommendation acceptance rate",
    metricUnit: "%",
    weeklyPoints: [ { week: "2026-05-01", value: 4 }, { week: "2026-05-08", value: 4 }, { week: "2026-05-15", value: 5 }, { week: "2026-05-22", value: 6 }, { week: "2026-05-29", value: 6 }, { week: "2026-06-05", value: 7 }, { week: "2026-06-12", value: 8 }, { week: "2026-06-19", value: 8 }, { week: "2026-06-26", value: 9 }, { week: "2026-07-03", value: 10 }, { week: "2026-07-10", value: 10 }, { week: "2026-07-17", value: 11 } ],
    signals: [
      { title: "Tuning payoff", detail: "The May 15 recommendation model update lifted acceptance ~3 points - taxonomy-adjacency was the biggest single lever." },
      { title: "Dealer-preferred bundles perform best", detail: "Pairs from the dealer-preferred bundle list convert at 2.4x the rate of raw co-quote pairs." },
    ],
  },
  "middleby-ex-01": {
    title: "RFQ Conversion Decline Analysis",
    question: "How does this weekly decline pattern compare to the prior 4-week baseline?",
    headline: "Conversion has declined 4 weeks running, with a cumulative drop of ~1.4 percentage points. Every driver traces back to the Jun 21 release + a Safari-cart regression that partial-fixed but did not fully clear.",
    metricLabel: "Weekly conversion vs 4-week baseline",
    metricUnit: "%",
    weeklyPoints: [ { week: "2026-05-01", value: 0.1 }, { week: "2026-05-08", value: 0.0 }, { week: "2026-05-15", value: 0.0 }, { week: "2026-05-22", value: -0.1 }, { week: "2026-05-29", value: -0.1 }, { week: "2026-06-05", value: -0.2 }, { week: "2026-06-12", value: -0.4 }, { week: "2026-06-19", value: -1.2 }, { week: "2026-06-26", value: -1.1 }, { week: "2026-07-03", value: -1.3 }, { week: "2026-07-10", value: -1.3 }, { week: "2026-07-17", value: -1.4 } ],
    signals: [
      { title: "Primary driver: r-2026-25 release", detail: "Safari cart regression - contributed ~-0.8 percentage points across the whole 4-week decline." },
      { title: "Secondary: PDP content aging", detail: "40+ hero PDPs weren't refreshed in >6 months (see uc-21) - accounts for the remaining -0.5 percentage points." },
    ],
  },
  "middleby-ex-02": {
    title: "Product Interest to Revenue Attribution",
    question: "How has interest-to-revenue conversion trended by brand over 12 weeks?",
    headline: "Portfolio-average interest-to-revenue conversion held roughly flat (0.7% → 0.6%), but variance widened - Follett doubled to 4.5% while Globe halved to 0.2%. Portfolio hygiene is the fix.",
    metricLabel: "Interest-to-revenue conversion (portfolio avg)",
    metricUnit: "%",
    weeklyPoints: [ { week: "2026-05-01", value: 0.7 }, { week: "2026-05-08", value: 0.7 }, { week: "2026-05-15", value: 0.7 }, { week: "2026-05-22", value: 0.7 }, { week: "2026-05-29", value: 0.7 }, { week: "2026-06-05", value: 0.7 }, { week: "2026-06-12", value: 0.7 }, { week: "2026-06-19", value: 0.6 }, { week: "2026-06-26", value: 0.6 }, { week: "2026-07-03", value: 0.6 }, { week: "2026-07-10", value: 0.6 }, { week: "2026-07-17", value: 0.6 } ],
    signals: [
      { title: "Follett doubled", detail: "Follett interest-to-revenue conversion grew from 2.2% to 4.5% - largely a hygiene story (deal-tag completeness lifted from 91% to 98%)." },
      { title: "Globe halved", detail: "Globe fell from 0.4% to 0.2% - traffic held but tagged quotes fell; a data-hygiene fix would recover most of it." },
    ],
  },
  "middleby-ex-03": {
    title: "Lead Quality & Sales Follow-up Effectiveness",
    question: "How many high-quality leads breached the response SLA over 12 weeks?",
    headline: "SLA-breaching high-quality leads grew from ~14 to ~28 per week. Most breaches trace to two rep queues that are chronically overloaded.",
    metricLabel: "High-quality leads over SLA (per week)",
    metricUnit: "",
    weeklyPoints: [ { week: "2026-05-01", value: 14 }, { week: "2026-05-08", value: 15 }, { week: "2026-05-15", value: 16 }, { week: "2026-05-22", value: 17 }, { week: "2026-05-29", value: 18 }, { week: "2026-06-05", value: 20 }, { week: "2026-06-12", value: 21 }, { week: "2026-06-19", value: 22 }, { week: "2026-06-26", value: 24 }, { week: "2026-07-03", value: 25 }, { week: "2026-07-10", value: 27 }, { week: "2026-07-17", value: 28 } ],
    signals: [
      { title: "Two queues carry 70%", detail: "Queues 411-Fryer and 413-Cooking-Suite - both understaffed by ~1 rep for the summer surge." },
      { title: "Loss-to-competitor rising", detail: "SLA-breaching leads are 32% more likely to close-lost with 'competitor selected' than in-SLA leads." },
    ],
  },
  "middleby-ex-04": {
    title: "Site Search & Product Discovery Optimization",
    question: "How has zero-result search rate trended over 12 weeks?",
    headline: "Zero-result search rate held at ~8% through May, then climbed to ~13% after the catalog migration on Jun 12. Legacy-URL redirects and taxonomy remaps are the remediation.",
    metricLabel: "Zero-result search rate",
    metricUnit: "%",
    weeklyPoints: [ { week: "2026-05-01", value: 8 }, { week: "2026-05-08", value: 8 }, { week: "2026-05-15", value: 8 }, { week: "2026-05-22", value: 8 }, { week: "2026-05-29", value: 9 }, { week: "2026-06-05", value: 9 }, { week: "2026-06-12", value: 11 }, { week: "2026-06-19", value: 13 }, { week: "2026-06-26", value: 13 }, { week: "2026-07-03", value: 13 }, { week: "2026-07-10", value: 13 }, { week: "2026-07-17", value: 13 } ],
    signals: [
      { title: "Catalog migration side-effect", detail: "The Jun 12 catalog migration removed ~500 legacy URLs without redirect - the immediate cause of the zero-result rise." },
      { title: "Top-10 misses concentrate", detail: "The top 10 zero-result queries account for ~40% of the affected search volume - a targeted synonym / redirect pass would recover most." },
    ],
  },
  "middleby-ex-05": {
    title: "Category Revenue-at-Risk Analysis",
    question: "How has at-risk category pipeline moved over 12 weeks?",
    headline: "At-risk pipeline dollars grew from ~$620K to ~$980K, concentrated in Cooking (55%) and Fryer (25%). Both categories intersect with the r-2026-25 release effect.",
    metricLabel: "Category at-risk pipeline ($K)",
    metricUnit: "K",
    weeklyPoints: [ { week: "2026-05-01", value: 620 }, { week: "2026-05-08", value: 650 }, { week: "2026-05-15", value: 680 }, { week: "2026-05-22", value: 710 }, { week: "2026-05-29", value: 740 }, { week: "2026-06-05", value: 770 }, { week: "2026-06-12", value: 810 }, { week: "2026-06-19", value: 850 }, { week: "2026-06-26", value: 890 }, { week: "2026-07-03", value: 920 }, { week: "2026-07-10", value: 950 }, { week: "2026-07-17", value: 980 } ],
    signals: [
      { title: "Cooking + Fryer 80% of growth", detail: "These two categories drove 80% of the at-risk-pipeline growth - matching the categories most affected by r-2026-25." },
      { title: "Stage concentration", detail: "72% of at-risk dollars sit at the Proposal-Sent stage - dealer follow-through is the top intervention." },
    ],
  },
  "middleby-ex-06": {
    title: "Executive Growth Intelligence Dashboard",
    question: "How has the executive growth composite moved over 12 weeks?",
    headline: "The composite growth index (traffic + RFQs + pipeline dollars, normalized) declined from 102 to 88 - the RFQ and conversion decline outweighed the traffic and pipeline-dollar gains.",
    metricLabel: "Executive growth composite (100 = baseline)",
    metricUnit: "",
    weeklyPoints: [ { week: "2026-05-01", value: 102 }, { week: "2026-05-08", value: 103 }, { week: "2026-05-15", value: 103 }, { week: "2026-05-22", value: 102 }, { week: "2026-05-29", value: 101 }, { week: "2026-06-05", value: 100 }, { week: "2026-06-12", value: 98 }, { week: "2026-06-19", value: 93 }, { week: "2026-06-26", value: 92 }, { week: "2026-07-03", value: 91 }, { week: "2026-07-10", value: 89 }, { week: "2026-07-17", value: 88 } ],
    signals: [
      { title: "Deal-size mix masking", detail: "Bigger average deal size (+22%) kept pipeline dollars from falling - but that reflects buyer mix, not sales-force improvement." },
      { title: "Conversion is the lever", detail: "Every 1-point conversion recovery would restore ~4 points of composite - the highest-leverage single fix." },
    ],
  },
  "middleby-ns-01": {
    title: "Revenue Recovery Simulator",
    question: "How has the recoverable-pipeline estimate moved over 12 weeks?",
    headline: "Recoverable pipeline from the top-3 site fixes grew from ~$180K to ~$450K per week - roughly matching the growing loss rate from the Jun 21 release plus PDP degradation.",
    metricLabel: "Recoverable pipeline from top-3 fixes ($K/week)",
    metricUnit: "K",
    weeklyPoints: [ { week: "2026-05-01", value: 180 }, { week: "2026-05-08", value: 195 }, { week: "2026-05-15", value: 215 }, { week: "2026-05-22", value: 240 }, { week: "2026-05-29", value: 265 }, { week: "2026-06-05", value: 290 }, { week: "2026-06-12", value: 315 }, { week: "2026-06-19", value: 350 }, { week: "2026-06-26", value: 380 }, { week: "2026-07-03", value: 405 }, { week: "2026-07-10", value: 425 }, { week: "2026-07-17", value: 450 } ],
    signals: [
      { title: "Fix #1: Safari cart", detail: "The remaining Safari cart regression is worth ~$220K/week if fully cleared - the largest single fix in the list." },
      { title: "Fix #2: PDP refresh", detail: "40+ hero PDPs refresh worth ~$130K/week in additional conversion." },
      { title: "Fix #3: Search redirects", detail: "Zero-result query cleanup worth ~$100K/week via recovered discovery." },
    ],
  },
  "middleby-ns-02": {
    title: "Next Best Actions for Sales",
    question: "How has the actionable-accounts count trended over 12 weeks?",
    headline: "AI-ranked actionable accounts grew from ~45 to ~72 per week. Rep engagement on the top-10 rose from 22% to 41% - the tool is being adopted.",
    metricLabel: "Actionable accounts identified per week",
    metricUnit: "",
    weeklyPoints: [ { week: "2026-05-01", value: 45 }, { week: "2026-05-08", value: 48 }, { week: "2026-05-15", value: 51 }, { week: "2026-05-22", value: 54 }, { week: "2026-05-29", value: 56 }, { week: "2026-06-05", value: 58 }, { week: "2026-06-12", value: 60 }, { week: "2026-06-19", value: 63 }, { week: "2026-06-26", value: 65 }, { week: "2026-07-03", value: 67 }, { week: "2026-07-10", value: 69 }, { week: "2026-07-17", value: 72 } ],
    signals: [
      { title: "Rep engagement growing", detail: "Rep engagement on the top-10 daily worklist rose from 22% to 41% - the tool is delivering habitual value." },
      { title: "Win-rate lift on acted accounts", detail: "Accounts touched within 24h of appearing on the list convert at 34% vs 21% for untouched - a real, measurable ROI." },
    ],
  },
  "middleby-ns-03": {
    title: "Brand Momentum Radar",
    question: "How has the brand-momentum spread across the portfolio evolved?",
    headline: "The gap between the fastest-rising brand (Follett, +18%) and the fastest-falling (Globe, -14%) widened to 32 points from 14 in early May - a sign that the portfolio is polarizing.",
    metricLabel: "Momentum spread (top brand - bottom brand, %)",
    metricUnit: "pts",
    weeklyPoints: [ { week: "2026-05-01", value: 14 }, { week: "2026-05-08", value: 15 }, { week: "2026-05-15", value: 17 }, { week: "2026-05-22", value: 19 }, { week: "2026-05-29", value: 21 }, { week: "2026-06-05", value: 23 }, { week: "2026-06-12", value: 25 }, { week: "2026-06-19", value: 27 }, { week: "2026-06-26", value: 29 }, { week: "2026-07-03", value: 30 }, { week: "2026-07-10", value: 31 }, { week: "2026-07-17", value: 32 } ],
    signals: [
      { title: "Follett accelerating", detail: "Follett grew +18% weekly quotes over the window - PDP investment + dealer-support push are the leading drivers." },
      { title: "Globe underperforming", detail: "Globe fell -14% - a portfolio hygiene sweep on deal-tagging would recover ~40% of the reported decline." },
    ],
  },
  "middleby-ns-04": {
    title: "Pipeline Forecast and What-If",
    question: "How has the forward-4-week pipeline forecast moved over 12 weeks?",
    headline: "The 4-week-ahead pipeline forecast has been softening for 5 straight weeks. Baseline projects $2.1M for the coming month; a 5-point conversion recovery scenario projects $2.7M.",
    metricLabel: "4-week-ahead pipeline forecast ($K)",
    metricUnit: "K",
    weeklyPoints: [ { week: "2026-05-01", value: 2280 }, { week: "2026-05-08", value: 2260 }, { week: "2026-05-15", value: 2250 }, { week: "2026-05-22", value: 2240 }, { week: "2026-05-29", value: 2220 }, { week: "2026-06-05", value: 2210 }, { week: "2026-06-12", value: 2190 }, { week: "2026-06-19", value: 2160 }, { week: "2026-06-26", value: 2140 }, { week: "2026-07-03", value: 2120 }, { week: "2026-07-10", value: 2110 }, { week: "2026-07-17", value: 2100 } ],
    signals: [
      { title: "Baseline continues down", detail: "The forecast has softened for 5 straight weeks. Without intervention, the base case is $2.1M vs the $2.5M plan." },
      { title: "5-point conversion recovery scenario", detail: "Fixing the Safari cart + PDP refresh lifts forecast to $2.7M - closing the plan gap and adding ~$200K buffer." },
    ],
  },
  "middleby-kpi-01": {
    title: "Traffic Source and Device Analysis",
    question: "How has device / channel conversion moved over 12 weeks?",
    headline: "Desktop conversion is stable (~5.4%); mobile fell from 3.1% to 2.4% following the Jun release - matching the Safari cart regression impact on mobile-heavy browsing.",
    metricLabel: "Mobile RFQ conversion",
    metricUnit: "%",
    weeklyPoints: [ { week: "2026-05-01", value: 3.1 }, { week: "2026-05-08", value: 3.1 }, { week: "2026-05-15", value: 3.0 }, { week: "2026-05-22", value: 3.0 }, { week: "2026-05-29", value: 2.9 }, { week: "2026-06-05", value: 2.9 }, { week: "2026-06-12", value: 2.8 }, { week: "2026-06-19", value: 2.5 }, { week: "2026-06-26", value: 2.5 }, { week: "2026-07-03", value: 2.4 }, { week: "2026-07-10", value: 2.4 }, { week: "2026-07-17", value: 2.4 } ],
    signals: [
      { title: "Mobile-safari specific", detail: "Chrome mobile held near-flat; Safari mobile did all the falling - the release regression pattern." },
      { title: "Desktop stable through the release", detail: "Desktop RFQ conversion barely moved - suggests the fix has an obvious device-scoped landing zone." },
    ],
  },
  "middleby-kpi-02": {
    title: "Closed-Won Rate and Lost Reasons",
    question: "How has closed-won rate moved over 12 weeks?",
    headline: "Closed-won rate slipped from 34% to 31% over the window. 'Competitor selected' and 'timeline mismatch' account for ~70% of the loss growth.",
    metricLabel: "Closed-won rate",
    metricUnit: "%",
    weeklyPoints: [ { week: "2026-05-01", value: 34 }, { week: "2026-05-08", value: 34 }, { week: "2026-05-15", value: 34 }, { week: "2026-05-22", value: 33 }, { week: "2026-05-29", value: 33 }, { week: "2026-06-05", value: 33 }, { week: "2026-06-12", value: 32 }, { week: "2026-06-19", value: 32 }, { week: "2026-06-26", value: 32 }, { week: "2026-07-03", value: 32 }, { week: "2026-07-10", value: 31 }, { week: "2026-07-17", value: 31 } ],
    signals: [
      { title: "'Competitor selected' rising", detail: "Competitive-loss rate grew from 12% to 17% of closed-lost - a signal that price-index views (uc-79 in retail) matter here too." },
      { title: "Timeline-mismatch also growing", detail: "Buyers wanting shorter timeframes with longer-lead brands - a matching problem, not necessarily a fix." },
    ],
  },
  "middleby-kpi-03": {
    title: "Live Chat Buy Intent",
    question: "How has buy-intent chat volume moved over 12 weeks?",
    headline: "'Can I buy' / 'buy now online' chats grew from ~26 to ~48 per week - roughly 2x. Chat-to-quote conversion held at ~19%, so the incremental volume is real pipeline.",
    metricLabel: "Buy-intent chats per week",
    metricUnit: "",
    weeklyPoints: [ { week: "2026-05-01", value: 26 }, { week: "2026-05-08", value: 28 }, { week: "2026-05-15", value: 30 }, { week: "2026-05-22", value: 32 }, { week: "2026-05-29", value: 34 }, { week: "2026-06-05", value: 36 }, { week: "2026-06-12", value: 38 }, { week: "2026-06-19", value: 40 }, { week: "2026-06-26", value: 42 }, { week: "2026-07-03", value: 44 }, { week: "2026-07-10", value: 46 }, { week: "2026-07-17", value: 48 } ],
    signals: [
      { title: "Volume roughly doubled", detail: "26 → 48 chats/week - the biggest growth channel outside organic search." },
      { title: "Conversion held steady", detail: "Chat-to-quote conversion stayed at ~19% through the growth - not a quality dilution, real pipeline." },
    ],
  },
  "middleby-kpi-04": {
    title: "Guided Shopping Tool Conversion",
    question: "How has guided-shopping tool conversion moved over 12 weeks?",
    headline: "Tool-to-quote conversion for the guided shopping surfaces climbed from ~9% to ~14%. The two updated tools drove most of the lift; three older tools held flat.",
    metricLabel: "Guided shopping tool-to-quote conversion",
    metricUnit: "%",
    weeklyPoints: [ { week: "2026-05-01", value: 9 }, { week: "2026-05-08", value: 9 }, { week: "2026-05-15", value: 10 }, { week: "2026-05-22", value: 10 }, { week: "2026-05-29", value: 11 }, { week: "2026-06-05", value: 11 }, { week: "2026-06-12", value: 12 }, { week: "2026-06-19", value: 12 }, { week: "2026-06-26", value: 13 }, { week: "2026-07-03", value: 13 }, { week: "2026-07-10", value: 14 }, { week: "2026-07-17", value: 14 } ],
    signals: [
      { title: "Two updated tools drove the lift", detail: "The Refrigeration selector and the Warewashing selector were redesigned in May - both improved 4+ points." },
      { title: "Three legacy tools flat", detail: "Cooking, Fryer, and Ice tools held near their May baseline - candidates for the next redesign cycle." },
    ],
  },
  "middleby-kpi-05": {
    title: "Bounce and Engagement by Entry Page",
    question: "How has bounce rate moved on hero entry pages over 12 weeks?",
    headline: "Hero-entry-page bounce rate stayed near baseline (~52%) through late June, then rose to 58% following the catalog migration. Legacy URLs and search-result mismatches are the drivers.",
    metricLabel: "Hero-entry-page bounce rate",
    metricUnit: "%",
    weeklyPoints: [ { week: "2026-05-01", value: 52 }, { week: "2026-05-08", value: 52 }, { week: "2026-05-15", value: 52 }, { week: "2026-05-22", value: 52 }, { week: "2026-05-29", value: 53 }, { week: "2026-06-05", value: 53 }, { week: "2026-06-12", value: 54 }, { week: "2026-06-19", value: 56 }, { week: "2026-06-26", value: 57 }, { week: "2026-07-03", value: 58 }, { week: "2026-07-10", value: 58 }, { week: "2026-07-17", value: 58 } ],
    signals: [
      { title: "Catalog migration side-effect", detail: "Bounce rose 6 points after the Jun 12 migration - mostly on pages that lost their canonical URL." },
      { title: "Top-5 pages account for 60%", detail: "Focus a URL / content pass on those 5 hero pages first - biggest recovery for the effort." },
    ],
  },
  "middleby-kpi-06": {
    title: "PO Reference Coverage on Closed-Won",
    question: "How has PO-reference coverage on closed-won deals moved over 12 weeks?",
    headline: "PO-reference-populated share of closed-won deals rose from 68% to 82% - the finance close hygiene sweep is paying off. Star and Wells brands remain the two laggards.",
    metricLabel: "PO-reference coverage on closed-won",
    metricUnit: "%",
    weeklyPoints: [ { week: "2026-05-01", value: 68 }, { week: "2026-05-08", value: 69 }, { week: "2026-05-15", value: 70 }, { week: "2026-05-22", value: 72 }, { week: "2026-05-29", value: 74 }, { week: "2026-06-05", value: 75 }, { week: "2026-06-12", value: 76 }, { week: "2026-06-19", value: 78 }, { week: "2026-06-26", value: 79 }, { week: "2026-07-03", value: 80 }, { week: "2026-07-10", value: 81 }, { week: "2026-07-17", value: 82 } ],
    signals: [
      { title: "Two brands remain laggards", detail: "Star (58%) and Wells (61%) - both need a targeted rep-coaching pass to close their gaps to portfolio average." },
      { title: "Finance-close impact", detail: "Higher coverage cut audit-adjustment count in half over the window - a direct back-office ROI." },
    ],
  },
  "middleby-kpi-07": {
    title: "Quotes by Sales Territory",
    question: "How has quote volume by territory shifted over 12 weeks?",
    headline: "The Southeast territory grew from 22% to 28% of national quote volume. West Coast held steady; Midwest softened from 24% to 20%.",
    metricLabel: "Southeast territory quote share",
    metricUnit: "%",
    weeklyPoints: [ { week: "2026-05-01", value: 22 }, { week: "2026-05-08", value: 23 }, { week: "2026-05-15", value: 23 }, { week: "2026-05-22", value: 24 }, { week: "2026-05-29", value: 24 }, { week: "2026-06-05", value: 25 }, { week: "2026-06-12", value: 25 }, { week: "2026-06-19", value: 26 }, { week: "2026-06-26", value: 26 }, { week: "2026-07-03", value: 27 }, { week: "2026-07-10", value: 27 }, { week: "2026-07-17", value: 28 } ],
    signals: [
      { title: "Southeast summer surge", detail: "Fryer / cooking-suite RFQs concentrate in the Southeast during the summer QSR opening cycle - expected seasonality plus a real growth signal." },
      { title: "Midwest softening", detail: "Midwest dropped 4 points - two dealer vacancies contributed; a hiring push is underway." },
    ],
  },
  "middleby-kpi-08": {
    title: "Quotes by Customer Segment",
    question: "How has quote share by customer segment moved over 12 weeks?",
    headline: "QSR (quick-service restaurant) share grew from 34% to 41% of quotes - the fastest-growing segment. Casual dining softened from 21% to 17%.",
    metricLabel: "QSR customer segment quote share",
    metricUnit: "%",
    weeklyPoints: [ { week: "2026-05-01", value: 34 }, { week: "2026-05-08", value: 35 }, { week: "2026-05-15", value: 35 }, { week: "2026-05-22", value: 36 }, { week: "2026-05-29", value: 37 }, { week: "2026-06-05", value: 37 }, { week: "2026-06-12", value: 38 }, { week: "2026-06-19", value: 39 }, { week: "2026-06-26", value: 40 }, { week: "2026-07-03", value: 40 }, { week: "2026-07-10", value: 41 }, { week: "2026-07-17", value: 41 } ],
    signals: [
      { title: "QSR segment leading", detail: "QSR quote win rate (41%) is also the highest of any segment - the growth is coming from the strongest cohort." },
      { title: "Casual dining softening", detail: "Casual dining segment quotes fell 4 points; matches the broader casual-dining industry compression this year." },
    ],
  },
  "middleby-kpi-09": {
    title: "Top Viewed Product Pages",
    question: "How have the top-10 PDPs shifted over 12 weeks?",
    headline: "The top-10 most-viewed PDPs shifted meaningfully - 4 new SKUs entered and 4 dropped out. The new entrants share a common trait: refreshed content in May.",
    metricLabel: "Top-10 PDP weekly views (thousands)",
    metricUnit: "K",
    weeklyPoints: [ { week: "2026-05-01", value: 45 }, { week: "2026-05-08", value: 46 }, { week: "2026-05-15", value: 47 }, { week: "2026-05-22", value: 47 }, { week: "2026-05-29", value: 48 }, { week: "2026-06-05", value: 49 }, { week: "2026-06-12", value: 50 }, { week: "2026-06-19", value: 51 }, { week: "2026-06-26", value: 52 }, { week: "2026-07-03", value: 52 }, { week: "2026-07-10", value: 53 }, { week: "2026-07-17", value: 54 } ],
    signals: [
      { title: "Refreshed PDPs winning", detail: "4 SKUs that got new content in May all entered the top-10 - PDP investment is measurably driving discovery." },
      { title: "Dropouts stale", detail: "The 4 SKUs that dropped from the top-10 hadn't seen content updates in 8+ months - matches the uc-21 pattern." },
    ],
  },
  "middleby-kpi-10": {
    title: "Quotes by Product Category",
    question: "How has quote share by L1 category moved over 12 weeks?",
    headline: "Refrigeration category quote share climbed from 21% to 27% of portfolio - reflecting the summer QSR opening cycle. Cooking held steady around 34%.",
    metricLabel: "Refrigeration category quote share",
    metricUnit: "%",
    weeklyPoints: [ { week: "2026-05-01", value: 21 }, { week: "2026-05-08", value: 22 }, { week: "2026-05-15", value: 22 }, { week: "2026-05-22", value: 23 }, { week: "2026-05-29", value: 23 }, { week: "2026-06-05", value: 24 }, { week: "2026-06-12", value: 25 }, { week: "2026-06-19", value: 25 }, { week: "2026-06-26", value: 26 }, { week: "2026-07-03", value: 26 }, { week: "2026-07-10", value: 27 }, { week: "2026-07-17", value: 27 } ],
    signals: [
      { title: "Refrigeration seasonality", detail: "Summer QSR openings drive the Refrigeration surge - expected and predictable each year." },
      { title: "Warewashing quietly growing", detail: "Warewashing rose from 8% to 12% - a longer-term category-strategy signal worth investigating." },
    ],
  },
  "middleby-kpi-11": {
    title: "Quotes by State",
    question: "How have state-level quote counts moved over 12 weeks?",
    headline: "TX, FL, and CA are the top-3 states (34% of national quotes combined). TX grew fastest (+18%); IL slipped -8% - mirroring the Southeast surge / Midwest softening from uc-kpi-07.",
    metricLabel: "TX weekly quotes",
    metricUnit: "",
    weeklyPoints: [ { week: "2026-05-01", value: 42 }, { week: "2026-05-08", value: 43 }, { week: "2026-05-15", value: 45 }, { week: "2026-05-22", value: 46 }, { week: "2026-05-29", value: 47 }, { week: "2026-06-05", value: 48 }, { week: "2026-06-12", value: 49 }, { week: "2026-06-19", value: 50 }, { week: "2026-06-26", value: 50 }, { week: "2026-07-03", value: 51 }, { week: "2026-07-10", value: 52 }, { week: "2026-07-17", value: 52 } ],
    signals: [
      { title: "TX leading growth", detail: "Texas grew 18% over 12 weeks - the strongest state in the sample. Fryer and refrigeration lead the mix." },
      { title: "IL softening", detail: "Illinois quotes fell 8% - correlates with the two Midwest dealer vacancies from uc-kpi-07." },
    ],
  },
  "middleby-kpi-12": {
    title: "Where each brand wins geographically",
    question: "How have the strongest brand-territory pairs shifted over 12 weeks?",
    headline: "Follett+Southeast is the fastest-growing brand-territory pair (+21%). Globe+West-Coast is the fastest-declining (-19%) - fixable via the dealer-hygiene sweep flagged in uc-18.",
    metricLabel: "Top brand-territory pair win rate (Follett+SE)",
    metricUnit: "%",
    weeklyPoints: [ { week: "2026-05-01", value: 34 }, { week: "2026-05-08", value: 35 }, { week: "2026-05-15", value: 36 }, { week: "2026-05-22", value: 37 }, { week: "2026-05-29", value: 38 }, { week: "2026-06-05", value: 39 }, { week: "2026-06-12", value: 40 }, { week: "2026-06-19", value: 41 }, { week: "2026-06-26", value: 42 }, { week: "2026-07-03", value: 43 }, { week: "2026-07-10", value: 44 }, { week: "2026-07-17", value: 45 } ],
    signals: [
      { title: "Follett+SE compounding", detail: "PDP investment + Southeast QSR seasonality make this the highest-yield pair - double-down on dealer support." },
      { title: "Globe+West Coast fixable", detail: "The 19% decline is disproportionately about tagging hygiene (see uc-18), not real demand loss - a fixable back-office issue." },
    ],
  },
  "middleby-kpi-13": {
    title: "What each customer segment buys",
    question: "How has the segment-category quote mix shifted over 12 weeks?",
    headline: "QSR segments doubled down on Refrigeration + Fryer (jointly 62% of QSR quotes). Casual dining is diversifying its mix across 5 categories - a more resilient buying pattern.",
    metricLabel: "QSR Refrigeration+Fryer share of segment quotes",
    metricUnit: "%",
    weeklyPoints: [ { week: "2026-05-01", value: 54 }, { week: "2026-05-08", value: 55 }, { week: "2026-05-15", value: 56 }, { week: "2026-05-22", value: 57 }, { week: "2026-05-29", value: 58 }, { week: "2026-06-05", value: 58 }, { week: "2026-06-12", value: 59 }, { week: "2026-06-19", value: 60 }, { week: "2026-06-26", value: 61 }, { week: "2026-07-03", value: 61 }, { week: "2026-07-10", value: 62 }, { week: "2026-07-17", value: 62 } ],
    signals: [
      { title: "QSR concentration risk", detail: "62% of QSR quotes in just 2 categories - a signal of over-reliance. Cross-sell into Warewashing would diversify." },
      { title: "Casual dining diversifying", detail: "Casual dining's top-2 categories are only 42% of its quotes - more resilient buying pattern; less exposure to any one category downturn." },
    ],
  },
};

// ── Layer 2: derived entry generator ────────────────────────────────────────
// Produces plausible 12-week trend content for any use case using its own
// insight-meta title / question / summary + primary spec metric labels.

function shortMetricLabel(spec) {
  if (!spec) return "primary metric";
  const dims = Array.isArray(spec.dimensionColumns) ? spec.dimensionColumns : [];
  const metrics = Array.isArray(spec.metricColumns) ? spec.metricColumns : [];
  const primary = metrics[0] || dims[0] || "primary metric";
  // Turn snake_case_example into "primary metric" style words
  return String(primary)
    .replace(/_example$/i, "")
    .replace(/_/g, " ")
    .toLowerCase();
}

function buildWeeklyPoints(seed) {
  // Deterministic pseudo-series so the same use case produces the same chart
  // between reloads. Range 60-100 with a mild directional drift.
  const weeks = [];
  const baseDate = new Date("2026-05-01T00:00:00Z");
  let value = 65 + (seed % 30);
  const drift = (seed % 3) - 1; // -1 / 0 / +1
  for (let i = 0; i < 12; i++) {
    const d = new Date(baseDate);
    d.setUTCDate(d.getUTCDate() + i * 7);
    const wobble = ((seed + i * 7) % 5) - 2;
    value = Math.max(30, Math.min(120, value + drift + wobble));
    weeks.push({ week: d.toISOString().slice(0, 10), value });
  }
  return weeks;
}

function hashCatalogId(catalogId) {
  let h = 0;
  const s = String(catalogId || "");
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
  return Math.abs(h);
}

function deriveTrackOverTime(catalogId) {
  const meta = getMiddlebyInsightMeta(catalogId);
  const spec = getMiddlebyPrimarySpec(catalogId);
  const useCaseName = spec?.useCaseName || catalogId;
  const sampleQuestion = spec?.sampleQuestion || "";
  const summary = meta?.reasoning?.summary || "";
  const metric = shortMetricLabel(spec);
  const seed = hashCatalogId(catalogId);
  const weeklyPoints = buildWeeklyPoints(seed);
  const first = weeklyPoints[0].value;
  const last = weeklyPoints[weeklyPoints.length - 1].value;
  const delta = last - first;
  const dir = delta > 2 ? "grown" : delta < -2 ? "declined" : "held roughly flat";

  return {
    title: useCaseName,
    question: sampleQuestion
      ? `How has this changed over the last 12 weeks?`
      : "How has this metric changed over the last 12 weeks?",
    headline: `Over the last 12 weeks, ${metric} has ${dir} from ${first} to ${last}. ${summary ? summary.split(/[.!]/)[0] + "." : ""}`.trim(),
    metricLabel: metric.charAt(0).toUpperCase() + metric.slice(1),
    metricUnit: "",
    weeklyPoints,
    signals: [
      {
        title: `Week-over-week delta: ${delta >= 0 ? "+" : ""}${delta}`,
        detail: `Net change across the 12-week window. Watch the biggest single-week movers first, they usually pair with a release, campaign, or supply event.`,
      },
      {
        title: "Cross-reference other tabs",
        detail:
          "The Answer tab explains the current-week number, See-the-data shows the breakdown by dimension, and the Look-at-next chips link to the drill-downs that matter for this trend.",
      },
    ],
  };
}

/** Returns Track-over-time content for a catalog id. Falls back to the
 *  derived generator when no hand-authored entry exists. Always returns
 *  a value so the panel can render for any Middleby use case. */
export function getMiddlebyTrackOverTime(catalogId) {
  if (!catalogId) return null;
  if (TRACK_OVER_TIME_BY_ID[catalogId]) return TRACK_OVER_TIME_BY_ID[catalogId];
  return deriveTrackOverTime(catalogId);
}
