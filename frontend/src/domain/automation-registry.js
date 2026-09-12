/**
 * Profile-aware automation registry.
 *
 * Merges playbooks and outputs from every profile (Middleby, retail, GFI) so
 * consumers can call one lookup function regardless of which profile is
 * active. Playbook ids do not collide across profiles (Middleby uses A*,
 * retail uses R*, GFI uses G*).
 */

import { MIDDLEBY_AUTOMATION_PLAYBOOKS } from "./middleby-automation-playbooks";
import { RETAIL_AUTOMATION_PLAYBOOKS } from "./retail-automation-playbooks";
import { GFI_AUTOMATION_PLAYBOOKS } from "./gfi-automation-playbooks";
import { MEDIA_AUTOMATION_PLAYBOOKS } from "./media-automation-playbooks";
import { MIDDLEBY_AUTOMATION_OUTPUTS } from "./middleby-automation-outputs";
import { RETAIL_AUTOMATION_OUTPUTS } from "./retail-automation-outputs";
import { GFI_AUTOMATION_OUTPUTS } from "./gfi-automation-outputs";
import { MEDIA_AUTOMATION_OUTPUTS } from "./media-automation-outputs";
import { USE_CASE_CATALOG_SEED } from "./use-case-catalog-seed";
import { GFI_USE_CASE_CATALOG_SEED } from "./gfi-demo-seed";
import { MEDIA_USE_CASE_CATALOG_SEED } from "./media-demo-seed";
import {
  generateOutputsForEntry,
  generatePlaybookForEntry,
} from "./generic-playbook-generator";
import {
  DEFAULT_DEMO_PROFILE_ID,
  GOURMET_FOODS_PROFILE_ID,
  MEDIA_PROFILE_ID,
  MIDDLEBY_PROFILE_ID,
} from "./demo-profiles";

const ALL_EXPLICIT_PLAYBOOKS = [
  ...MIDDLEBY_AUTOMATION_PLAYBOOKS,
  ...RETAIL_AUTOMATION_PLAYBOOKS,
  ...GFI_AUTOMATION_PLAYBOOKS,
  ...MEDIA_AUTOMATION_PLAYBOOKS,
];

const ALL_EXPLICIT_OUTPUTS = {
  ...MIDDLEBY_AUTOMATION_OUTPUTS,
  ...RETAIL_AUTOMATION_OUTPUTS,
  ...GFI_AUTOMATION_OUTPUTS,
  ...MEDIA_AUTOMATION_OUTPUTS,
};

// Every retail + GFI catalog entry that does not already have an explicit
// playbook gets one auto-generated on first lookup. This gives every use
// case the same activation-page shape without hand-authoring content for
// each of the 100+ catalog entries.
const CATALOG_ENTRIES_BY_ID = new Map();
for (const entry of [
  ...USE_CASE_CATALOG_SEED,
  ...GFI_USE_CASE_CATALOG_SEED,
  ...MEDIA_USE_CASE_CATALOG_SEED,
]) {
  if (entry?.id) CATALOG_ENTRIES_BY_ID.set(entry.id, entry);
}

const EXPLICIT_PLAYBOOK_CATALOG_IDS = new Set();
for (const p of ALL_EXPLICIT_PLAYBOOKS) {
  for (const id of p.catalogIds || []) EXPLICIT_PLAYBOOK_CATALOG_IDS.add(id);
}

const generatedPlaybookCache = new Map();
const generatedOutputsCache = new Map();

function getGeneratedPlaybookForCatalogId(catalogId) {
  if (!catalogId) return null;
  if (EXPLICIT_PLAYBOOK_CATALOG_IDS.has(catalogId)) return null;
  if (generatedPlaybookCache.has(catalogId)) return generatedPlaybookCache.get(catalogId);
  const entry = CATALOG_ENTRIES_BY_ID.get(catalogId);
  if (!entry) return null;
  const playbook = generatePlaybookForEntry(entry);
  generatedPlaybookCache.set(catalogId, playbook);
  return playbook;
}

function getGeneratedOutputsForPlaybookId(playbookId) {
  if (!playbookId?.startsWith("AUTO-")) return null;
  if (generatedOutputsCache.has(playbookId)) return generatedOutputsCache.get(playbookId);
  const catalogId = playbookId.slice(5);
  const entry = CATALOG_ENTRIES_BY_ID.get(catalogId);
  if (!entry) return null;
  const outputs = generateOutputsForEntry(entry);
  generatedOutputsCache.set(playbookId, outputs);
  return outputs;
}

/**
 * Register a live catalog entry (from localStorage / useUseCaseCatalog) so
 * subsequent auto-generated playbook lookups reflect the user's draft
 * snapshot (Step 4 planItems, Step 5 tasks) rather than the static seed.
 * Callers should invoke this before reading the playbook/outputs for a
 * catalog id whose entry may have a live draftSnapshot.
 */
export function registerCatalogEntry(entry) {
  if (!entry?.id) return;
  CATALOG_ENTRIES_BY_ID.set(entry.id, entry);
  // Invalidate caches so the next lookup regenerates with the fresh entry.
  generatedPlaybookCache.delete(entry.id);
  generatedOutputsCache.delete(`AUTO-${entry.id}`);
}

