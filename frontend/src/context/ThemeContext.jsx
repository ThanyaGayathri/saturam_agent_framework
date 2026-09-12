import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import { DEFAULT_THEME_ID, getTheme } from "../domain/themes";
import {
  readActiveThemeId,
  readAppearancePref,
  writeActiveThemeId,
  writeAppearancePref,
} from "../storage/theme-preference";

const ThemeContext = createContext(null);

/** Values mirror `index.css` :root / `html.dark`; set inline so `bg-surface-page` resolves reliably under Tailwind. */
const APP_SHELL = {
  light: {
    "--app-surface-page": "#f8f9ff",
    "--app-surface-page-top": "#f5f7ff",
    "--app-surface-default": "#ffffff",
    "--app-surface-secondary": "#fafafa",
    "--app-surface-tertiary": "#f7f7f7",
    "--app-text-primary": "#181d27",
    "--app-text-secondary": "#535862",
    "--app-text-tertiary": "#414651",
    "--app-text-muted": "#717680",
    "--app-border": "#e9e9eb",
    "--app-border-light": "#d5d7da",
    "--switch-track-off-bg": "#cdd3df",
    "--switch-track-off-border": "#afb7c9",
  },
  dark: {
    "--app-surface-page": "#0f1117",
    "--app-surface-page-top": "#14161f",
    "--app-surface-default": "#161922",
    "--app-surface-secondary": "#1c2030",
    "--app-surface-tertiary": "#252a38",
    "--app-text-primary": "#eef0fd",
    "--app-text-secondary": "#aeb4c5",
    "--app-text-tertiary": "#c5cbd9",
    "--app-text-muted": "#8f97ab",
    "--app-border": "#2a3042",
    "--app-border-light": "#383f54",
    "--switch-track-off-bg": "#353b4e",
    "--switch-track-off-border": "#484f67",
  },
};

function applyAppShellTokens(root, colorMode) {
  const bucket = APP_SHELL[colorMode === "dark" ? "dark" : "light"];
  Object.entries(bucket).forEach(([key, val]) => root.style.setProperty(key, val));
}

function getSystemPrefersDark() {
  if (typeof window === "undefined") return false;
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

/** @param {"light" | "dark" | "system"} pref */
export function resolveColorMode(pref) {
  if (pref === "dark") return "dark";
  if (pref === "light") return "light";
  return getSystemPrefersDark() ? "dark" : "light";
}

/**
 * @param {string} themeId
 * @param {"light" | "dark"} colorMode resolved (not "system")
 */
export function applyTheme(themeId, colorMode) {
  const theme = getTheme(themeId);
  const root = document.documentElement;

  root.setAttribute("data-theme", theme.id);
  root.style.setProperty("--theme-brand", theme.tokens.brand);
  root.style.setProperty("--theme-brand-hover", theme.tokens.brandHover);
  root.style.setProperty("--theme-brand-light", theme.tokens.brandLight);
  root.style.setProperty("--theme-brand-light-hover", theme.tokens.brandLightHover);
  root.style.setProperty("--theme-success", theme.tokens.success);
  root.style.setProperty("--theme-warning", theme.tokens.warning);
  root.style.setProperty("--theme-danger", theme.tokens.danger);
  root.style.setProperty("--theme-info", theme.tokens.info);

  const s = theme.semanticStatus.success;
  const w = theme.semanticStatus.warning;
  const c = theme.semanticStatus.critical;
  root.style.setProperty("--theme-status-success", s.DEFAULT);
  root.style.setProperty("--theme-status-success-light", s.light);
  root.style.setProperty("--theme-status-success-border", s.border);
  root.style.setProperty("--theme-status-success-muted", s.muted);
  root.style.setProperty("--theme-status-success-dot", s.dot);
  root.style.setProperty("--theme-status-warning", w.DEFAULT);
  root.style.setProperty("--theme-status-warning-light", w.light);
  root.style.setProperty("--theme-status-warning-border", w.border);
  root.style.setProperty("--theme-status-warning-ring", w.ring);
  root.style.setProperty("--theme-status-warning-dot", w.dot);
  root.style.setProperty("--theme-status-warning-accent", w.accent);
  root.style.setProperty("--theme-status-critical", c.DEFAULT);
  root.style.setProperty("--theme-status-critical-light", c.light);
  root.style.setProperty("--theme-status-critical-border", c.border);
  root.style.setProperty("--theme-status-critical-ring", c.ring);
  root.style.setProperty("--theme-status-critical-dot", c.dot);
  root.style.setProperty("--theme-status-critical-accent", c.accent);

  const inf = theme.semanticStatus.info;
  root.style.setProperty("--theme-status-info", inf.DEFAULT);
  root.style.setProperty("--theme-status-info-light", inf.light);
  root.style.setProperty("--theme-status-info-border", inf.border);
  root.style.setProperty("--theme-status-info-accent", inf.accent);
  root.style.setProperty("--theme-status-info-dot", inf.dot);

  applyAppShellTokens(root, colorMode);

  root.classList.toggle("dark", colorMode === "dark");
  root.setAttribute("data-color-mode", colorMode);
}

export function initThemeOnStartup() {
  try {
    const themeId = readActiveThemeId();
    const pref = readAppearancePref();
    const colorMode = resolveColorMode(pref);
    applyTheme(themeId, colorMode);
    document.documentElement.setAttribute("data-appearance-pref", pref);
  } catch {
    applyTheme(DEFAULT_THEME_ID, "light");
  }
}

export function ThemeProvider({ children }) {
  const [themeId, setThemeId] = useState(() => readActiveThemeId());
  const [appearance, setAppearance] = useState(() => readAppearancePref());
  const [systemTick, setSystemTick] = useState(0);

  useEffect(() => {
    if (appearance !== "system") return undefined;
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => setSystemTick((t) => t + 1);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [appearance]);

  const colorMode = useMemo(
    () => resolveColorMode(appearance),
    [appearance, systemTick],
  );

  useEffect(() => {
    applyTheme(themeId, colorMode);
    document.documentElement.setAttribute("data-appearance-pref", appearance);
  }, [themeId, colorMode, appearance]);

  const selectTheme = useCallback((id) => {
    setThemeId(writeActiveThemeId(id));
  }, []);

  const selectAppearance = useCallback((pref) => {
    setAppearance(writeAppearancePref(pref));
  }, []);

  const value = useMemo(
    () => ({
      themeId,
      theme: getTheme(themeId),
      selectTheme,
      appearance,
      colorMode,
      selectAppearance,
    }),
    [themeId, appearance, colorMode, selectTheme, selectAppearance],
  );

  return (
    <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
  );
}

export function useTheme() {
  const ctx = useContext(ThemeContext);
  if (!ctx) {
    throw new Error("useTheme must be used within ThemeProvider");
  }
  return ctx;
}
