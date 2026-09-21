import { readLocalJson, writeLocalJson } from "./local-json";

const VERSION = 1;

/**
 * Which sources answers are allowed to read, and which one the user has picked.
 *
 * Three separate copies of this lived as component `useState` before, so a switch flipped on
 * the Home composer meant nothing in chat and was gone on the next render. It is one persisted
 * record now, keyed by source id.
 *
 * `databases` and `connectors` hold only the EXPLICIT choices. An id that is absent falls back
 * to its default (on for a database, whatever the connector list declares), so adding a source
 * in Configure does not arrive switched off.
 */
export function loadSourcePrefs(storageKey) {
  const parsed = readLocalJson(storageKey, null);
  if (!parsed || typeof parsed !== "object") {
    return { version: VERSION, databases: {}, connectors: {}, selectedDatabaseId: null };
  }
  return {
    version: parsed.version ?? VERSION,
    databases: parsed.databases && typeof parsed.databases === "object" ? parsed.databases : {},
    connectors: parsed.connectors && typeof parsed.connectors === "object" ? parsed.connectors : {},
    selectedDatabaseId:
      typeof parsed.selectedDatabaseId === "string" ? parsed.selectedDatabaseId : null,
  };
}

export function saveSourcePrefs(prefs, storageKey) {
  writeLocalJson(storageKey, {
    version: prefs?.version ?? VERSION,
    databases: prefs?.databases ?? {},
    connectors: prefs?.connectors ?? {},
    selectedDatabaseId: prefs?.selectedDatabaseId ?? null,
  });
}

/** A connection that failed its test can never be on, whatever the stored preference says. */
export function databaseIsUsable(source) {
  return source?.connectionStatus !== "failed";
}

/**
 * Is this database switched on? Unknown ids default to on, failed ones always off, so the
 * answer never depends on whether the user has opened the menu yet.
 */
export function databaseIsOn(prefs, source) {
  if (!databaseIsUsable(source)) return false;
  const explicit = prefs?.databases?.[source?.id];
  return explicit === undefined ? true : Boolean(explicit);
}

export function connectorIsOn(prefs, connector) {
  const explicit = prefs?.connectors?.[connector?.id];
  return explicit === undefined ? Boolean(connector?.defaultOn) : Boolean(explicit);
}
