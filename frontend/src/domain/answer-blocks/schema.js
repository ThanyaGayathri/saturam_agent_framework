// Answer-block message schema for the Middleby demo profile.
//
// This file is the single source of truth for what an assistant "answer-block"
// message looks like on the wire (in IndexedDB) and in memory. The rendering
// layer (components/shared/answer-blocks/*) reads from these shapes only.
//
// Everything here is additive — legacy assistant messages that don't set
// `variant: "answer-block"` render exactly as they did before.

export const ANSWER_BLOCK_VARIANT = "answer-block";

// Block type constants. Add new types here first, then register a component
// in `registry.js` and update `AnswerBlock` typedef below.
export const BLOCK_TYPES = Object.freeze({
  INSIGHT: "insight",
  FINDINGS: "findings",
  CHART: "chart",
  TABLE: "table",
  RECOMMENDATION: "recommendation",
  CITATION: "citation",
  ACTION: "action",
  BRIEF: "brief",
  JOURNEY: "journey",
  REGION_MAP: "regionMap",
  RCA: "rca",
});

/**
 * @typedef {Object} AnswerBlockMessageMeta
 * @property {"answer-block"} variant
 * @property {string} [intro] - Short lead paragraph rendered above the blocks.
 * @property {AnswerBlock[]} blocks - Ordered list of typed blocks.
 * @property {string[]} [followUps] - Suggested next-prompt strings shown as chips.
 * @property {DataLineage} [lineage] - Where the numbers came from + Middleby handoff.
 * @property {Reasoning} [reasoning] - Plain-language explanation for the audience: why this answer, what was compared, what confidence.
 * @property {string} [catalogId] - Optional use-case identifier for deep linking.
 * @property {string} [source] - Provenance tag (e.g., "middleby-data").
 * @property {boolean} [dataGrounded] - True if backed by loaded dataset rows.
 */

/**
 * @typedef {Object} Reasoning
 * @property {string} summary - One-sentence "why we're showing you this".
 * @property {string[]} [steps] - Ordered short bullets describing how the answer was built.
 * @property {"high" | "medium" | "low" | "illustrative"} [confidence] - How much to trust the numbers.
 * @property {string[]} [caveats] - Things the audience should keep in mind.
 */

/**
 * @typedef {InsightBlock
 *   | FindingsBlock
 *   | ChartBlock
 *   | TableBlock
 *   | RecommendationBlock
 *   | CitationBlock
 *   | ActionBlock
 *   | BriefBlock
 *   | JourneyBlock} AnswerBlock
 */

/**
 * All block payloads may also carry:
 *   explanation?: string  — Plain-language "Why this? What did the AI compute?"
 *                            revealed by the small info icon in the block header.
 *
 * Kept off the individual typedefs for brevity; every block component honours it.
 */

/**
 * @typedef {Object} InsightBlock
 * @property {string} id
 * @property {"insight"} type
 * @property {string} headline
 * @property {string} [subtext]
 * @property {string} [explanation]
 * @property {"positive"|"negative"|"neutral"} [sentiment]
 */

/**
 * @typedef {Object} FindingItem
 * @property {string} title
 * @property {string} value
 * @property {string} [change]
 * @property {"up"|"down"|"flat"} [changeDirection]
 * @property {string} [desc]
 * @property {"positive"|"negative"|"neutral"} [sentiment]
 */

/**
 * @typedef {Object} FindingsBlock
 * @property {string} id
 * @property {"findings"} type
 * @property {string} [title]
 * @property {FindingItem[]} items
 */

/**
 * @typedef {"spark" | "bar" | "line" | "funnel" | "pie" | "sankey" | "treemap"} ChartKind
 */

/**
 * @typedef {Object} ChartBlock
 * @property {string} id
 * @property {"chart"} type
 * @property {string} title
 * @property {string} [description]
 * @property {ChartKind} chartKind
 * @property {Array<Object>} data
 * @property {Object} [config] - Chart-specific config (xKey, yKeys, colors, etc.)
 */

