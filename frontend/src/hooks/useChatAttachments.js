/**
 * Manages staged file attachments for a chat composer: open picker, keep a
 * bounded list of pending files, persist them to IndexedDB when the user
 * sends the message, and return lightweight metadata for the message meta.
 *
 * Storage strategy:
 * - `meta.attachments` on the chat message holds only `{id, name, mime, size}`
 * - Raw blobs live in the `chat_attachments` IndexedDB store, keyed by `id`,
 *   with a `messageId` foreign key so they can be cleaned up with the message.
 * - When IndexedDB is unavailable the blob is kept in an in-memory Map only,
 *   so attachments still work inside the current session but do not persist.
 */
import { useCallback, useMemo, useRef, useState } from "react";
import { CHAT_ATTACHMENT_LIMITS } from "../storage/keys";
import { getAttachmentById, putAttachment } from "../storage/idb/chat-db";

const memoryAttachments = new Map();

/**
 * One-shot in-memory buffer for files staged on a "home" composer that then
 * need to survive a client-side navigation into a chat screen. We cannot put
 * Blobs in sessionStorage, but module state is preserved across React Router
 * transitions, so the chat screen can retrieve them by handoff id and persist
 * them under the real user-message id it creates.
 */
const handoffAttachments = new Map();

function makeLocalId() {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `att-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

/**
 * Write blobs to the chat_attachments IndexedDB store under a specific
 * messageId and return lightweight metadata for the message `meta.attachments`.
 * Falls back to the in-memory map when IndexedDB is unavailable.
 */
async function persistItemsForMessage(items, sessionId, messageId) {
  if (!Array.isArray(items) || !items.length) return [];
  const now = Date.now();
  const out = [];
  for (const s of items) {
    const id = s.localId || makeLocalId();
    const record = {
      id,
      sessionId,
      messageId,
      name: s.name,
      mime: s.mime,
      size: s.size,
      blob: s.file,
      createdAt: now,
    };
    const ok = await putAttachment(record);
    if (!ok) memoryAttachments.set(id, record);
    out.push({ id, name: s.name, mime: s.mime, size: s.size });
  }
  return out;
}

/**
 * Stash the currently-staged items from a home-screen composer so a chat
 * screen can pick them up after navigation. Returns a handoff id (or null
 * when there is nothing to stash).
 */
export function stashAttachmentsForHandoff(items) {
  if (!Array.isArray(items) || !items.length) return null;
  const id = makeLocalId();
  handoffAttachments.set(
    id,
    items.map((s) => ({
      localId: s.localId,
      file: s.file,
      name: s.name,
      mime: s.mime,
      size: s.size,
    }))
  );
  return id;
}

/**
 * Persist handoff items under a real chat messageId. Returns the metadata
 * array for `meta.attachments` (empty when the handoff id is unknown).
 */
export async function persistHandoffAttachments(handoffId, sessionId, messageId) {
  if (!handoffId) return [];
  const items = handoffAttachments.get(handoffId);
  if (!items) return [];
  handoffAttachments.delete(handoffId);
  return persistItemsForMessage(items, sessionId, messageId);
}

/** Discard a handoff without persisting (e.g. when the chat never opens). */
export function discardAttachmentsHandoff(handoffId) {
  if (!handoffId) return;
  handoffAttachments.delete(handoffId);
}

export function formatAttachmentSize(bytes) {
  if (!Number.isFinite(bytes) || bytes < 0) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function useChatAttachments() {
  const inputRef = useRef(null);
  const [staged, setStaged] = useState([]);
  const [lastError, setLastError] = useState(null);

  const openPicker = useCallback(() => {
    setLastError(null);
    inputRef.current?.click();
  }, []);

  const addFiles = useCallback((fileList) => {
    const files = Array.from(fileList || []);
    if (!files.length) return;
    setStaged((prev) => {
      const out = [...prev];
      let error = null;
      for (const f of files) {
        if (out.length >= CHAT_ATTACHMENT_LIMITS.MAX_FILES_PER_MESSAGE) {
          error = `Only ${CHAT_ATTACHMENT_LIMITS.MAX_FILES_PER_MESSAGE} files can be attached per message.`;
          break;
        }
        if (f.size > CHAT_ATTACHMENT_LIMITS.MAX_FILE_BYTES) {
          error = `"${f.name}" is larger than ${formatAttachmentSize(
            CHAT_ATTACHMENT_LIMITS.MAX_FILE_BYTES
          )} and was skipped.`;
          continue;
        }
        out.push({
          localId: makeLocalId(),
          file: f,
          name: f.name,
          mime: f.type || "application/octet-stream",
          size: f.size,
        });
      }
      if (error) setLastError(error);
      return out;
    });
  }, []);

  const handleInputChange = useCallback(
    (e) => {
      addFiles(e.target.files);
      // Reset so re-selecting the same file fires onChange again.
      if (inputRef.current) inputRef.current.value = "";
    },
    [addFiles]
  );

  const removeStaged = useCallback((localId) => {
    setStaged((prev) => prev.filter((s) => s.localId !== localId));
  }, []);

  const clearStaged = useCallback(() => {
    setStaged([]);
    setLastError(null);
  }, []);

  /**
   * Persist all staged files under the given messageId and return the metadata
   * array to embed in the chat message `meta.attachments`. Clears staged on
   * completion. Returns `[]` if nothing to persist.
   */
  const persistAll = useCallback(
    async (sessionId, messageId) => {
      if (!staged.length) return [];
      const out = await persistItemsForMessage(staged, sessionId, messageId);
      setStaged([]);
      setLastError(null);
      return out;
    },
    [staged]
  );

  // Props for a visually-hidden <input type="file">. We deliberately do NOT
  // use `display: none` (`hidden`): some Chromium builds silently refuse to
  // forward a <label> click to a display:none file input, which breaks the
  // picker. `sr-only` keeps the element in the layout at 1x1 but invisible,
  // which reliably works across all browsers.
  //
  // Note: in React 18 `ref` does NOT flow through object spread onto DOM
  // elements, so the ref must be attached explicitly by the caller (see
  // `inputRef` in the return value).
  const fileInputProps = useMemo(
    () => ({
      type: "file",
      multiple: true,
      className: "sr-only",
      "aria-hidden": true,
      tabIndex: -1,
      onChange: handleInputChange,
    }),
    [handleInputChange]
  );

  return {
    staged,
    lastError,
    openPicker,
    removeStaged,
    clearStaged,
    persistAll,
    fileInputProps,
    inputRef,
  };
}

/**
 * Open a stored attachment in a new tab. Looks up the blob in IndexedDB first
 * (persistent path) and falls back to the in-memory map (same-session path).
 */
export async function openStoredAttachment(attachmentId) {
  if (!attachmentId) return false;
  let record = await getAttachmentById(attachmentId);
  if (!record) record = memoryAttachments.get(attachmentId) || null;
  if (!record || !record.blob) return false;
  try {
    const url = URL.createObjectURL(record.blob);
    const win = window.open(url, "_blank", "noopener,noreferrer");
    // Revoke after a minute so the browser can release memory while still
    // giving the opened tab time to load.
    window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
    return Boolean(win);
  } catch {
    return false;
  }
}
