import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useKpiRegistry } from "../../hooks/useKpiRegistry";
import { useProcesses } from "../../hooks/useProcesses";
import { useBusinessAreas } from "../../hooks/useBusinessAreas";
import { useDemoProfile } from "../../context/DemoProfileContext";
import { datasetIdsByLinkedKpiName, flattenKpiIndexRows } from "../../domain/kpi-mapping";
import { readLocalJson, writeLocalJson } from "../../storage/local-json";
import { AlertTriangle, Sparkles } from "lucide-react";
import KpiDiscovery from "./KpiDiscovery";
import KpiDetailPanel from "./KpiDetailPanel";
import CreateDashboardModal from "./CreateDashboardModal";
import { createDashboard } from "../../storage/dashboards";
import { SUGGESTED_KPI_CANDIDATES } from "./suggested-candidates";
import { metricsUnlockedBy } from "../configure/source-model-state";
import { fetchKpiList } from "../../services/kpis-api";
import { fetchBusinessAreas } from "../../services/business-areas-api";

/**
 * "What my data supports" -- the discovery surface, and the default tab of the KPIs hub.
 *
 * It lives here rather than under Configure because it is a *consumption* activity: what can I
 * measure, which of those matter, build a dashboard from them. Configure sets things up; KPIs
 * uses them. Configure > KPIs stays pure master data (add / edit / enable / map).
 *
 * Three inputs, deliberately kept apart:
 *
 *  1. ENABLED registry KPIs -- master data the customer governs. Disabled ones are excluded here,
 *     which is what makes the enable switch in Configure meaningful rather than decorative.
 *  2. SUGGESTED candidates -- metrics not in the registry yet. They are shown as adoptable
 *     candidates rather than as results, because a metric that cannot be edited, governed or
 *     reproduced is not something anyone should build a dashboard on.
 *  3. REAL registry KPIs (C1, #232) -- `GET /kpis`, merged in the same additive way suggested
 *     candidates already are, not a replacement for the two above. The seed catalogue has no
 *     real `identifier` on any card and the real registry has very few entries today (only
 *     what's actually been registered); replacing one with the other would either invent
 *     identifiers that don't exist or shrink the demo catalogue to almost nothing. A real row
 *     carries a real `kpi_identifier` and real `feasibility` from day one, so selecting one and
 *     building a dashboard from it (D2, #233) actually reaches the real API.
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

 // The real registry (C1, #232) -- fetched once, additive, never blocking: the seed/suggested
 // rows above render immediately regardless of this state. A failure is a small note, not a
 // crash -- the same posture `DashboardsPage.jsx`'s own real-data fetch already takes.
 const [realKpiState, setRealKpiState] = useState({ status: "loading", kpis: [] });
 const [realAreas, setRealAreas] = useState([]);

 useEffect(() => {
  let cancelled = false;
  fetchKpiList()
   .then((kpis) => {
    if (!cancelled) setRealKpiState({ status: "ready", kpis });
   })
   .catch(() => {
    if (!cancelled) setRealKpiState({ status: "error", kpis: [] });
   });
  return () => {
   cancelled = true;
  };
 }, []);

 useEffect(() => {
  let cancelled = false;
  fetchBusinessAreas()
   .then((hierarchy) => {
    if (!cancelled) setRealAreas(hierarchy?.areas || []);
   })
   .catch(() => {
    // Not fatal on its own: a real row without a resolved label still shows its raw
    // business_area id (see realAreaLabel below) rather than disappearing.
   });
  return () => {
   cancelled = true;
  };
 }, []);

 const realAreaLabelById = useMemo(
  () => Object.fromEntries(realAreas.map((a) => [a.id, a.label])),
  [realAreas]
 );
 const realAreaLabel = (id) => realAreaLabelById[id] || id || "";

 const nameToIdsMap = useMemo(() => datasetIdsByLinkedKpiName(), []);
 const processLabelById = useMemo(
  () => Object.fromEntries((processes || []).map((p) => [p.id, p.name])),
  [processes]
 );

 // Mirrors `suggested-candidates.js`'s own precedent for a non-tree row source: sentinel
 // `categoryIndex`/`sectionIndex` (-2 here, distinct from candidates' -1) keep `rowKey` unique
 // across every source `KpiDiscovery.jsx` merges together.
 const realRows = useMemo(
  () =>
   realKpiState.kpis.map((k, index) => ({
    kpi: {
     name: k.name,
     desc: k.description,
     unit: k.unit,
     businessAreaId: k.business_area,
     subjectArea: k.subject_area || "",
     datasetIds: k.dataset_ids || [],
     active: k.active,
     identifier: k.identifier,
     feasibility: k.feasibility,
     fromRegistry: true,
    },
    categoryIndex: -2,
    sectionIndex: -2,
    kpiIndex: index,
    // `KpiDetailPanel.jsx` renders these as "Business Area" / "Function" respectively -- the
    // real area label is the business area, the subject area the finer-grained function.
    categoryTitle: realAreaLabel(k.business_area) || k.business_area || "Your KPI registry",
    sectionName: k.subject_area || "Your KPI registry",
   })),
  [realKpiState.kpis, realAreaLabelById]
 );

 const rows = useMemo(() => {
  const registry = flattenKpiIndexRows(data?.categories || []).filter((r) => isEnabled(r.kpi));
  // Candidates carry `suggested` so the card can badge them and offer adoption. They are appended
  // rather than merged into the registry: until adopted they are not master data.
  const candidates = SUGGESTED_KPI_CANDIDATES.filter((c) => !adopted.has(c.kpi.name));
  const all = [...registry, ...candidates, ...realRows];
  return unlockedNames ? all.filter((r) => unlockedNames.has(r.kpi?.name)) : all;
 }, [data?.categories, adopted, realRows, unlockedNames]);

 const disabledCount = useMemo(
  () => flattenKpiIndexRows(data?.categories || []).filter((r) => !isEnabled(r.kpi)).length,
  [data?.categories]
 );

 if (!hydrated) return <p className="text-sm text-text-secondary">Loading…</p>;

 return (
  <div className="flex w-full flex-col gap-5">
   {realKpiState.status === "error" && (
    <div className="flex items-center gap-2 rounded-xl border border-status-warning/30 bg-status-warning/10 px-4 py-2.5 text-sm text-text-secondary">
     <AlertTriangle size={15} className="shrink-0 text-status-warning" />
     Couldn't load your KPI registry — showing demo metrics only.
    </div>
   )}

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
