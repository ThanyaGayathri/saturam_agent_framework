import { useCallback, useEffect, useMemo, useState } from "react";
import { useDemoProfile } from "../context/DemoProfileContext";
import { deleteSession } from "../storage/idb/chat-db";
import {
  loadConversations,
  saveConversations,
  newConversation,
  titleFromText,
  legacySessionId,
} from "../storage/chat-conversations";

/**
 * The Playground's conversations: the list, which one is open, and the operations the
 * sidebar offers. Each demo profile keeps its own, the way every other stored thing here does.
 */
export function useChatConversations() {
  const { profileId, storageKeys } = useDemoProfile();
  const storageKey = storageKeys.CHAT_CONVERSATIONS;
  const [state, setState] = useState(() => loadConversations(storageKey));

  // On first run there is no index but there may well be a chat: the single session the
  // playground used before. Adopt it once, rather than stranding those messages.
  //
  // `adopted` is remembered so this never happens twice. Without it, deleting every
  // conversation looked undone on the next load: an empty index read as "first run" and the
  // old session was offered back.
  useEffect(() => {
    const loaded = loadConversations(storageKey);
    if (!loaded.adopted && loaded.conversations.length === 0) {
      const adopted = newConversation(profileId, { title: "Playground chat", legacy: true });
      const next = { version: 1, adopted: true, conversations: [adopted], activeId: adopted.id };
      saveConversations(next, storageKey);
      setState(next);
      return;
    }
    setState(loaded);
  }, [storageKey, profileId]);

  const persist = useCallback(
    (next) => {
      setState(next);
      saveConversations(next, storageKey);
    },
    [storageKey]
  );

  const activeId = useMemo(() => {
    if (state.activeId && state.conversations.some((c) => c.id === state.activeId)) {
      return state.activeId;
    }
    // Never fall back to the legacy session id: that is a real conversation with real
    // messages, and landing on it after a clear is exactly what looked like a failed delete.
    return state.conversations[0]?.id || null;
  }, [state]);

  const startNew = useCallback(() => {
    const created = newConversation(profileId);
    persist({
      ...state,
      adopted: true,
      conversations: [created, ...state.conversations],
      activeId: created.id,
    });
    return created.id;
  }, [persist, profileId, state]);

  const select = useCallback(
    (id) => {
      if (!state.conversations.some((c) => c.id === id)) return;
      persist({ ...state, activeId: id });
    },
    [persist, state]
  );

  /** Deleting a chat takes its messages with it, not just its row in the list. */
  const remove = useCallback(
    (id) => {
      void deleteSession(id);
      const rest = state.conversations.filter((c) => c.id !== id);
      // Clearing the last one leaves a fresh empty chat, so the page always has somewhere to
      // type rather than rendering a conversation that no longer exists.
      const replacement = rest.length === 0 ? newConversation(profileId) : null;
      const conversations = replacement ? [replacement] : rest;
      persist({
        ...state,
        adopted: true,
        conversations,
        activeId:
          state.activeId === id ? replacement?.id || rest[0]?.id || null : state.activeId,
      });
    },
    [persist, profileId, state]
  );

  const rename = useCallback(
    (id, title) => {
      persist({
        ...state,
        conversations: state.conversations.map((c) =>
          c.id === id ? { ...c, title: titleFromText(title) } : c
        ),
      });
    },
    [persist, state]
  );

  /**
   * Called when a turn is sent. It moves the conversation to the top of the list and, for a
   * chat still called "New chat", names it after the first thing asked.
   */
  const touch = useCallback(
    (id, firstText) => {
      persist({
        ...state,
        conversations: state.conversations.map((c) =>
          c.id === id
            ? {
                ...c,
                updatedAt: Date.now(),
                title: c.title && c.title !== "New chat" ? c.title : titleFromText(firstText),
              }
            : c
        ),
      });
    },
    [persist, state]
  );

  return {
    conversations: state.conversations,
    activeId,
    startNew,
    select,
    remove,
    rename,
    touch,
  };
}
