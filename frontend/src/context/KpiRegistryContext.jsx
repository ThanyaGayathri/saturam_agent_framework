import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { useDemoProfile } from "./DemoProfileContext";
import { readActiveDemoProfileId } from "../storage/demo-profile-preference";
import { storageKeysForProfile } from "../storage/keys";
import { loadKpiRegistry, saveKpiRegistry } from "../storage/kpi-registry";
import {
  listCustomRows,
  insertCustomRow,
  updateCustomRow,
  softDeleteCustomRow,
  subscribeCustomTable,
} from "../lib/customRepo";
import { CUSTOM_TABLES, isSupabaseEnabled } from "../lib/supabase";
import { useHiddenSeedRows } from "../hooks/useHiddenSeedRows";
import { kpiSeedId } from "../domain/seed-ids";

/**
 * KPI Registry — shared state via context.
 *
 * Previously each `useKpiRegistry()` call had its own useState, so an edit
 * in AddKpiPanel didn't propagate to KpiMappings, KpiRegistry, etc. until
 * one of those components remounted. Now the state lives in a single
 * provider; every consumer sees the same `data` reference and re-renders
 * together when a KPI is added/edited/deleted.
 */

const CUSTOM_CATEGORY_NAME = "Custom KPIs";

function customRowToKpiShape(row) {
  return {
    __supabaseId: row.id,        // marker so the UI can tell it's a custom row
    __originSeedId: row.origin_seed_id || null,
    name: row.name || "",
    desc: row.description || "",
    businessAreaId: row.business_area_id || "",
    subjectArea: row.subject_area || "",
    level: row.level || "Operational",
    unit: row.unit || "Count",
    industryBenchmark: row.industry_benchmark || "",
    datasetIds: Array.isArray(row.dataset_ids) ? row.dataset_ids : [],
    processMapping: Array.isArray(row.process_mapping) ? row.process_mapping : [],
    active: row.extras?.active !== false,   // custom rows default active
    seed: false,                             // never treat as seed
    ...(row.extras || {}),
  };
}

function groupCustomKpisIntoSections(rows) {
  const bySection = new Map();
  for (const row of rows) {
    const kpi = customRowToKpiShape(row);
    const secKey = `${kpi.businessAreaId || "custom"}\0${kpi.subjectArea || "Custom"}`;
    if (!bySection.has(secKey)) {
      bySection.set(secKey, {
        name: kpi.subjectArea || "Custom",
        businessAreaId: kpi.businessAreaId || "custom",
        kpis: [],
      });
    }
    bySection.get(secKey).kpis.push(kpi);
  }
  return Array.from(bySection.values());
}

function mergeCustomIntoData(base, customRows) {
  if (!customRows.length) return base;
  const sections = groupCustomKpisIntoSections(customRows);
  const nextCategories = [...(base?.categories || [])];
  const existingIdx = nextCategories.findIndex((c) => c?.category === CUSTOM_CATEGORY_NAME);
  const customCategory = { category: CUSTOM_CATEGORY_NAME, sections };
  if (existingIdx >= 0) nextCategories[existingIdx] = customCategory;
  else nextCategories.push(customCategory);
  return { ...base, categories: nextCategories };
}

const KpiRegistryContext = createContext(null);

