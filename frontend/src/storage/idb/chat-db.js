import { IDB } from "../keys";

let dbPromise;

/**
 * Invalidate the cached openDb() promise so the next caller reopens fresh.
 * Called on any transaction error and on IDBDatabase onclose/onversionchange
 * events — otherwise a browser-side delete (DevTools, `indexedDB.deleteDatabase`,
 * "Clear site data") leaves the module cache pointing at a closed handle and
 * every subsequent write silently returns false.
 */
function invalidateDb() {
  dbPromise = null;
}

function openDb() {
  if (dbPromise) return dbPromise;
  dbPromise = new Promise((resolve, reject) => {
    if (typeof indexedDB === "undefined") {
      resolve(null);
      return;
    }
    const req = indexedDB.open(IDB.DB_NAME, IDB.DB_VERSION);
    req.onerror = () => {
      dbPromise = null;
      reject(req.error);
    };
    req.onupgradeneeded = (event) => {
      const db = event.target.result;
      const tx = event.target.transaction;
      if (!db.objectStoreNames.contains(IDB.STORE_MESSAGES)) {
        const store = db.createObjectStore(IDB.STORE_MESSAGES, { keyPath: "id" });
        store.createIndex("sessionId", "sessionId", { unique: false });
      } else {
        const store = tx.objectStore(IDB.STORE_MESSAGES);
        if (!store.indexNames.contains("sessionId")) {
          store.createIndex("sessionId", "sessionId", { unique: false });
        }
      }
      if (!db.objectStoreNames.contains(IDB.STORE_ATTACHMENTS)) {
        const attStore = db.createObjectStore(IDB.STORE_ATTACHMENTS, { keyPath: "id" });
        attStore.createIndex("messageId", "messageId", { unique: false });
        attStore.createIndex("sessionId", "sessionId", { unique: false });
      } else {
        const attStore = tx.objectStore(IDB.STORE_ATTACHMENTS);
        if (!attStore.indexNames.contains("messageId")) {
          attStore.createIndex("messageId", "messageId", { unique: false });
        }
        if (!attStore.indexNames.contains("sessionId")) {
          attStore.createIndex("sessionId", "sessionId", { unique: false });
        }
      }
    };
    req.onsuccess = () => {
      const db = req.result;
      // Invalidate the cache when the browser closes this connection (e.g. a
      // DevTools "Clear site data" or a manual `indexedDB.deleteDatabase`
      // triggers `onversionchange` on any open connections). Without this,
      // subsequent transactions on the stale handle throw InvalidStateError
      // and messages silently fail to persist.
      db.onclose = () => invalidateDb();
      db.onversionchange = () => {
        try {
          db.close();
        } finally {
          invalidateDb();
        }
      };
      resolve(db);
    };
  });
  return dbPromise;
}

export const CHAT_SESSIONS = {
  PLAYGROUND: "playground-active",
  /** Bumped when builder seed/replies should reset (same seed as Playground). */
  BUILDER: "builder-chat-v3",
};

function sortByCreatedAt(rows) {
  const list = rows || [];
  list.sort((a, b) => (a.createdAt || 0) - (b.createdAt || 0));
  return list;
}

export async function getMessagesForSession(sessionId) {
  try {
    const db = await openDb();
    if (!db) return [];
    return await new Promise((resolve, reject) => {
      const tx = db.transaction(IDB.STORE_MESSAGES, "readonly");
      const store = tx.objectStore(IDB.STORE_MESSAGES);
      const runWithIndex = () => {
        const idx = store.index("sessionId");
        const req = idx.getAll(sessionId);
        req.onerror = () => reject(req.error);
        req.onsuccess = () => resolve(sortByCreatedAt(req.result || []));
      };
      if (store.indexNames.contains("sessionId")) {
        runWithIndex();
        return;
      }
      const reqAll = store.getAll();
      reqAll.onerror = () => reject(reqAll.error);
      reqAll.onsuccess = () => {
        const rows = (reqAll.result || []).filter((r) => r.sessionId === sessionId);
        resolve(sortByCreatedAt(rows));
      };
    });
  } catch {
    // Cached handle is stale (browser deleted the DB out from under us).
    // Drop the cache so callers reopen fresh on the next tick.
    invalidateDb();
    return [];
  }
}

/**
 * Delete a conversation's messages and attachments.
 *
 * Deleting a chat used to remove only its row from the index, leaving every message behind.
 * They were invisible but not gone: the next load saw a session with messages in it and
 * offered the "cleared" conversation straight back.
 *
 * @returns {Promise<boolean>}
 */
