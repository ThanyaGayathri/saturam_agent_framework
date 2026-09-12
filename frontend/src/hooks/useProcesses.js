import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useDemoProfile } from "../context/DemoProfileContext";
import { readActiveDemoProfileId } from "../storage/demo-profile-preference";
import { storageKeysForProfile } from "../storage/keys";
import { createProcess, loadProcesses, saveProcesses } from "../storage/processes";
import {
  listCustomRows,
  insertCustomRow,
  updateCustomRow,
  softDeleteCustomRow,
  subscribeCustomTable,
} from "../lib/customRepo";
import { CUSTOM_TABLES, isSupabaseEnabled } from "../lib/supabase";
import { useHiddenSeedRows } from "./useHiddenSeedRows";

/**
 * Process registry hook.
 *
 * Same overlay pattern as useKpiRegistry: seed + localStorage
 * synchronously first, then Supabase custom_processes async merged on
 * top. Custom rows are appended to the returned `processes` array so
 * every consumer (ProcessDetail, KPI Registry maps-to, catalog cards)
 * picks them up without further changes.
 */

function customRowToProcessShape(row) {
  return {
    id: `sb:${row.id}`,               // prefixed so ids never collide with seed ids
    __supabaseId: row.id,
    __originSeedId: row.origin_seed_id || null,
    name: row.name || "",
    description: row.description || "",
    businessAreaId: row.business_area_id || "",
    subjectArea: row.subject_area || "",
    linkedKpiNames: Array.isArray(row.linked_kpi_names) ? row.linked_kpi_names : [],
    seed: false,
    ...(row.extras || {}),
  };
}

export function useProcesses() {
  const { profileId, storageKeys } = useDemoProfile();
  const skipNextSave = useRef(false);

  const [localData, setLocalData] = useState(() =>
    loadProcesses(storageKeysForProfile(readActiveDemoProfileId()).PROCESSES, readActiveDemoProfileId())
  );
  const [customRows, setCustomRows] = useState([]);
  const [hydrated, setHydrated] = useState(false);

  useEffect(() => {
    skipNextSave.current = true;
    setLocalData(loadProcesses(storageKeys.PROCESSES, profileId));
    setHydrated(true);
  }, [profileId, storageKeys.PROCESSES]);

  useEffect(() => {
    if (!hydrated) return;
    if (skipNextSave.current) {
      skipNextSave.current = false;
      return;
    }
    saveProcesses(localData, storageKeys.PROCESSES);
  }, [localData, hydrated, storageKeys.PROCESSES]);

  const refreshCustom = useCallback(async () => {
    if (!isSupabaseEnabled() || !profileId) {
      setCustomRows([]);
      return;
    }
    const rows = await listCustomRows(CUSTOM_TABLES.processes, profileId);
    setCustomRows(rows);
  }, [profileId]);

  useEffect(() => {
    void refreshCustom();
  }, [refreshCustom]);

  useEffect(() => {
    if (!isSupabaseEnabled() || !profileId) return undefined;
    return subscribeCustomTable(CUSTOM_TABLES.processes, profileId, () => {
      void refreshCustom();
    });
  }, [profileId, refreshCustom]);

  const { isHidden: isSeedHidden } = useHiddenSeedRows();
  const processes = useMemo(() => {
    const base = (localData?.processes || []).filter((p) => {
      // Custom (sb:) processes bypass the hide filter — those are managed
      // via soft-delete on custom_processes. Only filter true seed rows.
      if (p.__supabaseId) return true;
      return !isSeedHidden("process", p.id);
    });
    const custom = customRows.map(customRowToProcessShape);
    return [...base, ...custom];
  }, [localData, customRows, isSeedHidden]);

  const addProcess = useCallback(
    async (fields) => {
      if (isSupabaseEnabled()) {
        const row = {
          profile_id: profileId,
          name: (fields?.name || "").trim() || "Untitled process",
          description: fields?.description || null,
          business_area_id: fields?.businessAreaId || null,
          subject_area: fields?.subjectArea || null,
          linked_kpi_names: Array.isArray(fields?.linkedKpiNames) ? fields.linkedKpiNames : [],
          extras: fields?.extras || {},
          origin_seed_id: fields?.originSeedId || null,
          created_by_label: fields?.createdByLabel || null,
        };
        const result = await insertCustomRow(CUSTOM_TABLES.processes, row);
        if (result.ok) await refreshCustom();
        return result;
      }
      // Fallback when Supabase isn't configured — behave like today.
      setLocalData((prev) => ({
        ...prev,
        processes: [...(prev.processes || []), createProcess(prev.processes || [], fields)],
      }));
      return { ok: true };
    },
    [profileId, refreshCustom]
  );

  const updateCustomProcess = useCallback(
    async (id, patch) => {
      if (!isSupabaseEnabled()) return { ok: false, error: "supabase-disabled" };
      const result = await updateCustomRow(CUSTOM_TABLES.processes, id, patch);
      if (result.ok) await refreshCustom();
      return result;
    },
    [refreshCustom]
  );

  const softDeleteCustomProcess = useCallback(
    async (id) => {
      if (!isSupabaseEnabled()) return { ok: false, error: "supabase-disabled" };
      const result = await softDeleteCustomRow(CUSTOM_TABLES.processes, id);
      if (result.ok) await refreshCustom();
      return result;
    },
    [refreshCustom]
  );

  const setData = useCallback((updater) => {
    setLocalData((prev) => (typeof updater === "function" ? updater(prev) : updater));
  }, []);

  return {
    processes,
    setData,
    addProcess,
    updateCustomProcess,
    softDeleteCustomProcess,
    refreshCustom,
    hydrated,
    supabaseEnabled: isSupabaseEnabled(),
  };
}
