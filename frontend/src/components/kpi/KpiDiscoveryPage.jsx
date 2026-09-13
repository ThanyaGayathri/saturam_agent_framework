import { useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useKpiRegistry } from "../../hooks/useKpiRegistry";
import { useProcesses } from "../../hooks/useProcesses";
import { useBusinessAreas } from "../../hooks/useBusinessAreas";
import { useDemoProfile } from "../../context/DemoProfileContext";
import { datasetIdsByLinkedKpiName, flattenKpiIndexRows } from "../../domain/kpi-mapping";
import { readLocalJson, writeLocalJson } from "../../storage/local-json";
import { Sparkles } from "lucide-react";
import KpiDiscovery from "./KpiDiscovery";
import KpiDetailPanel from "./KpiDetailPanel";
import CreateDashboardModal from "./CreateDashboardModal";
import { createDashboard } from "../../storage/dashboards";
import { SUGGESTED_KPI_CANDIDATES, SUGGESTED_KPI_SUGGESTIONS } from "./suggested-candidates";
import { metricsUnlockedBy } from "../configure/source-model-state";

/**
 * "What my data supports" -- the discovery surface, and the default tab of the KPIs hub.
 *
 * It lives here rather than under Configure because it is a *consumption* activity: what can I
 * measure, which of those matter, build a dashboard from them. Configure sets things up; KPIs
 * uses them. Configure > KPIs stays pure master data (add / edit / enable / map).
 *
 * Two inputs, deliberately kept apart:
 *
 *  1. ENABLED registry KPIs -- master data the customer governs. Disabled ones are excluded here,
 *     which is what makes the enable switch in Configure meaningful rather than decorative.
 *  2. SUGGESTED candidates -- metrics not in the registry yet. They are shown as adoptable
 *     candidates rather than as results, because a metric that cannot be edited, governed or
 *     reproduced is not something anyone should build a dashboard on.
 */

const FOCUS_KEY = "retailgpt:kpi-focus-areas";

/**
 * A KPI counts as enabled unless it has been explicitly switched off. Default-on is the right
 * polarity: default-off leaves a new workspace with an empty discovery list on the one screen
 * that is supposed to show what it can do, so the switch reads as suppression -- "we do not
 * track that here" -- rather than as opt-in.
 */
function isEnabled(kpi) {
 return kpi?.active !== false;
}

