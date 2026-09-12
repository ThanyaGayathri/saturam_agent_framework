import { MIDDLEBY_PROFILE_ID, normalizeDemoRoleId } from "./demo-profiles";
import { MIDDLEBY_USE_CASE_CATALOG_SEED } from "./middleby-demo-seed";
import { getMiddlebyPrimarySpec } from "./middleby-screen-data";
import { loadMiddlebyDataset, scaleMiddlebyRowsForPeriod } from "./middleby-analytics-data";
import { resolveMiddlebyCatalogWithConfidence } from "./middleby-nl-router";
import { resolveMiddlebyInsightFollowUpLocal } from "./middleby-insight-follow-up-resolver";
import { formatMiddlebyChatSourceFooter } from "./middleby-data-honesty";
import { resolveMiddlebyPeriodSpan } from "./middleby-dataset-info";

function appendSourceFooter(text, catalogId, spec) {
  const body = String(text || "").trim();
  if (!body) return body;
  if (/\b(based on|source:)\b/i.test(body)) return body;
  return `${body}${formatMiddlebyChatSourceFooter(catalogId, spec)}`;
}

/**
 * Pre-LLM data grounding for Middleby playground chat when the question routes to a known UC.
 * @returns {Promise<{ text: string, meta: object } | null>}
 */
export async function tryMiddlebyPlaygroundDataGrounding(userText, profileId, exploreHub, userMeta) {
  if (profileId !== MIDDLEBY_PROFILE_ID) return null;

  const question = String(userText || "").trim();
  if (!question) return null;

  let catalogId = userMeta?.catalogId ?? exploreHub?.catalogId ?? null;
  if (!catalogId) {
    const routed = resolveMiddlebyCatalogWithConfidence(question);
    if (routed.band !== "high" && routed.band !== "medium") return null;
    catalogId = routed.catalogId;
  }

  if (!catalogId || !String(catalogId).startsWith("middleby-uc-")) return null;

  const entry = MIDDLEBY_USE_CASE_CATALOG_SEED.find((e) => e.id === catalogId);
  const spec = getMiddlebyPrimarySpec(catalogId);
  if (!spec?.filePath || spec.dataMode === "blocked") return null;

  const dataset = await loadMiddlebyDataset(spec.filePath);
  if (dataset.blocked || !dataset.rows?.length) return null;

  // Scale rows and derive the active window label from the period filter the
  // user chose in the UI (falls back to the fixture's weekly window).
  const periodSpan = resolveMiddlebyPeriodSpan(
    userMeta?.period,
    userMeta?.fromDate,
    userMeta?.toDate
  );
  const scaledRows = scaleMiddlebyRowsForPeriod(dataset.rows, spec, periodSpan.factor);
  const windowLabel = periodSpan.label || null;

  const role = normalizeDemoRoleId(userMeta?.role);
  const local = resolveMiddlebyInsightFollowUpLocal(
    question,
    catalogId,
    spec,
    scaledRows,
    role,
    { windowLabel }
  );

  if (!local.handled || !local.text?.trim()) return null;

  const text = appendSourceFooter(local.text, catalogId, spec);

  return {
    text,
    meta: {
      source: "middleby-data",
      dataGrounded: true,
      catalogId,
      useCaseTitle: entry?.title,
      ...(local.clipboard ? { clipboard: local.clipboard } : {}),
    },
  };
}
