import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { useDemoProfile } from "./DemoProfileContext";
import { storageKeysForProfile } from "../storage/keys";
import {
  loadSourcePrefs,
  saveSourcePrefs,
  databaseIsOn,
  databaseIsUsable,
  connectorIsOn,
} from "../storage/source-prefs";

/**
 * Which databases and connectors answers may read, shared by every composer.
 *
 * The Database and Connectors menus exist in three places (Home, chat, the Playground pages).
 * Each kept its own `useState`, so a switch flipped in one was invisible to the others and
 * reset on navigation. One provider means the choice is the same everywhere and survives a
 * refresh, which is also what lets anything downstream ask "is this source on?".
 */
const SourcePrefsContext = createContext(null);

export function SourcePrefsProvider({ children }) {
  const { profileId } = useDemoProfile();
  const storageKey = useMemo(() => storageKeysForProfile(profileId).SOURCE_PREFS, [profileId]);
  const [prefs, setPrefs] = useState(() => loadSourcePrefs(storageKey));

  // Each demo profile has its own workspace, so its own choices travel with it.
  useEffect(() => {
    setPrefs(loadSourcePrefs(storageKey));
  }, [storageKey]);

  const persist = useCallback(
    (next) => {
      setPrefs(next);
      saveSourcePrefs(next, storageKey);
    },
    [storageKey]
  );

  /**
   * Turning a database off drops it as the pick too: a source that is not being read cannot
   * be the one answers are drawn from.
   */
  const setDatabaseOn = useCallback(
    (source, on) => {
      if (!databaseIsUsable(source)) return;
      persist({
        ...prefs,
        databases: { ...prefs.databases, [source.id]: Boolean(on) },
        selectedDatabaseId:
          !on && prefs.selectedDatabaseId === source.id ? null : prefs.selectedDatabaseId,
      });
    },
    [persist, prefs]
  );

  /** Picking a database implies switching it on; picking the current one clears the pick. */
  const toggleDatabaseSelection = useCallback(
    (source) => {
      if (!databaseIsUsable(source)) return;
      const alreadyPicked = prefs.selectedDatabaseId === source.id;
      persist({
        ...prefs,
        databases: alreadyPicked ? prefs.databases : { ...prefs.databases, [source.id]: true },
        selectedDatabaseId: alreadyPicked ? null : source.id,
      });
    },
    [persist, prefs]
  );

  const setConnectorOn = useCallback(
    (connector, on) => {
      persist({ ...prefs, connectors: { ...prefs.connectors, [connector.id]: Boolean(on) } });
    },
    [persist, prefs]
  );

  const value = useMemo(
    () => ({
      prefs,
      isDatabaseOn: (source) => databaseIsOn(prefs, source),
      isConnectorOn: (connector) => connectorIsOn(prefs, connector),
      selectedDatabaseId: prefs.selectedDatabaseId,
      setDatabaseOn,
      toggleDatabaseSelection,
      setConnectorOn,
      /** The sources a live answer, KPI or chart is allowed to read. */
      activeDatabases: (sources) => (sources || []).filter((s) => databaseIsOn(prefs, s)),
    }),
    [prefs, setDatabaseOn, toggleDatabaseSelection, setConnectorOn]
  );

  return <SourcePrefsContext.Provider value={value}>{children}</SourcePrefsContext.Provider>;
}

export function useSourcePrefs() {
  const ctx = useContext(SourcePrefsContext);
  if (!ctx) throw new Error("useSourcePrefs must be used within SourcePrefsProvider");
  return ctx;
}
