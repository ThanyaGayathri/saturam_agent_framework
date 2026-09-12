import { getMiddlebyDecisionSeedCards, MIDDLEBY_DECISIONS_KEY } from "./middleby-decision-seeds";

/** Per-playbook simulate-run flags (A3, A5, etc.). */
export const MIDDLEBY_PLAYBOOK_RUN_KEY = "retailgpt:middleby-playbook-run:v1";

/** Per-playbook delivery timestamps (real Date captured when the user approves/runs). */
export const MIDDLEBY_PLAYBOOK_DELIVERED_AT_KEY = "retailgpt:middleby-playbook-delivered-at:v1";

export function readSimulatedRun(patternId) {
  if (!patternId) return false;
  try {
    const raw = sessionStorage.getItem(MIDDLEBY_PLAYBOOK_RUN_KEY);
    const map = raw ? JSON.parse(raw) : {};
    return Boolean(map[patternId]);
  } catch {
    return false;
  }
}

export function writeSimulatedRun(patternId, completed) {
  if (!patternId) return;
  try {
    const raw = sessionStorage.getItem(MIDDLEBY_PLAYBOOK_RUN_KEY);
    const map = raw ? JSON.parse(raw) : {};
    if (completed) map[patternId] = true;
    else delete map[patternId];
    sessionStorage.setItem(MIDDLEBY_PLAYBOOK_RUN_KEY, JSON.stringify(map));
  } catch {
    /* ignore */
  }
}

/** Read the real delivery timestamp for a playbook, or null if none. */
export function readDeliveredAt(patternId) {
  if (!patternId) return null;
  try {
    const raw = sessionStorage.getItem(MIDDLEBY_PLAYBOOK_DELIVERED_AT_KEY);
    const map = raw ? JSON.parse(raw) : {};
    const iso = map[patternId];
    return iso ? new Date(iso) : null;
  } catch {
    return null;
  }
}

/** Write the real delivery timestamp for a playbook. Pass null to clear. */
export function writeDeliveredAt(patternId, date) {
  if (!patternId) return;
  try {
    const raw = sessionStorage.getItem(MIDDLEBY_PLAYBOOK_DELIVERED_AT_KEY);
    const map = raw ? JSON.parse(raw) : {};
    if (date instanceof Date && !isNaN(date.getTime())) {
      map[patternId] = date.toISOString();
    } else {
      delete map[patternId];
    }
    sessionStorage.setItem(MIDDLEBY_PLAYBOOK_DELIVERED_AT_KEY, JSON.stringify(map));
  } catch {
    /* ignore */
  }
}

/**
 * Reset approval + simulate state + delivery timestamp for a playbook (demo rehearsals).
 */
export function resetPlaybookDemoState(patternId, decisionCardId) {
  if (decisionCardId) {
    try {
      const raw = sessionStorage.getItem(MIDDLEBY_DECISIONS_KEY);
      const cards = raw ? JSON.parse(raw) : getMiddlebyDecisionSeedCards();
      const next = cards.map((c) =>
        c.id === decisionCardId ? { ...c, status: "pending" } : c
      );
      sessionStorage.setItem(MIDDLEBY_DECISIONS_KEY, JSON.stringify(next));
    } catch {
      /* ignore */
    }
  }
  writeSimulatedRun(patternId, false);
  writeDeliveredAt(patternId, null);
}
