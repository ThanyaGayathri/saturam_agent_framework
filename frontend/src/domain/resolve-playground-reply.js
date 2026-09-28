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
} from "../services/playground-api.js";
import {
  PLAYGROUND_MODES,
  readPlaygroundMode,
  rememberPlaygroundSession,
} from "../storage/playground-mode.js";

/**
 * Which mode, scope and session this turn runs in.
 *
 * `userMeta` wins when a caller supplies it (a hand-off from the hub naming a business area,
 * say); otherwise the user's stored choice. Two call sites in `PlaygroundActiveChat` — retry
 * and the format preview — pass no `userMeta` at all, and reading the store here is what stops
 * those silently running in `data` mode against different tools than the turn they are
 * repeating.
 */
function turnContext(profileId, userMeta) {
  const stored = readPlaygroundMode(profileId);
  const requested = userMeta?.playgroundMode;
  const mode = Object.values(PLAYGROUND_MODES).includes(requested) ? requested : stored.mode;
  const scopeId =
    userMeta?.scopeId ?? (mode === stored.mode ? stored.scopeId : null) ?? null;
  // Only continue a session that belongs to this mode -- the tool set changes with the mode,
  // and a history the agent can no longer act on is worse than no history.
  const sessionId = mode === stored.mode && scopeId === stored.scopeId ? stored.sessionId : null;
  return { mode, scopeId, sessionId };
}

/**
 * @returns {Promise<{ text: string, meta?: object }>} the shape `PlaygroundActiveChat` renders.
 */
export async function resolvePlaygroundReply(userText, profileId, _exploreHub, userMeta) {
  const { mode, scopeId, sessionId } = turnContext(profileId, userMeta);
  const ask = () => askPlayground({ question: userText, mode, scopeId, sessionId });
  let result;
  try {
    result = await ask();
    // A model occasionally answers with prose that never lands in the schema -- a genuine
    // grounded answer, just not a parseable one, distinct from a refusal or a backend error.
    // One silent retry, same question and session: the numbers were already computed once
    // (a cache the tools underneath may still hold), and a second attempt independently
    // structuring the same answer is far cheaper than making the user re-ask by hand.
    if (typeof result.answer === "string" && result.answer.trim() && !result.refused) {
      result = await ask();
    }
  } catch (err) {
    // Said plainly rather than papered over with a seeded answer. `ApiError` carries the
    // backend's own `detail`, which names what went wrong far better than anything invented here.
    return {
      text: `The playground could not reach the backend: ${err?.message || "unknown error"}`,
      meta: undefined,
    };
  }

  const { runId, sessionId: newSessionId, answer, refused } = result;
  // Carry the backend session forward so "and Q2?" continues the conversation instead of
  // opening a fresh one with no history.
  rememberPlaygroundSession(profileId, newSessionId);

  // Citations come from R7's provenance record. The renderer derives its citation block from
  // `meta.lineage`; asking the model to cite itself is what provenance exists to replace.
  const lineage = lineageFromProvenance(await fetchProvenance(runId));
  const meta = blocksFromAnswer(answer, { lineage });

  if (meta.blocks.length) return { text: "", meta };

  // No blocks is not automatically a refusal. When the model answers in prose instead of
  // filling the schema -- which the Azure Foundry Claude class makes likely, since it sets
  // `supports_native_structured_outputs = False` and the schema is prompt-enforced -- the
  // framework leaves `content` as a raw STRING. Reporting "could not be answered" there
  // throws away a real answer and tells the user the mode refused, which it did not.
  if (typeof answer === "string" && answer.trim()) return { text: answer, meta: undefined };
  if (answer?.notes) return { text: answer.notes, meta: undefined };
  if (refused) return { text: "That question could not be answered in this mode.", meta: undefined };
  return { text: "The backend returned an empty answer.", meta: undefined };
}
