import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useDemoProfile } from "../context/DemoProfileContext";
import { readActiveDemoProfileId } from "../storage/demo-profile-preference";
import { storageKeysForProfile } from "../storage/keys";
import {
  acknowledgeAlert,
  loadAlerts,
  markAllAlertsRead,
  reopenAlert,
  resolveAlert,
  saveAlerts,
  snoozeAlert,
} from "../storage/alerts";

const TICK_INTERVAL_MS = 30 * 1000;

/**
 * React hook over the alerts store. Scoped to the active demo profile.
 * Exposes the alert list, an unread count (status === "new"), and mutators
 * for the bell dropdown and /alerts page. Auto-ticks every 30s so relative
 * "X min ago" labels stay fresh without touching storage.
 */
export function useAlerts() {
  const { profileId, storageKeys } = useDemoProfile();
  const skipNextSave = useRef(false);

  const [data, setData] = useState(() =>
    loadAlerts(
      storageKeysForProfile(readActiveDemoProfileId()).ALERTS,
      Date.now(),
      readActiveDemoProfileId()
    )
  );
  const [hydrated, setHydrated] = useState(false);
  // eslint-disable-next-line no-unused-vars
  const [, forceTick] = useState(0);

  useEffect(() => {
    skipNextSave.current = true;
    setData(loadAlerts(storageKeys.ALERTS, Date.now(), profileId));
    setHydrated(true);
  }, [profileId, storageKeys.ALERTS]);

  useEffect(() => {
    if (!hydrated) return;
    if (skipNextSave.current) {
      skipNextSave.current = false;
      return;
    }
    saveAlerts(data, storageKeys.ALERTS);
  }, [data, hydrated, storageKeys.ALERTS]);

  useEffect(() => {
    const id = window.setInterval(() => forceTick((n) => (n + 1) % 1_000_000), TICK_INTERVAL_MS);
    return () => window.clearInterval(id);
  }, []);

  const alerts = useMemo(() => data.alerts || [], [data]);

  const unreadCount = useMemo(
    () => alerts.filter((a) => a.status === "new").length,
    [alerts]
  );

  const criticalUnreadCount = useMemo(
    () => alerts.filter((a) => a.status === "new" && a.severity === "critical").length,
    [alerts]
  );

  const acknowledge = useCallback((id) => {
    setData((prev) => ({ ...prev, alerts: acknowledgeAlert(prev.alerts || [], id) }));
  }, []);

  const snooze = useCallback((id, untilMs) => {
    setData((prev) => ({ ...prev, alerts: snoozeAlert(prev.alerts || [], id, untilMs) }));
  }, []);

  const resolve = useCallback((id) => {
    setData((prev) => ({ ...prev, alerts: resolveAlert(prev.alerts || [], id) }));
  }, []);

  const reopen = useCallback((id) => {
    setData((prev) => ({ ...prev, alerts: reopenAlert(prev.alerts || [], id) }));
  }, []);

  const markAllRead = useCallback(() => {
    setData((prev) => ({ ...prev, alerts: markAllAlertsRead(prev.alerts || []) }));
  }, []);

  return {
    alerts,
    hydrated,
    unreadCount,
    criticalUnreadCount,
    acknowledge,
    snooze,
    resolve,
    reopen,
    markAllRead,
  };
}
