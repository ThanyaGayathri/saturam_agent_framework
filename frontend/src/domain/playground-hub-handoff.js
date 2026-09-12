/** One-shot Playground hub → active chat payload (JSON in sessionStorage). */

/** Strip ZW* / BOM / odd spaces so chip labels still map to week | quarter after copy/paste or Figma export. */
export function normalizeExploreChipLabel(s) {
  if (s == null) return "";
  return String(s)
    .replace(/[\u200B-\u200D\uFEFF]/g, "")
    .replace(/\u00A0/g, " ")
    .replace(/\u202F/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

/** Map hub chip / stored lens to canonical horizon (week | quarter). */
export function lensDisplayToHorizon(lens) {
  const t = normalizeExploreChipLabel(lens).toLowerCase();
  if (t === "this week" || t === "week") return "week";
  if (t === "this quarter" || t === "quarter") return "quarter";
  return undefined;
}

export function serializePlaygroundHubHandoff({ text, lens, horizon, catalogId, businessArea, subjectArea }) {
  /** Chip text (`lens`) is what the user clicked; it wins over `horizon` if they ever disagree. */
  const fromLens = lensDisplayToHorizon(lens);
  const fromParam = horizon === "week" || horizon === "quarter" ? horizon : undefined;
  const h = fromLens ?? fromParam;
  const lensOut = lens != null ? normalizeExploreChipLabel(lens) : "";
  const ba = typeof businessArea === "string" ? businessArea.trim() : "";
  const sa = typeof subjectArea === "string" ? subjectArea.trim() : "";
  return JSON.stringify({
    v: 1,
    text: String(text || "").trim(),
    ...(lensOut ? { lens: lensOut } : {}),
    ...(h ? { horizon: h } : {}),
    ...(catalogId != null && String(catalogId).trim() ? { catalogId: String(catalogId).trim() } : {}),
    ...(ba ? { businessArea: ba } : {}),
    ...(sa ? { subjectArea: sa } : {}),
  });
}

/**
 * @returns {{ text: string, lens?: string, horizon?: string, catalogId?: string, businessArea?: string, subjectArea?: string } | null}
 */
export function parsePlaygroundHubHandoff(raw) {
  const s = String(raw || "").trim();
  if (!s) return null;
  if (s.startsWith("{")) {
    try {
      const o = JSON.parse(s);
      if (o && typeof o.text === "string" && o.text.trim()) {
        const lensNorm = typeof o.lens === "string" ? normalizeExploreChipLabel(o.lens) : "";
        const lens = lensNorm || undefined;
        const fromJson = o.horizon === "week" || o.horizon === "quarter" ? o.horizon : undefined;
        const horizon = lensDisplayToHorizon(lens) ?? fromJson;
        return {
          text: o.text.trim(),
          lens,
          horizon,
          catalogId: typeof o.catalogId === "string" && o.catalogId.trim() ? o.catalogId.trim() : undefined,
          businessArea: typeof o.businessArea === "string" && o.businessArea.trim() ? o.businessArea.trim() : undefined,
          subjectArea: typeof o.subjectArea === "string" && o.subjectArea.trim() ? o.subjectArea.trim() : undefined,
        };
      }
    } catch {
      /* legacy plain string */
    }
  }
  return { text: s };
}
