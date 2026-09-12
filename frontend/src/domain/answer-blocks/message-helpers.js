// Helpers for building answer-block message payloads used by the Middleby demo.
//
// These live in the domain layer so response generators can construct messages
// without importing React or touching the storage layer directly.

import { ANSWER_BLOCK_VARIANT, BLOCK_TYPES, isKnownBlockType } from "./schema";

let sequentialBlockId = 0;
function nextBlockId(prefix) {
  sequentialBlockId += 1;
  return `${prefix}-${Date.now().toString(36)}-${sequentialBlockId}`;
}

// Compose a full answer-block message meta from parts. Any parts left off are
// simply omitted from the resulting meta.
export function buildAnswerBlockMeta({
  intro,
  blocks = [],
  followUps,
  lineage,
  catalogId,
  source,
  dataGrounded,
} = {}) {
  const cleaned = blocks
    .filter(Boolean)
    .filter((b) => isKnownBlockType(b.type))
    .map((b) => (b.id ? b : { ...b, id: nextBlockId(b.type) }));

  const meta = {
    variant: ANSWER_BLOCK_VARIANT,
    blocks: cleaned,
  };
  if (intro) meta.intro = intro;
  if (followUps?.length) meta.followUps = followUps;
  if (lineage) meta.lineage = lineage;
  if (catalogId) meta.catalogId = catalogId;
  if (source) meta.source = source;
  if (dataGrounded) meta.dataGrounded = true;
  return meta;
}

// Individual block factories — each returns a valid block payload.
export function insightBlock({ headline, subtext, sentiment, id } = {}) {
  return {
    id: id || nextBlockId("insight"),
    type: BLOCK_TYPES.INSIGHT,
    headline: headline || "",
    ...(subtext ? { subtext } : {}),
    ...(sentiment ? { sentiment } : {}),
  };
}

export function findingsBlock({ title, items = [], id } = {}) {
  return {
    id: id || nextBlockId("findings"),
    type: BLOCK_TYPES.FINDINGS,
    ...(title ? { title } : {}),
    items,
  };
}

export function chartBlock({ title, description, chartKind, data = [], config = {}, id } = {}) {
  return {
    id: id || nextBlockId("chart"),
    type: BLOCK_TYPES.CHART,
    title: title || "",
    ...(description ? { description } : {}),
    chartKind: chartKind || "bar",
    data,
    config,
  };
}

export function tableBlock({ title, columns = [], rows = [], maxRows, id } = {}) {
  return {
    id: id || nextBlockId("table"),
    type: BLOCK_TYPES.TABLE,
    ...(title ? { title } : {}),
    columns,
    rows,
    ...(maxRows ? { maxRows } : {}),
  };
}

export function recommendationBlock({ title, items = [], id } = {}) {
  return {
    id: id || nextBlockId("recommendation"),
    type: BLOCK_TYPES.RECOMMENDATION,
    ...(title ? { title } : {}),
    items,
  };
}

export function citationBlock({ sources = [], askForMiddleby, id } = {}) {
  return {
    id: id || nextBlockId("citation"),
    type: BLOCK_TYPES.CITATION,
    sources,
    ...(askForMiddleby ? { askForMiddleby } : {}),
  };
}

export function actionBlock({ items = [], id } = {}) {
  return {
    id: id || nextBlockId("action"),
    type: BLOCK_TYPES.ACTION,
    items,
  };
}

export function briefBlock({ headline, bullets = [], chartRef, recommendationRef, id } = {}) {
  return {
    id: id || nextBlockId("brief"),
    type: BLOCK_TYPES.BRIEF,
    headline: headline || "",
    bullets,
    ...(chartRef ? { chartRef } : {}),
    ...(recommendationRef ? { recommendationRef } : {}),
  };
}

export function rcaBlock({ id, question, candidateCauses = [] } = {}) {
  return {
    id: id || nextBlockId("rca"),
    type: BLOCK_TYPES.RCA,
    ...(question ? { question } : {}),
    candidateCauses,
  };
}

export function regionMapBlock({ title, description, metricLabel, metricFormat, states = [], summary, id } = {}) {
  return {
    id: id || nextBlockId("regionMap"),
    type: BLOCK_TYPES.REGION_MAP,
    ...(title ? { title } : {}),
    ...(description ? { description } : {}),
    ...(metricLabel ? { metricLabel } : {}),
    ...(metricFormat ? { metricFormat } : {}),
    ...(summary ? { summary } : {}),
    states,
  };
}

export function journeyBlock({ customerId, customerLabel, title, lanes = [], events = [], id } = {}) {
  return {
    id: id || nextBlockId("journey"),
    type: BLOCK_TYPES.JOURNEY,
    ...(customerId ? { customerId } : {}),
    ...(customerLabel ? { customerLabel } : {}),
    ...(title ? { title } : {}),
    lanes,
    events,
  };
}

// Look up a block by id within a message's block list.
export function findBlockById(messageMeta, id) {
  if (!messageMeta?.blocks) return null;
  return messageMeta.blocks.find((b) => b.id === id) || null;
}
