/** Payload written to sessionStorage on Launch (Step 10) for `/use-cases/launch-summary`. */
export const USE_CASE_LAUNCH_SUMMARY_VERSION = 1;

/**
 * Build a demo “execution result” for the launch summary screen from the draft + new catalog row.
 */
export function buildLaunchSummaryPayload(draft, newEntry) {
  const planItemCount = draft.step3?.planItems?.length ?? 0;
  const enabledSources = (draft.step6?.sources || []).filter((s) => s.enabled).length;
  const kpiCount = (draft.step8?.selectedKpiIds || []).length;
  const agentType = draft.step5?.agentType?.trim() || "";
  const chartType = draft.step7?.selectedChart || "table";

  const achievements = [];
  achievements.push(`Executed the ${planItemCount}-stage AI plan end to end.`);
  achievements.push(
    `Ran knowledge retrieval across ${enabledSources} connected data source${enabledSources === 1 ? "" : "s"}.`
  );
  achievements.push(`Attached ${kpiCount} KPI${kpiCount === 1 ? "" : "s"} for monitoring and alerting.`);
  const pq = draft.step2?.primaryQuestion?.trim();
  if (pq) {
    achievements.push(
      `Primary objective: ${pq.length > 140 ? `${pq.slice(0, 137)}…` : pq}`
    );
  }
  const orch =
    agentType === ""
      ? "Sequential agent (default)"
      : `Agent mode: ${agentType}`;
  achievements.push(`${orch} · Output surface: ${chartType}.`);

  return {
    version: USE_CASE_LAUNCH_SUMMARY_VERSION,
    completedAt: new Date().toISOString(),
    catalogEntry: {
      id: newEntry.id,
      title: newEntry.title,
      businessArea: newEntry.businessArea,
      tags: newEntry.tags,
    },
    achievements,
    execution: {
      planItemCount,
      enabledSources,
      kpiCount,
      agentType: agentType || null,
      chartType,
    },
  };
}

/** Same shape as {@link buildLaunchSummaryPayload}; used when opening an existing catalog row as “already executed”. */
export function buildLaunchSummaryPayloadFromCatalogRow(row) {
  if (!row || typeof row !== "object") return null;
  const id = row.id != null ? String(row.id).trim() : "";
  const title = row.title != null ? String(row.title).trim() : "";
  if (!id || !title) return null;

  const tags = Array.isArray(row.tags) ? row.tags.map((t) => String(t)) : [];
  const businessArea =
    row.businessArea != null && String(row.businessArea).trim() !== ""
      ? String(row.businessArea).trim()
      : undefined;

  const achievements = [];
  const problem = row.problemSignal != null ? String(row.problemSignal).trim() : "";
  if (problem) {
    achievements.push(problem.length > 180 ? `${problem.slice(0, 177)}…` : problem);
  }
  const items = Array.isArray(row.items) ? row.items.map((x) => String(x || "").trim()).filter(Boolean) : [];
  if (!problem && items[0]) {
    const line = items[0];
    achievements.push(line.length > 180 ? `${line.slice(0, 177)}…` : line);
  }
  const actionType = row.actionType != null ? String(row.actionType).trim() : "";
  if (actionType) achievements.push(`Action type: ${actionType}.`);
  const proc = row.process != null ? String(row.process).trim() : "";
  if (proc) achievements.push(`Aligned to process: ${proc}.`);
  achievements.push("Executed the multi-stage AI plan end to end.");
  achievements.push("Knowledge retrieval ran against connected internal sources.");
  achievements.push("KPIs attached for monitoring and alerting.");

  return {
    version: USE_CASE_LAUNCH_SUMMARY_VERSION,
    completedAt: new Date().toISOString(),
    catalogEntry: {
      id,
      title,
      businessArea,
      tags,
    },
    achievements,
    execution: {
      planItemCount: 5,
      enabledSources: 2,
      kpiCount: 2,
      agentType: null,
      chartType: "table",
    },
  };
}

export function parseLaunchSummaryPayload(raw) {
  try {
    const o = typeof raw === "string" ? JSON.parse(raw) : raw;
    if (!o || typeof o !== "object") return null;
    if (o.version !== USE_CASE_LAUNCH_SUMMARY_VERSION) return null;
    if (!o.catalogEntry?.id || !o.catalogEntry?.title) return null;
    return o;
  } catch {
    return null;
  }
}
