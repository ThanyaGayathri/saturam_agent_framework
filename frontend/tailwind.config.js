/** @type {import('tailwindcss').Config} */

// Every semantic colour below is a CSS variable with a hex fallback, so `useTheme()` can
// repaint the app at runtime. A bare `var(...)` string, though, is opaque to Tailwind's
// opacity modifier: it cannot parse the channels, so `bg-primary/10`, `border-primary/40`
// and `ring-primary/25` compiled to no rule at all -- over a thousand of them across the
// app, which is why tints, soft borders and focus rings were invisible.
//
// Returning a function instead lets Tailwind hand us the requested alpha, and `color-mix`
// applies it to the variable without needing to know its channels. `opacityValue` arrives
// undefined for the plain utility, as a number-ish string for `/40`, and as a var reference
// for the legacy `bg-opacity-*` classes -- `calc()` covers the last two.
const themed = (variable, fallback) => {
  const base = `var(${variable}, ${fallback})`;
  return ({ opacityValue }) =>
    opacityValue === undefined
      ? base
      : `color-mix(in srgb, ${base} calc(${opacityValue} * 100%), transparent)`;
};

export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  // Dark mode is toggled via `.dark` class on <html> by the useTheme hook.
  darkMode: "class",
  theme: {
    extend: {
      fontFamily: {
        sans: ["Inter", "system-ui", "sans-serif"],
      },
      colors: {
        // Primary — driven by CSS variables set by useTheme(). The hex
        // fallbacks preserve the previous palette if the variable is missing.
        primary: {
          DEFAULT:       themed("--theme-brand", "#731dd8"),
          hover:         themed("--theme-brand-hover", "#5d18ad"),
          light:         themed("--theme-brand-light", "#f3e9ff"),
          "light-hover": themed("--theme-brand-light-hover", "#e9d6ff"),
        },
        // Text — CSS vars toggle in dark mode (`index.css` → `html.dark`)
        text: {
          primary: themed("--app-text-primary", "#181d27"),
          secondary: themed("--app-text-secondary", "#535862"),
          tertiary: themed("--app-text-tertiary", "#414651"),
          muted: themed("--app-text-muted", "#717680"),
        },
        // Backgrounds — same semantic tokens.
        // Both `DEFAULT` (→ `bg-surface`) and `default` (→ `bg-surface-default`)
        // are exposed so either utility name works in components.
        surface: {
          DEFAULT: themed("--app-surface-default", "#ffffff"),
          default: themed("--app-surface-default", "#ffffff"),
          secondary: themed("--app-surface-secondary", "#fafafa"),
          tertiary: themed("--app-surface-tertiary", "#f7f7f7"),
          page: themed("--app-surface-page", "#f8f9ff"),
          "page-top": themed("--app-surface-page-top", "#f5f7ff"),
        },
        // Borders — same dual-key pattern.
        border: {
          DEFAULT: themed("--app-border", "#e9e9eb"),
          default: themed("--app-border", "#e9e9eb"),
          light: themed("--app-border-light", "#d5d7da"),
        },
        // Brand
        brand: {
          dark: "#2a2a37",
        },
        danger: {
          DEFAULT: themed("--theme-danger", "#f44708"),
        },
        // Status (alerts, KPI deltas, health). Driven by theme preset via CSS vars.
        status: {
          success: {
            DEFAULT: themed("--theme-status-success", "#05a8aa"),
            light: themed("--theme-status-success-light", "#e6fffe"),
            border: themed("--theme-status-success-border", "#5eead4"),
            muted: themed("--theme-status-success-muted", "#ccfbf1"),
            dot: themed("--theme-status-success-dot", "#0d9488"),
          },
          warning: {
            DEFAULT: themed("--theme-status-warning", "#d97706"),
            light: themed("--theme-status-warning-light", "#fffbeb"),
            border: themed("--theme-status-warning-border", "#fde68a"),
            ring: themed("--theme-status-warning-ring", "#fcd34d"),
            dot: themed("--theme-status-warning-dot", "#d97706"),
            accent: themed("--theme-status-warning-accent", "#fcd34d"),
          },
          critical: {
            DEFAULT: themed("--theme-status-critical", "#dc2626"),
            light: themed("--theme-status-critical-light", "#fef2f2"),
            border: themed("--theme-status-critical-border", "#fecaca"),
            ring: themed("--theme-status-critical-ring", "#f87171"),
            dot: themed("--theme-status-critical-dot", "#dc2626"),
            accent: themed("--theme-status-critical-accent", "#ef4444"),
          },
          info: {
            DEFAULT: themed("--theme-status-info", "#5271ff"),
            light: themed("--theme-status-info-light", "#f0f4ff"),
            border: themed("--theme-status-info-border", "#b4c4ff"),
            accent: themed("--theme-status-info-accent", "#4361ee"),
            dot: themed("--theme-status-info-dot", "#5271ff"),
          },
        },
        // Avatar
        avatar: "#ced1e5",
        // Use case gallery cards (light lavender panel)
        gallery: {
          card: "#f3f4f9",
          "card-hover": "#eceef5",
          border: "#e4e6ed",
        },
        // Use case builder chrome: the cool grey-lavender rail panel and the border shared by
        // the rail and the sheet beside it, so the two read as one pair. Fixed values like
        // `gallery` above -- dark mode is handled by `dark:` utilities at the call site.
        builder: {
          rail: "#e9ebf4",
          border: "#dcdfeb",
        },
      },
      borderRadius: {
        sm: "4px",
        md: "6px",
        lg: "8px",
        xl: "12px",
      },
      boxShadow: {
        sm: "0 1px 2px 0 rgba(0,0,0,0.05)",
        DEFAULT: "0 1px 3px 0 rgba(0,0,0,0.1)",
        xl: "0 20px 25px -5px rgba(0,0,0,0.1)",
        "gallery-card":
          "0 1px 2px rgba(24, 29, 39, 0.04), 0 4px 12px rgba(105, 65, 198, 0.06)",
      },
      backgroundImage: {
        "gallery-operational":
          "linear-gradient(to bottom right, #efe8ff 0%, #f7f3ff 42%, #ffffff 100%)",
        "gallery-customer":
          "linear-gradient(to bottom right, #efe8ff 0%, #f0f0ff 48%, #ffffff 100%)",
      },
      spacing: {
        18: "72px",
      },
    },
  },
  plugins: [],
};
