// Named reports. Each report is a titled list of pinned blocks. One report is
// "active" at a time — the Add-to-report action pins to the active report so
// the single-click pin stays fast. Users can create additional reports and
// switch which one is active from the drawer.
//
// Storage shape (per profile):
//   {
//     activeId: "rp-...",
//     reports: [
//       { id, title, createdAt, updatedAt, items: [{ id, block, sourceQuestion, catalogId, pinnedAt }] }
//     ]
//   }
//
// The previous flat-array format is migrated on read into a single report
// called "My first report" so nothing pinned before this change is lost.

import { readLocalJson, writeLocalJson } from "./local-json";
import { normalizeDemoProfileId, DEFAULT_DEMO_PROFILE_ID } from "../domain/demo-profiles";

const BASE_KEY = "retailgpt:pinned-report:v1";

function keyForProfile(profileId) {
  const id = normalizeDemoProfileId(profileId);
  return id === DEFAULT_DEMO_PROFILE_ID ? BASE_KEY : `${BASE_KEY}:profile:${id}`;
}

function makeReportId() {
  return `rp-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

function makeItemId() {
  return `it-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

function emptyState() {
  const first = {
    id: makeReportId(),
    title: "My first report",
    createdAt: Date.now(),
    updatedAt: Date.now(),
    items: [],
  };
  return { activeId: first.id, reports: [first] };
}

function normaliseItem(raw) {
  if (!raw || !raw.block) return null;
  return {
    id: raw.id || makeItemId(),
    block: raw.block,
    sourceQuestion: raw.sourceQuestion || "",
    catalogId: raw.catalogId || null,
    pinnedAt: raw.pinnedAt || Date.now(),
  };
}

function readState(profileId) {
  const raw = readLocalJson(keyForProfile(profileId), null);
  if (!raw) return emptyState();

  // Legacy: previously stored as a flat array of items — migrate to a single report.
  if (Array.isArray(raw)) {
    const items = raw.map(normaliseItem).filter(Boolean);
    const first = {
      id: makeReportId(),
      title: "My first report",
      createdAt: Date.now(),
      updatedAt: Date.now(),
      items,
    };
    const next = { activeId: first.id, reports: [first] };
    writeState(profileId, next);
    return next;
  }

  const reports = Array.isArray(raw.reports)
    ? raw.reports.map((r) => ({
        id: r.id || makeReportId(),
        title: (r.title || "Untitled report").trim() || "Untitled report",
        createdAt: r.createdAt || Date.now(),
        updatedAt: r.updatedAt || Date.now(),
        items: Array.isArray(r.items) ? r.items.map(normaliseItem).filter(Boolean) : [],
      }))
    : [];

  if (!reports.length) return emptyState();
  const activeId = reports.some((r) => r.id === raw.activeId) ? raw.activeId : reports[0].id;
  return { activeId, reports };
}

function writeState(profileId, state) {
  writeLocalJson(keyForProfile(profileId), state);
}

// -- reads ------------------------------------------------------------------

export function readReports(profileId) {
  return readState(profileId);
}

export function readActiveReport(profileId) {
  const s = readState(profileId);
  return s.reports.find((r) => r.id === s.activeId) || s.reports[0] || null;
}

// -- report lifecycle -------------------------------------------------------

export function createReport(profileId, { title, makeActive = true } = {}) {
  const s = readState(profileId);
  const report = {
    id: makeReportId(),
    title: (title || "").trim() || "Untitled report",
    createdAt: Date.now(),
    updatedAt: Date.now(),
    items: [],
  };
  const next = {
    activeId: makeActive ? report.id : s.activeId,
    reports: [report, ...s.reports],
  };
  writeState(profileId, next);
  return report;
}

export function renameReport(profileId, reportId, title) {
  const s = readState(profileId);
  const next = {
    ...s,
    reports: s.reports.map((r) =>
      r.id === reportId
        ? { ...r, title: (title || "").trim() || r.title, updatedAt: Date.now() }
        : r
    ),
  };
  writeState(profileId, next);
}

export function deleteReport(profileId, reportId) {
  const s = readState(profileId);
  const remaining = s.reports.filter((r) => r.id !== reportId);
  if (!remaining.length) {
    // Never leave the user with zero reports — replace with an empty one.
    const first = {
      id: makeReportId(),
      title: "My first report",
      createdAt: Date.now(),
      updatedAt: Date.now(),
      items: [],
    };
    writeState(profileId, { activeId: first.id, reports: [first] });
    return;
  }
  const activeId = s.activeId === reportId ? remaining[0].id : s.activeId;
  writeState(profileId, { activeId, reports: remaining });
}

export function setActiveReport(profileId, reportId) {
  const s = readState(profileId);
  if (!s.reports.some((r) => r.id === reportId)) return;
  writeState(profileId, { ...s, activeId: reportId });
}

// -- item lifecycle ---------------------------------------------------------

export function addBlockToActiveReport(profileId, { block, sourceQuestion, catalogId }) {
  if (!block?.id) return null;
  const s = readState(profileId);
  const activeId = s.activeId;
  let entry = null;
  const nextReports = s.reports.map((r) => {
    if (r.id !== activeId) return r;
    if (r.items.some((it) => it.block.id === block.id)) return r; // already pinned
    entry = {
      id: makeItemId(),
      block,
      sourceQuestion: sourceQuestion || "",
      catalogId: catalogId || null,
      pinnedAt: Date.now(),
    };
    return { ...r, items: [...r.items, entry], updatedAt: Date.now() };
  });
  writeState(profileId, { ...s, reports: nextReports });
  return entry;
}

export function removeBlockFromReport(profileId, reportId, blockId) {
  const s = readState(profileId);
  const nextReports = s.reports.map((r) =>
    r.id === reportId
      ? { ...r, items: r.items.filter((it) => it.block.id !== blockId), updatedAt: Date.now() }
      : r
  );
  writeState(profileId, { ...s, reports: nextReports });
}

export function clearReport(profileId, reportId) {
  const s = readState(profileId);
  const nextReports = s.reports.map((r) =>
    r.id === reportId ? { ...r, items: [], updatedAt: Date.now() } : r
  );
  writeState(profileId, { ...s, reports: nextReports });
}

export function isBlockInActiveReport(profileId, blockId) {
  const active = readActiveReport(profileId);
  return active ? active.items.some((it) => it.block.id === blockId) : false;
}