export async function deleteSession(sessionId) {
  try {
    const db = await openDb();
    if (!db) return false;
    return await new Promise((resolve, reject) => {
      const tx = db.transaction([IDB.STORE_MESSAGES, IDB.STORE_ATTACHMENTS], "readwrite");
      tx.onerror = () => reject(tx.error);
      tx.oncomplete = () => resolve(true);
      for (const storeName of [IDB.STORE_MESSAGES, IDB.STORE_ATTACHMENTS]) {
        const store = tx.objectStore(storeName);
        if (store.indexNames.contains("sessionId")) {
          const req = store.index("sessionId").getAllKeys(sessionId);
          req.onsuccess = () => (req.result || []).forEach((key) => store.delete(key));
          continue;
        }
        // No index on this store yet: fall back to a scan rather than leaving rows behind.
        const all = store.getAll();
        all.onsuccess = () =>
          (all.result || [])
            .filter((row) => row.sessionId === sessionId)
            .forEach((row) => store.delete(row.id));
      }
    });
  } catch {
    invalidateDb();
    return false;
  }
}

/** @returns {Promise<boolean>} */
export async function putMessage(sessionId, message) {
  try {
    const db = await openDb();
    if (!db) return false;
    await new Promise((resolve, reject) => {
      const tx = db.transaction(IDB.STORE_MESSAGES, "readwrite");
      const store = tx.objectStore(IDB.STORE_MESSAGES);
      const req = store.put(message);
      req.onerror = () => reject(req.error);
      req.onsuccess = () => resolve();
    });
    return true;
  } catch {
    // A common reason we get here is that the browser deleted the DB out
    // from under us (DevTools reset, `indexedDB.deleteDatabase`, etc.) so
    // the cached handle is now unusable. Drop the cache so the next caller
    // reopens fresh and one retry lands on a valid connection.
    invalidateDb();
    try {
      const db2 = await openDb();
      if (!db2) return false;
      await new Promise((resolve, reject) => {
        const tx = db2.transaction(IDB.STORE_MESSAGES, "readwrite");
        const store = tx.objectStore(IDB.STORE_MESSAGES);
        const req = store.put(message);
        req.onerror = () => reject(req.error);
        req.onsuccess = () => resolve();
      });
      return true;
    } catch {
      return false;
    }
  }
}

/** Ensures stable transcript order when two messages share the same millisecond `Date.now()`. */
let createdAtTieBreaker = 0;

/**
 * @returns {Promise<{ ok: boolean, msg: object }>}
 */
export async function addChatMessage(sessionId, { role, text, meta, id }) {
  createdAtTieBreaker += 1;
  const msg = {
    id: id || crypto.randomUUID?.() || `m-${Date.now()}-${Math.random().toString(16).slice(2)}`,
    sessionId,
    role,
    text,
    createdAt: Date.now() + createdAtTieBreaker * 1e-6,
    meta: meta && typeof meta === "object" ? meta : undefined,
  };
  const ok = await putMessage(sessionId, msg);
  return { ok, msg };
}

/** Store an attachment blob keyed by {id} with a messageId foreign key. */
export async function putAttachment(record) {
  try {
    const db = await openDb();
    if (!db) return false;
    await new Promise((resolve, reject) => {
      const tx = db.transaction(IDB.STORE_ATTACHMENTS, "readwrite");
      const store = tx.objectStore(IDB.STORE_ATTACHMENTS);
      const req = store.put(record);
      req.onerror = () => reject(req.error);
      req.onsuccess = () => resolve();
    });
    return true;
  } catch {
    return false;
  }
}

/** @returns {Promise<object|null>} Full attachment row including the stored Blob, or null if missing. */
export async function getAttachmentById(id) {
  try {
    const db = await openDb();
    if (!db) return null;
    return await new Promise((resolve, reject) => {
      const tx = db.transaction(IDB.STORE_ATTACHMENTS, "readonly");
      const store = tx.objectStore(IDB.STORE_ATTACHMENTS);
      const req = store.get(id);
      req.onerror = () => reject(req.error);
      req.onsuccess = () => resolve(req.result || null);
    });
  } catch {
    return null;
  }
}

/** False when IndexedDB is missing or cannot open (chat should use in-memory fallback). */
export async function isIndexedDbChatAvailable() {
  try {
    const db = await openDb();
    return !!db;
  } catch {
    return false;
  }
}

/** Serialize seeding per session so concurrent calls (e.g. React Strict Mode) cannot double-seed. */
const seedChains = new Map();

function enqueueSeedWork(sessionId, work) {
  const prev = seedChains.get(sessionId) || Promise.resolve();
  const next = prev.then(work).catch(() => {});
  seedChains.set(sessionId, next);
  return next;
}

export async function seedSessionIfEmpty(sessionId, seedMessages) {
  if (!seedMessages?.length) return;
  await enqueueSeedWork(sessionId, async () => {
    const existing = await getMessagesForSession(sessionId);
    if (existing.length > 0) return;
    for (const s of seedMessages) {
      try {
        const { ok } = await addChatMessage(sessionId, s);
        if (!ok) break;
      } catch {
        // Ignore seeding failures; UI will still render seeded/previous messages when available.
      }
    }
  });
}
