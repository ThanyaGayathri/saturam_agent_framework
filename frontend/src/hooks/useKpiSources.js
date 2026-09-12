import { useCallback, useEffect, useState } from "react";
import { useDemoProfile } from "../context/DemoProfileContext";
import { loadKpiSources, saveKpiSources } from "../storage/kpi-sources";

export function useKpiSources() {
  const { storageKeys } = useDemoProfile();
  const [data, setData] = useState({ version: 1, sources: [] });
  const [hydrated, setHydrated] = useState(false);

  useEffect(() => {
    setData(loadKpiSources(storageKeys.KPI_SOURCES));
    setHydrated(true);
  }, [storageKeys.KPI_SOURCES]);

  useEffect(() => {
    if (!hydrated) return;
    saveKpiSources(data, storageKeys.KPI_SOURCES);
  }, [data, hydrated, storageKeys.KPI_SOURCES]);

  const addSource = useCallback((src) => {
    setData((prev) => ({
      ...prev,
      sources: [src, ...(prev.sources || [])],
    }));
  }, []);

  const removeSource = useCallback((id) => {
    setData((prev) => ({
      ...prev,
      sources: (prev.sources || []).filter((s) => s.id !== id),
    }));
  }, []);

  return { sources: data.sources || [], hydrated, addSource, removeSource };
}
