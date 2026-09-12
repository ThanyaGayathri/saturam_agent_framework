/**
 * Small helpers that connect use cases to processes and business areas.
 * Used by the Processes page and the Hierarchy map so both views count and
 * group use cases identically.
 */

/** Split a use case's `process` field (e.g. "P1" or "P2, P5") into an array of ids. */
export function parseUseCaseProcessIds(useCase) {
  const raw = useCase?.process;
  if (!raw) return [];
  return String(raw)
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

/** Return the use cases whose `process` field references the given process id. */
export function useCasesForProcessId(useCases, processId) {
  if (!processId || !Array.isArray(useCases)) return [];
  const needle = String(processId).trim();
  if (!needle) return [];
  return useCases.filter((uc) => parseUseCaseProcessIds(uc).includes(needle));
}

/** Return the use cases whose `businessArea` matches the area label or id (case-insensitive). */
export function useCasesForBusinessArea(useCases, area) {
  if (!area || !Array.isArray(useCases)) return [];
  const label = String(area.label || "").toLowerCase();
  const id = String(area.id || "").toLowerCase();
  return useCases.filter((uc) => {
    const ba = String(uc?.businessArea || "").toLowerCase();
    return ba !== "" && (ba === label || ba === id);
  });
}
