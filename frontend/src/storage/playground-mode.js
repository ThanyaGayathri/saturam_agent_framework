/**
 * Which playground mode the user is chatting in, and the backend session it belongs to.
 *
 * R11's four grounding scopes are chosen per request via `factory_input`, so something has to
 * hold the choice between turns. Kept in localStorage per demo profile, like `chat-mode.js`
 * next door, because it is a UI preference rather than backend state.
 *
 * The session id is stored alongside because the two move together: switching mode changes
 * which tools the agent has, so continuing the previous session under a different mode would
 * hand the model a history it can no longer act on. Changing mode therefore clears the session.
 */

import { readLocalJson, writeLocalJson } from "./local-json";
import { normalizeDemoProfileId, DEFAULT_DEMO_PROFILE_ID } from "../domain/demo-profiles";

const BASE_KEY = "retailgpt:playground-mode:v1";

/** Mirrors `egpt_retail.playground.modes.PlaygroundMode`. */
export const PLAYGROUND_MODES = Object.freeze({
  DATA: "data",
  KNOWLEDGE: "knowledge",
  USE_CASE: "use_case",
  BUSINESS_AREA: "business_area",
});

/** The two modes that need a `scope_id`; the backend refuses them without one. */
export const MODES_NEEDING_SCOPE = Object.freeze([
  PLAYGROUND_MODES.USE_CASE,
  PLAYGROUND_MODES.BUSINESS_AREA,
]);

export const DEFAULT_PLAYGROUND_MODE = PLAYGROUND_MODES.DATA;

function keyForProfile(profileId) {
  const id = normalizeDemoProfileId(profileId);
  return id === DEFAULT_DEMO_PROFILE_ID ? BASE_KEY : `${BASE_KEY}:profile:${id}`;
}

/** `{ mode, scopeId, sessionId }`, defaulted and validated. */
export function readPlaygroundMode(profileId) {
  const stored = readLocalJson(keyForProfile(profileId), null);
  const mode = Object.values(PLAYGROUND_MODES).includes(stored?.mode)
    ? stored.mode
    : DEFAULT_PLAYGROUND_MODE;
  return {
    mode,
    scopeId: stored?.scopeId ?? null,
    sessionId: stored?.sessionId ?? null,
  };
}

/**
 * Change mode or scope. Clears `sessionId` whenever either changes, so the next turn opens a
 * fresh backend session — continuing one across a tool-set change is what makes a model cite
 * something it can no longer reach.
 */
export function writePlaygroundMode(profileId, { mode, scopeId = null }) {
  const previous = readPlaygroundMode(profileId);
  const changed = previous.mode !== mode || previous.scopeId !== scopeId;
  const next = {
    mode,
    scopeId,
    sessionId: changed ? null : previous.sessionId,
  };
  writeLocalJson(keyForProfile(profileId), next);
  return next;
}

/** Remember the backend session so the next turn continues the same conversation. */
export function rememberPlaygroundSession(profileId, sessionId) {
  if (!sessionId) return readPlaygroundMode(profileId);
  const current = readPlaygroundMode(profileId);
  const next = { ...current, sessionId };
  writeLocalJson(keyForProfile(profileId), next);
  return next;
}
