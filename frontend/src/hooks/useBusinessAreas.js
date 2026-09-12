import { useCallback, useEffect, useMemo, useState } from "react";
import { useDemoProfile } from "../context/DemoProfileContext";
import { useKpiRegistry } from "./useKpiRegistry";
import { loadBusinessAreas, saveBusinessAreas } from "../storage/business-areas";

function slugify(text) {
  return String(text || "")
    .toLowerCase()
    .trim()
    .replace(/&/g, "and")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

/**
 * React hook wrapping the persisted business hierarchy.
 * Returns the areas plus mutation helpers and impact-preview helpers
 * (used by the Configure > Business hierarchy tab to show what will
 * break before a rename/delete).
 */
export function useBusinessAreas() {
  const { profileId, storageKeys } = useDemoProfile();
  const [data, setData] = useState({ version: 1, areas: [] });
  const [hydrated, setHydrated] = useState(false);

  // KPI registry, used to compute delete-impact previews.
  const { data: registry } = useKpiRegistry();

  useEffect(() => {
    setData(loadBusinessAreas(storageKeys.BUSINESS_AREAS, profileId));
    setHydrated(true);
  }, [storageKeys.BUSINESS_AREAS, profileId]);

  useEffect(() => {
    if (!hydrated) return;
    saveBusinessAreas(data, storageKeys.BUSINESS_AREAS);
  }, [data, hydrated, storageKeys.BUSINESS_AREAS]);

  const areas = data.areas || [];

  /** Count KPIs currently tagged with a given business area id. */
  const kpiCountByAreaId = useMemo(() => {
    const counts = {};
    const categories = registry?.categories || [];
    for (const cat of categories) {
      for (const sec of cat.sections || []) {
        for (const kpi of sec.kpis || []) {
          const areaId = kpi.businessAreaId || sec.businessAreaId || "";
          if (!areaId) continue;
          counts[areaId] = (counts[areaId] || 0) + 1;
        }
      }
    }
    return counts;
  }, [registry?.categories]);

  /** Count KPIs currently tagged with a given (areaId, subject area name) pair. */
  const kpiCountBySubjectArea = useMemo(() => {
    const counts = {};
    const categories = registry?.categories || [];
    for (const cat of categories) {
      for (const sec of cat.sections || []) {
        for (const kpi of sec.kpis || []) {
          const areaId = kpi.businessAreaId || sec.businessAreaId || "";
          const subject = kpi.subjectArea || sec.name || "";
          if (!areaId || !subject) continue;
          const key = `${areaId}::${subject}`;
          counts[key] = (counts[key] || 0) + 1;
        }
      }
    }
    return counts;
  }, [registry?.categories]);

  const addArea = useCallback((patch) => {
    const label = String(patch?.label || "").trim();
    if (!label) return null;
    let newId = null;
    setData((prev) => {
      const existingIds = new Set((prev.areas || []).map((a) => a.id));
      let baseId = slugify(label);
      if (!baseId) baseId = `area-${Date.now().toString(36)}`;
      let id = baseId;
      let n = 2;
      while (existingIds.has(id)) {
        id = `${baseId}-${n}`;
        n += 1;
      }
      newId = id;
      const next = {
        ...prev,
        areas: [
          ...(prev.areas || []),
          {
            id,
            label,
            subjectAreas: [],
            ...(patch?.description ? { description: patch.description.trim() } : {}),
          },
        ],
      };
      return next;
    });
    return newId;
  }, []);

  const renameArea = useCallback((id, nextLabel, nextDescription) => {
    const label = String(nextLabel || "").trim();
    if (!label) return false;
    setData((prev) => ({
      ...prev,
      areas: (prev.areas || []).map((a) =>
        a.id === id
          ? {
              ...a,
              label,
              ...(nextDescription !== undefined
                ? nextDescription
                  ? { description: String(nextDescription).trim() }
                  : {}
                : {}),
            }
          : a
      ),
    }));
    return true;
  }, []);

  const removeArea = useCallback((id) => {
    setData((prev) => ({
      ...prev,
      areas: (prev.areas || []).filter((a) => a.id !== id),
    }));
  }, []);

  const addSubjectArea = useCallback((areaId, subjectName) => {
    const name = String(subjectName || "").trim();
    if (!name) return false;
    setData((prev) => ({
      ...prev,
      areas: (prev.areas || []).map((a) => {
        if (a.id !== areaId) return a;
        if ((a.subjectAreas || []).includes(name)) return a;
        return { ...a, subjectAreas: [...(a.subjectAreas || []), name] };
      }),
    }));
    return true;
  }, []);

  const renameSubjectArea = useCallback((areaId, oldName, newName) => {
    const cleaned = String(newName || "").trim();
    if (!cleaned) return false;
    setData((prev) => ({
      ...prev,
      areas: (prev.areas || []).map((a) => {
        if (a.id !== areaId) return a;
        return {
          ...a,
          subjectAreas: (a.subjectAreas || []).map((s) => (s === oldName ? cleaned : s)),
        };
      }),
    }));
    return true;
  }, []);

  const removeSubjectArea = useCallback((areaId, subjectName) => {
    setData((prev) => ({
      ...prev,
      areas: (prev.areas || []).map((a) => {
        if (a.id !== areaId) return a;
        return {
          ...a,
          subjectAreas: (a.subjectAreas || []).filter((s) => s !== subjectName),
        };
      }),
    }));
  }, []);

  return {
    areas,
    hydrated,
    addArea,
    renameArea,
    removeArea,
    addSubjectArea,
    renameSubjectArea,
    removeSubjectArea,
    kpiCountByAreaId,
    kpiCountBySubjectArea,
  };
}
