import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useDemoProfile } from "../context/DemoProfileContext";
import { readActiveDemoProfileId } from "../storage/demo-profile-preference";
import { storageKeysForProfile } from "../storage/keys";
import {
  appendCatalogFromDraft,
  getDefaultCatalogEntries,
  loadUseCaseCatalog,
  saveUseCaseCatalog,
} from "../storage/use-cases-catalog";
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
 * Use case catalog hook.
 *
 * Seed rows + user-authored drafts stay in localStorage; brand-new
 * catalog additions written via Supabase land in custom_use_cases and
 * appear for every user. Overlay pattern matches useKpiRegistry and
 * useProcesses.
 */

function customRowToCatalogEntry(row) {
  return {
    id: `sb:${row.id}`,             // prefixed so it never collides with a seed id
    __supabaseId: row.id,
    __originSeedId: row.origin_seed_id || null,
    title: row.title || "Untitled use case",
    items: Array.isArray(row.items) ? row.items : [],
    tags: Array.isArray(row.tags) ? row.tags : [],
    businessArea: row.business_area || undefined,
    problemSignal: row.problem_signal || undefined,
    actionType: row.action_type || undefined,
    process: row.process || undefined,
    tier: row.tier || undefined,
    galleryOrder: typeof row.gallery_order === "number" ? row.gallery_order : undefined,
    sampleQuestion: row.sample_question || undefined,
    dataReadiness: row.data_readiness || undefined,
    linkedKpiKeys: Array.isArray(row.linked_kpi_keys) ? row.linked_kpi_keys : undefined,
    linkedProcessIds: Array.isArray(row.linked_process_ids) ? row.linked_process_ids : undefined,
    draftSnapshot: row.draft_snapshot || undefined,
    status: row.status || "active",
    source: "user",
    updatedAt: row.updated_at || new Date().toISOString(),
    ...(row.extras || {}),
  };
}

