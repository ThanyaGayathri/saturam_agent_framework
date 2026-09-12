/** Client-side KPI placement helpers (demo registry). */

export function parseProcessMapping(input) {
  if (Array.isArray(input)) return input.map(String).map((s) => s.trim()).filter(Boolean);
  if (input == null || String(input).trim() === "") return [];
  return String(input)
    .split(/[,;]+|\s+/)
    .map((s) => s.trim())
    .filter(Boolean);
}

export function formatProcessMapping(arr) {
  if (!arr?.length) return "";
  return arr.join(", ");
}

function normalizePid(raw) {
  const s = String(raw || "").trim().toUpperCase();
  const m = /^P(\d+)$/.exec(s);
  return m ? `P${m[1]}` : s;
}

export function parseProcessMappingNormalized(input) {
  return parseProcessMapping(input).map(normalizePid).filter((s) => /^P\d+$/.test(s));
}

/** Normalize process id list from UI (checkboxes); always returns internal P ids only. */
export function normalizeProcessIdList(ids) {
  if (!Array.isArray(ids)) return [];
  const out = [];
  const seen = new Set();
  for (const raw of ids) {
    const id = normalizePid(raw);
    if (!/^P\d+$/.test(id) || seen.has(id)) continue;
    seen.add(id);
    out.push(id);
  }
  return out;
}

/**
 * Ensure a section exists with `name === subjectArea` inside categories[categoryIndex].
 * Returns { section, sectionIndex }.
 */
export function ensureSection(categories, categoryIndex, subjectArea, businessAreaId) {
  const cat = categories[categoryIndex];
  if (!cat) throw new Error("Invalid category");
  const idx = cat.sections.findIndex((s) => s.name === subjectArea);
  if (idx >= 0) {
    const sec = cat.sections[idx];
    if (businessAreaId) sec.businessAreaId = businessAreaId;
    return { section: sec, sectionIndex: idx };
  }
  const section = {
    name: subjectArea,
    businessAreaId: businessAreaId || "",
    count: 0,
    kpis: [],
  };
  cat.sections.push(section);
  return { section, sectionIndex: cat.sections.length - 1 };
}

export function removeKpiAt(categories, categoryIndex, sectionIndex, kpiIndex) {
  const cat = categories[categoryIndex];
  if (!cat) return null;
  const sec = cat.sections[sectionIndex];
  if (!sec?.kpis) return null;
  const [removed] = sec.kpis.splice(kpiIndex, 1);
  return removed ?? null;
}

export function pruneEmptySections(categories) {
  for (const cat of categories) {
    cat.sections = (cat.sections || []).filter((s) => (s.kpis || []).length > 0);
  }
}
