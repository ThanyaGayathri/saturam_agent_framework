import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { useDemoProfile } from "./DemoProfileContext";
import { readActiveDemoProfileId } from "../storage/demo-profile-preference";
import { storageKeysForProfile } from "../storage/keys";
import {
  loadConfigureWorkspace,
  saveConfigureWorkspace,
  createLlmModel,
  createExternalIntegration,
  normalizeInternal,
  normalizeLlm,
  normalizeExternal,
} from "../storage/configure-workspace";
import {
  listCustomRows,
  insertCustomRow,
  updateCustomRow,
  softDeleteCustomRow,
  subscribeCustomTable,
} from "../lib/customRepo";
import { CUSTOM_TABLES, isSupabaseEnabled } from "../lib/supabase";
import {
  createSource,
  deleteSource,
  fetchSources,
  startSourceHarvest,
  testSource,
  updateSource,
} from "../services/sources-api";

/**
 * Configure Workspace context.
 *
 * Internal sources (Configure > Data sources) are real (#308): the list is
 * `GET /configure/sources`, every add/edit/remove is a call to that API, and while
 * any source is `building` the list is re-read on the same cadence the KPI suggestion
 * run is polled (2s, relaxing to 5s after half a minute, giving up at four minutes) so
 * the card's readiness follows #306's harvest as it runs. The seeded demo rows stay
 * as UI fixtures -- marked "Demo", hidden by default -- and are the only internal
 * sources still read from localStorage; a localStorage-added internal source from
 * before this wiring is no longer shown, since it never existed anywhere real.
 *
 * LLM models and external integrations are unchanged: seed rows (from code) +
 * localStorage-added rows, with new / edited rows going to Supabase when it's
 * enabled so the whole sales team sees the same list. Same overlay pattern as
 * useKpiRegistry etc. No backend exists for them yet.
 *
 * Cloud profile stays in localStorage (single settings object; no shared
 * edit needed today). See pendingItems.md 6.10 for future migration.
 */

const ConfigureWorkspaceContext = createContext(null);

// Poll cadence for a running harvest -- the same numbers `useDashboardSuggestionRun` and the
// KPI suggestion run use, so every long-running thing in the product waits the same way.
const POLL_MS = 2000;
const POLL_RELAXED_MS = 5000;
const POLL_RELAX_AFTER_MS = 30_000;
const POLL_CEILING_MS = 4 * 60 * 1000;

// ─── API row ↔ app shape converters (internal sources) ────────────────

/** `InternalSource` (contract 1.2.8) -> the shape every Configure screen already reads.
 * Secrets never travel: `password`/`apiKey` are empty on the way in, and go out only when a
 * person typed a new one. The harvest fields ride alongside for `source-model-state.js`. */
export function internalSourceFromApi(row) {
  return {
    ...normalizeInternal({
      id: row.id,
      sourceType: row.source_type,
      name: row.name,
      host: row.host,
      port: row.port == null ? "" : String(row.port),
      databaseName: row.database_name,
      schema: row.schema || "",
      authMethod: row.auth_method,
      username: row.username || "",
      sslMode: row.ssl_mode,
      permissionLevel: row.permission_level,
      seed: false,
    }),
    remote: true,
    hasCredentials: !!row.has_credentials,
    harvestState: row.harvest_state || "not_started",
    lastHarvestId: row.last_harvest_id || null,
    lastHarvestAt: row.last_harvest_at || null,
    createdAt: row.created_at || null,
  };
}

function portFromShape(record) {
  const raw = String(record.port ?? "").trim();
  if (!raw) return null;
  const n = Number(raw);
  return Number.isInteger(n) && n > 0 ? n : null;
}

function internalCommonFields(record) {
  return {
    source_type: record.sourceType || "postgres",
    name: (record.name || "").trim(),
    host: (record.host || "").trim(),
    port: portFromShape(record),
    database_name: (record.databaseName || "").trim(),
    schema: (record.schema || "").trim(),
    ssl_mode: record.sslMode || "require",
    permission_level: record.permissionLevel === "read_write" ? "read_write" : "read_only",
  };
}

/** `InternalSourceWrite` for `POST /configure/sources`: everything, credential included. */
function internalWriteFromShape(record) {
  const body = {
    ...internalCommonFields(record),
    auth_method: record.authMethod || "username_password",
    username: record.username || "",
  };
  if (record.password) body.password = record.password;
  if (record.apiKey) body.api_key = record.apiKey;
  return body;
}