export function useUseCaseCatalog() {
  const { profileId, storageKeys } = useDemoProfile();
  const skipNextSave = useRef(false);

  const [data, setData] = useState(() =>
    loadUseCaseCatalog(
      storageKeysForProfile(readActiveDemoProfileId()).USE_CASE_CATALOG,
      readActiveDemoProfileId()
    )
  );
  const [customRows, setCustomRows] = useState([]);
  const [hydrated, setHydrated] = useState(false);

  useEffect(() => {
    skipNextSave.current = true;
    setData(loadUseCaseCatalog(storageKeys.USE_CASE_CATALOG, profileId));
    setHydrated(true);
  }, [profileId, storageKeys.USE_CASE_CATALOG]);

  useEffect(() => {
    if (!hydrated) return;
    if (!Array.isArray(data.entries) || data.entries.length === 0) {
      setData(loadUseCaseCatalog(storageKeys.USE_CASE_CATALOG, profileId));
    }
  }, [hydrated, data.entries, profileId, storageKeys.USE_CASE_CATALOG]);

  useEffect(() => {
    if (!hydrated) return;
    if (skipNextSave.current) {
      skipNextSave.current = false;
      return;
    }
    if (!Array.isArray(data.entries) || data.entries.length === 0) return;
    saveUseCaseCatalog(data, storageKeys.USE_CASE_CATALOG);
  }, [data, hydrated, storageKeys.USE_CASE_CATALOG]);

  const refreshCustom = useCallback(async () => {
    if (!isSupabaseEnabled() || !profileId) {
      setCustomRows([]);
      return;
    }
    const rows = await listCustomRows(CUSTOM_TABLES.useCases, profileId);
    setCustomRows(rows);
  }, [profileId]);

  useEffect(() => {
    void refreshCustom();
  }, [refreshCustom]);

  useEffect(() => {
    if (!isSupabaseEnabled() || !profileId) return undefined;
    return subscribeCustomTable(CUSTOM_TABLES.useCases, profileId, () => {
      void refreshCustom();
    });
  }, [profileId, refreshCustom]);

  const { isHidden: isSeedHidden } = useHiddenSeedRows();
  const entries = useMemo(() => {
    const localEntries = Array.isArray(data?.entries) && data.entries.length > 0
      ? data.entries
      : getDefaultCatalogEntries(profileId);
    const filteredLocal = localEntries.filter((e) => {
      // Custom entries (sb:) bypass hide filter — those go through soft-delete.
      if (e.__supabaseId) return true;
      // User-created localStorage entries also bypass — hide is a seed concept.
      if (e.source && e.source !== "seed") return true;
      return !isSeedHidden("use_case", e.id);
    });
    const custom = customRows.map(customRowToCatalogEntry);
    return [...filteredLocal, ...custom];
  }, [data, profileId, customRows, isSeedHidden]);

  // Shared column shape for Supabase custom_use_cases writes (insert + update).
  const draftToRow = useCallback(
    (draft) => ({
      profile_id: profileId,
      title: draft.step1?.title?.trim() || "New use case",
      problem_signal: draft.step1?.description || null,
      items: [
        ...(draft.step2?.primaryQuestion ? [draft.step2.primaryQuestion] : []),
        ...(draft.step2?.supportingQuestions || []),
      ],
      tags: (draft.step1?.tags || "")
        .split(",")
        .map((t) => t.trim())
        .filter(Boolean),
      business_area: null,
      action_type: null,
      process: null,
      tier: null,
      gallery_order: null,
      sample_question: draft.step2?.primaryQuestion?.trim() || null,
      data_readiness: null,
      linked_kpi_keys: draft.analytics?.linkedKpiKeys || [],
      linked_process_ids: [],
      draft_snapshot: draft || null,
      status: "active",
      extras: {},
      origin_seed_id: draft.builderSession?.fromSeedTemplateId || null,
    }),
    [profileId]
  );

  // Add a use case from a completed Builder draft. When Supabase is
  // available the launched entry is persisted server-side so every user
  // sees it. Fallback: original localStorage behavior.
  const addFromDraft = useCallback(
    async (draft) => {
      if (isSupabaseEnabled()) {
        const result = await insertCustomRow(CUSTOM_TABLES.useCases, draftToRow(draft));
        if (result.ok) await refreshCustom();
        return result;
      }
      // Local fallback (localStorage behavior)
      setData((prev) => appendCatalogFromDraft(draft, prev, profileId).catalog);
      return { ok: true };
    },
    [profileId, refreshCustom, draftToRow]
  );

  // Update an existing Supabase-backed use case from a Builder draft.
  // Used when a user reopens an `sb:` entry, edits it, and re-launches — we
  // update the row in place instead of inserting a duplicate.
  const updateFromDraft = useCallback(
    async (supabaseId, draft) => {
      if (!isSupabaseEnabled()) return { ok: false, error: "supabase-disabled" };
      const { profile_id: _pid, ...patch } = draftToRow(draft);
      const result = await updateCustomRow(CUSTOM_TABLES.useCases, supabaseId, patch);
      if (result.ok) await refreshCustom();
      return result;
    },
    [refreshCustom, draftToRow]
  );

  const updateCustomUseCase = useCallback(
    async (id, patch) => {
      if (!isSupabaseEnabled()) return { ok: false, error: "supabase-disabled" };
      const result = await updateCustomRow(CUSTOM_TABLES.useCases, id, patch);
      if (result.ok) await refreshCustom();
      return result;
    },
    [refreshCustom]
  );

  const softDeleteCustomUseCase = useCallback(
    async (id) => {
      if (!isSupabaseEnabled()) return { ok: false, error: "supabase-disabled" };
      const result = await softDeleteCustomRow(CUSTOM_TABLES.useCases, id);
      if (result.ok) await refreshCustom();
      return result;
    },
    [refreshCustom]
  );

  return {
    catalog: data,
    entries,
    setData,
    addFromDraft,
    updateFromDraft,
    updateCustomUseCase,
    softDeleteCustomUseCase,
    refreshCustom,
    hydrated,
    supabaseEnabled: isSupabaseEnabled(),
  };
}
