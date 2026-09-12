/** Initial rows for IndexedDB chat sessions (plain objects; `meta` optional). */

import {
  DEFAULT_DEMO_PROFILE_ID,
  GOURMET_FOODS_PROFILE_ID,
  MEDIA_PROFILE_ID,
  MIDDLEBY_PROFILE_ID,
  normalizeDemoProfileId,
} from "../domain/demo-profiles";
import { MIDDLEBY_TOPIC_PANELS } from "../domain/middleby-playground-chat";
import { MEDIA_TOPIC_PANELS } from "../domain/media-playground-chat";

/**
 * Same first-run thread for Playground and Use Case builder chat so demos get identical
 * contextual panels and keyword behavior (`getPlaygroundAssistantReply`).
 */
export const PLAYGROUND_CHAT_SEED = [
  /** Distinct from hub / card copy to avoid duplicate user bubbles when opening chat after typing a similar prompt. */
  { role: "user", text: "What should I look at first for North East performance this week?" },
  {
    role: "assistant",
    text: "Summarizing demand and inventory pressure for the North East (demo data):",
    meta: { variant: "playground-rich", topic: "inventory" },
  },
];

const GOURMET_PLAYGROUND_CHAT_SEED = [
  {
    role: "user",
    text: "How tight is our Brie cover in Denver vs. what Edison needs this week, and can we move it on our own trucks without breaking temp?",
  },
  {
    role: "assistant",
    text: "Classic footprint mismatch: Front Range is long soft-ripened, Mid-Atlantic is chasing allocation. I've staged a two-stop consolidated move (Denver → Kansas City → Edison) within temperature spec; ETA protects both DC cut times.",
    meta: { variant: "playground-rich", topic: "inventory" },
  },
];

const MIDDLEBY_PLAYGROUND_CHAT_SEED = [
  {
    role: "user",
    text: "Which quotes require immediate follow-up today, and where is buyer intent strongest?",
  },
  {
    role: "assistant",
    text: "Sorting HubSpot quotes for dealer follow-up this week (Middleby demo data):",
    meta: {
      variant: "playground-rich",
      topic: "dealer_copilot",
      panel: MIDDLEBY_TOPIC_PANELS.dealer_copilot,
      catalogId: "middleby-uc-04",
    },
  },
];

const MEDIA_PLAYGROUND_CHAT_SEED = [
  {
    role: "user",
    text: "What content themes are trending among audiences this month?",
  },
  {
    role: "assistant",
    text: "Conversation around mythological drama reboots has grown for four straight weeks, driven mainly by nostalgia-led social clips and a rival network's successful relaunch. Strongest signals come from Hindi-speaking audiences in Tier-2 markets. Similar themes have historically performed well in our mythology and family-drama slate.",
    meta: {
      variant: "playground-rich",
      topic: "content_trend_intelligence",
      panel: MEDIA_TOPIC_PANELS.content_trend_intelligence,
      catalogId: "media-uc-01-content-conversation-trends",
    },
  },
];

/** @param {string} [profileId] */
export function getPlaygroundChatSeed(profileId = DEFAULT_DEMO_PROFILE_ID) {
  const id = normalizeDemoProfileId(profileId);
  if (id === GOURMET_FOODS_PROFILE_ID) return GOURMET_PLAYGROUND_CHAT_SEED;
  if (id === MIDDLEBY_PROFILE_ID) return MIDDLEBY_PLAYGROUND_CHAT_SEED;
  if (id === MEDIA_PROFILE_ID) return MEDIA_PLAYGROUND_CHAT_SEED;
  return PLAYGROUND_CHAT_SEED;
}

export const BUILDER_CHAT_SEED = PLAYGROUND_CHAT_SEED;
