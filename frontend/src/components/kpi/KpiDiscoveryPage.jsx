import { useEffect, useMemo, useRef, useState } from "react";
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
import { tileWriteFromKpi } from "../../storage/dashboards";
import { createDashboardApi } from "../../services/dashboards-api";
import { rowFromSuggestion } from "./suggested-candidates";
import { metricsUnlockedBy } from "../configure/source-model-state";
import {
 fetchKpiList,
 startKpiSuggestionRun,
 fetchKpiSuggestionRun,
 createKpi,
 deleteKpi,
} from "../../services/kpis-api";
import { fetchBusinessAreas } from "../../services/business-areas-api";

// Polling cadence for a suggestion run (#254): every 2s at first, every 5s once the run has
// been going for half a minute, and give up at 4 minutes -- the backend's own default bound
// (180s) plus the grace it allows before it closes a lost run, so a well-behaved run is never
// abandoned by the client before the server has said what became of it.
const SUGGEST_POLL_MS = 2000;
const SUGGEST_POLL_RELAXED_MS = 5000;
const SUGGEST_POLL_RELAX_AFTER_MS = 30_000;
const SUGGEST_CEILING_MS = 4 * 60 * 1000;

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
 *  2. SUGGESTED candidates -- metrics not in the registry yet, proposed live by a run of the
 *     `kpi_suggestion` agent (`POST /kpis/suggestions`, polled until done -- F1, #234 and #254)
 *     when the customer asks for them. Nothing is started until "Suggest KPIs" is clicked:
 *     unlike the registry/real-area fetches below, this is a minutes-long frontier-tier model
 *     run, not something to run unprompted on page load. They
 *     are shown as adoptable candidates rather than as results, because a metric that cannot be
 *     edited, governed or reproduced is not something anyone should build a dashboard on --
 *     "adopt" calls `POST /kpis` for real, and only once the suggestion's own feasibility says
 *     `ready` (a `partial`/`blocked` one has nothing valid to adopt).
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

 // The live suggestion run (F1, #234): idle until "Suggest KPIs" is clicked, since each run is
 // two real frontier-tier model calls that take minutes, not something to fire on mount. It is
 // a run to poll, not a response to await (#254): `runId` is the row `POST /kpis/suggestions`
 // returned and `startedAt` when this click started it, so the button can show elapsed time
 // and the poll loop can give up at a ceiling. `notes` is the skill's own refusal channel --
 // why it proposed nothing, not a generic empty state.
 const [suggestionState, setSuggestionState] = useState({
  status: "idle",
  suggestions: [],
  notes: "",
  error: null,
  runId: null,
  startedAt: null,
 });
 const [suggestElapsed, setSuggestElapsed] = useState(0);
 // Which suggestions (by `identifier`) are mid-adopt, and the last adopt failure per identifier --
 // both keyed by identifier, not a single scalar, so concurrent adopts on two different cards
 // don't clear or unblock each other's still-pending state.
 const [adoptingIds, setAdoptingIds] = useState(() => new Set());
 const [adoptErrors, setAdoptErrors] = useState({});
 // The reverse of adopting: one removal at a time, from the detail panel. `error` is the
 // api's own account (a 409 names the dashboards still using the KPI) and stays with the
 // panel until it is closed or the removal is retried.
 const [removeState, setRemoveState] = useState({ identifier: null, error: null });

 // A single focused area scopes the agent's search; zero or several ask it to range over
 // everything rather than firing one request per area (each is its own live model call).
 const suggestBusinessArea = focusAreaIds.length === 1 ? focusAreaIds[0] : undefined;

 // The real registry (C1, #232) -- fetched once, additive, never blocking: the seed/suggested
 // rows above render immediately regardless of this state. A failure is a small note, not a
 // crash -- the same posture `DashboardsPage.jsx`'s own real-data fetch already takes.
 const [realKpiState, setRealKpiState] = useState({ status: "loading", kpis: [] });
 const [realAreas, setRealAreas] = useState([]);

 // `runSuggestions`/`handleAdopt` are click-triggered, not effects, but they need the same
 // unmount guard the two effects below already have -- an `AbortController` per in-flight
 // request, forwarded as `signal` (both `apiGet`/`apiJson` already support one), aborted on
 // unmount so navigating away mid-request doesn't resolve into a `setState` on a gone component.
 const suggestAbortRef = useRef(null);
 // The pending poll timer between two `GET /kpis/suggestions/{run_id}` calls -- cleared on
 // unmount and on a new click, so a stale loop never wakes up and polls a run nobody wants.
 const suggestPollTimerRef = useRef(null);
 const adoptAbortRefs = useRef(new Map());
 const removeAbortRef = useRef(null);
 useEffect(
  () => () => {
   suggestAbortRef.current?.abort();
   window.clearTimeout(suggestPollTimerRef.current);
   for (const controller of adoptAbortRefs.current.values()) controller.abort();
   removeAbortRef.current?.abort();
  },
  []
 );

 // A once-a-second re-render while a run is in flight, so the button's elapsed counter ticks
 // and a long run reads as alive rather than stuck. Stops (and resets) the moment it isn't.
 useEffect(() => {
  if (suggestionState.status !== "loading") {
   setSuggestElapsed(0);
   return undefined;
  }
  const startedAt = suggestionState.startedAt || Date.now();
  const tick = () => setSuggestElapsed(Math.floor((Date.now() - startedAt) / 1000));
  tick();
  const id = window.setInterval(tick, 1000);
  return () => window.clearInterval(id);
 }, [suggestionState.status, suggestionState.startedAt]);

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

 // Start a run, then poll it (#254). One `AbortController` covers the whole loop -- the POST
 // and every GET after it -- so unmount or a second click cancels wherever it happens to be;
 // the timer between polls is cleared alongside it. The backend closes a lost run as `failed`
 // on its own, so the loop normally ends on the row's status; the ceiling below is for the
 // contract stub, which serves the `queued` example forever, and for anything else that never
 // turns terminal -- a spinner with no end is the failure mode this whole shape exists to remove.
 const runSuggestions = () => {
  suggestAbortRef.current?.abort();
  window.clearTimeout(suggestPollTimerRef.current);
  const controller = new AbortController();
  suggestAbortRef.current = controller;
  const startedAt = Date.now();
  setSuggestionState({
   status: "loading",
   suggestions: [],
   notes: "",
   error: null,
   runId: null,
   startedAt,
  });

  const fail = (message) =>
   setSuggestionState({
    status: "error",
    suggestions: [],
    notes: "",
    error: message,
    runId: null,
    startedAt: null,
   });
  const failWith = (err) => {
   if (err?.name === "AbortError") return;
   fail(err?.detail || err?.message || "Something went wrong.");
  };

  const poll = (runId) => {
   if (controller.signal.aborted) return;
   if (Date.now() - startedAt > SUGGEST_CEILING_MS) {
    controller.abort();
    fail("The run is taking longer than expected. Try again in a few minutes.");
    return;
   }
   fetchKpiSuggestionRun(runId, { signal: controller.signal })
    .then((run) => {
     if (controller.signal.aborted) return;
     if (run?.status === "succeeded") {
      setSuggestionState({
       status: "ready",
       suggestions: run.result?.suggestions || [],
       notes: run.result?.notes || "",
       error: null,
       runId,
       startedAt: null,
      });
      return;
     }
     if (run?.status === "failed") {
      fail(run.error || "The run failed without saying why.");
      return;
     }
     // Tight at first (a fast run should feel fast), then relaxed: after half a minute the
     // run is clearly a long one and a poll every few seconds is plenty.
     const delay =
      Date.now() - startedAt > SUGGEST_POLL_RELAX_AFTER_MS ? SUGGEST_POLL_RELAXED_MS : SUGGEST_POLL_MS;
     suggestPollTimerRef.current = window.setTimeout(() => poll(runId), delay);
    })
    .catch(failWith);
  };

  startKpiSuggestionRun(suggestBusinessArea, { signal: controller.signal })
   .then((run) => {
    if (controller.signal.aborted) return;
    if (!run?.id) {
     fail("The run was not accepted.");
     return;
    }
    setSuggestionState((prev) => ({ ...prev, runId: run.id }));
    poll(run.id);
   })
   .catch(failWith);
 };

 const handleAdopt = (row) => {
  const suggestion = row.suggestion;
  if (!suggestion?.identifier) return;
  const controller = new AbortController();
  adoptAbortRefs.current.set(suggestion.identifier, controller);
  setAdoptingIds((prev) => new Set(prev).add(suggestion.identifier));
  setAdoptErrors((prev) => {
   const next = { ...prev };
   delete next[suggestion.identifier];
   return next;
  });
  createKpi(
   {
    identifier: suggestion.identifier,
    name: suggestion.name,
    description: suggestion.description,
    business_area: suggestion.business_area,
    concept: suggestion.concept ?? null,
    grain: suggestion.grain,
    result_shape: suggestion.result_shape,
    sql: suggestion.proposed_sql,
    source_tables: suggestion.derived_from_tables || [],
   },
   { signal: controller.signal }
  )
   .then((created) => {
    // Leave `status` as-is (don't hardcode "ready"): the adopt POST succeeding says nothing
    // about whether the earlier `GET /kpis` registry fetch above did. Forcing "ready" here would
    // silently clear a genuine "error" banner for a registry that still never actually loaded.
    setRealKpiState((prev) => ({ ...prev, kpis: [...prev.kpis, created] }));
    setSuggestionState((prev) => ({
     ...prev,
     suggestions: prev.suggestions.filter((s) => s.identifier !== suggestion.identifier),
    }));
   })
   .catch((err) => {
    if (err?.name === "AbortError") return;
    setAdoptErrors((prev) => ({
     ...prev,
     [suggestion.identifier]: err?.detail || err?.message || "Couldn't add this KPI.",
    }));
   })
   .finally(() => {
    adoptAbortRefs.current.delete(suggestion.identifier);
    // Aborted (unmount) means this card is already gone -- skip the now-pointless setState.
    if (controller.signal.aborted) return;
    setAdoptingIds((prev) => {
     const next = new Set(prev);
     next.delete(suggestion.identifier);
     return next;
    });
   });
 };

 const handleRemove = (row) => {
  const identifier = row?.kpi?.identifier;
  if (!identifier) return;
  removeAbortRef.current?.abort();
  const controller = new AbortController();
  removeAbortRef.current = controller;
  setRemoveState({ identifier, error: null });
  deleteKpi(identifier, { signal: controller.signal })
   .then(() => {
    if (controller.signal.aborted) return;
    setRealKpiState((prev) => ({
     ...prev,
     kpis: prev.kpis.filter((k) => k.identifier !== identifier),
    }));
    setRemoveState({ identifier: null, error: null });
    setDetailRow(null);
   })
   .catch((err) => {
    if (err?.name === "AbortError") return;
    setRemoveState({
     identifier: null,
     error: err?.detail || err?.message || "Couldn't remove this KPI.",
    });
   })
   .finally(() => {
    if (removeAbortRef.current === controller) removeAbortRef.current = null;
   });
 };

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
 // The removal error belongs to the row it happened on; another row's panel must not show it.
 const removeError = (row) => (row?.kpi?.fromRegistry ? removeState.error : null);

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

 // Live suggestions, mapped to rows the same way the static array used to be. A suggestion the
 // customer just adopted is dropped from `suggestionState.suggestions` in `handleAdopt`, so it
 // does not need filtering again here. Keyed by `identifier`, not array position: adopting one
 // suggestion shifts every later index, which would otherwise change every following row's key
 // (`KpiDiscovery.jsx`'s `rowKey`) and force React to remount those cards instead of reconciling.
 const suggestionRows = useMemo(
  () => suggestionState.suggestions.map((s) => rowFromSuggestion(s, s.identifier)),
  [suggestionState.suggestions]
 );

 const rows = useMemo(() => {
  const registry = flattenKpiIndexRows(data?.categories || []).filter((r) => isEnabled(r.kpi));
  const all = [...registry, ...suggestionRows, ...realRows];
  return unlockedNames ? all.filter((r) => unlockedNames.has(r.kpi?.name)) : all;
 }, [data?.categories, suggestionRows, realRows, unlockedNames]);

 const disabledCount = useMemo(
  () => flattenKpiIndexRows(data?.categories || []).filter((r) => !isEnabled(r.kpi)).length,
  [data?.categories]
 );

 const hasSuggestions = rows.some((r) => r.suggested);

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

   {/* Suggest KPIs (F1, #234): a real, live run of the frontier-tier `kpi_suggestion` agent --
       not fired on mount, since it takes minutes and is something the user should ask for. */}
   <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-dashed border-border-light bg-surface-secondary px-4 py-3">
    <span className="flex items-center gap-2 text-sm text-text-secondary">
     <Sparkles size={15} className="shrink-0 text-primary" />
     Ask the model what else your connected data could measure.
    </span>
    <button
     type="button"
     onClick={runSuggestions}
     disabled={suggestionState.status === "loading"}
     className="inline-flex shrink-0 items-center gap-2 rounded-lg bg-primary px-3 py-1.5 text-xs font-semibold text-white transition-colors hover:bg-primary-hover disabled:cursor-not-allowed disabled:opacity-60"
    >
     <Sparkles size={13} />
     {suggestionState.status === "loading"
      ? `Asking the model… ${suggestElapsed}s`
      : "Suggest KPIs"}
    </button>
   </div>

   {suggestionState.status === "error" && (
    <div className="flex items-center gap-2 rounded-xl border border-status-critical-border bg-status-critical-light px-4 py-2.5 text-sm text-status-critical">
     <AlertTriangle size={15} className="shrink-0" />
     Couldn't get suggestions: {suggestionState.error}
    </div>
   )}

   <KpiDiscovery
    rows={rows}
    areas={businessAreas}
    focusAreaIds={focusAreaIds}
    onFocusChange={setFocusAreaIds}
    onOpenDetail={setDetailRow}
    onAdopt={handleAdopt}
    adoptingIds={adoptingIds}
    adoptErrors={adoptErrors}
    onCreateDashboard={setPendingRows}
   />

   {/* The agent's own account of why it proposed nothing, in the schema's refusal channel. An
       empty suggestion list with no reason is indistinguishable from a feature that never ran. */}
   {suggestionState.status === "ready" && !hasSuggestions && suggestionState.notes && (
    <p className="flex items-start gap-2 rounded-xl border border-dashed border-border-light bg-surface-secondary px-4 py-3 text-xs text-text-secondary">
     <Sparkles size={13} className="mt-0.5 shrink-0 text-status-info" />
     <span>
      <span className="font-semibold text-text-primary">No suggestions this time.</span> {suggestionState.notes}
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
     onCreate={async ({ name, refresh, readyRows }) => {
      const created = await createDashboardApi({
       name: String(name || "").trim() || "Untitled dashboard",
       refresh_schedule: refresh,
       tiles: readyRows.map((row, index) => tileWriteFromKpi(row.kpi, index)),
      });
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
     onClose={() => { setRemoveState({ identifier: null, error: null }); setDetailRow(null); }}
     onRemove={handleRemove}
     removing={removeState.identifier === detailRow.kpi?.identifier}
     removeError={removeError(detailRow)}
    />
   )}
  </div>
 );
}