export function KpiRegistryProvider({ children }) {
  const { profileId, storageKeys } = useDemoProfile();
  const skipNextSave = useRef(false);

  const [localData, setLocalData] = useState(() =>
    loadKpiRegistry(
      storageKeysForProfile(readActiveDemoProfileId()).KPI_REGISTRY,
      readActiveDemoProfileId()
    )
  );

  const [customRows, setCustomRows] = useState([]);
  const [hydrated, setHydrated] = useState(false);

  useEffect(() => {
    skipNextSave.current = true;
    setLocalData(loadKpiRegistry(storageKeys.KPI_REGISTRY, profileId));
    setHydrated(true);
  }, [profileId, storageKeys.KPI_REGISTRY]);

  useEffect(() => {
    if (!hydrated) return;
    if (skipNextSave.current) {
      skipNextSave.current = false;
      return;
    }
    saveKpiRegistry(localData, storageKeys.KPI_REGISTRY);
  }, [localData, hydrated, storageKeys.KPI_REGISTRY]);

  const refreshCustom = useCallback(async () => {
    if (!isSupabaseEnabled() || !profileId) {
      setCustomRows([]);
      return;
    }
    const rows = await listCustomRows(CUSTOM_TABLES.kpis, profileId);
    setCustomRows(rows);
  }, [profileId]);

  useEffect(() => {
    void refreshCustom();
  }, [refreshCustom]);

  useEffect(() => {
    if (!isSupabaseEnabled() || !profileId) return undefined;
    const unsubscribe = subscribeCustomTable(CUSTOM_TABLES.kpis, profileId, () => {
      void refreshCustom();
    });
    return unsubscribe;
  }, [profileId, refreshCustom]);

  const merged = mergeCustomIntoData(localData, customRows);

  const { isHidden: isSeedHidden } = useHiddenSeedRows();
  const data = useMemo(() => {
    if (!merged?.categories) return merged;
    const nextCategories = merged.categories
      .map((cat) => {
        if (cat.category === "Custom KPIs") return cat;
        const nextSections = (cat.sections || [])
          .map((sec) => {
            const kpis = (sec.kpis || []).filter((k) => {
              if (k.__supabaseId) return true;
              return !isSeedHidden("kpi", kpiSeedId(cat.category, sec.name, k.name));
            });
            return { ...sec, kpis };
          })
          .filter((sec) => (sec.kpis?.length || 0) > 0);
        return { ...cat, sections: nextSections };
      })
      .filter((cat) => (cat.sections?.length || 0) > 0 || cat.category === "Custom KPIs");
    return { ...merged, categories: nextCategories };
  }, [merged, isSeedHidden]);

  const addCustomKpi = useCallback(
    async (kpiDraft) => {
      if (!isSupabaseEnabled()) return { ok: false, error: "supabase-disabled" };
      const row = {
        profile_id: profileId,
        name: kpiDraft.name?.trim() || "Untitled KPI",
        description: kpiDraft.desc || null,
        business_area_id: kpiDraft.businessAreaId || null,
        subject_area: kpiDraft.subjectArea || null,
        level: kpiDraft.level || "Operational",
        unit: kpiDraft.unit || "Count",
        industry_benchmark: kpiDraft.industryBenchmark || null,
        dataset_ids: kpiDraft.datasetIds || [],
        process_mapping: kpiDraft.processMapping || [],
        extras: kpiDraft.extras || {},
        origin_seed_id: kpiDraft.originSeedId || null,
        created_by_label: kpiDraft.createdByLabel || null,
      };
      const result = await insertCustomRow(CUSTOM_TABLES.kpis, row);
      if (result.ok) await refreshCustom();
      return result;
    },
    [profileId, refreshCustom]
  );

  const updateCustomKpi = useCallback(
    async (id, patch) => {
      if (!isSupabaseEnabled()) return { ok: false, error: "supabase-disabled" };
      const result = await updateCustomRow(CUSTOM_TABLES.kpis, id, patch);
      if (result.ok) await refreshCustom();
      return result;
    },
    [refreshCustom]
  );

  const softDeleteCustomKpi = useCallback(
    async (id) => {
      if (!isSupabaseEnabled()) return { ok: false, error: "supabase-disabled" };
      const result = await softDeleteCustomRow(CUSTOM_TABLES.kpis, id);
      if (result.ok) await refreshCustom();
      return result;
    },
    [refreshCustom]
  );

  const undoDeleteCustomKpi = useCallback(
    async (id) => {
      if (!isSupabaseEnabled()) return { ok: false, error: "supabase-disabled" };
      const result = await updateCustomRow(CUSTOM_TABLES.kpis, id, { deleted_at: null });
      if (result.ok) await refreshCustom();
      return result;
    },
    [refreshCustom]
  );

  const setData = useCallback((updater) => {
    setLocalData((prev) => {
      const nextLocal = typeof updater === "function" ? updater(prev) : updater;
      return nextLocal;
    });
  }, []);

  const value = useMemo(
    () => ({
      data,
      setData,
      hydrated,
      addCustomKpi,
      updateCustomKpi,
      softDeleteCustomKpi,
      undoDeleteCustomKpi,
      refreshCustom,
      supabaseEnabled: isSupabaseEnabled(),
    }),
    [data, setData, hydrated, addCustomKpi, updateCustomKpi, softDeleteCustomKpi, undoDeleteCustomKpi, refreshCustom]
  );

  return <KpiRegistryContext.Provider value={value}>{children}</KpiRegistryContext.Provider>;
}

/**
 * Consumer hook. Returns the shared KPI registry state from KpiRegistryProvider.
 * If called outside the provider, falls back to a null-shaped record so tests
 * or accidental usage don't crash.
 */
export function useKpiRegistry() {
  const ctx = useContext(KpiRegistryContext);
  if (!ctx) {
    // Fallback shape — behaves like the hook returns before hydration.
    return {
      data: { categories: [] },
      setData: () => {},
      hydrated: false,
      addCustomKpi: async () => ({ ok: false, error: "provider-missing" }),
      updateCustomKpi: async () => ({ ok: false, error: "provider-missing" }),
      softDeleteCustomKpi: async () => ({ ok: false, error: "provider-missing" }),
      undoDeleteCustomKpi: async () => ({ ok: false, error: "provider-missing" }),
      refreshCustom: async () => {},
      supabaseEnabled: false,
    };
  }
  return ctx;
}
