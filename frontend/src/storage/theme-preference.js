import { DEFAULT_THEME_ID, isThemeId } from "../domain/themes";

const THEME_STORAGE_KEY = "retailgpt:theme-active:v2";
const THEME_STORAGE_KEY_V1 = "retailgpt:theme-active:v1";
const APPEARANCE_STORAGE_KEY = "retailgpt:theme-appearance:v1";

/** One-time migration from v1 ids (classic / vivid / duo) to v2 (classic / soft). */
function migrateThemeStorageFromV1() {
  try {
    if (localStorage.getItem(THEME_STORAGE_KEY)) return;
    const v1 = localStorage.getItem(THEME_STORAGE_KEY_V1);
    if (!v1) return;
    let next = DEFAULT_THEME_ID;
    if (v1 === "vivid" || v1 === "duo") next = "classic";
    else if (v1 === "classic") next = "soft";
    else if (isThemeId(v1)) next = v1;
    localStorage.setItem(THEME_STORAGE_KEY, next);
    localStorage.removeItem(THEME_STORAGE_KEY_V1);
  } catch {
    /* ignore */
  }
}

/** @returns {"light" | "dark" | "system"} */
export function readAppearancePref() {
  try {
    const raw = localStorage.getItem(APPEARANCE_STORAGE_KEY);
    if (raw === "light" || raw === "dark" || raw === "system") return raw;
  } catch {
    /* ignore */
  }
  return "light";
}

/** @param {"light" | "dark" | "system"} value */
export function writeAppearancePref(value) {
  const next =
    value === "dark" ? "dark" : value === "system" ? "system" : "light";
  try {
    localStorage.setItem(APPEARANCE_STORAGE_KEY, next);
  } catch {
    /* ignore */
  }
  return next;
}

export function readActiveThemeId() {
  try {
    migrateThemeStorageFromV1();
    const raw = localStorage.getItem(THEME_STORAGE_KEY);
    if (raw && isThemeId(raw)) return raw;
  } catch {
    /* ignore */
  }
  return DEFAULT_THEME_ID;
}

export function writeActiveThemeId(id) {
  const next = isThemeId(id) ? id : DEFAULT_THEME_ID;
  try {
    localStorage.setItem(THEME_STORAGE_KEY, next);
  } catch {
    /* ignore */
  }
  return next;
}

export {
  THEME_STORAGE_KEY,
  THEME_STORAGE_KEY_V1,
  APPEARANCE_STORAGE_KEY,
};