/** `InternalSourcePatch` for `PATCH /configure/sources/{id}`. The backend reads any of
 * `auth_method`/`password`/`api_key` on a patch as "rotate the credential" and validates the
 * whole credential again -- an `auth_method` sent with an empty password is a 422, not a
 * no-op -- so the credential fields go only when a new secret was actually typed. The
 * username is part of that credential (`_credential_from_patch` ignores it on its own), so a
 * username or auth-method change with no new secret cannot be expressed as a patch;
 * `ConfigureInternalSourceForm` refuses to send one rather than let it save as a no-op. */
function internalPatchFromShape(record) {
  const body = internalCommonFields(record);
  if (record.password || record.apiKey) {
    body.auth_method = record.authMethod || "username_password";
    body.username = record.username || "";
    if (record.password) body.password = record.password;
    if (record.apiKey) body.api_key = record.apiKey;
  }
  return body;
}

// ─── Supabase row ↔ app shape converters (models, integrations) ──────

function llmRowToShape(row) {
  return {
    ...normalizeLlm({
      id: `sb:${row.id}`,
      providerId: row.provider_id,
      modelId: row.model_id,
      apiKey: row.api_key,
      baseUrl: row.base_url,
      isDefault: row.is_default,
      seed: false,
    }),
    __supabaseId: row.id,
    __originSeedId: row.origin_seed_id || null,
  };
}

function llmShapeToRow(record, profileId) {
  return {
    profile_id: profileId,
    provider_id: record.providerId || "openai",
    model_id: record.modelId || "gpt-4o-mini",
    api_key: record.apiKey || null,
    base_url: record.baseUrl || null,
    is_default: !!record.isDefault,
    extras: record.extras || {},
    origin_seed_id: record.__originSeedId || null,
  };
}

function externalRowToShape(row) {
  return {
    ...normalizeExternal({
      id: `sb:${row.id}`,
      providerId: row.provider_id,
      name: row.name,
      integrationType: row.integration_type,
      environment: row.environment,
      syncFrequency: row.sync_frequency,
      dataScope: row.data_scope,
      status: row.status,
      authType: row.auth_type,
      clientId: row.client_id,
      clientSecret: row.client_secret,
      authUrl: row.auth_url,
      tokenUrl: row.token_url,
      redirectUri: row.redirect_uri,
      scopes: row.scopes,
      apiKey: row.api_key,
      apiSecret: row.api_secret,
      baseUrl: row.base_url,
      apiVersion: row.api_version,
      basicUser: row.basic_user,
      basicPassword: row.basic_password,
      webhookUrl: row.webhook_url,
      webhookSecret: row.webhook_secret,
      webhookEvents: row.webhook_events,
      connectionStatus: row.connection_status,
      lastSyncAt: row.last_sync_at,
      lastError: row.last_error,
    }),
    __supabaseId: row.id,
    __originSeedId: row.origin_seed_id || null,
  };
}

function externalShapeToRow(record, profileId) {
  return {
    profile_id: profileId,
    provider_id: record.providerId || "shopify",
    name: record.name || "",
    integration_type: record.integrationType || null,
    environment: record.environment || "production",
    sync_frequency: record.syncFrequency || "hourly",
    data_scope: record.dataScope || null,
    status: record.status === "inactive" ? "inactive" : "active",
    auth_type: record.authType || "api_key",
    client_id: record.clientId || null,
    client_secret: record.clientSecret || null,
    auth_url: record.authUrl || null,
    token_url: record.tokenUrl || null,
    redirect_uri: record.redirectUri || null,
    scopes: record.scopes || null,
    api_key: record.apiKey || null,
    api_secret: record.apiSecret || null,
    base_url: record.baseUrl || null,
    api_version: record.apiVersion || null,
    basic_user: record.basicUser || null,
    basic_password: record.basicPassword || null,
    webhook_url: record.webhookUrl || null,
    webhook_secret: record.webhookSecret || null,
    webhook_events: record.webhookEvents || null,
    connection_status: record.connectionStatus || "untested",
    last_sync_at: record.lastSyncAt || null,
    last_error: record.lastError || null,
    extras: record.extras || {},
    origin_seed_id: record.__originSeedId || null,
  };
}

