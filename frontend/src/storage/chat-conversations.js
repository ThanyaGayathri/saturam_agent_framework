import { readLocalJson, writeLocalJson } from "./local-json";
import { CHAT_SESSIONS } from "./idb/chat-db";
import { scopedChatSessionId } from "../domain/demo-profiles";

const VERSION = 1;

/**
 * The index of Playground conversations: one row per chat, newest first.
 *
 * Messages already live in IndexedDB keyed by `sessionId`, so a conversation is nothing more
 * than a session id with a title attached. The playground used one fixed id, which is why
 * there was a single ever-growing chat and no history to show. This index is what turns that
 * into many, and it stays in localStorage because it is small and read on every render.
 *
 * The rows hold no message text: the title is derived from the first thing the user typed.
 */
export function loadConversations(storageKey) {
  const parsed = readLocalJson(storageKey, null);
  if (!parsed || !Array.isArray(parsed.conversations)) {
    return { version: VERSION, adopted: false, conversations: [], activeId: null };
  }
  return {
    version: parsed.version ?? VERSION,
    /** Whether the pre-existing single session has already been taken into the list. */
    adopted: Boolean(parsed.adopted),
    conversations: parsed.conversations.filter((c) => c && typeof c.id === "string"),
    activeId: typeof parsed.activeId === "string" ? parsed.activeId : null,
  };
}

export function saveConversations(state, storageKey) {
  writeLocalJson(storageKey, {
    version: state?.version ?? VERSION,
    adopted: Boolean(state?.adopted),
    conversations: state?.conversations ?? [],
    activeId: state?.activeId ?? null,
  });
}

/**
 * The session id the chat before this change wrote to. The first load adopts it as an
 * ordinary conversation, so an existing chat keeps its messages instead of appearing to
 * vanish behind a brand new empty one.
 */
export function legacySessionId(profileId) {
  return scopedChatSessionId(CHAT_SESSIONS.PLAYGROUND, profileId);
}

export function newConversation(profileId, { title = "", legacy = false } = {}) {
  const id = legacy
    ? legacySessionId(profileId)
    : `${legacySessionId(profileId)}:${crypto.randomUUID?.() || Date.now().toString(36)}`;
  const now = Date.now();
  return { id, title, createdAt: now, updatedAt: now };
}

/** A title from the first thing asked, the way ChatGPT and Claude name a thread. */
export function titleFromText(text) {
  const clean = String(text || "")
    .replace(/\s+/g, " ")
    .trim();
  if (!clean) return "New chat";
  return clean.length > 48 ? `${clean.slice(0, 47).trimEnd()}…` : clean;
}

const DAY = 24 * 60 * 60 * 1000;

/**
 * Today / Previous 7 days / Older, the grouping every chat sidebar uses. Rows keep their
 * newest-first order inside each group.
 */
export function groupConversations(conversations, now = Date.now()) {
  const startOfToday = new Date(now);
  startOfToday.setHours(0, 0, 0, 0);
  const todayMs = startOfToday.getTime();

  const groups = [
    { id: "today", label: "Today", rows: [] },
    { id: "week", label: "Previous 7 days", rows: [] },
    { id: "older", label: "Older", rows: [] },
  ];
  for (const row of [...(conversations || [])].sort((a, b) => (b.updatedAt || 0) - (a.updatedAt || 0))) {
    const at = row.updatedAt || row.createdAt || 0;
    if (at >= todayMs) groups[0].rows.push(row);
    else if (at >= todayMs - 7 * DAY) groups[1].rows.push(row);
    else groups[2].rows.push(row);
  }
  return groups.filter((g) => g.rows.length > 0);
}
