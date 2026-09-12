/**
 * Small repository over Supabase for the four custom_* tables. Every domain
 * (KPIs, use cases, processes, alerts) uses the same CRUD shape, so we
 * define it once here.
 *
 * All functions gracefully no-op when Supabase isn't configured — callers
 * still get a working seed-only view.
 *
 * Rows are scoped by profile_id ('default' | 'gourmet-foods-intl' | 'middleby').
 * Soft-delete: rows keep a `deleted_at` timestamp; reads filter it out.
 */

import { supabase, isSupabaseEnabled } from "./supabase";

/**
 * List all active (non-deleted) custom rows for a given table + profile.
 * Returns [] when Supabase is disabled.
 *
 * @param {string} tableName
 * @param {string} profileId
 * @returns {Promise<object[]>}
 */
export async function listCustomRows(tableName, profileId) {
  if (!isSupabaseEnabled() || !profileId) return [];
  const { data, error } = await supabase
    .from(tableName)
    .select("*")
    .eq("profile_id", profileId)
    .is("deleted_at", null)
    .order("created_at", { ascending: false });
  if (error) {
    console.warn(`[customRepo] listCustomRows(${tableName}) failed`, error);
    return [];
  }
  return data || [];
}

/**
 * Insert one row. Supabase generates the id + timestamps.
 *
 * @param {string} tableName
 * @param {object} row  - domain payload (columns already snake_cased)
 * @returns {Promise<{ok: true, data: object} | {ok: false, error: string}>}
 */
export async function insertCustomRow(tableName, row) {
  if (!isSupabaseEnabled()) {
    return { ok: false, error: "supabase-disabled" };
  }
  const { data, error } = await supabase
    .from(tableName)
    .insert(row)
    .select()
    .single();
  if (error) {
    console.warn(`[customRepo] insertCustomRow(${tableName}) failed`, error);
    return { ok: false, error: String(error.message || error) };
  }
  return { ok: true, data };
}

/**
 * Update fields on one row by id. Supabase's updated_at trigger touches
 * the timestamp automatically.
 */
export async function updateCustomRow(tableName, id, patch) {
  if (!isSupabaseEnabled()) {
    return { ok: false, error: "supabase-disabled" };
  }
  const { data, error } = await supabase
    .from(tableName)
    .update(patch)
    .eq("id", id)
    .select()
    .single();
  if (error) {
    console.warn(`[customRepo] updateCustomRow(${tableName}) failed`, error);
    return { ok: false, error: String(error.message || error) };
  }
  return { ok: true, data };
}

/**
 * Soft-delete: set deleted_at = now. The undo action just sets it back to null
 * within the 24h window.
 */
export async function softDeleteCustomRow(tableName, id) {
  return updateCustomRow(tableName, id, { deleted_at: new Date().toISOString() });
}

/**
 * Undo a soft-delete by clearing deleted_at. No time-check here — the undo
 * UI enforces the 24h window; if a user hits the API directly outside the
 * window, that's fine, the row just comes back.
 */
export async function undoDeleteCustomRow(tableName, id) {
  return updateCustomRow(tableName, id, { deleted_at: null });
}

/**
 * Subscribe to inserts/updates/deletes on a custom_* table scoped to a
 * profile. `onChange` is called with (eventType, row) each time a change
 * propagates. Returns an unsubscribe function.
 *
 * Gracefully no-ops when Supabase is disabled.
 */
export function subscribeCustomTable(tableName, profileId, onChange) {
  if (!isSupabaseEnabled() || !profileId) return () => {};
  // Unique channel per call. Supabase caches channels by name, so a stable
  // name gets reused across React StrictMode's double-mount and rejects new
  // `.on()` callbacks with "cannot add callbacks after subscribe()".
  const nonce = Math.random().toString(36).slice(2, 10);
  const channel = supabase
    .channel(`custom-${tableName}-${profileId}-${nonce}`)
    .on(
      "postgres_changes",
      {
        event: "*",
        schema: "public",
        table: tableName,
        filter: `profile_id=eq.${profileId}`,
      },
      (payload) => {
        try {
          onChange?.(payload.eventType, payload.new || payload.old);
        } catch (e) {
          console.warn(`[customRepo] subscribe callback threw`, e);
        }
      }
    )
    .subscribe();
  return () => {
    try {
      supabase.removeChannel(channel);
    } catch {
      /* ignore */
    }
  };
}
