/** @type {import('tailwindcss').Config} */
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
          DEFAULT:       "var(--theme-brand, #731dd8)",
          hover:         "var(--theme-brand-hover, #5d18ad)",
          light:         "var(--theme-brand-light, #f3e9ff)",
          "light-hover": "var(--theme-brand-light-hover, #e9d6ff)",
        },
        // Text — CSS vars toggle in dark mode (`index.css` → `html.dark`)
        text: {
          primary: "var(--app-text-primary, #181d27)",
          secondary: "var(--app-text-secondary, #535862)",
          tertiary: "var(--app-text-tertiary, #414651)",
          muted: "var(--app-text-muted, #717680)",
        },
        // Backgrounds — same semantic tokens.
        // Both `DEFAULT` (→ `bg-surface`) and `default` (→ `bg-surface-default`)
        // are exposed so either utility name works in components.
        surface: {
          DEFAULT: "var(--app-surface-default, #ffffff)",
          default: "var(--app-surface-default, #ffffff)",
          secondary: "var(--app-surface-secondary, #fafafa)",
          tertiary: "var(--app-surface-tertiary, #f7f7f7)",
          page: "var(--app-surface-page, #f8f9ff)",
          "page-top": "var(--app-surface-page-top, #f5f7ff)",
        },
        // Borders — same dual-key pattern.
        border: {
          DEFAULT: "var(--app-border, #e9e9eb)",
          default: "var(--app-border, #e9e9eb)",
          light: "var(--app-border-light, #d5d7da)",
        },
        // Brand
        brand: {
          dark: "#2a2a37",
        },
        danger: {
          DEFAULT: "var(--theme-danger, #f44708)",
        },
        // Status (alerts, KPI deltas, health). Driven by theme preset via CSS vars.
        status: {
          success: {
            DEFAULT: "var(--theme-status-success, #05a8aa)",
            light: "var(--theme-status-success-light, #e6fffe)",
            border: "var(--theme-status-success-border, #5eead4)",
            muted: "var(--theme-status-success-muted, #ccfbf1)",
            dot: "var(--theme-status-success-dot, #0d9488)",
          },
          warning: {
            DEFAULT: "var(--theme-status-warning, #d97706)",
            light: "var(--theme-status-warning-light, #fffbeb)",
            border: "var(--theme-status-warning-border, #fde68a)",
            ring: "var(--theme-status-warning-ring, #fcd34d)",
            dot: "var(--theme-status-warning-dot, #d97706)",
            accent: "var(--theme-status-warning-accent, #fcd34d)",
          },
          critical: {
            DEFAULT: "var(--theme-status-critical, #dc2626)",
            light: "var(--theme-status-critical-light, #fef2f2)",
            border: "var(--theme-status-critical-border, #fecaca)",
            ring: "var(--theme-status-critical-ring, #f87171)",
            dot: "var(--theme-status-critical-dot, #dc2626)",
            accent: "var(--theme-status-critical-accent, #ef4444)",
          },
          info: {
            DEFAULT: "var(--theme-status-info, #5271ff)",
            light: "var(--theme-status-info-light, #f0f4ff)",
            border: "var(--theme-status-info-border, #b4c4ff)",
            accent: "var(--theme-status-info-accent, #4361ee)",
            dot: "var(--theme-status-info-dot, #5271ff)",
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
