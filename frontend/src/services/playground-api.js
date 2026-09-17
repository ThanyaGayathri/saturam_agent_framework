/**
 * The playground, against the real backend.
 *
 * Two surfaces, not one, and the split is the backend's rather than ours:
 *
 *   - **Chat is a framework endpoint.** `POST /agents/{id}/runs`, multipart form, with the
 *     mode carried in `factory_input`. R11 is an `AgentFactory`: EgptOS validates that JSON
 *     against `PlaygroundModeInput` *before* the factory builds an agent, so an invalid mode
 *     is a 400 rather than a wrong answer. R11 deliberately never touches chat transport.
 *   - **Everything else is the retail router** — saved prompts, filters, preferences, reports
 *     and `/runs/{id}/provenance` — reached through `lib/api.js` like every other wired screen.
 *
 * `blocksFromAnswer` is the one piece of translation, and it is deliberately thin. The backend
 * emits six groundable kinds; this app's renderer has eleven richer types. They are not the
 * same vocabulary and should not be forced into one: the backend's set is what a tool result
 * can actually fill, and the extra frontend types (journey, rca, regionMap, action) need data
 * no executor produces. Mapping down is honest; extending the backend's enum up would hand a
 * model a vocabulary it can only fill by inventing.
 */

import { apiForm, apiGet } from "../lib/api.js";
import { BLOCK_TYPES } from "../domain/answer-blocks/schema.js";

const AGENT_ID = "playground";

/** The four grounding scopes `resolve_mode_scope` accepts. Mirrors `PlaygroundMode`. */
export const PLAYGROUND_MODES = Object.freeze({
  DATA: "data",
  KNOWLEDGE: "knowledge",
  USE_CASE: "use_case",
  BUSINESS_AREA: "business_area",
});

/**
 * Backend `AnswerBlockKind` -> a block the renderer can actually draw.
 *
 * **Each target type has its own required shape, and a block missing it renders nothing.**
 * `FindingsBlock` and `RecommendationBlock` both open with `if (!items.length) return null`;
 * `TableBlock` needs `columns`/`rows`; `ChartBlock` needs `data`. An earlier version of this
 * file emitted a uniform `{headline, subtext, content, source}` for every kind, so five of the
 * six backend kinds produced a blank bubble — the answer arrived intact from the backend and
 * the component dropped it on the floor. Hence one builder per target rather than one mapping
 * table: the shape is the contract, not the type name.
 *
 * The backend's six kinds are prose-and-numbers; the renderer's richer types (journey, rca,
 * regionMap) need data no executor produces, so nothing maps onto them.
 */

/** `text` -> InsightBlock: `headline` + `subtext`, the renderer's prose block. */
function asInsight(block, body) {
  return {
    id: block.id,
    type: BLOCK_TYPES.INSIGHT,
    kind: block.kind,
    headline: block.title || "",
    subtext: body,
  };
}

/**
 * `finding` / `kpi` -> FindingsBlock, whose `items` are `{title, value, change,
 * changeDirection, sentiment, desc}` stat tiles.
 *
 * One backend block becomes one tile. The backend sends prose, not a parsed number, so `value`
 * carries the text and `desc` the source attribution — a tile with a sentence in it reads
 * fine, and inventing a numeric split here would mean parsing the model's prose, which is
 * exactly the kind of guessing the rest of this pipeline refuses to do.
 */
function asFindings(block, body) {
  return {
    id: block.id,
    type: BLOCK_TYPES.FINDINGS,
    kind: block.kind,
    title: block.title || "",
    items: [{ title: block.title || "", value: body, desc: block.source || "" }],
  };
}

/** `recommendation` -> RecommendationBlock, whose items are `{text}`. */
function asRecommendation(block, body) {
  return {
    id: block.id,
    type: BLOCK_TYPES.RECOMMENDATION,
    kind: block.kind,
    title: block.title || "",
    items: [{ text: body }],
  };
}

/**
 * `table` / `chart` -> Insight, deliberately.
 *
 * `TableBlock` needs `columns` and `rows`; `ChartBlock` needs `data` and a `chartKind`. The
 * backend's `AnswerBlock` carries none of that — the contract declares only `id` and `kind`,
 * and `playground.md` asks for `title`/`content`/`source`. Rendering the prose in an Insight
 * block shows the answer; emitting an empty TableBlock shows a header and no data, which reads
 * as "there were no rows" rather than "this surface cannot draw it yet". When the backend
 * grows structured table and series payloads, these get their own builders.
 */
const BUILDERS = Object.freeze({
  text: asInsight,
  finding: asFindings,
  kpi: asFindings,
  recommendation: asRecommendation,
  table: asInsight,
  chart: asInsight,
});

