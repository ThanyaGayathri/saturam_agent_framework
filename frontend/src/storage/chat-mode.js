// Persisted "quick summary" toggle. When enabled, answers are formatted as an
// executive-style brief (one headline + up to three bullets) instead of the
// full block layout.

import { readLocalJson, writeLocalJson } from "./local-json";
import { normalizeDemoProfileId, DEFAULT_DEMO_PROFILE_ID } from "../domain/demo-profiles";

const BASE_KEY = "retailgpt:chat-mode:v1";

export const CHAT_MODES = Object.freeze({
  FULL: "full",
  BRIEF: "brief",
});

function keyForProfile(profileId) {
  const id = normalizeDemoProfileId(profileId);
  return id === DEFAULT_DEMO_PROFILE_ID ? BASE_KEY : `${BASE_KEY}:profile:${id}`;
}

export function readChatMode(profileId) {
  const raw = readLocalJson(keyForProfile(profileId), { mode: CHAT_MODES.FULL });
  const mode = raw?.mode === CHAT_MODES.BRIEF ? CHAT_MODES.BRIEF : CHAT_MODES.FULL;
  return { mode };
}

export function writeChatMode(profileId, mode) {
  const clean = mode === CHAT_MODES.BRIEF ? CHAT_MODES.BRIEF : CHAT_MODES.FULL;
  writeLocalJson(keyForProfile(profileId), { mode: clean });
}
