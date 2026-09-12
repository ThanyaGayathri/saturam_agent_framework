import { DEFAULT_DEMO_PROFILE_ID, normalizeDemoProfileId } from "../domain/demo-profiles";

/** Browser storage key constants - separate key per domain, prefixed. */
export const STORAGE_KEYS = {
  KPI_REGISTRY: "retailgpt:kpis:v1",
  PROCESSES: "retailgpt:processes:v1",
  USE_CASE_CATALOG: "retailgpt:use-cases:v1",
  USE_CASE_DRAFT: "retailgpt:use-case-draft:v1",
  /** Internal sources, LLM models, cloud profile, external integrations. */
  CONFIGURE_WORKSPACE: "retailgpt:configure-workspace:v1",
  /** Operational alerts (seeded on first load; mutable by user actions). */
  ALERTS: "retailgpt:alerts:v1",
  /** User-added KPI sources shown in the KPI Gallery. */
  KPI_SOURCES: "retailgpt:kpi-sources:v1",
  /** Persisted business hierarchy (business areas + subject areas). */
  BUSINESS_AREAS: "retailgpt:business-areas:v1",
};

/** Scoped copies per demo customer profile (`default` keeps legacy unprefixed keys). */
export function storageKeysForProfile(profileId) {
  const id = normalizeDemoProfileId(profileId);
  const suffix = id === DEFAULT_DEMO_PROFILE_ID ? "" : `:profile:${id}`;
  return {
    KPI_REGISTRY: `retailgpt:kpis:v1${suffix}`,
    PROCESSES: `retailgpt:processes:v1${suffix}`,
    USE_CASE_CATALOG: `retailgpt:use-cases:v1${suffix}`,
    USE_CASE_DRAFT: `retailgpt:use-case-draft:v1${suffix}`,
    CONFIGURE_WORKSPACE: `retailgpt:configure-workspace:v1${suffix}`,
    ALERTS: `retailgpt:alerts:v1${suffix}`,
    KPI_SOURCES: `retailgpt:kpi-sources:v1${suffix}`,
    BUSINESS_AREAS: `retailgpt:business-areas:v1${suffix}`,
  };
}

/** Full builder draft snapshot for a catalog row (separate key so large payloads persist reliably). */
export function useCaseCatalogEntrySnapshotKey(catalogId, profileId) {
  const id = normalizeDemoProfileId(profileId);
  const suffix = id === DEFAULT_DEMO_PROFILE_ID ? "" : `:profile:${id}`;
  return `retailgpt:uc-entry-snapshot:v1:${catalogId}${suffix}`;
}

/** One-shot handoff: Playground hub "Ask anything" → `/playground/chat` (sessionStorage). */
export const PLAYGROUND_HUB_CHAT_MESSAGE_KEY = "retailgpt:playground-hub-chat-message:v1";

/** One-shot handoff: hub "See the new answer format" pill → assistant-only preview injection. */
export const PLAYGROUND_HUB_SHOW_PREVIEW_KEY = "retailgpt:playground-hub-show-preview:v1";

/** One-shot handoff: Use Case templates ask bar / "Try asking" → `/use-cases/builder-chat` (sessionStorage). */
export const BUILDER_HUB_CHAT_MESSAGE_KEY = "retailgpt:builder-hub-chat-message:v1";

/** One-shot handoff: Step 10 Launch → `/use-cases/launch-summary` (sessionStorage). */
export const USE_CASE_LAUNCH_SUMMARY_KEY = "retailgpt:use-case-launch-summary:v1";

/** Co-Pilot Decision Cards seeded after use case activation (sessionStorage). */
export const COPILOT_DECISIONS_KEY = "retailgpt:copilot-decisions:v1";

/** One-shot: Playground chat (seed template) → Use Case Builder step 1 prefill (sessionStorage). */
export const BUILDER_PREFILL_FROM_TEMPLATE_KEY = "retailgpt:builder-prefill-from-template:v1";

/**
 * One-shot handoff: attachment files staged on a "home" composer that need to
 * travel into the chat screen. Stores only a handoff id pointing at an
 * in-memory blob buffer (blobs can't go in sessionStorage).
 */
export const HUB_CHAT_ATTACH_HANDOFF_KEY = "retailgpt:hub-chat-attach-handoff:v1";

/** IndexedDB database (chat and other append-heavy data). */
export const IDB = {
  DB_NAME: "retailgpt",
  /** Bumped when chat schema needs migration (e.g. new object stores or indexes). */
  DB_VERSION: 3,
  STORE_MESSAGES: "chat_messages",
  STORE_ATTACHMENTS: "chat_attachments",
};

export const CHAT_ATTACHMENT_LIMITS = {
  /** Per-file cap (bytes). Keeps the demo quota and UI responsive. */
  MAX_FILE_BYTES: 10 * 1024 * 1024,
  /** Max staged files per outgoing message. */
  MAX_FILES_PER_MESSAGE: 5,
};
