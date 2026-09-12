/**
 * Supabase client for shared team additions (custom KPIs, use cases,
 * processes, alerts). Seed data stays in the repo (JS/JSON) - only
 * user-added content lives here.
 *
 * Access model (iteration 1): the prototype's own login gates the app.
 * Anyone past that login can read + write. Supabase Auth is not used.
 *
 * If env vars aren't set (local dev without Supabase configured, Vercel
 * without secrets), `supabase` is `null` and every hook falls back to
 * seed-only mode - the app doesn't crash. See `isSupabaseEnabled()`.
 *
 * Setup: docs/supabase-setup.md
 */

import { createClient } from "@supabase/supabase-js";

const SUPABASE_URL = import.meta.env.VITE_SUPABASE_URL;
const SUPABASE_ANON_KEY = import.meta.env.VITE_SUPABASE_ANON_KEY;

/** Set to a real client when env vars are configured, otherwise null. */
export const supabase =
  SUPABASE_URL && SUPABASE_ANON_KEY
    ? createClient(SUPABASE_URL, SUPABASE_ANON_KEY, {
        auth: {
          // We are not using Supabase Auth. Skip session persistence to
          // avoid any warnings when the client is created in an anon-only
          // context.
          persistSession: false,
          autoRefreshToken: false,
        },
      })
    : null;

/** True when the client is configured. Guards all hook-level Supabase calls. */
export function isSupabaseEnabled() {
  return supabase !== null;
}

/** Table names used across the custom_* tables + hidden_seed_rows. */
export const CUSTOM_TABLES = {
  kpis: "custom_kpis",
  useCases: "custom_use_cases",
  processes: "custom_processes",
  alerts: "custom_alerts",
  internalSources: "custom_internal_sources",
  llmModels: "custom_llm_models",
  externalIntegrations: "custom_external_integrations",
  hiddenSeeds: "hidden_seed_rows",
};

/** Warn once per session if Supabase is missing so setup issues are visible. */
if (typeof window !== "undefined" && !isSupabaseEnabled()) {
  const key = "__supabase_missing_warn__";
  if (!window[key]) {
    window[key] = true;
    // Not an error - the app has a working seed-only fallback. But this
    // logs so a developer notices env vars aren't set.
    console.info(
      "[supabase] VITE_SUPABASE_URL / VITE_SUPABASE_ANON_KEY not set — running in seed-only mode. See docs/supabase-setup.md."
    );
  }
}