const ALL_PLAYBOOKS = ALL_EXPLICIT_PLAYBOOKS;
const ALL_OUTPUTS = ALL_EXPLICIT_OUTPUTS;

const PLAYBOOK_PROFILE_BY_ID_PREFIX = {
  A: MIDDLEBY_PROFILE_ID,
  R: DEFAULT_DEMO_PROFILE_ID,
  G: GOURMET_FOODS_PROFILE_ID,
  M: MEDIA_PROFILE_ID,
};

/** Returns the playbook by id, across all profiles. */
export function getPlaybook(playbookId) {
  const explicit = ALL_PLAYBOOKS.find((p) => p.id === playbookId);
  if (explicit) return explicit;
  // AUTO-generated playbook lookup by playbookId.
  if (playbookId?.startsWith("AUTO-")) {
    const catalogId = playbookId.slice(5);
    return getGeneratedPlaybookForCatalogId(catalogId);
  }
  return null;
}

/**
 * Playbooks whose catalogIds include the given catalog id. Returns explicit
 * playbooks (A*, R*, G*) when they exist; otherwise falls back to a
 * generated one so every catalog entry has a playbook available.
 */
export function getPlaybooksForCatalogId(catalogId) {
  if (!catalogId) return [];
  const explicit = ALL_PLAYBOOKS.filter((p) => (p.catalogIds || []).includes(catalogId));
  if (explicit.length > 0) return explicit;
  const generated = getGeneratedPlaybookForCatalogId(catalogId);
  return generated ? [generated] : [];
}

/**
 * Patterns (subset shape) for a catalog id, for use with the Insight Session
 * automation panel. Each has {id, label, poc}.
 */
export function getPatternsForCatalogId(catalogId) {
  return getPlaybooksForCatalogId(catalogId).map((p) => ({
    id: p.id,
    label: p.label,
    poc: true,
  }));
}

/** Outputs for a playbook, across all profiles.
 *
 * If the caller has already registered a catalog entry with a draftSnapshot
 * (via registerCatalogEntry, which the activation flow does on every render)
 * and the playbook's catalogIds match that entry, we OVERLAY the user's
 * Step 4 plan items onto the playbook's reviewSteps. This keeps the
 * activation page in sync with what the user reviewed in the Builder,
 * even for hand-authored playbooks (R1, R2, G1, G2, A1-A7).
 */
export function getAutomationOutputs(playbookId) {
  const base = ALL_OUTPUTS[playbookId] ?? getGeneratedOutputsForPlaybookId(playbookId);
  if (!base) return null;

  // Find the catalog entry that this playbook targets.
  const playbook = getPlaybook(playbookId);
  const catalogId = playbook?.catalogIds?.[0];
  if (!catalogId) return base;
  const entry = CATALOG_ENTRIES_BY_ID.get(catalogId);
  const draftPlanItems = Array.isArray(entry?.draftSnapshot?.step4?.planItems)
    ? entry.draftSnapshot.step4.planItems
    : [];
  if (draftPlanItems.length === 0) return base;

  return {
    ...base,
    reviewSteps: draftPlanItems.map((pi) => ({
      title: String(pi.title || "Prepared step"),
      description: pi.description ? String(pi.description) : undefined,
    })),
  };
}

/** True if the playbook requires human approval before write actions. */
export function playbookRequiresApproval(playbook) {
  return Boolean(playbook?.decisionCardId);
}

/** Best-effort profile id for a playbook id, based on the id prefix. */
export function profileIdForPlaybookId(playbookId) {
  if (!playbookId) return null;
  const prefix = playbookId.charAt(0).toUpperCase();
  return PLAYBOOK_PROFILE_BY_ID_PREFIX[prefix] ?? null;
}

/** All registered playbooks. Used by the Enterprise GPT impact dashboard. */
export function getAllPlaybooks() {
  return ALL_PLAYBOOKS;
}

/** Playbooks scoped to a specific profile (via id prefix). */
export function getPlaybooksForProfile(profileId) {
  if (!profileId) return [];
  return ALL_PLAYBOOKS.filter((p) => profileIdForPlaybookId(p.id) === profileId);
}

/**
 * For each KPI name in the input list, return the playbooks that target it.
 * Case-insensitive match on the target `name` field.
 */
export function playbooksTargetingKpiName(kpiName, profileId = null) {
  if (!kpiName) return [];
  const needle = String(kpiName).trim().toLowerCase();
  const pool = profileId ? getPlaybooksForProfile(profileId) : ALL_PLAYBOOKS;
  return pool.filter((p) =>
    (p.kpiTargets || []).some((t) => String(t.name || "").trim().toLowerCase() === needle)
  );
}

/**
 * The right destination when a user clicks a use case card. All use cases
 * open on the Activation flow (the "use case page") for consistency across
 * profiles. Playbook-backed use cases render the Prepare -> Review ->
 * Approve -> Delivered flow; non-playbook use cases render the
 * "automation not configured yet" placeholder with an Edit CTA and a link
 * to the Impact Dashboard.
 */
export function useCaseActivationPath(catalogId) {
  if (!catalogId) return "/use-cases";
  return `/use-cases/activate/${encodeURIComponent(catalogId)}`;
}
