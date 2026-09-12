// Development preview message that exercises every block type.
//
// Triggered via the slash command `/blocks-preview` in the Middleby playground
// chat. This is intentionally kept out of the user-visible copy paths  -  it is
// only used to verify rendering during development. It also serves as a live
// reference for the answer-block schema.

import {
  buildAnswerBlockMeta,
  insightBlock,
  findingsBlock,
  chartBlock,
  tableBlock,
  recommendationBlock,
  citationBlock,
  actionBlock,
  briefBlock,
  journeyBlock,
} from "./message-helpers";

// Friendly user-facing question that also functions as the preview trigger.
// Kept in plain English so the chat transcript reads naturally when someone
// clicks the "See the new answer format" pill.
export const BLOCKS_PREVIEW_TRIGGER = "Show me a full example answer";

// Match the pill text plus a couple of forgiving variants. Anything the user
// might type that clearly asks for the preview should hit this.
export function isBlocksPreviewTrigger(text) {
  if (!text) return false;
  const t = String(text).trim().toLowerCase();
  if (!t) return false;
  return (
    t === "show me a full example answer" ||
    t === "show me an example answer" ||
    t === "show me the new answer format" ||
    t === "see the new answer format" ||
    t === "/blocks-preview" ||
    t === "/preview"
  );
}

// Compact preview used by the home-page modal. Six curated blocks that show
// the key layout patterns without overwhelming the reader.
export function buildBlocksPreviewCompactReply() {
  const meta = buildAnswerBlockMeta({
    intro: "This is what a new-style answer looks like. Real answers pick and mix these cards based on your question.",
    reasoning: {
      summary: "Every answer starts by explaining why it picked what it picked  -  so the audience can trust the numbers.",
      confidence: "illustrative",
    },
    blocks: [
      briefBlock({
        headline: "RFQs up 12% this week, but two brands are dragging conversion.",
        bullets: [
          "TurboChef and Pitco account for 68% of the drop after add-to-cart.",
          "Fixing pricing visibility on those two brands could recover ~$1.8M pipeline.",
        ],
      }),
      findingsBlock({
        title: "This week at a glance",
        items: [
          { title: "RFQs submitted", value: "1,284", change: "+12%", changeDirection: "up", sentiment: "positive", desc: "vs previous 7 days" },
          { title: "Add-to-cart → RFQ", value: "38%", change: "-4pp", changeDirection: "down", sentiment: "negative", desc: "conversion rate" },
          { title: "Est. pipeline", value: "$4.6M", change: "+8%", changeDirection: "up", sentiment: "positive", desc: "avg deal × RFQs" },
        ],
      }),
      chartBlock({
        title: "Where visitors drop out of the RFQ funnel",
        chartKind: "funnel",
        data: [
          { stage: "Product page view", value: 42800 },
          { stage: "Added to cart", value: 8210 },
          { stage: "Started RFQ form", value: 4180 },
          { stage: "Submitted RFQ", value: 1284 },
        ],
        config: { xKey: "stage", yKey: "value" },
        explanation: "This funnel comes from GA4 pageviews and RFQ form events; the biggest drop is between cart and form.",
      }),
      recommendationBlock({
        title: "What to try next",
        items: [
          { text: "Make TurboChef Encore pricing visible without an RFQ.", priority: "high" },
          { text: "Group multi-brand carts into one RFQ (Phase 1.5).", priority: "medium" },
        ],
      }),
      citationBlock({
        sources: [
          { system: "GA4", scope: "Last 30 days", isReal: false, note: "Illustrative example so you can see the layout." },
          { system: "HubSpot", scope: "RFQ deals", isReal: false },
        ],
        askForMiddleby: {
          text: "Live GA4 → HubSpot linkage would turn every number here into a real one.",
          sectionId: "rfq-funnel",
        },
      }),
    ],
    followUps: [
      "Which brands have the biggest gap between traffic and quotes?",
      "Show me the RFQ funnel",
      "Give me this week's leadership summary",
    ],
    catalogId: "middleby-preview-compact",
    source: "answer-block-preview-compact",
    dataGrounded: true,
  });

  return { text: "Here's an example of the new answer format.", meta };
}

