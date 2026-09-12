export function canUseBrowserStorage() {
  return typeof window !== "undefined" && window.localStorage != null;
}

export function readLocalJson(key, fallback) {
  if (!canUseBrowserStorage()) return fallback;
  try {
    const raw = window.localStorage.getItem(key);
    if (raw == null || raw === "") return fallback;
    return JSON.parse(raw);
  } catch {
    return fallback;
  }
}

export function writeLocalJson(key, value) {
  if (!canUseBrowserStorage()) return;
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch (e) {
    console.warn("[retailgpt] localStorage write failed", key, e);
  }
}

export function removeLocalKey(key) {
  if (!canUseBrowserStorage()) return;
  try {
    window.localStorage.removeItem(key);
  } catch {
    /* ignore */
  }
}
