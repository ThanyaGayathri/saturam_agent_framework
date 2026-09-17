/**
 * The playground's assistant reply. R11 and nothing else.
 *
 * Replaces `resolve-playground-assistant.js` **for the playground**. That module is still the
 * use-case builder's resolver (`UseCaseBuilderChat.jsx`) and keeps its scripted chain, because
 * the builder's chat is R9's spec extraction rather than R11 and wiring it is a different piece
 * of work. This file exists so the playground stops depending on that chain at all, rather than
 * keeping a fallback nobody can tell they are looking at.
 *
 * **There is no scripted fallback here, deliberately.** A fallback that silently answers from
 * `domain/*-chat.js` when the backend is unreachable is the failure mode this whole change
 * exists to end: the user cannot tell a grounded answer from a seeded one, and neither can a
 * reviewer. A backend that is down says so.
 *
 * Refusals are answers, not errors. `resolve_mode_scope` declines an unknown use case or an
 * area outside the ontology *before* an agent is built, and the reason it gives is the useful
 * thing to show.
 */

import {
  askPlayground,
  blocksFromAnswer,
  fetchProvenance,
  lineageFromProvenance,
  PLAYGROUND_MODES,
} from "../services/playground-api.js";

/** Which mode a turn runs in. Defaults to `data` — the mode that needs no scope id. */
function modeFor(userMeta) {
  const mode = userMeta?.playgroundMode;
  return Object.values(PLAYGROUND_MODES).includes(mode) ? mode : PLAYGROUND_MODES.DATA;
}

/**
 * @returns {Promise<{ text: string, meta?: object }>} the shape `PlaygroundActiveChat` renders.
 */
export async function resolvePlaygroundReply(userText, _profileId, _exploreHub, userMeta) {
  let result;
  try {
    result = await askPlayground({
      question: userText,
      mode: modeFor(userMeta),
      scopeId: userMeta?.scopeId || null,
      sessionId: userMeta?.sessionId || null,
    });
  } catch (err) {
    // Said plainly rather than papered over with a seeded answer. `ApiError` carries the
    // backend's own `detail`, which names what went wrong far better than anything invented here.
    return {
      text: `The playground could not reach the backend: ${err?.message || "unknown error"}`,
      meta: undefined,
    };
  }

  const { runId, answer, refused } = result;

  // Citations come from R7's provenance record. The renderer derives its citation block from
  // `meta.lineage`; asking the model to cite itself is what provenance exists to replace.
  const lineage = lineageFromProvenance(await fetchProvenance(runId));
  const meta = blocksFromAnswer(answer, { lineage });

  if (refused || !meta.blocks.length) {
    return {
      text: answer?.notes || "That question could not be answered in this mode.",
      meta: undefined,
    };
  }
  return { text: "", meta };
}
