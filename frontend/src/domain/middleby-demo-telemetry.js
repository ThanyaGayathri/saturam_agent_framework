const TELEMETRY_KEY = "retailgpt:middleby:telemetry:v1";
const MAX_EVENTS = 200;

function readEvents() {
  try {
    const raw = sessionStorage.getItem(TELEMETRY_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function writeEvents(events) {
  try {
    sessionStorage.setItem(TELEMETRY_KEY, JSON.stringify(events.slice(-MAX_EVENTS)));
  } catch {
    /* ignore quota */
  }
}

/**
 * Client-side demo telemetry (proposal §10). Stored in sessionStorage for demo review.
 */
export function logMiddlebyTelemetry(event) {
  if (!event?.type) return;
  const row = {
    ts: new Date().toISOString(),
    ...event,
  };
  const events = readEvents();
  events.push(row);
  writeEvents(events);
  if (import.meta.env.DEV) {
    console.debug("[Middleby telemetry]", row);
  }
}

export function getMiddlebyTelemetryEvents() {
  return readEvents();
}

export function clearMiddlebyTelemetry() {
  try {
    sessionStorage.removeItem(TELEMETRY_KEY);
  } catch {
    /* ignore */
  }
}
