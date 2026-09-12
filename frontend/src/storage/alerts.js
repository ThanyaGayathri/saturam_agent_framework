import { readLocalJson, writeLocalJson } from "./local-json";
import {
  materializeAlertsSeed,
  materializeMediaAlertsSeed,
  ALERT_SEED,
  MEDIA_ALERT_SEED,
} from "../domain/alerts-seed";
import { MEDIA_PROFILE_ID, MIDDLEBY_PROFILE_ID, normalizeDemoProfileId } from "../domain/demo-profiles";

// Bumped 2 → 3 (2026-08-14) after media alert taxonomy audit: the
// competitor-pricing alert was pointing at the retired `pricing` business
// area, which no longer exists in MEDIA_BUSINESS_AREAS.
const CURRENT_VERSION = 3;

const VALID_SEVERITIES = new Set(["critical", "warning", "info"]);
const VALID_STATUSES = new Set(["new", "acknowledged", "snoozed", "resolved"]);

function normalizeAlert(raw) {
  if (!raw || typeof raw !== "object") return null;
  const severity = VALID_SEVERITIES.has(raw.severity) ? raw.severity : "info";
  const status = VALID_STATUSES.has(raw.status) ? raw.status : "new";
  const createdAt = Number(raw.createdAt) || Date.now();
  return {
    id: String(raw.id || ""),
    severity,
    status,
    title: String(raw.title || "").trim(),
    summary: String(raw.summary || "").trim(),
    businessAreaId: String(raw.businessAreaId || ""),
    processId: String(raw.processId || ""),
    useCaseId: raw.useCaseId ?? null,
    kpi:
      raw.kpi && typeof raw.kpi === "object"
        ? {
            name: String(raw.kpi.name || ""),
            value: String(raw.kpi.value || ""),
            delta: raw.kpi.delta ? String(raw.kpi.delta) : "",
            unit: raw.kpi.unit ? String(raw.kpi.unit) : "",
          }
        : null,
    rule: String(raw.rule || ""),
    recommendedActions: Array.isArray(raw.recommendedActions)
      ? raw.recommendedActions.map((s) => String(s)).filter(Boolean)
      : [],
    createdAt,
    acknowledgedAt:
      typeof raw.acknowledgedAt === "number" ? raw.acknowledgedAt : null,
    resolvedAt:
      typeof raw.resolvedAt === "number" ? raw.resolvedAt : null,
    snoozeUntil:
      typeof raw.snoozeUntil === "number" ? raw.snoozeUntil : null,
    seed: Boolean(raw.seed),
  };
}

function normalizeList(rawList) {
  if (!Array.isArray(rawList)) return [];
  return rawList
    .map(normalizeAlert)
    .filter((a) => a && a.id && a.title);
}

/**
 * Merge stored alerts with the canonical seed so any new seed rows show up
 * on next load, while preserving status changes the user has made (ack,
 * snooze, resolve). Uses seed ids as the identity.
 */
function mergeWithSeed(storedList, nowMs, profileId = null) {
  const id = profileId != null ? normalizeDemoProfileId(profileId) : null;
  const isMedia = id === MEDIA_PROFILE_ID;
  const seedMaterialized = isMedia
    ? materializeMediaAlertsSeed(nowMs)
    : materializeAlertsSeed(nowMs);
  const seedRows = isMedia ? MEDIA_ALERT_SEED : ALERT_SEED;
  const byId = new Map();
  for (const a of seedMaterialized) byId.set(a.id, a);
  for (const a of storedList) {
    const existing = byId.get(a.id);
    if (existing) {
      byId.set(a.id, {
        ...existing,
        status: a.status || existing.status,
        acknowledgedAt: a.acknowledgedAt ?? existing.acknowledgedAt,
        resolvedAt: a.resolvedAt ?? existing.resolvedAt,
        snoozeUntil: a.snoozeUntil ?? existing.snoozeUntil,
        createdAt: existing.createdAt,
      });
    } else {
      byId.set(a.id, a);
    }
  }
  const seedOrder = seedRows.map((s) => s.id);
  const extras = [...byId.keys()].filter((id2) => !seedOrder.includes(id2));
  return [
    ...seedOrder.map((id2) => byId.get(id2)).filter(Boolean),
    ...extras.map((id2) => byId.get(id2)),
  ];
}

export function loadAlerts(storageKey, nowMs = Date.now(), profileId = null) {
  const id = profileId != null ? normalizeDemoProfileId(profileId) : null;
  if (id === MIDDLEBY_PROFILE_ID) {
    const empty = { version: CURRENT_VERSION, alerts: [] };
    const parsed = readLocalJson(storageKey, null);
    if (!parsed || typeof parsed !== "object" || !Array.isArray(parsed.alerts)) {
      writeLocalJson(storageKey, empty);
      return empty;
    }
    const out = { version: CURRENT_VERSION, alerts: normalizeList(parsed.alerts) };
    writeLocalJson(storageKey, out);
    return out;
  }

  const parsed = readLocalJson(storageKey, null);
  const storedVersion = parsed?.version ?? 0;
  if (
    !parsed ||
    typeof parsed !== "object" ||
    !Array.isArray(parsed.alerts) ||
    storedVersion < CURRENT_VERSION
  ) {
    const initial = {
      version: CURRENT_VERSION,
      alerts:
        id === MEDIA_PROFILE_ID
          ? materializeMediaAlertsSeed(nowMs)
          : materializeAlertsSeed(nowMs),
    };
    writeLocalJson(storageKey, initial);
    return initial;
  }
  const stored = normalizeList(parsed.alerts);
  const merged = mergeWithSeed(stored, nowMs, id);
  const out = { version: CURRENT_VERSION, alerts: merged };
  writeLocalJson(storageKey, out);
  return out;
}

export function saveAlerts(data, storageKey) {
  writeLocalJson(storageKey, {
    version: CURRENT_VERSION,
    alerts: normalizeList(data?.alerts),
  });
}

// ── Mutations ──────────────────────────────────────────────────────────────

export function acknowledgeAlert(list, id, nowMs = Date.now()) {
  return list.map((a) =>
    a.id === id && a.status !== "resolved"
      ? { ...a, status: "acknowledged", acknowledgedAt: nowMs }
      : a
  );
}

export function snoozeAlert(list, id, untilMs) {
  return list.map((a) =>
    a.id === id && a.status !== "resolved"
      ? { ...a, status: "snoozed", snoozeUntil: untilMs }
      : a
  );
}

export function resolveAlert(list, id, nowMs = Date.now()) {
  return list.map((a) =>
    a.id === id ? { ...a, status: "resolved", resolvedAt: nowMs } : a
  );
}

export function reopenAlert(list, id) {
  return list.map((a) =>
    a.id === id
      ? { ...a, status: "new", resolvedAt: null, acknowledgedAt: null, snoozeUntil: null }
      : a
  );
}

/** Mark every alert that is still "new" as "acknowledged". Used by the bell's "Mark all as read". */
export function markAllAlertsRead(list, nowMs = Date.now()) {
  return list.map((a) =>
    a.status === "new" ? { ...a, status: "acknowledged", acknowledgedAt: nowMs } : a
  );
}
