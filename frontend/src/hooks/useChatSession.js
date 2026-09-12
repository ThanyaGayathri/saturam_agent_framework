import { useCallback, useEffect, useRef, useState } from "react";
import {
  addChatMessage,
  getMessagesForSession,
  isIndexedDbChatAvailable,
  seedSessionIfEmpty,
} from "../storage/idb/chat-db";

function buildMemoryMessage(sessionId, { role, text, meta, id }, i = 0) {
  return {
    id: id || crypto.randomUUID?.() || `mem-${Date.now()}-${i}-${Math.random().toString(16).slice(2)}`,
    sessionId,
    role,
    text: String(text || "").trim(),
    createdAt: Date.now() + i,
    meta: meta && typeof meta === "object" ? meta : undefined,
  };
}

export function useChatSession(sessionId, seedMessages = []) {
  const [messages, setMessages] = useState([]);
  const [ready, setReady] = useState(false);
  const seedsRef = useRef(seedMessages);
  seedsRef.current = seedMessages;
  const memoryOnlyRef = useRef(false);

  const refresh = useCallback(async () => {
    const rows = await getMessagesForSession(sessionId);
    setMessages(rows);
  }, [sessionId]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const idbOk = await isIndexedDbChatAvailable();
        if (cancelled) return;
        if (!idbOk) {
          memoryOnlyRef.current = true;
          const seeds = seedsRef.current || [];
          setMessages(seeds.map((s, i) => buildMemoryMessage(sessionId, s, i)));
          return;
        }
        await seedSessionIfEmpty(sessionId, seedsRef.current);
        if (!cancelled) await refresh();
      } catch (e) {
        console.error("[useChatSession] init failed", e);
        try {
          if (!cancelled) await refresh();
        } catch (e2) {
          console.error("[useChatSession] refresh after error failed", e2);
        }
      } finally {
        if (!cancelled) setReady(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [sessionId, refresh]);

  const send = useCallback(
    async (text, role = "user", meta, options) => {
      const trimmed = String(text || "").trim();
      // Allow empty text when the message carries structured meta (answer blocks).
      const hasStructuredMeta = meta && typeof meta === "object" && (meta.variant === "answer-block" || meta.variant === "playground-rich");
      if (!trimmed && !hasStructuredMeta) return undefined;
      const payload = { role, text: trimmed };
      if (meta && typeof meta === "object") {
        payload.meta = meta;
      }
      if (options && typeof options.id === "string" && options.id) {
        payload.id = options.id;
      }
      if (memoryOnlyRef.current) {
        return await new Promise((resolve) => {
          setMessages((prev) => {
            const next = [...prev, buildMemoryMessage(sessionId, payload)];
            resolve(next);
            return next;
          });
        });
      }
      const { ok, msg } = await addChatMessage(sessionId, payload);
      if (ok) {
        await refresh();
        return await getMessagesForSession(sessionId);
      }
      const existing = await getMessagesForSession(sessionId);
      const next = [...existing, msg];
      setMessages(next);
      return next;
    },
    [sessionId, refresh]
  );

  return { messages, ready, send, refresh };
}
