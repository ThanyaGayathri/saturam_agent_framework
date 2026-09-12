// Color theme presets surfaced in Profile → Theme.
//
// `tokens` map to CSS custom properties on :root. `semanticStatus` drives
// Tailwind `status.*` utilities for alerts, Monitor, and processes.

/** Calm violet + neutral statuses (secondary preset, id `soft`). */
const SOFT_SEMANTIC = {
  success: {
    DEFAULT: "#0b7b46",
    light: "#ecfdf3",
    border: "#a7f3d0",
    muted: "#dcfce7",
    dot: "#16a34a",
  },
  warning: {
    DEFAULT: "#b45309",
    light: "#fffbeb",
    border: "#fde68a",
    ring: "#fbbf24",
    dot: "#d97706",
    accent: "#fcd34d",
  },
  critical: {
    DEFAULT: "#b42318",
    light: "#fef2f2",
    border: "#fecaca",
    ring: "#f87171",
    dot: "#dc2626",
    accent: "#dc2626",
  },
  info: {
    DEFAULT: "#2563eb",
    light: "#eff6ff",
    border: "#93c5fd",
    accent: "#3b82f6",
    dot: "#3b82f6",
  },
};

/** Default product palette (primary preset, id `classic`). */
const CLASSIC_SEMANTIC = {
  success: {
    DEFAULT: "#05A8AA",
    light: "#e6fffe",
    border: "#5eead4",
    muted: "#ccfbf1",
    dot: "#0d9488",
  },
  warning: {
    DEFAULT: "#d97706",
    light: "#fffbeb",
    border: "#fde68a",
    ring: "#fcd34d",
    dot: "#d97706",
    accent: "#fcd34d",
  },
  critical: {
    DEFAULT: "#dc2626",
    light: "#fef2f2",
    border: "#fecaca",
    ring: "#f87171",
    dot: "#dc2626",
    accent: "#ef4444",
  },
  info: {
    DEFAULT: "#5271ff",
    light: "#f0f4ff",
    border: "#b4c4ff",
    accent: "#4361ee",
    dot: "#5271ff",
  },
};

export const THEMES = [
  {
    id: "classic",
    name: "Classic",
    description:
      "Default palette. Saturated brand violet and high-contrast status colors for dashboards.",
    tokens: {
      brand: "#731DD8",
      brandHover: "#5d18ad",
      brandLight: "#f3e9ff",
      brandLightHover: "#e9d6ff",
      success: "#05A8AA",
      warning: "#FFAE03",
      danger: "#F44708",
      info: "#5271ff",
    },
    semanticStatus: CLASSIC_SEMANTIC,
  },
  {
    id: "soft",
    name: "Soft",
    description: "Calm violet with neutral status colors.",
    tokens: {
      brand: "#6941c6",
      brandHover: "#5b35b3",
      brandLight: "#f0f0ff",
      brandLightHover: "#ebe5ff",
      success: "#0b7b46",
      warning: "#b45309",
      danger: "#dc2626",
      info: "#2563eb",
    },
    semanticStatus: SOFT_SEMANTIC,
  },
];

export const DEFAULT_THEME_ID = "classic";

export function getTheme(id) {
  return THEMES.find((t) => t.id === id) ?? THEMES[0];
}

export function isThemeId(id) {
  return typeof id === "string" && THEMES.some((t) => t.id === id);
}
