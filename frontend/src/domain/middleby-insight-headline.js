import { fetchRetailLlmAssistantText, fetchRetailLlmConfigured } from "../services/retail-llm";
import { buildRoleLensAnswer } from "./middleby-role-lens";
import { demoProfileLabel } from "./demo-profiles";
import { MIDDLEBY_PROFILE_ID } from "./demo-profiles";
import { MIDDLEBY_DEMO_WINDOW } from "./middleby-dataset-info";

/**
 * Hybrid headline: deterministic answer first; optional LLM polish when API is configured.
 */
// Prefix the summary with the selected period so the answer always states which
// timeline it covers (skipped for the base demo week, which is already concrete).
function withPeriod(answer, timelineLabel) {
  if (!answer || !timelineLabel || timelineLabel === MIDDLEBY_DEMO_WINDOW) return answer;
  const summary = String(answer.summary || "").trim();
  if (!summary || summary.includes(timelineLabel)) return answer;
  return { ...answer, summary: `${timelineLabel}: ${summary}` };
}

export async function resolveMiddlebyInsightHeadline(entry, spec, rows, role, catalogId, timelineLabel) {
  const deterministic = buildRoleLensAnswer(entry, spec, rows, role, catalogId);

  const configured = await fetchRetailLlmConfigured();
  if (!configured) return withPeriod(deterministic, timelineLabel);

  const timeline = timelineLabel || rows?.[0]?.demo_window || MIDDLEBY_DEMO_WINDOW;

  const kpis = (deterministic.items || [])
    .slice(0, 4)
    .map((i) => `${i.label}: ${i.value}`)
    .join("; ");

  try {
    const prompt = [
      "Rewrite this analytics headline in one or two plain sentences for a business audience.",
      `Use case: ${entry?.title || catalogId}.`,
      `Time period: ${timeline}.`,
      `Deterministic summary: ${deterministic.summary}`,
      kpis ? `Evidence rows: ${kpis}.` : "",
      "Keep all numbers exactly as given. Do not say 'this week'; if you mention the period, use the time period above. No markdown.",
    ]
      .filter(Boolean)
      .join(" ");

    const llmText = await fetchRetailLlmAssistantText({
      userQuestion: prompt,
      customerName: demoProfileLabel(MIDDLEBY_PROFILE_ID),
      timeline,
      catalogId,
      profileContext: null,
    });

    if (llmText?.trim()) {
      return { ...deterministic, summary: llmText.trim(), headlineSource: "hybrid" };
    }
  } catch {
    /* fall through */
  }

  return withPeriod({ ...deterministic, headlineSource: "deterministic" }, timelineLabel);
}
