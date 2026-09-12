import { MIDDLEBY_USE_CASE_CATALOG_SEED } from "./middleby-demo-seed";

const HIGH_CONFIDENCE = 0.7;
const MEDIUM_CONFIDENCE = 0.4;

function tokenize(text) {
  return String(text || "")
    .toLowerCase()
    .replace(/[^a-z0-9\s-]/g, " ")
    .split(/\s+/)
    .filter((t) => t.length > 2);
}

function scoreEntry(entry, tokens, lower) {
  let score = 0;
  const title = String(entry.title || "").toLowerCase();
  const problem = String(entry.problemSignal || "").toLowerCase();
  const sample = String(entry.sampleQuestion || "").toLowerCase();
  const id = entry.id;

  if (lower.includes(title) && title.length > 8) score += 0.45;
  if (problem.length > 20 && lower.includes(problem.slice(0, 50))) score += 0.35;
  if (sample.length > 20 && lower.includes(sample.slice(0, 50))) score += 0.25;

  for (const t of tokens) {
    if (title.includes(t)) score += 0.06;
    if (problem.includes(t)) score += 0.05;
    if (sample.includes(t)) score += 0.04;
  }

  if (/dealer|follow.?up|copilot|urgent|worklist|consultation/.test(lower) && id === "middleby-uc-04") score += 0.5;
  if (/potential buyer|high.?intent|intent score|quote candidate/.test(lower) && id === "middleby-uc-01") score += 0.55;
  if (/browse.*quote|gap|ga.*hubspot|traffic vs quote/.test(lower) && id === "middleby-uc-18") score += 0.5;
  if (/repeat.*brand|new.to.brand|loyalty/.test(lower) && id === "middleby-uc-11") score += 0.45;
  if (/timeframe|0-1 month|buyer intent/.test(lower) && id === "middleby-uc-09") score += 0.4;
  if (/dealer region|revenue leakage|rep group/.test(lower) && id === "middleby-uc-06") score += 0.4;
  if (/install|delivery|region|zip/.test(lower) && id === "middleby-uc-12") score += 0.35;
  if (/weekly|anomaly|headline/.test(lower) && id === "middleby-uc-16") score += 0.3;
  if (/unified|silo|ga4.*hubspot/.test(lower) && id === "middleby-uc-23") score += 0.35;

  return Math.min(score, 1);
}

/**
 * NL → use case routing with confidence band (proposal §4.1.1).
 * @returns {{ catalogId: string|null, confidence: number, band: 'high'|'medium'|'low'|'none', suggestions: Array<{ catalogId: string, title: string, confidence: number, sampleQuestion?: string }> }}
 */
export function resolveMiddlebyCatalogWithConfidence(userText) {
  const lower = String(userText || "").trim().toLowerCase();
  if (!lower) {
    return { catalogId: null, confidence: 0, band: "none", suggestions: [] };
  }

  const tokens = tokenize(lower);
  const scored = MIDDLEBY_USE_CASE_CATALOG_SEED.map((entry) => ({
    catalogId: entry.id,
    title: entry.title,
    sampleQuestion: entry.sampleQuestion,
    confidence: scoreEntry(entry, tokens, lower),
  }))
    .filter((r) => r.confidence > 0)
    .sort((a, b) => b.confidence - a.confidence);

  const best = scored[0] || null;
  const confidence = best?.confidence ?? 0;
  let band = "none";
  if (confidence >= HIGH_CONFIDENCE) band = "high";
  else if (confidence >= MEDIUM_CONFIDENCE) band = "medium";
  else if (confidence > 0) band = "low";

  return {
    catalogId: band === "high" || band === "medium" ? best.catalogId : null,
    confidence,
    band,
    suggestions: scored.slice(0, 3),
  };
}

export function resolveMiddlebyCatalogIdFromText(userText) {
  const routed = resolveMiddlebyCatalogWithConfidence(userText);
  if (routed.catalogId) return routed.catalogId;
  return routed.suggestions[0]?.catalogId ?? null;
}

export { HIGH_CONFIDENCE, MEDIUM_CONFIDENCE };
