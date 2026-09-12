import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { useDemoProfile } from "./DemoProfileContext";
import { readActiveDemoProfileId } from "../storage/demo-profile-preference";
import { storageKeysForProfile, BUILDER_PREFILL_FROM_TEMPLATE_KEY } from "../storage/keys";
import { createEmptyDraft, loadUseCaseDraft, mergeDraftFromSnapshot, saveUseCaseDraft } from "../storage/use-case-draft";
import { loadUseCaseCatalog, readCatalogEntryDraftSnapshot, seedEntryToDraftPrefill } from "../storage/use-cases-catalog";
import { supabase, isSupabaseEnabled, CUSTOM_TABLES } from "../lib/supabase";

const UseCaseDraftContext = createContext(null);

export function UseCaseDraftProvider({ children }) {
  const { profileId, storageKeys } = useDemoProfile();
  const location = useLocation();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const skipNextSave = useRef(false);
  const reopenConsumed = useRef(false);

  const [draft, setDraft] = useState(() =>
    loadUseCaseDraft(storageKeysForProfile(readActiveDemoProfileId()).USE_CASE_DRAFT)
  );
  const [hydrated, setHydrated] = useState(false);
  // Async cache of draft_snapshot for `sb:` catalog ids, since those live in
  // Supabase and aren't visible to loadUseCaseCatalog.
  const [supabaseSnapshot, setSupabaseSnapshot] = useState({ id: null, snap: undefined });

  useEffect(() => {
    skipNextSave.current = true;
    setDraft(loadUseCaseDraft(storageKeys.USE_CASE_DRAFT));
    setHydrated(true);
  }, [profileId, storageKeys.USE_CASE_DRAFT]);

  useEffect(() => {
    if (!hydrated) return;
    if (skipNextSave.current) {
      skipNextSave.current = false;
      return;
    }
    saveUseCaseDraft(draft, storageKeys.USE_CASE_DRAFT);
  }, [draft, hydrated, storageKeys.USE_CASE_DRAFT]);

  useEffect(() => {
    if (!hydrated) return;
    const reopenEarly = searchParams.get("reopenCatalogId")?.trim() || "";
    if (reopenEarly) {
      try {
        sessionStorage.removeItem(BUILDER_PREFILL_FROM_TEMPLATE_KEY);
      } catch { /* ignore */ }
      return;
    }
    let raw;
    try {
      raw = sessionStorage.getItem(BUILDER_PREFILL_FROM_TEMPLATE_KEY);
    } catch {
      return;
    }
    if (!raw) return;
    let payload;
    try {
      payload = JSON.parse(raw);
    } catch {
      try {
        sessionStorage.removeItem(BUILDER_PREFILL_FROM_TEMPLATE_KEY);
      } catch { /* ignore */ }
      return;
    }
    const catalogId = typeof payload.catalogId === "string" ? payload.catalogId.trim() : "";
    if (!catalogId) {
      try {
        sessionStorage.removeItem(BUILDER_PREFILL_FROM_TEMPLATE_KEY);
      } catch { /* ignore */ }
      return;
    }
    const cat = loadUseCaseCatalog(storageKeys.USE_CASE_CATALOG, profileId);
    const entry = cat.entries?.find((e) => e.id === catalogId);
    if (!entry || entry.source !== "seed") {
      try {
        sessionStorage.removeItem(BUILDER_PREFILL_FROM_TEMPLATE_KEY);
      } catch { /* ignore */ }
      return;
    }
    try {
      sessionStorage.removeItem(BUILDER_PREFILL_FROM_TEMPLATE_KEY);
    } catch { /* ignore */ }
    skipNextSave.current = true;
    const primaryQuestion = typeof payload.primaryQuestion === "string" ? payload.primaryQuestion : "";
    const prefill = seedEntryToDraftPrefill(entry, primaryQuestion);
    const base = createEmptyDraft();
    const merged = mergeDraftFromSnapshot({
      ...base,
      step1: { ...base.step1, ...prefill.step1 },
      step2: { ...base.step2, primaryQuestion: prefill.step2.primaryQuestion },
      builderSession: { ...base.builderSession, ...prefill.builderSession },
    });
    setDraft(merged);
    saveUseCaseDraft(merged, storageKeys.USE_CASE_DRAFT);
  }, [hydrated, profileId, searchParams, storageKeys.USE_CASE_CATALOG, storageKeys.USE_CASE_DRAFT]);

  // Fetch the Supabase draft_snapshot for `sb:` catalog ids. Runs on
  // mount/param change; cancels stale requests via a closure flag.
  useEffect(() => {
    if (!hydrated) return;
    const reopenCatalogId = searchParams.get("reopenCatalogId")?.trim() || "";
    if (!reopenCatalogId.startsWith("sb:")) return;
    if (supabaseSnapshot.id === reopenCatalogId) return;
    if (!isSupabaseEnabled()) {
      setSupabaseSnapshot({ id: reopenCatalogId, snap: null });
      return;
    }
    const supabaseId = reopenCatalogId.slice(3);
    let cancelled = false;
    (async () => {
      const { data, error } = await supabase
        .from(CUSTOM_TABLES.useCases)
        .select("draft_snapshot")
        .eq("id", supabaseId)
        .single();
      if (cancelled) return;
      if (error) {
        console.warn("[UseCaseDraft] fetch draft_snapshot failed", error);
        setSupabaseSnapshot({ id: reopenCatalogId, snap: null });
        return;
      }
      setSupabaseSnapshot({ id: reopenCatalogId, snap: data?.draft_snapshot ?? null });
    })();
    return () => { cancelled = true; };
  }, [hydrated, searchParams, supabaseSnapshot.id]);

  // Honor `?new=1` — reset the draft so external "New Use Case" entry points
  // don't inherit fields from whatever was last opened. Step-back inside the
  // builder navigates without this flag, so mid-flow edits are preserved.
  useEffect(() => {
    if (!hydrated) return;
    const isNew = searchParams.get("new") === "1";
    if (!isNew) return;
    skipNextSave.current = true;
    const empty = createEmptyDraft();
    setDraft(empty);
    saveUseCaseDraft(empty, storageKeys.USE_CASE_DRAFT);
    reopenConsumed.current = false;
    const next = new URLSearchParams(searchParams);
    next.delete("new");
    const qs = next.toString();
    navigate(
      { pathname: location.pathname, search: qs ? `?${qs}` : "", hash: location.hash },
      { replace: true, state: {} }
    );
  }, [hydrated, searchParams, navigate, location.pathname, location.hash, storageKeys.USE_CASE_DRAFT]);

  useEffect(() => {
    if (!hydrated) return;
    const reopenCatalogId = searchParams.get("reopenCatalogId")?.trim() || "";
    let snap = location.state?.reopenDraftSnapshot;
    // For entries that have a saved draft (user-created or launched via the
    // Builder), read the snapshot. For seed entries (catalog templates), we
    // fall back below to a prefill derived from the seed row itself so the
    // Builder is not empty when a user opens Edit on a seeded use case.
    let seedEntry = null;
    if (!snap && reopenCatalogId) {
      const cat = loadUseCaseCatalog(storageKeys.USE_CASE_CATALOG, profileId);
      seedEntry = cat.entries?.find((e) => e.id === reopenCatalogId) ?? null;
      snap = seedEntry?.draftSnapshot ?? readCatalogEntryDraftSnapshot(reopenCatalogId, profileId);
    }
    // Supabase-backed entries (`sb:${uuid}`) aren't in the local catalog; wait
    // for the async fetch to land, then use its draft_snapshot.
    if (!snap && reopenCatalogId.startsWith("sb:")) {
      if (supabaseSnapshot.id !== reopenCatalogId) return; // fetch pending
      snap = supabaseSnapshot.snap || null;
    }
    if (!snap && reopenCatalogId && seedEntry) {
      // Seed entry with no saved snapshot: build a prefill from the entry itself.
      const prefill = seedEntryToDraftPrefill(seedEntry, "");
      const base = createEmptyDraft();
      snap = {
        ...base,
        step1: { ...base.step1, ...prefill.step1 },
        step2: { ...base.step2, primaryQuestion: prefill.step2.primaryQuestion },
        builderSession: { ...base.builderSession, ...prefill.builderSession },
      };
    }
    if (!snap) {
      if (reopenCatalogId) {
        const next = new URLSearchParams(searchParams);
        next.delete("reopenCatalogId");
        const qs = next.toString();
        navigate({ pathname: location.pathname, search: qs ? `?${qs}` : "", hash: location.hash }, { replace: true, state: {} });
      }
      return;
    }
    if (reopenConsumed.current) return;
    reopenConsumed.current = true;
    skipNextSave.current = true;
    const merged = mergeDraftFromSnapshot(snap);
    const catalogEntry = seedEntry
      || (reopenCatalogId
        ? loadUseCaseCatalog(storageKeys.USE_CASE_CATALOG, profileId).entries?.find((e) => e.id === reopenCatalogId)
        : null);
    const toSave = {
      ...merged,
      builderSession: {
        ...merged.builderSession,
        hideTemplatesDraftBanner: catalogEntry ? catalogEntry.status !== "draft" : false,
        reopenedCatalogId: reopenCatalogId || null,
      },
    };
    setDraft(toSave);
    saveUseCaseDraft(toSave, storageKeys.USE_CASE_DRAFT);
    const next = new URLSearchParams(searchParams);
    next.delete("reopenCatalogId");
    const qs = next.toString();
    navigate(
      { pathname: location.pathname, search: qs ? `?${qs}` : "", hash: location.hash },
      { replace: true, state: {} }
    );
  }, [hydrated, location.pathname, location.hash, location.state, navigate, storageKeys.USE_CASE_CATALOG, storageKeys.USE_CASE_DRAFT, profileId, searchParams, supabaseSnapshot]);

  const updateDraft = useCallback((patch) => {
    setDraft((prev) => {
      const next = typeof patch === "function" ? patch(prev) : { ...prev, ...patch };
      return next;
    });
  }, []);

  const resetDraft = useCallback(() => {
    const empty = createEmptyDraft();
    setDraft(empty);
    saveUseCaseDraft(empty, storageKeys.USE_CASE_DRAFT);
  }, [storageKeys.USE_CASE_DRAFT]);

  const value = useMemo(
    () => ({
      draft,
      updateDraft,
      hydrated,
      resetDraft,
    }),
    [draft, updateDraft, hydrated, resetDraft]
  );

  return <UseCaseDraftContext.Provider value={value}>{children}</UseCaseDraftContext.Provider>;
}

export function useUseCaseDraft() {
  const ctx = useContext(UseCaseDraftContext);
  if (!ctx) {
    throw new Error("useUseCaseDraft must be used within UseCaseDraftProvider");
  }
  return ctx;
}
