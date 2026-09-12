import { useCallback, useEffect, useMemo, useState } from "react";
import { useDemoProfile } from "../context/DemoProfileContext";
import { supabase, isSupabaseEnabled, CUSTOM_TABLES } from "../lib/supabase";
import { subscribeCustomTable } from "../lib/customRepo";

/**
 * Hidden Seed Rows hook.
 *
 * Reads the `hidden_seed_rows` Supabase table and exposes a fast lookup +
 * hide/show mutations. Consumers (useKpiRegistry, useProcesses,
 * useUseCaseCatalog) filter seed rows out of their merged data using
 * `isHidden(kind, seedId)`.
 *
 * Kinds: 'kpi' | 'process' | 'use_case'
 *
 * Gracefully no-ops (empty set) when Supabase isn't configured.
 */

const KINDS = ["kpi", "process", "use_case"];

export function useHiddenSeedRows() {
  const { profileId } = useDemoProfile();
  const [rows, setRows] = useState([]);
  const [hydrated, setHydrated] = useState(false);

  const refresh = useCallback(async () => {
    if (!isSupabaseEnabled() || !profileId) {
      setRows([]);
      setHydrated(true);
      return;
    }
    const { data, error } = await supabase
      .from(CUSTOM_TABLES.hiddenSeeds)
      .select("*")
      .eq("profile_id", profileId);
    if (error) {
      console.warn("[useHiddenSeedRows] fetch failed", error);
      setRows([]);
    } else {
      setRows(data || []);
    }
    setHydrated(true);
  }, [profileId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (!isSupabaseEnabled() || !profileId) return undefined;
    return subscribeCustomTable(CUSTOM_TABLES.hiddenSeeds, profileId, () => {
      void refresh();
    });
  }, [profileId, refresh]);

  // Composite key so lookup is O(1) and doesn't need a nested Map.
  const hiddenKeySet = useMemo(() => {
    const s = new Set();
    for (const r of rows) {
      if (KINDS.includes(r.kind) && r.seed_id) s.add(`${r.kind}:${r.seed_id}`);
    }
    return s;
  }, [rows]);

  const isHidden = useCallback(
    (kind, seedId) => hiddenKeySet.has(`${kind}:${seedId}`),
    [hiddenKeySet]
  );

  const countsByKind = useMemo(() => {
    const c = { kpi: 0, process: 0, use_case: 0 };
    for (const r of rows) {
      if (c[r.kind] !== undefined) c[r.kind] += 1;
    }
    return c;
  }, [rows]);

  const hide = useCallback(
    async (kind, seedId, hiddenByLabel = null) => {
      if (!isSupabaseEnabled()) return { ok: false, error: "supabase-disabled" };
      if (!KINDS.includes(kind)) return { ok: false, error: `unknown kind ${kind}` };
      const { error } = await supabase.from(CUSTOM_TABLES.hiddenSeeds).upsert(
        {
          profile_id: profileId,
          kind,
          seed_id: seedId,
          hidden_by_label: hiddenByLabel,
        },
        { onConflict: "profile_id,kind,seed_id" }
      );
      if (error) {
        console.warn("[useHiddenSeedRows] hide failed", error);
        return { ok: false, error: String(error.message || error) };
      }
      await refresh();
      return { ok: true };
    },
    [profileId, refresh]
  );

  const show = useCallback(
    async (kind, seedId) => {
      if (!isSupabaseEnabled()) return { ok: false, error: "supabase-disabled" };
      const { error } = await supabase
        .from(CUSTOM_TABLES.hiddenSeeds)
        .delete()
        .match({ profile_id: profileId, kind, seed_id: seedId });
      if (error) {
        console.warn("[useHiddenSeedRows] show failed", error);
        return { ok: false, error: String(error.message || error) };
      }
      await refresh();
      return { ok: true };
    },
    [profileId, refresh]
  );

  return {
    rows,
    hydrated,
    isHidden,
    hiddenKeySet,
    countsByKind,
    hide,
    show,
    refresh,
    supabaseEnabled: isSupabaseEnabled(),
  };
}
