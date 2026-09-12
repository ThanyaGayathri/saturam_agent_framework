import { readLocalJson, writeLocalJson } from "./local-json";
import { MEDIA_PROFILE_ID, MIDDLEBY_PROFILE_ID, normalizeDemoProfileId } from "../domain/demo-profiles";
import {
 MIDDLEBY_CONFIGURE_SEED_EXTERNAL,
 MIDDLEBY_CONFIGURE_SEED_INTERNAL,
 MIDDLEBY_CONFIGURE_SEED_LLM,
} from "../domain/middleby-configure-seed";
import {
 MEDIA_CONFIGURE_SEED_EXTERNAL,
 MEDIA_CONFIGURE_SEED_INTERNAL,
 MEDIA_CONFIGURE_SEED_LLM,
} from "../domain/media-configure-seed";

function genId(prefix) {
 if (typeof crypto !== "undefined" && crypto.randomUUID) return `${prefix}_${crypto.randomUUID()}`;
 return `${prefix}_${Date.now()}_${Math.random().toString(36).slice(2, 9)}`;
}

const DEFAULT_CLOUD = {
 summaryPublic: "SaaS hosted in cloud provider of your choice.",
 provider: "gcp",
 region: "us-central1",
 credentials: "",
 environment: "production",
};

const LEGACY_CLOUD_SUMMARY = "SaaS hosted by Saturam - Production";

export const CONFIGURE_SEED_INTERNAL = [
 {
 id: "seed_postgres_demo",
 sourceType: "postgres",
 name: "PostgreSQL - retail_db",
 host: "db.retail.internal",
 port: "5432",
 databaseName: "retail_db",
 schema: "public",
 authMethod: "username_password",
 username: "ai_readonly",
 password: "",
 apiKey: "",
 sslMode: "require",
 permissionLevel: "read_only",
 seed: true,
 },
 {
 id: "seed_snowflake_demo",
 sourceType: "snowflake",
 name: "Snowflake - RETAIL_DW",
 host: "xy12345.us-east-1.snowflakecomputing.com",
 port: "443",
 databaseName: "RETAIL_DW",
 schema: "ANALYTICS",
 authMethod: "username_password",
 username: "AI_SERVICE",
 password: "",
 apiKey: "",
 sslMode: "require",
 permissionLevel: "read_only",
 seed: true,
 },
 {
 id: "seed_sap_s4_gallery",
 sourceType: "sap_s4hana",
 name: "SAP S/4HANA - sap_prod",
 host: "s4hana.retail.internal",
 port: "44300",
 databaseName: "S4HANA_PROD",
 schema: "SAPHANADB",
 authMethod: "username_password",
 username: "AI_READONLY",
 password: "",
 apiKey: "",
 sslMode: "require",
 permissionLevel: "read_only",
 seed: true,
 },
 {
 id: "seed_pricing_system",
 sourceType: "postgres",
 name: "Pricing system - price_book",
 host: "pricing.retail.internal",
 port: "5432",
 databaseName: "pricing",
 schema: "price_book",
 authMethod: "username_password",
 username: "egpt_writer",
 password: "",
 apiKey: "",
 sslMode: "require",
 permissionLevel: "read_write",
 seed: true,
 },
 {
 id: "seed_inventory_system",
 sourceType: "sap_s4hana",
 name: "Inventory system - MRP parameters",
 host: "s4hana.retail.internal",
 port: "44300",
 databaseName: "S4HANA_PROD",
 schema: "MRP",
 authMethod: "username_password",
 username: "EGPT_WRITER",
 password: "",
 apiKey: "",
 sslMode: "require",
 permissionLevel: "read_write",
 seed: true,
 },
 {
 id: "seed_salesforce_gallery",
 sourceType: "salesforce",
 name: "Salesforce - Service Cloud",
 host: "retail-enterprise.my.salesforce.com",
 port: "443",
 databaseName: "ORG_00D_KPI_DEMO",
 schema: "ServiceCloud",
 authMethod: "oauth",
 username: "integration.api@retail.demo",
 password: "",
 apiKey: "",
 sslMode: "require",
 permissionLevel: "read_only",
 seed: true,
 },
];

/** A channel the alerts can be pointed at. Only a configured, active integration is offered
 *  as a delivery channel on the use-case form. */
export const CONFIGURE_SEED_EXTERNAL = [
 {
 id: "seed_slack_alerts",
 providerId: "slack",
 name: "Slack - #replenishment-alerts",
 integrationType: "alerts",
 environment: "production",
 syncFrequency: "realtime",
 dataScope: "Alert pointers only",
 status: "active",
 authType: "oauth2",
 connectionStatus: "connected",
 seed: true,
 },
];

