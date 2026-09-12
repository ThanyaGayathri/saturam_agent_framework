import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { useDemoProfile } from "./DemoProfileContext";
import { readActiveDemoProfileId } from "../storage/demo-profile-preference";
import { storageKeysForProfile } from "../storage/keys";
import {
  loadConfigureWorkspace,
  saveConfigureWorkspace,
  createInternalSource,
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

/**
 * Configure Workspace context.
 *
 * Seed rows (from code) + localStorage-added rows still work as before.
 * New / edited rows go to Supabase when it's enabled so the whole sales
 * team sees the same list. Same overlay pattern as useKpiRegistry etc.
 *
 * Cloud profile stays in localStorage (single settings object; no shared
 * edit needed today). See pendingItems.md 6.10 for future migration.
 */

const ConfigureWorkspaceContext = createContext(null);

// ─── Supabase row ↔ app shape converters ──────────────────────────────

function internalRowToShape(row) {
  return normalizeInternal({
    id: `sb:${row.id}`,
    __supabaseId: row.id,
    __originSeedId: row.origin_seed_id || null,
    sourceType: row.source_type,
    name: row.name,
    host: row.host,
    port: row.port,
    databaseName: row.database_name,
    schema: row.schema_name,
    authMethod: row.auth_method,
    username: row.username,
    password: row.password,
    apiKey: row.api_key,
    sslMode: row.ssl_mode,
    permissionLevel: row.permission_level,
    seed: false,
  });
}

function internalShapeToRow(record, profileId) {
  return {
    profile_id: profileId,
    source_type: record.sourceType || "postgres",
    name: record.name || "",
    host: record.host || null,
    port: record.port || null,
    database_name: record.databaseName || null,
    schema_name: record.schema || null,
    auth_method: record.authMethod || "username_password",
    username: record.username || null,
    password: record.password || null,
    api_key: record.apiKey || null,
    ssl_mode: record.sslMode || "require",
    permission_level: record.permissionLevel === "read_write" ? "read_write" : "read_only",
    extras: record.extras || {},
    origin_seed_id: record.__originSeedId || null,
  };
}

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
  const [customInternal, setCustomInternal] = useState([]);
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

  // Supabase fetch + realtime for the three list-shaped domains.
  const refreshCustom = useCallback(async () => {
    if (!isSupabaseEnabled() || !profileId) {
      setCustomInternal([]);
      setCustomLlm([]);
      setCustomExternal([]);
      return;
    }
    const [ins, lls, exs] = await Promise.all([
      listCustomRows(CUSTOM_TABLES.internalSources, profileId),
      listCustomRows(CUSTOM_TABLES.llmModels, profileId),
      listCustomRows(CUSTOM_TABLES.externalIntegrations, profileId),
    ]);
    setCustomInternal(ins);
    setCustomLlm(lls);
    setCustomExternal(exs);
  }, [profileId]);

  useEffect(() => {
    void refreshCustom();
  }, [refreshCustom]);

  useEffect(() => {
    if (!isSupabaseEnabled() || !profileId) return undefined;
    const unsubs = [
      subscribeCustomTable(CUSTOM_TABLES.internalSources, profileId, () => void refreshCustom()),
      subscribeCustomTable(CUSTOM_TABLES.llmModels, profileId, () => void refreshCustom()),
      subscribeCustomTable(CUSTOM_TABLES.externalIntegrations, profileId, () => void refreshCustom()),
    ];
    return () => unsubs.forEach((u) => u && u());
  }, [profileId, refreshCustom]);

  const supabaseEnabled = isSupabaseEnabled();

  const data = useMemo(() => {
    const merged = {
      ...localData,
      internalSources: [...(localData.internalSources || []), ...customInternal.map(internalRowToShape)],
      llmModels: [...(localData.llmModels || []), ...customLlm.map(llmRowToShape)],
      externalIntegrations: [
        ...(localData.externalIntegrations || []),
        ...customExternal.map(externalRowToShape),
      ],
    };
    return merged;
  }, [localData, customInternal, customLlm, customExternal]);

  // ─── Internal sources ────────────────────────────────────────────────

  const upsertInternal = async (record) => {
    // Existing sb: row → update in Supabase. normalize* strips the
    // __supabaseId marker on save, so fall back to reading the `sb:` prefix
    // off the id itself.
    const sbId =
      record.__supabaseId ||
      (typeof record.id === "string" && record.id.startsWith("sb:") ? record.id.slice(3) : null);
    if (sbId) {
      await updateCustomRow(
        CUSTOM_TABLES.internalSources,
        sbId,
        internalShapeToRow(record, profileId)
      );
      await refreshCustom();
      return;
    }
    // New row while Supabase is enabled → insert into Supabase
    if (supabaseEnabled && !record.seed) {
      await insertCustomRow(CUSTOM_TABLES.internalSources, internalShapeToRow(record, profileId));
      await refreshCustom();
      return;
    }
    // Otherwise (Supabase off, or seed override) → localStorage as before
    setLocalData((prev) => {
      const list = prev.internalSources || [];
      const i = list.findIndex((x) => x.id === record.id);
      const next = i >= 0 ? list.map((x) => (x.id === record.id ? record : x)) : [...list, record];
      return { ...prev, internalSources: next };
    });
  };

  const removeInternal = async (id) => {
    const sbId = typeof id === "string" && id.startsWith("sb:") ? id.slice(3) : null;
    if (sbId) {
      await softDeleteCustomRow(CUSTOM_TABLES.internalSources, sbId);
      await refreshCustom();
      return;
    }
    // Legacy localStorage or seed row → filter out locally (seeds re-hydrate on reload)
    setLocalData((prev) => ({
      ...prev,
      internalSources: (prev.internalSources || []).filter((x) => x.id !== id),
    }));
  };

  const addInternal = (fields) => upsertInternal(createInternalSource(fields));

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
