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
 * Backend `AnswerBlockKind` -> this app's `BLOCK_TYPES`.
 *
 * `kpi` and `finding` both land on FINDINGS: the renderer has no kpi tile, and a finding tile
 * carries a number perfectly well. `text` becomes INSIGHT, which is the renderer's prose block.
 * Anything unrecognised also becomes INSIGHT rather than being dropped — a block the backend
 * added and this map has not caught up with should still render its content, not vanish.
 */
const KIND_TO_TYPE = Object.freeze({
  text: BLOCK_TYPES.INSIGHT,
  finding: BLOCK_TYPES.FINDINGS,
  kpi: BLOCK_TYPES.FINDINGS,
  table: BLOCK_TYPES.TABLE,
  chart: BLOCK_TYPES.CHART,
  recommendation: BLOCK_TYPES.RECOMMENDATION,
});

/**
 * One backend block as the renderer's shape.
 *
 * The contract declares only `id` and `kind` ("the playground's answer format owns the inner
 * shape"), and `playground.md` asks the model for `title`, `content` and `source`. Both are
 * carried through: `content` is the prose, `source` says which tool call produced it, and the
 * original `kind` is preserved so a citation panel can tell a kpi from a finding later.
 */
function toRenderableBlock(block) {
  const type = KIND_TO_TYPE[block.kind] || BLOCK_TYPES.INSIGHT;
  const body = block.content ?? block.text ?? "";
  return {
    id: block.id,
    type,
    kind: block.kind,
    headline: block.title || "",
    subtext: body,
    content: body,
    source: block.source || "",
  };
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
 * Datasets read and statements executed are what the record actually holds; the citation block
 * wants a list of sources, so each becomes one.
 */
export function lineageFromProvenance(record) {
  if (!record) return null;
  const sources = [
    ...(record.datasets_read || []).map((d) => ({
      label: typeof d === "string" ? d : d.name || d.dataset || "dataset",
      kind: "dataset",
    })),
    ...(record.retrieval_hits || []).map((h) => ({
      label: h.origin ? `${h.origin}: ${h.id ?? ""}`.trim() : String(h.id ?? "retrieval hit"),
      kind: "retrieval",
    })),
  ];
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
