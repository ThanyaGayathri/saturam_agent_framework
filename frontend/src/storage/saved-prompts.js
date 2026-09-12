// Persisted list of prompts the user has saved from chat. Scoped per demo
// profile so the retail workspace and the Middleby workspace stay separated.

import { readLocalJson, writeLocalJson } from "./local-json";
import { normalizeDemoProfileId, DEFAULT_DEMO_PROFILE_ID } from "../domain/demo-profiles";

const BASE_KEY = "retailgpt:saved-prompts:v1";

function keyForProfile(profileId) {
  const id = normalizeDemoProfileId(profileId);
  return id === DEFAULT_DEMO_PROFILE_ID ? BASE_KEY : `${BASE_KEY}:profile:${id}`;
}

function makeId() {
  return `sp-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

export function readSavedPrompts(profileId) {
  const raw = readLocalJson(keyForProfile(profileId), []);
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((p) => p && typeof p.text === "string")
    .map((p) => ({
      id: p.id || makeId(),
      text: p.text,
      label: p.label || "",
      savedAt: p.savedAt || Date.now(),
      lastUsedAt: p.lastUsedAt || null,
      usageCount: typeof p.usageCount === "number" ? p.usageCount : 0,
    }));
}

export function writeSavedPrompts(profileId, prompts) {
  writeLocalJson(keyForProfile(profileId), prompts || []);
}

export function addSavedPrompt(profileId, { text, label }) {
  const clean = String(text || "").trim();
  if (!clean) return null;
  const current = readSavedPrompts(profileId);
  const existing = current.find((p) => p.text.trim().toLowerCase() === clean.toLowerCase());
  if (existing) return existing;
  const entry = {
    id: makeId(),
    text: clean,
    label: (label || "").trim(),
    savedAt: Date.now(),
    lastUsedAt: null,
    usageCount: 0,
  };
  writeSavedPrompts(profileId, [entry, ...current]);
  return entry;
}

export function removeSavedPrompt(profileId, promptId) {
  const current = readSavedPrompts(profileId);
  writeSavedPrompts(profileId, current.filter((p) => p.id !== promptId));
}

export function markSavedPromptUsed(profileId, promptId) {
  const current = readSavedPrompts(profileId);
  const next = current.map((p) =>
    p.id === promptId
      ? { ...p, lastUsedAt: Date.now(), usageCount: (p.usageCount || 0) + 1 }
      : p
  );
  writeSavedPrompts(profileId, next);
}

export function updateSavedPromptLabel(profileId, promptId, label) {
  const current = readSavedPrompts(profileId);
  const next = current.map((p) =>
    p.id === promptId ? { ...p, label: String(label || "").trim() } : p
  );
  writeSavedPrompts(profileId, next);
}

export function updateSavedPromptText(profileId, promptId, text) {
  const clean = String(text || "").trim();
  if (!clean) return;
  const current = readSavedPrompts(profileId);
  const next = current.map((p) =>
    p.id === promptId ? { ...p, text: clean } : p
  );
  writeSavedPrompts(profileId, next);
}