/**
 * @typedef {Object} TableColumn
 * @property {string} key
 * @property {string} label
 * @property {"number" | "percent" | "currency" | "text" | "date"} [format]
 */

/**
 * @typedef {Object} TableBlock
 * @property {string} id
 * @property {"table"} type
 * @property {string} [title]
 * @property {TableColumn[]} columns
 * @property {Array<Object>} rows
 * @property {number} [maxRows]
 */

/**
 * @typedef {Object} RecommendationItem
 * @property {string} text
 * @property {"high" | "medium" | "low"} [priority]
 * @property {string} [rationale]
 */

/**
 * @typedef {Object} RecommendationBlock
 * @property {string} id
 * @property {"recommendation"} type
 * @property {string} [title]
 * @property {(string | RecommendationItem)[]} items
 */

/**
 * @typedef {Object} LineageSource
 * @property {string} system - "GA4" | "LogRocket" | "HubSpot" | "Salesforce" | "Adobe Commerce" | "Akeneo" | ...
 * @property {string} scope - Human-readable window/scope (e.g., "Last 30 days")
 * @property {boolean} isReal - True if real data, false if synthetic-for-demo.
 * @property {string} [note] - Extra context shown on hover.
 */

/**
 * @typedef {Object} MiddlebyHandoffAsk
 * @property {string} text - Plain-language summary of what would make this live.
 * @property {string} [sectionId] - Anchor into docs/ASK_FOR_MIDDLEBY.md.
 */

/**
 * @typedef {Object} DataLineage
 * @property {LineageSource[]} sources
 * @property {MiddlebyHandoffAsk} [askForMiddleby]
 */

/**
 * @typedef {Object} CitationBlock
 * @property {string} id
 * @property {"citation"} type
 * @property {LineageSource[]} sources
 * @property {MiddlebyHandoffAsk} [askForMiddleby]
 */

/**
 * @typedef {Object} ActionItem
 * @property {string} label - Plain, user-facing label ("Break down by brand", "Open in HubSpot").
 * @property {"drill" | "filter" | "external" | "prompt"} kind
 * @property {Object} [payload] - Kind-specific payload (URL for external, prompt string for prompt, etc.)
 * @property {string} [tooltip]
 */

/**
 * @typedef {Object} ActionBlock
 * @property {string} id
 * @property {"action"} type
 * @property {ActionItem[]} items
 */

/**
 * @typedef {Object} BriefBlock
 * @property {string} id
 * @property {"brief"} type
 * @property {string} headline
 * @property {string[]} bullets - Three short bullets, executive-style.
 * @property {string} [chartRef] - id of a ChartBlock in the same message.
 * @property {string} [recommendationRef] - id of a RecommendationBlock in the same message.
 */

/**
 * @typedef {Object} JourneyEvent
 * @property {string} lane - "GA4" | "LogRocket" | "HubSpot" | "Salesforce" | ...
 * @property {string} at - ISO timestamp.
 * @property {string} label
 * @property {"info" | "issue" | "milestone"} [kind]
 * @property {Object} [detail] - Free-form payload shown on click.
 * @property {string} [href]
 */

/**
 * @typedef {Object} JourneyBlock
 * @property {string} id
 * @property {"journey"} type
 * @property {string} [customerId]
 * @property {string} [title]
 * @property {string[]} lanes - Ordered lane names shown top-to-bottom.
 * @property {JourneyEvent[]} events
 */

// Type guard: is this an answer-block-shaped message meta?
export function isAnswerBlockMeta(meta) {
  return !!(meta && meta.variant === ANSWER_BLOCK_VARIANT && Array.isArray(meta.blocks));
}

// Type guard: is this a known block type we can render?
export function isKnownBlockType(type) {
  return Object.values(BLOCK_TYPES).includes(type);
}