export function normalizeInternal(s) {
 return {
 id: s.id,
 sourceType: s.sourceType || "postgres",
 name: s.name || "",
 host: s.host || "",
 port: s.port || "",
 databaseName: s.databaseName || "",
 schema: s.schema || "",
 authMethod: s.authMethod || "username_password",
 username: s.username || "",
 password: s.password || "",
 apiKey: s.apiKey || "",
 sslMode: s.sslMode || "require",
 permissionLevel: s.permissionLevel === "read_write" ? "read_write" : "read_only",
 seed: !!s.seed,
 };
}

export const CONFIGURE_SEED_LLM = [
 {
 id: "seed_openai_gpt4o",
 providerId: "openai",
 modelId: "gpt-4o",
 apiKey: "",
 baseUrl: "",
 isDefault: true,
 seed: true,
 },
 {
 id: "seed_anthropic_sonnet",
 providerId: "anthropic",
 modelId: "claude-sonnet-4-20250514",
 apiKey: "",
 baseUrl: "",
 isDefault: false,
 seed: true,
 },
 {
 id: "seed_azure_gpt4o",
 providerId: "azure_openai",
 modelId: "gpt-4o",
 apiKey: "",
 baseUrl: "https://retail-ai.openai.azure.com/",
 isDefault: false,
 seed: true,
 },
 {
 id: "seed_bedrock_claude",
 providerId: "bedrock",
 modelId: "anthropic.claude-3-5-sonnet-20240620-v1:0",
 apiKey: "",
 baseUrl: "",
 isDefault: false,
 seed: true,
 },
 {
 id: "seed_google_gemini",
 providerId: "google",
 modelId: "gemini-2.0-flash",
 apiKey: "",
 baseUrl: "",
 isDefault: false,
 seed: true,
 },
 {
 id: "seed_openai_mini",
 providerId: "openai",
 modelId: "gpt-4o-mini",
 apiKey: "",
 baseUrl: "",
 isDefault: false,
 seed: true,
 },
];

export function normalizeLlm(m) {
 return {
 id: m.id,
 providerId: m.providerId || "openai",
 modelId: m.modelId || "gpt-4o-mini",
 apiKey: m.apiKey || "",
 baseUrl: m.baseUrl || "",
 isDefault: !!m.isDefault,
 seed: !!m.seed,
 };
}

export function normalizeExternal(e) {
 return {
 id: e.id,
 providerId: e.providerId || "shopify",
 name: e.name || "",
 integrationType: e.integrationType || "",
 environment: e.environment || "production",
 syncFrequency: e.syncFrequency || "hourly",
 dataScope: e.dataScope || "",
 status: e.status === "inactive" ? "inactive" : "active",
 authType: e.authType || "api_key",
 clientId: e.clientId || "",
 clientSecret: e.clientSecret || "",
 authUrl: e.authUrl || "",
 tokenUrl: e.tokenUrl || "",
 redirectUri: e.redirectUri || "",
 scopes: e.scopes || "",
 apiKey: e.apiKey || "",
 apiSecret: e.apiSecret || "",
 baseUrl: e.baseUrl || "",
 apiVersion: e.apiVersion || "",
 basicUser: e.basicUser || "",
 basicPassword: e.basicPassword || "",
 webhookUrl: e.webhookUrl || "",
 webhookSecret: e.webhookSecret || "",
 webhookEvents: e.webhookEvents || "",
 connectionStatus: e.connectionStatus || "untested",
 lastSyncAt: e.lastSyncAt || null,
 lastError: e.lastError || "",
 };
}

function mergeExternalSeeds(list, profileId = null) {
 const seeds = configureSeedsForProfile(profileId).external || [];
 const byId = new Map((list || []).map((x) => [x.id, normalizeExternal(x)]));
 for (const seed of seeds) {
 if (!byId.has(seed.id)) {
 byId.set(seed.id, normalizeExternal({ ...seed, status: seed.seed === false ? "inactive" : "active" }));
 }
 }
 const order = [...seeds.map((s) => s.id), ...[...byId.keys()].filter((id) => !seeds.some((s) => s.id === id))];
 return order.map((id) => byId.get(id)).filter(Boolean);
}

function configureSeedsForProfile(profileId = null) {
 const id = profileId != null ? normalizeDemoProfileId(profileId) : null;
 if (id === MIDDLEBY_PROFILE_ID) {
 return {
 internal: MIDDLEBY_CONFIGURE_SEED_INTERNAL,
 llm: MIDDLEBY_CONFIGURE_SEED_LLM,
 external: MIDDLEBY_CONFIGURE_SEED_EXTERNAL,
 };
 }
 if (id === MEDIA_PROFILE_ID) {
 return {
 internal: MEDIA_CONFIGURE_SEED_INTERNAL,
 llm: MEDIA_CONFIGURE_SEED_LLM,
 external: MEDIA_CONFIGURE_SEED_EXTERNAL,
 };
 }
 return {
 internal: CONFIGURE_SEED_INTERNAL,
 llm: CONFIGURE_SEED_LLM,
 external: CONFIGURE_SEED_EXTERNAL,
 };
}