export function ConfigureWorkspaceProvider({ children }) {
  const { profileId, storageKeys } = useDemoProfile();
  const skipNextSave = useRef(false);

  const [localData, setLocalData] = useState(() =>
    loadConfigureWorkspace(
      storageKeysForProfile(readActiveDemoProfileId()).CONFIGURE_WORKSPACE,
      readActiveDemoProfileId()
    )
  );
  const [customLlm, setCustomLlm] = useState([]);
  const [customExternal, setCustomExternal] = useState([]);
  const [hydrated, setHydrated] = useState(false);

  useEffect(() => {
    skipNextSave.current = true;
    setLocalData(loadConfigureWorkspace(storageKeys.CONFIGURE_WORKSPACE, profileId));
    setHydrated(true);
  }, [storageKeys.CONFIGURE_WORKSPACE, profileId]);

  useEffect(() => {
    if (!hydrated) return;
    if (skipNextSave.current) {
      skipNextSave.current = false;
      return;
    }
    saveConfigureWorkspace(localData, storageKeys.CONFIGURE_WORKSPACE);
  }, [localData, hydrated, storageKeys.CONFIGURE_WORKSPACE]);

  // ─── Internal sources: the real API ──────────────────────────────────

  const [remoteInternal, setRemoteInternal] = useState([]);
  // `loading` until the first answer; `error` keeps whatever list was last read rather than
  // blanking it, so one failed poll mid-harvest does not empty the screen.
  const [sourcesStatus, setSourcesStatus] = useState({ state: "loading", error: null });

  const refreshSources = useCallback(async ({ signal } = {}) => {
    try {
      const rows = await fetchSources({ signal });
      setRemoteInternal(rows.map(internalSourceFromApi));
      setSourcesStatus({ state: "ready", error: null });
      return rows;
    } catch (err) {
      if (err?.name === "AbortError") return null;
      setSourcesStatus({
        state: "error",
        error: err?.detail || err?.message || "Could not load the connected sources.",
      });
      return null;
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void refreshSources({ signal: controller.signal });
    return () => controller.abort();
  }, [refreshSources]);

  // Poll while anything is building. Keyed on the *set* of building ids (not the list), so a
  // refresh that changes nothing does not restart the timer chain, and one source finishing
  // while another still runs does. `pollEpoch` lets a retried harvest restart a chain that
  // already gave up. The four-minute ceiling is per source: `pollStartedAt` maps each building
  // source id to when *its* harvest began, so starting a second harvest never buys the first
  // one more time, and a source that has gone quiet stops being polled without silencing the
  // rest.
  const buildingIds = useMemo(
    () =>
      remoteInternal
        .filter((s) => s.harvestState === "building")
        .map((s) => s.id)
        .sort()
        .join(","),
    [remoteInternal]
  );
  const [pollEpoch, setPollEpoch] = useState(0);
  const [harvestPollingStalled, setHarvestPollingStalled] = useState(false);
  const pollStartedAt = useRef(new Map());

  useEffect(() => {
    const ids = buildingIds ? buildingIds.split(",") : [];
    const started = pollStartedAt.current;
    for (const known of [...started.keys()]) {
      if (!ids.includes(known)) started.delete(known);
    }
    for (const id of ids) {
      if (!started.has(id)) started.set(id, Date.now());
    }
    const ages = () => ids.map((id) => Date.now() - started.get(id));
    const withinCeiling = () => ages().filter((age) => age <= POLL_CEILING_MS);
    // The backend's own bound plus grace has passed for a source that is still `building`.
    // Stop asking about it rather than poll forever; the screen says so and offers a manual
    // refresh.
    setHarvestPollingStalled(withinCeiling().length < ids.length);
    if (!withinCeiling().length) return undefined;

    const controller = new AbortController();
    let cancelled = false;
    let timer = null;
    const tick = async () => {
      const live = withinCeiling();
      if (live.length < ids.length) setHarvestPollingStalled(true);
      if (!live.length) return;
      await refreshSources({ signal: controller.signal });
      if (cancelled) return;
      // The newest harvest sets the pace: a fresh build is checked every 2s even while an
      // older one has already relaxed to 5s.
      timer = window.setTimeout(tick, Math.min(...live) > POLL_RELAX_AFTER_MS ? POLL_RELAXED_MS : POLL_MS);
    };
    timer = window.setTimeout(tick, POLL_MS);
    return () => {
      cancelled = true;
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [buildingIds, pollEpoch, refreshSources]);

  const placeRemote = (shaped) =>
    setRemoteInternal((prev) => {
      const i = prev.findIndex((s) => s.id === shaped.id);
      return i >= 0 ? prev.map((s) => (s.id === shaped.id ? shaped : s)) : [...prev, shaped];
    });

  /** Create or update through the API and return the saved source in app shape. A record whose
   * id names a real source is a PATCH; anything else -- a new form, or a seeded demo row someone
   * filled in with real values -- is a POST that registers a new real source. Throws `ApiError`
   * (the backend's 422 `detail` says what was wrong with the credential) for the form to show. */
  const upsertInternal = async (record) => {
    const isRemote = !!record.remote || remoteInternal.some((s) => s.id === record.id);
    const saved = isRemote
      ? await updateSource(record.id, internalPatchFromShape(record))
      : await createSource(internalWriteFromShape(record));
    const shaped = internalSourceFromApi(saved);
    placeRemote(shaped);
    return shaped;
  };

  const removeInternal = async (id) => {
    if (remoteInternal.some((s) => s.id === id)) {
      await deleteSource(id);
      setRemoteInternal((prev) => prev.filter((s) => s.id !== id));
      return;
    }
    // A seeded demo row: hidden for this session (seeds re-hydrate on reload, as before).
    setLocalData((prev) => ({
      ...prev,
      internalSources: (prev.internalSources || []).filter((x) => x.id !== id),
    }));
  };

  const addInternal = (fields) => upsertInternal(normalizeInternal({ ...fields, seed: false }));

  /** `POST /configure/sources/{id}/test` -- resolves `{ok, detail, tested_at}`. */
  const testInternalConnection = (id) => testSource(id);

  /** Start #306's harvest for a source and show it `building` at once; the poll above takes
   * it from there. Resolves the framework's 202 `{run_id, session_id, status}`. */
  const startInternalHarvest = async (id) => {
    const run = await startSourceHarvest(id);
    setRemoteInternal((prev) =>
      prev.map((s) =>
        s.id === id ? { ...s, harvestState: "building", lastHarvestId: run?.run_id || s.lastHarvestId } : s
      )
    );
    // Only this source's clock restarts; the effect re-derives the stalled flag from the map.
    pollStartedAt.current.set(id, Date.now());
    setPollEpoch((n) => n + 1);
    return run;
  };

  // ─── Supabase fetch + realtime for models and integrations ───────────

  const refreshCustom = useCallback(async () => {
    if (!isSupabaseEnabled() || !profileId) {
      setCustomLlm([]);
      setCustomExternal([]);
      return;
    }
    const [lls, exs] = await Promise.all([
      listCustomRows(CUSTOM_TABLES.llmModels, profileId),
      listCustomRows(CUSTOM_TABLES.externalIntegrations, profileId),
    ]);
    setCustomLlm(lls);
    setCustomExternal(exs);
  }, [profileId]);

  useEffect(() => {
    void refreshCustom();
  }, [refreshCustom]);

  useEffect(() => {
    if (!isSupabaseEnabled() || !profileId) return undefined;
    const unsubs = [
      subscribeCustomTable(CUSTOM_TABLES.llmModels, profileId, () => void refreshCustom()),
      subscribeCustomTable(CUSTOM_TABLES.externalIntegrations, profileId, () => void refreshCustom()),
    ];
    return () => unsubs.forEach((u) => u && u());
  }, [profileId, refreshCustom]);

  const supabaseEnabled = isSupabaseEnabled();

  const data = useMemo(() => {
    const merged = {
      ...localData,
      // Demo fixtures first (hidden by default on the grid), then what the API knows.
      internalSources: [...(localData.internalSources || []).filter((s) => s.seed), ...remoteInternal],
      llmModels: [...(localData.llmModels || []), ...customLlm.map(llmRowToShape)],
      externalIntegrations: [
        ...(localData.externalIntegrations || []),
        ...customExternal.map(externalRowToShape),
      ],
    };
    return merged;
  }, [localData, remoteInternal, customLlm, customExternal]);

  // ─── LLM models ──────────────────────────────────────────────────────

  const upsertLlm = async (record) => {
    const sbId =
      record.__supabaseId ||
      (typeof record.id === "string" && record.id.startsWith("sb:") ? record.id.slice(3) : null);
    if (sbId) {
      await updateCustomRow(
        CUSTOM_TABLES.llmModels,
        sbId,
        llmShapeToRow(record, profileId)
      );
      await refreshCustom();
      return;
    }
    if (supabaseEnabled && !record.seed) {
      // Clear default flag on other rows first when this one is marked default.
      if (record.isDefault) {
        // Local-only default clearing; Supabase rows keep their is_default until
        // updated separately if needed. Prototype-level correctness.
        setLocalData((prev) => ({
          ...prev,
          llmModels: (prev.llmModels || []).map((m) => ({ ...m, isDefault: false })),
        }));
      }
      await insertCustomRow(CUSTOM_TABLES.llmModels, llmShapeToRow(record, profileId));
      await refreshCustom();
      return;
    }
    setLocalData((prev) => {
      let list = [...(prev.llmModels || [])];
      if (record.isDefault) {
        list = list.map((m) => ({ ...m, isDefault: false }));
      }
      const i = list.findIndex((x) => x.id === record.id);
      const next = i >= 0 ? list.map((x) => (x.id === record.id ? record : x)) : [...list, record];
      return { ...prev, llmModels: next };
    });
  };

  const removeLlm = async (id) => {
    const sbId = typeof id === "string" && id.startsWith("sb:") ? id.slice(3) : null;
    if (sbId) {
      await softDeleteCustomRow(CUSTOM_TABLES.llmModels, sbId);
      await refreshCustom();
      return;
    }
    setLocalData((prev) => ({
      ...prev,
      llmModels: (prev.llmModels || []).filter((x) => x.id !== id),
    }));
  };

  const addLlm = (fields) => {
    const rec = createLlmModel(fields);
    void upsertLlm(rec);
    return rec;
  };

  // ─── External integrations ───────────────────────────────────────────

  const upsertExternal = async (record) => {
    const sbId =
      record.__supabaseId ||
      (typeof record.id === "string" && record.id.startsWith("sb:") ? record.id.slice(3) : null);
    if (sbId) {
      await updateCustomRow(
        CUSTOM_TABLES.externalIntegrations,
        sbId,
        externalShapeToRow(record, profileId)
      );
      await refreshCustom();
      return;
    }
    if (supabaseEnabled && !record.seed) {
      await insertCustomRow(CUSTOM_TABLES.externalIntegrations, externalShapeToRow(record, profileId));
      await refreshCustom();
      return;
    }
    setLocalData((prev) => {
      const list = prev.externalIntegrations || [];
      const i = list.findIndex((x) => x.id === record.id);
      const next = i >= 0 ? list.map((x) => (x.id === record.id ? record : x)) : [...list, record];
      return { ...prev, externalIntegrations: next };
    });
  };

  const removeExternal = async (id) => {
    const sbId = typeof id === "string" && id.startsWith("sb:") ? id.slice(3) : null;
    if (sbId) {
      await softDeleteCustomRow(CUSTOM_TABLES.externalIntegrations, sbId);
      await refreshCustom();
      return;
    }
    setLocalData((prev) => ({
      ...prev,
      externalIntegrations: (prev.externalIntegrations || []).filter((x) => x.id !== id),
    }));
  };

  const addExternal = (fields) => upsertExternal(createExternalIntegration(fields));

  const setCloudProfile = (cloudProfile) => {
    setLocalData((prev) => ({ ...prev, cloudProfile: { ...prev.cloudProfile, ...cloudProfile } }));
  };

  const setData = useCallback((updater) => {
    setLocalData((prev) => (typeof updater === "function" ? updater(prev) : updater));
  }, []);

  const value = {
    data,
    hydrated,
    setData,
    upsertInternal,
    removeInternal,
    addInternal,
    testInternalConnection,
    startInternalHarvest,
    refreshSources,
    sourcesStatus,
    harvestPollingStalled,
    upsertLlm,
    removeLlm,
    addLlm,
    upsertExternal,
    removeExternal,
    addExternal,
    setCloudProfile,
    refreshCustom,
    supabaseEnabled,
  };

  return (
    <ConfigureWorkspaceContext.Provider value={value}>{children}</ConfigureWorkspaceContext.Provider>
  );
}

export function useConfigureWorkspace() {
  const ctx = useContext(ConfigureWorkspaceContext);
  if (!ctx) {
    throw new Error("useConfigureWorkspace must be used within ConfigureWorkspaceProvider");
  }
  return ctx;
}