export function buildBlocksPreviewReply() {
  const meta = buildAnswerBlockMeta({
    intro: "Here is a preview of every card style we can now show inside the chat. Real answers will pick and mix these based on your question.",
    blocks: [
      briefBlock({
        headline: "RFQ conversions look healthy overall, but two brands are lagging.",
        bullets: [
          "Total RFQ submissions are up 12% week over week.",
          "TurboChef and Pitco account for 68% of the drop-off after add-to-cart.",
          "Fixing product page pricing visibility could recover ~$1.8M pipeline.",
        ],
      }),
      insightBlock({
        headline: "Drop-off between add-to-cart and RFQ submit widened this week.",
        subtext: "This is the second week in a row we've seen it. Worth a closer look at product content.",
        sentiment: "negative",
      }),
      findingsBlock({
        title: "This week at a glance",
        items: [
          { title: "RFQs submitted", value: "1,284", change: "+12%", changeDirection: "up", sentiment: "positive", desc: "vs. previous 7 days" },
          { title: "Add-to-cart → RFQ", value: "38%", change: "-4pp", changeDirection: "down", sentiment: "negative", desc: "conversion rate" },
          { title: "Estimated pipeline", value: "$4.6M", change: "+8%", changeDirection: "up", sentiment: "positive", desc: "based on avg deal size" },
        ],
      }),
      chartBlock({
        title: "RFQ funnel",
        description: "Where visitors leave the RFQ journey",
        chartKind: "funnel",
        data: [
          { stage: "Product page view", value: 42800 },
          { stage: "Added to cart", value: 8210 },
          { stage: "Started RFQ form", value: 4180 },
          { stage: "Submitted RFQ", value: 1284 },
        ],
        config: { xKey: "stage", yKey: "value" },
      }),
      chartBlock({
        title: "RFQ volume by brand",
        description: "Last 30 days",
        chartKind: "bar",
        data: [
          { label: "TurboChef", value: 512 },
          { label: "Pitco", value: 388 },
          { label: "Blodgett", value: 305 },
          { label: "Southbend", value: 214 },
          { label: "Middleby Marshall", value: 178 },
        ],
        config: { xKey: "label", yKey: "value" },
      }),
      chartBlock({
        title: "Where visitors came from",
        chartKind: "pie",
        data: [
          { label: "Organic search", value: 41 },
          { label: "Direct", value: 24 },
          { label: "Paid campaigns", value: 18 },
          { label: "Referral", value: 11 },
          { label: "Email", value: 6 },
        ],
        config: { xKey: "label", yKey: "value" },
      }),
      tableBlock({
        title: "Top 5 products by RFQ volume",
        columns: [
          { key: "sku", label: "SKU" },
          { key: "name", label: "Product" },
          { key: "brand", label: "Brand" },
          { key: "rfqs", label: "RFQs", format: "number" },
          { key: "conv", label: "Cart → RFQ", format: "percent" },
        ],
        rows: [
          { sku: "TCE1", name: "TurboChef Encore 1", brand: "TurboChef", rfqs: 128, conv: 41 },
          { sku: "PTX1", name: "Pitco Fryer PTX", brand: "Pitco", rfqs: 94, conv: 38 },
          { sku: "BLDG", name: "Blodgett Combi Oven", brand: "Blodgett", rfqs: 71, conv: 46 },
          { sku: "SBRG", name: "Southbend Range", brand: "Southbend", rfqs: 55, conv: 33 },
          { sku: "MMPS", name: "Middleby Marshall Conveyor", brand: "Middleby Marshall", rfqs: 42, conv: 29 },
        ],
      }),
      recommendationBlock({
        title: "What to try next",
        items: [
          { text: "Make TurboChef Encore pricing visible without RFQ.", priority: "high", rationale: "Pricing is the top LogRocket rage-click on that page." },
          { text: "Group multi-brand carts into single RFQ submission (Phase 1.5).", priority: "medium" },
          { text: "Send a weekly RFQ drop-off email to the merchandising team.", priority: "low" },
        ],
      }),
      actionBlock({
        items: [
          { label: "Show me only TurboChef", kind: "filter", payload: { brand: "TurboChef" } },
          { label: "Break down by region", kind: "drill", payload: { by: "region" } },
          { label: "Open in HubSpot", kind: "external", payload: { href: "https://hubspot.com" } },
          { label: "How did last week compare?", kind: "prompt", payload: { prompt: "How did the RFQ funnel look last week?" } },
        ],
      }),
      citationBlock({
        sources: [
          { system: "GA4", scope: "Last 30 days", isReal: false, note: "Extract exported 2026-06-19; sessions sampled at 10%." },
          { system: "LogRocket", scope: "Sessions with rage-clicks on product pages", isReal: false, note: "Example issues from the LogRocket demo shared by Middleby." },
          { system: "HubSpot", scope: "Deals with RFQ source tag", isReal: false },
          { system: "Salesforce", scope: "Opportunities synced from HubSpot", isReal: false, note: "Post RFQ Phase 1.5 auto-sync; scoped example." },
        ],
        askForMiddleby: {
          text: "Live GA4 → LogRocket session ID mapping, and a nightly HubSpot → Salesforce opportunity ID join, would turn every example number here into a real one.",
          sectionId: "rfq-funnel",
        },
      }),
    ],
    followUps: [
      "Why did add-to-cart to RFQ drop this week?",
      "Show me the same picture for TurboChef only.",
      "What's the weekly leadership summary?",
    ],
    lineage: {
      sources: [
        { system: "GA4", scope: "Last 30 days", isReal: false },
        { system: "HubSpot", scope: "RFQ deals", isReal: false },
      ],
      askForMiddleby: { text: "Same as above  -  real GA4 + HubSpot linkage.", sectionId: "rfq-funnel" },
    },
    catalogId: "middleby-preview",
    source: "answer-block-preview",
    dataGrounded: true,
  });

  return {
    text: "",
    meta,
  };
}