/**
 * One backend block as a renderable one.
 *
 * `content` is what `playground.md` asks the model for; `text` is accepted as a fallback
 * because the contract leaves the inner shape open. An unrecognised kind renders as prose
 * rather than vanishing — a block the backend added and this file has not caught up with
 * should still show its content.
 */
function toRenderableBlock(block) {
  const body = block.content ?? block.text ?? "";
  const build = BUILDERS[block.kind] || asInsight;
  return build(block, body);
}

/**
 * A `PlaygroundAnswer` as `AnswerBlockRenderer` meta.
 *
 * `lineage` is left for the caller to fill from the real provenance record rather than being
 * derived from the answer: the renderer builds its citation block from `meta.lineage`, and a
 * model citing itself is exactly what R7's provenance row exists to replace.
 */
export function blocksFromAnswer(answer, { lineage = null } = {}) {
  const blocks = Array.isArray(answer?.blocks) ? answer.blocks : [];
  return {
    variant: "answer-block",
    blocks: blocks.map(toRenderableBlock),
    ...(answer?.notes ? { intro: answer.notes } : {}),
    ...(lineage ? { lineage } : {}),
  };
}

/**
 * Ask the playground agent one question.
 *
 * `stream: false` on purpose for now: the renderer draws whole blocks, so there is nothing to
 * paint incrementally until a block-level streaming shape exists. Swapping to SSE later is a
 * change to this function and to nothing that calls it.
 */
export async function askPlayground(
  { question, mode = PLAYGROUND_MODES.DATA, scopeId = null, sessionId = null, signal } = {},
) {
  const form = new FormData();
  form.append("message", question);
  form.append("factory_input", JSON.stringify({ mode, scope_id: scopeId }));
  form.append("stream", "false");
  if (sessionId) form.append("session_id", sessionId);

  const run = await apiForm(`/agents/${AGENT_ID}/runs`, "POST", form, { signal });

  // A refused mode still returns 200 with an empty answer and the reason in `notes` --
  // `resolve_mode_scope` declines before an agent is built, which is a refusal rather than a
  // failure and must not be rendered as an error.
  const answer = run?.content ?? {};
  return {
    runId: run?.run_id ?? null,
    sessionId: run?.session_id ?? null,
    status: run?.status ?? "UNKNOWN",
    answer,
    refused: Array.isArray(answer.blocks) && answer.blocks.length === 0 && !!answer.notes,
  };
}

/** Where every number in an answer came from (R7). Null when the run wrote no record. */
export async function fetchProvenance(runId, { signal } = {}) {
  if (!runId) return null;
  try {
    return await apiGet(`/runs/${runId}/provenance`, { signal });
  } catch {
    // A missing provenance row is not an error the user can act on: the answer still stands,
    // it just cannot show its sources. Returning null lets the renderer omit the citation
    // block rather than fail the whole message.
    return null;
  }
}

/**
 * A provenance record as the renderer's `lineage`.
 *
 * `CitationBlock`'s `SourceRow` reads `{system, scope, isReal, note}`. An earlier version
 * emitted `{label, kind}`, so every row rendered blank **and**, because `isReal` was
 * `undefined`, carried the "Example data — Middleby data would replace this" badge. A real
 * provenance row was therefore displayed as fabricated, which is the exact inversion of what
 * R7 exists to prove.
 *
 * `isReal: true` is correct by construction here: a provenance row is only written by a run
 * that actually read something, so anything reaching this function came from real data.
 *
 * The record's `datasets_read` entries are `{schema, table, as_of}` (`DatasetRead` in the
 * generated API models) — there is no `name` and no `dataset`, which an earlier `d.name ||
 * d.dataset || "dataset"` fell through to, labelling every source with the literal word
 * "dataset".
 */
export function lineageFromProvenance(record) {
  if (!record) return null;
  const sources = [
    ...(record.datasets_read || []).map((d) => ({
      system: typeof d === "string" ? d : [d.schema, d.table].filter(Boolean).join("."),
      scope: d?.as_of ? `as of ${d.as_of}` : "",
      isReal: true,
      note: "",
    })),
    ...(record.sql_executed || []).map((q) => ({
      system: q?.table || q?.dataset || "warehouse",
      scope: q?.rows != null ? `${q.rows} row(s)` : "",
      isReal: true,
      note: q?.statement ? String(q.statement).slice(0, 120) : "",
    })),
    ...(record.retrieval_hits || []).map((h) => ({
      system: h?.origin || "retrieval",
      scope: h?.id ? String(h.id) : "",
      isReal: true,
      note: h?.score != null ? `score ${h.score}` : "",
    })),
  ].filter((s) => s.system);
  if (!sources.length) return null;
  return { sources, modelTier: record.model_tier || null };
}

export const playgroundApi = {
  ask: askPlayground,
  provenance: fetchProvenance,
  lineageFromProvenance,
  blocksFromAnswer,
  MODES: PLAYGROUND_MODES,
};