function mergeInternalSeeds(list, profileId = null) {
 const seeds = configureSeedsForProfile(profileId).internal;
 const byId = new Map((list || []).map((x) => [x.id, normalizeInternal(x)]));
 // Only re-add seeds that are missing from localStorage. Do NOT overwrite
 // existing entries — that would revert user edits on every reload. Matches
 // the mergeLlmSeeds / mergeExternalSeeds behavior for consistency.
 for (const seed of seeds) {
 if (!byId.has(seed.id)) byId.set(seed.id, normalizeInternal({ ...seed }));
 }
 const order = [...seeds.map((s) => s.id), ...[...byId.keys()].filter((id) => !seeds.some((s) => s.id === id))];
 return order.map((id) => byId.get(id)).filter(Boolean);
}

function mergeLlmSeeds(list, profileId = null) {
 const seeds = configureSeedsForProfile(profileId).llm;
 const byId = new Map((list || []).map((x) => [x.id, normalizeLlm(x)]));
 for (const seed of seeds) {
 if (!byId.has(seed.id)) byId.set(seed.id, normalizeLlm({ ...seed }));
 }
 const order = [...seeds.map((s) => s.id), ...[...byId.keys()].filter((id) => !seeds.some((s) => s.id === id))];
 return order.map((id) => byId.get(id)).filter(Boolean);
}

function defaultWorkspace(profileId = null) {
 return {
 version: 1,
 internalSources: mergeInternalSeeds([], profileId),
 llmModels: mergeLlmSeeds([], profileId),
 cloudProfile: { ...DEFAULT_CLOUD },
 externalIntegrations: mergeExternalSeeds([], profileId),
 };
}

export function loadConfigureWorkspace(storageKey, profileId = null) {
 const seeds = configureSeedsForProfile(profileId);
 const parsed = readLocalJson(storageKey, null);
 if (!parsed || typeof parsed !== "object") {
 const initial = defaultWorkspace(profileId);
 writeLocalJson(storageKey, initial);
 return initial;
 }
 const internalSources = mergeInternalSeeds(parsed.internalSources, profileId);
 const externalIntegrations = mergeExternalSeeds(
 Array.isArray(parsed.externalIntegrations) ? parsed.externalIntegrations : [],
 profileId
 );
 const hadAllInternalSeeds = seeds.internal.every((s) =>
 (parsed.internalSources || []).some((p) => p.id === s.id)
 );
 const hadAllLlmSeeds = seeds.llm.every((s) => (parsed.llmModels || []).some((p) => p.id === s.id));
 const hadAllExternalSeeds = (seeds.external || []).every((s) =>
 (parsed.externalIntegrations || []).some((p) => p.id === s.id)
 );
 const mergedCloud = parsed.cloudProfile && typeof parsed.cloudProfile === "object"
 ? { ...DEFAULT_CLOUD, ...parsed.cloudProfile }
 : { ...DEFAULT_CLOUD };
 const hadLegacySummary = mergedCloud.summaryPublic === LEGACY_CLOUD_SUMMARY;
 if (hadLegacySummary) {
 mergedCloud.summaryPublic = DEFAULT_CLOUD.summaryPublic;
 }

 const out = {
 version: parsed.version ?? 1,
 internalSources,
 llmModels: mergeLlmSeeds(Array.isArray(parsed.llmModels) ? parsed.llmModels : [], profileId),
 cloudProfile: mergedCloud,
 externalIntegrations,
 };
 if (!hadAllInternalSeeds || !hadAllLlmSeeds || !hadAllExternalSeeds || hadLegacySummary) writeLocalJson(storageKey, out);
 return out;
}

export function saveConfigureWorkspace(data, storageKey) {
 writeLocalJson(storageKey, {
 version: data.version ?? 1,
 internalSources: (data.internalSources || []).map(normalizeInternal),
 llmModels: (data.llmModels || []).map(normalizeLlm),
 cloudProfile: { ...DEFAULT_CLOUD, ...(data.cloudProfile || {}) },
 externalIntegrations: (data.externalIntegrations || []).map(normalizeExternal),
 });
}

export function createInternalSource(fields) {
 return normalizeInternal({
 ...fields,
 id: genId("isrc"),
 seed: false,
 });
}

export function createLlmModel(fields) {
 return normalizeLlm({
 ...fields,
 id: genId("llm"),
 });
}

export function createExternalIntegration(fields) {
 return normalizeExternal({
 ...fields,
 id: genId("ext"),
 });
}