export default function KpiDiscoveryPage() {
 const navigate = useNavigate();
 const [params, setParams] = useSearchParams();
 // Arriving from "N metrics unlocked" on a source card. Scopes the page to what that source made
 // measurable, so the claim on the card and the list here are the same set.
 const unlockedBy = params.get("unlocked");
 const unlockedNames = useMemo(
  () => (unlockedBy ? new Set(metricsUnlockedBy(unlockedBy)) : null),
  [unlockedBy]
 );
 const { data, hydrated } = useKpiRegistry();
 const { processes } = useProcesses();
 const { profileId } = useDemoProfile();
 const { areas: businessAreas } = useBusinessAreas();

 const [detailRow, setDetailRow] = useState(null);
 // Rows handed to the create modal. Non-empty means the modal is open.
 const [pendingRows, setPendingRows] = useState(null);
 const [focusAreaIds, setFocusState] = useState(() => {
  const saved = readLocalJson(FOCUS_KEY, []);
  return Array.isArray(saved) ? saved : [];
 });
 const setFocusAreaIds = (ids) => { setFocusState(ids); writeLocalJson(FOCUS_KEY, ids); };

 // Candidates the customer has adopted this session. Adoption writes to the registry for real in
 // production; here it moves the card out of "suggested" so the flow reads correctly.
 const [adopted, setAdopted] = useState(() => new Set());

 const nameToIdsMap = useMemo(() => datasetIdsByLinkedKpiName(), []);
 const processLabelById = useMemo(
  () => Object.fromEntries((processes || []).map((p) => [p.id, p.name])),
  [processes]
 );

 const rows = useMemo(() => {
  const registry = flattenKpiIndexRows(data?.categories || []).filter((r) => isEnabled(r.kpi));
  // Candidates carry `suggested` so the card can badge them and offer adoption. They are appended
  // rather than merged into the registry: until adopted they are not master data.
  const candidates = SUGGESTED_KPI_CANDIDATES.filter((c) => !adopted.has(c.kpi.name));
  const all = [...registry, ...candidates];
  return unlockedNames ? all.filter((r) => unlockedNames.has(r.kpi?.name)) : all;
 }, [data?.categories, adopted, unlockedNames]);

 const disabledCount = useMemo(
  () => flattenKpiIndexRows(data?.categories || []).filter((r) => !isEnabled(r.kpi)).length,
  [data?.categories]
 );

 const hasSuggestions = SUGGESTED_KPI_CANDIDATES.some((c) => !adopted.has(c.kpi.name));
 const suggestionNotes = SUGGESTED_KPI_SUGGESTIONS.notes;

 if (!hydrated) return <p className="text-sm text-text-secondary">Loading…</p>;

 return (
  <div className="flex w-full flex-col gap-5">
   {/* Says what is being shown and how to leave it. A filtered list that does not announce
       itself reads as an empty product. */}
   {unlockedNames && (
    <div className="flex flex-wrap items-center gap-2 rounded-xl border border-primary/30 bg-primary-light/50 px-4 py-2.5 text-sm dark:bg-primary/10">
     <Sparkles size={15} className="text-primary" />
     <span className="text-text-secondary">
      Showing the <strong className="text-text-primary">{rows.length} metrics</strong> unlocked by this source
     </span>
     <button
      type="button"
      onClick={() => { params.delete("unlocked"); setParams(params, { replace: true }); }}
      className="ml-auto text-xs font-semibold text-primary hover:underline"
     >
      Show everything
     </button>
    </div>
   )}

   <KpiDiscovery
    rows={rows}
    areas={businessAreas}
    focusAreaIds={focusAreaIds}
    onFocusChange={setFocusAreaIds}
    onOpenDetail={setDetailRow}
    onAdopt={(row) => setAdopted((prev) => new Set(prev).add(row.kpi.name))}
    onCreateDashboard={setPendingRows}
   />

   {/* The agent's own account of why it proposed nothing, in the schema's refusal channel. An
       empty suggestion list with no reason is indistinguishable from a feature that never ran. */}
   {!hasSuggestions && suggestionNotes && (
    <p className="flex items-start gap-2 rounded-xl border border-dashed border-border-light bg-surface-secondary px-4 py-3 text-xs text-text-secondary">
     <Sparkles size={13} className="mt-0.5 shrink-0 text-status-info" />
     <span>
      <span className="font-semibold text-text-primary">No suggestions this time.</span> {suggestionNotes}
     </span>
    </p>
   )}

   {/* Disabled metrics are absent by design, but silently absent is indistinguishable from
       missing. One line, with a route to the place that controls it. */}
   {disabledCount > 0 && (
    <p className="text-xs text-text-muted">
     {disabledCount} metric{disabledCount === 1 ? " is" : "s are"} switched off and excluded here.{" "}
     <button
      type="button"
      onClick={() => navigate("/configure/domain-mapping")}
      className="font-semibold text-primary hover:underline"
     >
      Manage in Configure
     </button>
    </p>
   )}

   {pendingRows && (
    <CreateDashboardModal
     rows={pendingRows}
     onCancel={() => setPendingRows(null)}
     onCreate={({ name, refresh, delivery }) => {
      const created = createDashboard({ name, rows: pendingRows, refresh, delivery });
      setPendingRows(null);
      navigate(`/kpis/dashboards/${created.id}`);
     }}
    />
   )}

   {detailRow && (
    <KpiDetailPanel
     detailRow={detailRow}
     nameToIdsMap={nameToIdsMap}
     profileId={profileId}
     processLabelById={processLabelById}
     onEdit={() => { setDetailRow(null); navigate("/configure/domain-mapping"); }}
     onClose={() => setDetailRow(null)}
    />
   )}
  </div>
 );
}
