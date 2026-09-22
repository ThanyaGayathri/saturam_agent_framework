import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useKpiRegistry } from "../../hooks/useKpiRegistry";
import { useProcesses } from "../../hooks/useProcesses";
import { useDemoProfile } from "../../context/DemoProfileContext";
import { datasetIdsByLinkedKpiName, flattenKpiIndexRows } from "../../domain/kpi-mapping";
import { readLocalJson, writeLocalJson } from "../../storage/local-json";
import { AlertTriangle, Sparkles } from "lucide-react";
import KpiDiscovery from "./KpiDiscovery";
import KpiDetailPanel from "./KpiDetailPanel";
import { SourceRunFailure, SuggestionsGenerating } from "./SuggestionStatus";
import CreateDashboardModal from "./CreateDashboardModal";
import AddKpiModal from "./add-kpi/AddKpiModal";
import { buildDashboardWritePayload } from "../../storage/dashboards";
import { createDashboardApi } from "../../services/dashboards-api";
import { rowFromSuggestion } from "./suggested-candidates";
import { metricsUnlockedBy } from "../configure/source-model-state";
import { useCaseActivationPath } from "../../domain/automation-registry";
import {
 fetchKpiList,
 fetchKpi,
 startKpiSuggestionRun,
 fetchCurrentKpiSuggestions,
 dismissKpiSuggestion,
 createKpi,
 fetchKpiSeries,
 computeKpi,
 deleteKpi,
 updateKpi,
} from "../../services/kpis-api";
import { fetchBusinessAreas } from "../../services/business-areas-api";
import { fetchSources } from "../../services/sources-api";
import { listUseCases } from "../../services/use-cases-api";

// Polling cadence while a source's KPI suggestions are being generated: the standing list is read
// every 3s, every 6s once it has been going for a minute, and given up at 5 minutes -- the backend's
// own bound on a run (180s) plus the grace it allows before it closes a lost run as failed, which
// the read applies, so a well-behaved run is never abandoned before the server has said what became
// of it.
const SUGGEST_POLL_MS = 3000;
const SUGGEST_POLL_RELAXED_MS = 6000;
const SUGGEST_POLL_RELAX_AFTER_MS = 60_000;
const SUGGEST_CEILING_MS = 5 * 60 * 1000;
// One stable empty array, not a fresh `[]` literal per render -- handed to `KpiDetailPanel` as
// `sourceTableStatus` before its lineage fetch resolves, so a row with nothing to show yet
// doesn't get a new array reference on every unrelated re-render either.
const EMPTY_SOURCE_TABLE_STATUS = [];
// Same reasoning as `EMPTY_SOURCE_TABLE_STATUS`, for `columnUsage` -- the list read's own
// `column_usage` has no `definition` (only `GET /kpis/{id}` pays for the semantic-layer read
// that fills it in), so the panel's "columns it uses" table reads this decoupled, freshly
// fetched copy instead of `detailRow.kpi.columnUsage`, exactly like `sourceTableStatus`.
const EMPTY_COLUMN_USAGE = [];

// `suggestions` is the standing list (`GET /kpis/suggestions/current`: every source's newest run,
// merged, less what was adopted or dismissed) and `notes` what the runs said was missing.
// `generating` is a run still working -- `{ id, sourceId }` -- or null. `loaded` says the first read
// has come back, so "nothing here" is not shown for a page that has simply not heard yet.
const INITIAL_SUGGESTION_STATE = { suggestions: [], notes: "", generating: null, loaded: false };

/**
 * "What my data supports" -- the discovery surface, and the default tab of the KPIs hub.
 *
 * It lives here rather than under Configure because it is a *consumption* activity: what can I
 * measure, which of those matter, build a dashboard from them. Configure sets things up; KPIs
 * uses them. Configure > KPIs stays pure master data (add / edit / enable / map).
 *
 * Two inputs, deliberately kept apart (the seed "Retail standard" catalogue that used to be a
 * third is no longer mixed into this grid -- it has no real identifiers, so nothing on it could
 * be adopted or put on a dashboard; it still backs Configure > KPIs and the tours):
 *
 *  1. SUGGESTED candidates -- metrics not in the registry yet, proposed by runs of the
 *     `kpi_suggestion` agent (F1, #234 and #254) and kept between visits (contract 1.2.9): the page
 *     reads what is standing (`GET /kpis/suggestions/current`) when it opens, so proposals are still
 *     here after a reload. A connected source's suggestions are proposed automatically when its
 *     harvest ends -- there is no button on this page to ask for them; if that run is still working
 *     the page says so and reads the list again until it lands, and one that failed is reported with
 *     a "Try again" -- and a person can dismiss one for good (`POST /kpis/suggestions/dismissals`).
 *     They are shown as adoptable candidates rather than as results, because a metric that cannot be
 *     edited, governed or reproduced is not something anyone should build a dashboard on --
 *     "adopt" calls `POST /kpis` for real, and only once the suggestion's own feasibility says
 *     `ready` (a `partial`/`blocked` one has nothing valid to adopt).
 *  2. REAL registry KPIs (C1, #232) -- `GET /kpis`, merged in the same additive way suggested
 *     candidates are. A real row carries a real `kpi_identifier` and real `feasibility` from
 *     day one, so selecting one and building a dashboard from it (D2, #233) actually reaches
 *     the real API.
 */

const FOCUS_KEY = "retailgpt:kpi-focus-areas";

// `?add=1` opens the "Add KPI" modal. In the URL rather than state because the button that
// opens it lives in the hub header (`KpisHubLayout.jsx`), outside this page; and it composes with
// back/forward -- closing the modal is a `replace`, so Back never reopens it.
const ADD_PARAM = "add";

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

 const [detailRow, setDetailRow] = useState(null);
 // Rows handed to the create modal. Non-empty means the modal is open.
 const [pendingRows, setPendingRows] = useState(null);
 const [focusAreaIds, setFocusState] = useState(() => {
  const saved = readLocalJson(FOCUS_KEY, []);
  return Array.isArray(saved) ? saved : [];
 });
 const setFocusAreaIds = (ids) => { setFocusState(ids); writeLocalJson(FOCUS_KEY, ids); };

 // The suggestions on the page (F1, #234; kept between visits, contract 1.2.9). KPIs the data supports
 // are proposed for a source automatically when its harvest ends, so nothing here starts a run: the
 // page reads what is standing when it opens (`loadCurrent`, below) and, while a run is still
 // working, reads it again until it lands.
 const [suggestionState, setSuggestionState] = useState(INITIAL_SUGGESTION_STATE);
 // Which suggestions (by `identifier`) are mid-adopt, and the last adopt failure per identifier --
 // both keyed by identifier, not a single scalar, so concurrent adopts on two different cards
 // don't clear or unblock each other's still-pending state.
 const [adoptingIds, setAdoptingIds] = useState(() => new Set());
 const [adoptErrors, setAdoptErrors] = useState({});
 // The reverse of adopting: one removal at a time, from the detail panel. `error` is the
 // api's own account (a 409 names the dashboards still using the KPI) and stays with the
 // panel until it is closed or the removal is retried.
 const [removeState, setRemoveState] = useState({ identifier: null, error: null });
 // Dismissing a suggestion, one at a time like a removal: `identifier` stays set on failure so the
 // row (and the panel) that failed can show the api's own account of why.
 const [dismissState, setDismissState] = useState({ identifier: null, error: null });
 // The newest run that failed with no later success for its source (`GET /kpis/suggestions/
 // current`'s `failed`), and the connected sources' names to say which one. A failure after a
 // harvest would otherwise be invisible -- nobody clicked anything to see it fail.
 const [failedRun, setFailedRun] = useState(null);
 const [sourceNames, setSourceNames] = useState({});
 // A registry row's description/sql edit (`PATCH /kpis/{id}`), one at a time from whichever
 // panel is open. A suggestion's edit never reaches here -- it is a local patch to
 // `suggestionState.suggestions`, always synchronous, with nothing to load or fail.
 const [updateState, setUpdateState] = useState({ identifier: null, saving: false, error: null });
 // Which registry rows' Status switch (hierarchy table) is mid-toggle -- keyed by identifier,
 // not a single scalar, because the table has every row's switch interactive at once (unlike
 // the detail panel's own single-row edit state this mirrors): toggling one KPI must not abort
 // another KPI's still-in-flight PATCH. A second click on the *same* row is what this guards
 // against, same as `adoptingIds` above for adopt.
 const [togglingActiveIds, setTogglingActiveIds] = useState(() => new Set());

 // The real registry (C1, #232) -- fetched once, additive, never blocking: the seed/suggested
 // rows above render immediately regardless of this state. A failure is a small note, not a
 // crash -- the same posture `DashboardsPage.jsx`'s own real-data fetch already takes.
 const [realKpiState, setRealKpiState] = useState({ status: "loading", kpis: [] });
 // The platform's own use cases (K16's `/use-cases`), fetched once, additive like
 // `realKpiState` above: a KPI's "Powered by" chips and the detail panel's "Mapping detail
 // -> Use cases" list read `linked_kpi_ids` off these rather than the frontend-only demo
 // catalog (`kpi-usecase-links.js`), which never heard of a use case this deployment
 // actually stored. A failure here is a small note, not a crash -- the "Powered by" column
 // just falls back to the demo catalog entries alone.
 const [realUseCaseState, setRealUseCaseState] = useState({ status: "loading", useCases: [] });
 // The business areas, from `GET /business-areas` on page load: the options the focus prompt
 // offers (supported ones selectable, with K9's evidence; the rest greyed with its reason) and
 // the labels real rows are grouped under. Held here, not in the per-profile localStorage list
 // the rest of the app still reads -- that list is a different vocabulary and is left alone.
 const [areaState, setAreaState] = useState({ status: "loading", areas: [], error: null });


 // One registry KPI's value over time (`GET /kpis/{id}/series`), keyed by identifier, for the
 // detail panel's `KpiTrend`: `{status: "loading"|"computing"|"ready"|"error", data, error,
 // retry}`. Keyed rather than per-panel because the compute that "Add to my metrics" starts
 // (`POST /kpis/{id}/compute`, a few seconds) outlives any one panel: the customer adopts, the
 // panel is closed, they open the new card's panel -- and find it computing, or done, rather
 // than a fresh "no values yet". `retry` remembers which call failed so "Try again" repeats it.
 const [seriesById, setSeriesById] = useState({});
 const seriesRef = useRef(seriesById);
 seriesRef.current = seriesById;
 const seriesAbortRefs = useRef(new Map());
 const patchSeries = useCallback((identifier, patch) => {
  setSeriesById((prev) => ({ ...prev, [identifier]: { ...(prev[identifier] || {}), ...patch } }));
 }, []);
 const loadSeries = useCallback(
  (identifier, dimension) => {
   seriesAbortRefs.current.get(identifier)?.abort();
   const controller = new AbortController();
   seriesAbortRefs.current.set(identifier, controller);
   patchSeries(identifier, { status: "loading", error: null, retry: "load" });
   fetchKpiSeries(identifier, { dimension, signal: controller.signal })
    .then((data) => {
     if (controller.signal.aborted) return;
     patchSeries(identifier, { status: "ready", data, error: null });
    })
    .catch((err) => {
     if (err?.name === "AbortError") return;
     patchSeries(identifier, {
      status: "error",
      error: err?.detail || err?.message || "Couldn't load values.",
      retry: "load",
     });
    });
  },
  [patchSeries]
 );
 const computeSeries = useCallback(
  (identifier, asOf) => {
   seriesAbortRefs.current.get(identifier)?.abort();
   const controller = new AbortController();
   seriesAbortRefs.current.set(identifier, controller);
   // `retryAsOf` survives every patch below (`patchSeries` merges) so "Try again" after a
   // failure re-runs the same date the customer actually asked for, not silently today's.
   patchSeries(identifier, { status: "computing", error: null, retry: "compute", retryAsOf: asOf });
   computeKpi(identifier, { asOf, signal: controller.signal })
    .then((result) => {
     if (controller.signal.aborted) return;
     // A formula that fails is a 200 saying so -- the request did what it said; the formula
     // did not. Shown as the error it is, with the api's own words.
     if (result?.status === "failed") {
      patchSeries(identifier, {
       status: "error",
       error: result.error || "the formula failed.",
       retry: "compute",
      });
      return;
     }
     // `loadSeries` reloads the default trailing window, which only shows this result when
     // `asOf` happens to fall inside it (or is today, the common "Add to my metrics" case).
     // A backfill for an older date needs its own, explicit confirmation -- `lastCompute` --
     // that the panel's Trust section shows regardless of what the trailing window contains,
     // so "Re-run for another date" never *looks* like it silently did nothing.
     patchSeries(identifier, { lastCompute: result });
     loadSeries(identifier);
    })
    .catch((err) => {
     if (err?.name === "AbortError") return;
     patchSeries(identifier, {
      status: "error",
      error: err?.detail || err?.message || "Couldn't compute it.",
      retry: "compute",
     });
    });
  },
  [patchSeries, loadSeries]
 );

 // One registry KPI's `source_table_status` (`GET /kpis/{id}`, the single-KPI read -- the list
 // read `realKpiState.kpis` comes from never fills it in, see `rowFromRegistryKpi`'s own
 // comment), keyed by identifier for the detail panel's "Where this number comes from" tab.
 // Same shape as `seriesById` above, and for the same reason: opening a panel loads it once,
 // an entry already there is shown as-is.
 const [lineageById, setLineageById] = useState({});
 const lineageRef = useRef(lineageById);
 lineageRef.current = lineageById;
 const lineageAbortRefs = useRef(new Map());
 const loadLineage = useCallback((identifier) => {
  lineageAbortRefs.current.get(identifier)?.abort();
  const controller = new AbortController();
  lineageAbortRefs.current.set(identifier, controller);
  setLineageById((prev) => ({ ...prev, [identifier]: { status: "loading" } }));
  fetchKpi(identifier, { signal: controller.signal })
   .then((k) => {
    if (controller.signal.aborted) return;
    setLineageById((prev) => ({
     ...prev,
     [identifier]: {
      status: "ready",
      sourceTableStatus: k.source_table_status || [],
      columnUsage: k.column_usage || [],
     },
    }));
   })
   .catch((err) => {
    if (err?.name === "AbortError") return;
    // Enrichment, not core data (the row's own fields already came from the list read) --
    // an empty result reads the same as "nothing loaded yet", not an error banner.
    setLineageById((prev) => ({
     ...prev,
     [identifier]: { status: "ready", sourceTableStatus: [], columnUsage: [] },
    }));
   });
 }, []);

 // `handleAdopt` is click-triggered, not an effect, but it needs the same unmount guard the effects
 // below already have -- an `AbortController` per in-flight request, forwarded as `signal` (both
 // `apiGet`/`apiJson` already support one), aborted on unmount so navigating away mid-request
 // doesn't resolve into a `setState` on a gone component.
 const adoptAbortRefs = useRef(new Map());
 const removeAbortRef = useRef(null);
 const dismissAbortRef = useRef(null);
 const updateAbortRef = useRef(null);
 // The manual "Add KPI" form's `POST /kpis` (`onCreateManual` below) -- one at a time, since
 // the modal refuses to close while it is in flight.
 const createAbortRef = useRef(null);
 // Keyed by KPI identifier, like `adoptAbortRefs` above -- every row's Status switch can be
 // mid-PATCH at once, so aborting one row's request must never reach another's.
 const toggleActiveAbortRefs = useRef(new Map());
 useEffect(
  () => () => {
   for (const controller of adoptAbortRefs.current.values()) controller.abort();
   for (const controller of seriesAbortRefs.current.values()) controller.abort();
   for (const controller of lineageAbortRefs.current.values()) controller.abort();
   removeAbortRef.current?.abort();
   updateAbortRef.current?.abort();
   createAbortRef.current?.abort();
   for (const controller of toggleActiveAbortRefs.current.values()) controller.abort();
  },
  []
 );

 // Opening a registry KPI's panel loads its series once; an entry already there (ready, still
 // computing from the adopt, or failed) is shown as it is rather than refetched.
 const detailIdentifier = detailRow?.kpi?.fromRegistry ? detailRow.kpi.identifier : null;
 useEffect(() => {
  if (!detailIdentifier) return;
  if (!seriesRef.current[detailIdentifier]) loadSeries(detailIdentifier);
 }, [detailIdentifier, loadSeries]);
 // Same, for the single-KPI read that fills in `source_table_status` -- the list read that
 // built this row's other fields never does (see `rowFromRegistryKpi`'s own comment).
 useEffect(() => {
  if (!detailIdentifier) return;
  if (!lineageRef.current[detailIdentifier]) loadLineage(detailIdentifier);
 }, [detailIdentifier, loadLineage]);

 // `source_table_status` is handed to `KpiDetailPanel` as its OWN prop, never merged into a
 // copy of `detailRow` -- round 1 tried memoizing that merge, but the merge itself still
 // produces a new `detailRow` reference the instant lineage goes loading->ready, and
 // `KpiDetailPanel` resets its active tab and any open edit form whenever `detailRow`'s
 // reference changes (`useEffect` on it). A customer who switches tabs before that warehouse
 // round trip resolves (routinely faster than a customer's first click) would still get bounced
 // back to Overview. Keeping this out of `detailRow` entirely means `detailRow`'s identity is
 // exactly what it was before this PR -- lineage arriving can never trigger that effect.
 const sourceTableStatusForPanel =
  (detailIdentifier && lineageById[detailIdentifier]?.sourceTableStatus) || EMPTY_SOURCE_TABLE_STATUS;
 // Same reasoning, for `column_usage`'s `definition` field -- see `EMPTY_COLUMN_USAGE`'s comment.
 const columnUsageForPanel =
  (detailIdentifier && lineageById[detailIdentifier]?.columnUsage) || EMPTY_COLUMN_USAGE;

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
  listUseCases()
   .then((useCases) => {
    if (!cancelled) setRealUseCaseState({ status: "ready", useCases });
   })
   .catch(() => {
    if (!cancelled) setRealUseCaseState({ status: "error", useCases: [] });
   });
  return () => {
   cancelled = true;
  };
 }, []);

 // identifier -> [{id, title}], from every stored use case's `linked_kpi_ids` (K16's own
 // `spec.kpi_identifiers`, unchanged since -- see `usecases/service.py`'s `catalog_view`).
 // A KPI with no use case pointing at it is simply absent, not an empty-array entry.
 const realUseCasesByKpiId = useMemo(() => {
  const map = new Map();
  for (const uc of realUseCaseState.useCases) {
   for (const kpiId of uc.linked_kpi_ids || []) {
    if (!map.has(kpiId)) map.set(kpiId, []);
    map.get(kpiId).push({ id: uc.id, title: uc.title });
   }
  }
  return map;
 }, [realUseCaseState.useCases]);

 // Retriable (the prompt offers a Retry when the graph could not be asked), hence a function
 // the effect calls rather than the effect itself. On success the persisted focus is reconciled
 // first: ids this browser stored under an older vocabulary (or for an area since removed)
 // would otherwise leave a collapsed "Ranked for" line with no pills on it. Both updates land
 // in one commit, so the prompt never sees the new options with the old answer.
 const areasAbortRef = useRef(null);
 const loadAreas = () => {
  areasAbortRef.current?.abort();
  const controller = new AbortController();
  areasAbortRef.current = controller;
  setAreaState((prev) => ({ ...prev, status: "loading", error: null }));
  fetchBusinessAreas({ signal: controller.signal })
   .then((hierarchy) => {
    if (controller.signal.aborted) return;
    const areas = hierarchy?.areas || [];
    const known = new Set(areas.map((a) => a.id));
    setFocusState((prev) => {
     const kept = prev.filter((id) => known.has(id));
     if (kept.length !== prev.length) writeLocalJson(FOCUS_KEY, kept);
     return kept.length === prev.length ? prev : kept;
    });
    setAreaState({ status: "ready", areas, error: null });
   })
   .catch((err) => {
    if (err?.name === "AbortError") return;
    setAreaState({
     status: "error",
     areas: [],
     error: { status: err?.status ?? null, detail: err?.detail || err?.message || "" },
    });
   });
 };
 useEffect(() => {
  loadAreas();
  return () => areasAbortRef.current?.abort();
  // eslint-disable-next-line react-hooks/exhaustive-deps
 }, []);


 // The standing suggestions (contract 1.2.9): what is kept between visits, so proposed KPIs are still
 // here after a reload and on the next visit. A source's suggestions are proposed automatically when
 // its harvest ends, so opening this page after connecting one finds them ready -- or, if the run is
 // still working, shows that it is (`generating`) and reads the list again until it lands.
 //
 // While a run is working only `generating` is refreshed: replacing the list on every poll would
 // throw away an edit the person is making to a suggestion's draft. When the run lands the list is
 // replaced once, with the merged result the backend computed (a source's new run supersedes that
 // source's earlier suggestions and leaves every other source's in place, less what was adopted or
 // dismissed).
 //
 // Failing to read the list is silent: it is a convenience over a page that works without it, and an
 // error banner for something nobody asked for would only be noise.
 const currentAbortRef = useRef(null);
 const pollTimerRef = useRef(null);
 const pollStartedAtRef = useRef(null);
 const loadCurrent = useCallback(() => {
  currentAbortRef.current?.abort();
  window.clearTimeout(pollTimerRef.current);
  const controller = new AbortController();
  currentAbortRef.current = controller;
  fetchCurrentKpiSuggestions({ signal: controller.signal })
   .then((current) => {
    if (controller.signal.aborted) return;
    const generating = current?.generating
     ? { id: current.generating.id, sourceId: current.generating.source_id ?? null }
     : null;
    setFailedRun(current?.failed || null);
    setSuggestionState((prev) => ({
     ...prev,
     generating,
     loaded: true,
     // Keep the list (and any draft edit in it) while a run is working -- but take it on the very
     // first read, so a page opened mid-run still shows what is already standing.
     ...(generating && prev.loaded
      ? {}
      : { suggestions: current?.suggestions || [], notes: current?.notes || "" }),
    }));
    if (!generating) {
     pollStartedAtRef.current = null;
     return;
    }
    if (pollStartedAtRef.current == null) pollStartedAtRef.current = Date.now();
    const elapsed = Date.now() - pollStartedAtRef.current;
    if (elapsed >= SUGGEST_CEILING_MS) return; // the backend closes a lost run; stop asking
    pollTimerRef.current = window.setTimeout(
     loadCurrent,
     elapsed > SUGGEST_POLL_RELAX_AFTER_MS ? SUGGEST_POLL_RELAXED_MS : SUGGEST_POLL_MS
    );
   })
   .catch((err) => {
    if (err?.name === "AbortError") return;
    setSuggestionState((prev) => ({ ...prev, loaded: true }));
   });
 }, []);

 useEffect(() => {
  loadCurrent();
  return () => {
   currentAbortRef.current?.abort();
   window.clearTimeout(pollTimerRef.current);
  };
 }, [loadCurrent]);

 // The connected sources' names, for saying which source a run failed for.
 useEffect(() => {
  const controller = new AbortController();
  fetchSources({ signal: controller.signal })
   .then((list) => {
    if (controller.signal.aborted) return;
    setSourceNames(Object.fromEntries((Array.isArray(list) ? list : []).map((src) => [src.id, src.name])));
   })
   .catch(() => {});
  return () => controller.abort();
 }, []);

 // "Try again" on a run that failed after a harvest: start one for that source (`POST /kpis/
 // suggestions` with `source_id`; a source whose harvest has not finished is a 409 that says so),
 // then read the standing list, which now names the new run as `generating`. It stays the only way
 // to start a run from this page.
 const [retryState, setRetryState] = useState({ busy: false, error: null });
 const retryFailedRun = () => {
  const sourceId = failedRun?.source_id || null;
  setRetryState({ busy: true, error: null });
  startKpiSuggestionRun({ sourceId })
   .then(() => {
    setFailedRun(null);
    pollStartedAtRef.current = null;
    loadCurrent();
    setRetryState({ busy: false, error: null });
   })
   .catch((err) => {
    setRetryState({ busy: false, error: err?.detail || err?.message || "Couldn't start the run." });
   });
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
    // `KpiSuggestion` carries no `subject_area` at all (it's a curated field, assigned only
    // once a KPI is governed) -- but the discovery hierarchy displays a suggestion under its
    // `concept` as a stand-in Subject Area (`suggested-candidates.js`'s `rowFromSuggestion`)
    // so it isn't dumped in "Unsorted" before it even has a chance to be reviewed. Carrying
    // that same value over here keeps the row in the group the customer already saw it in,
    // instead of it silently landing in "Unsorted" the moment "Add to my metrics" succeeds.
    subject_area: suggestion.concept ?? null,
    grain: suggestion.grain,
    result_shape: suggestion.result_shape,
    sql: suggestion.proposed_sql,
    source_tables: suggestion.derived_from_tables || [],
    // The two suggestion-era sentences the panel shows ("Why the model proposed it" / "What
    // value it provides") go into the registry with the KPI, so the panel keeps saying them
    // after adopt -- on reload and for every viewer -- instead of only for this session.
    rationale: suggestion.rationale ?? null,
    business_value: suggestion.business_value ?? null,
    formula_explanation: suggestion.formula_explanation ?? null,
    column_usage: suggestion.column_usage ?? null,
    table_usage: suggestion.table_usage ?? null,
   },
   { signal: controller.signal }
  )
   .then((created) => {
    setSuggestionState((prev) => ({
     ...prev,
     suggestions: prev.suggestions.filter((s) => s.identifier !== suggestion.identifier),
    }));
    // A panel open on the suggestion now shows the registry row it became -- the suggestion
    // row is gone from the list, and the registry row is the one with a value to show. A
    // panel open on anything else (or none) is left alone.
    absorbCreatedKpi(
     created,
     (prev) => prev?.suggested && prev?.kpi?.identifier === suggestion.identifier
    );
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

 // What a new registry row does once `POST /kpis` lands, shared by an adopt and the manual
 // "Add KPI" form: into the registry list, its panel open on the new row when `openPanel`
 // says so given the panel currently open (the manual form always opens it -- the value is
 // the reason to look; an adopt only when the panel was on the suggestion it replaced), and
 // today's value computed now for a scalar.
 //
 // `status` is left as-is (never hardcoded to "ready"): the POST succeeding says nothing
 // about whether the earlier `GET /kpis` registry fetch did, and forcing "ready" would
 // silently clear a genuine "error" banner for a registry that never actually loaded.
 //
 // The compute is the nightly gold pass for this one KPI, so the graph has its first point at
 // once rather than after the first night; it runs whether or not a panel is open. Skipped for
 // `rowset` KPIs: `KpiTrend` never shows a compute affordance or a line for one, so the call
 // could only ever produce a skip the client already knows about.
 const absorbCreatedKpi = (created, openPanel = () => true) => {
  setRealKpiState((prev) => ({ ...prev, kpis: [...prev.kpis, created] }));
  setDetailRow((prev) =>
   openPanel(prev) ? rowFromRegistryKpi(created, Number.MAX_SAFE_INTEGER) : prev
  );
  if (created.result_shape !== "rowset") computeSeries(created.identifier);
 };

 // The manual form's create, with the same unmount guard every other request on this page
 // has: aborted on unmount (the cleanup above), and nothing absorbed once it is -- otherwise
 // navigating away mid-save would still append the row and fire an un-abortable compute from
 // a gone component. A dismiss mid-save is refused by the modal itself, so the only way here
 // is the page going away.
 const createManualKpi = async (body) => {
  createAbortRef.current?.abort();
  const controller = new AbortController();
  createAbortRef.current = controller;
  try {
   const created = await createKpi(body, { signal: controller.signal });
   if (controller.signal.aborted) return;
   closeAddKpi();
   absorbCreatedKpi(created);
  } finally {
   if (createAbortRef.current === controller) createAbortRef.current = null;
  }
 };

 const addKpiOpen = params.get(ADD_PARAM) === "1";
 // Reads the URL live rather than this render's `params`: `createManualKpi` calls this after
 // an `await`, by which time the run-sync effect above may already have stripped a failed
 // run's `?run=` -- writing back a click-time snapshot would put it back, and the effect
 // (keyed on `runId`, unchanged) would never correct it. `BrowserRouter` updates
 // `window.location` synchronously on every navigation, so what is there is the truth. With a
 // live read the `has` guard is also meaningful: a second close (or one after the URL moved
 // on) is a no-op instead of a redundant replace.
 const closeAddKpi = () => {
  const live = new URLSearchParams(window.location.search);
  if (!live.has(ADD_PARAM)) return;
  live.delete(ADD_PARAM);
  const search = live.toString();
  navigate({ search: search ? `?${search}` : "" }, { replace: true });
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
    // `identifier` stays set on failure (not reset to `null`) so a caller can tell WHICH row's
    // removal failed -- both the panel's `removeError` and the table row's own inline
    // confirmation (`KpiDiscovery.jsx`'s `KpiTable`) key off it matching their own row.
    setRemoveState({
     identifier,
     error: err?.detail || err?.message || "Couldn't remove this KPI.",
    });
   })
   .finally(() => {
    if (removeAbortRef.current === controller) removeAbortRef.current = null;
   });
 };

 // Remove a suggestion for good (contract 1.2.9): `POST /kpis/suggestions/dismissals`, then off
 // the page. The backend remembers it, so it is not on the next reload either -- nor in a later
 // run that proposes the same KPI again, for anyone. Not optimistic: the card stays until the
 // backend has said yes, so a refused dismissal (a viewer without `kpis:write`) is a visible
 // error on the row rather than a card that vanishes and comes back on reload.
 const handleDismissSuggestion = (row) => {
  const identifier = row?.suggestion?.identifier;
  if (!identifier) return;
  dismissAbortRef.current?.abort();
  const controller = new AbortController();
  dismissAbortRef.current = controller;
  setDismissState({ identifier, error: null });
  dismissKpiSuggestion(identifier, { signal: controller.signal })
   .then(() => {
    if (controller.signal.aborted) return;
    setSuggestionState((prev) => ({
     ...prev,
     suggestions: prev.suggestions.filter((sug) => sug.identifier !== identifier),
    }));
    setDismissState({ identifier: null, error: null });
    setDetailRow((prev) => (prev?.suggested && prev?.suggestion?.identifier === identifier ? null : prev));
   })
   .catch((err) => {
    if (err?.name === "AbortError") return;
    setDismissState({
     identifier,
     error: err?.detail || err?.message || "Couldn't dismiss this suggestion.",
    });
   })
   .finally(() => {
    if (dismissAbortRef.current === controller) dismissAbortRef.current = null;
   });
 };

 // A suggestion is never persisted until adopted, so "editing" it is just rewriting the draft
 // this page is already holding in `suggestionState.suggestions` -- no request, and nothing
 // that can fail. Routed through `rowFromSuggestion` (not a manual spread) so the row's `kpi.desc`
 // etc. stay in sync with `suggestion.description`/`proposed_sql`, the same derivation every
 // other suggestion row already goes through.
 const handleEditSuggestionDraft = (row, patch) => {
  const identifier = row?.suggestion?.identifier;
  if (!identifier) return;
  const next = { ...row.suggestion, description: patch.description, proposed_sql: patch.sql };
  setSuggestionState((prev) => ({
   ...prev,
   suggestions: prev.suggestions.map((s) => (s.identifier === identifier ? next : s)),
  }));
  setDetailRow((prev) =>
   prev?.suggested && prev?.suggestion?.identifier === identifier
    ? rowFromSuggestion(next, prev.kpiIndex)
    : prev
  );
 };

 // The reverse of `handleAdopt`'s create -- `PATCH /kpis/{id}` on an already-governed KPI.
 // `KpiWrite`'s required fields (name, business_area, grain, source_tables) must be resent in
 // full even though only description/sql are what the form actually changed -- see
 // `updateKpi`'s own doc comment for why. `detailRow` is refreshed only on success, which is
 // also what closes `KpiDetailPanel`'s edit form (see its `useEffect` on `detailRow`); a
 // failure leaves both the row and the customer's unsaved edit exactly where they were.
 const handleUpdateKpi = (row, patch) => {
  const identifier = row?.kpi?.identifier;
  if (!identifier) return;
  updateAbortRef.current?.abort();
  const controller = new AbortController();
  updateAbortRef.current = controller;
  setUpdateState({ identifier, saving: true, error: null });
  updateKpi(
   identifier,
   {
    name: row.kpi.name,
    description: patch.description,
    business_area: row.kpi.businessAreaId,
    grain: row.kpi.grain,
    sql: patch.sql,
    source_tables: row.kpi.sourceTables || [],
   },
   { signal: controller.signal }
  )
   .then((updated) => {
    if (controller.signal.aborted) return;
    setRealKpiState((prev) => ({
     ...prev,
     kpis: prev.kpis.map((k) => (k.identifier === identifier ? updated : k)),
    }));
    setDetailRow((prev) =>
     prev?.kpi?.identifier === identifier ? rowFromRegistryKpi(updated, prev.kpiIndex) : prev
    );
    setUpdateState({ identifier: null, saving: false, error: null });
   })
   .catch((err) => {
    if (err?.name === "AbortError") return;
    setUpdateState({
     identifier,
     saving: false,
     error: err?.detail || err?.message || "Couldn't save changes.",
    });
   })
   .finally(() => {
    if (updateAbortRef.current === controller) updateAbortRef.current = null;
   });
 };

 // The hierarchy table's Status switch -- `PATCH /kpis/{id}` with everything `KpiWrite` requires
 // resent unchanged (same constraint `handleUpdateKpi` documents) and only `active` flipped.
 // Not optimistic: the switch stays where it was until the server confirms, so a failure never
 // shows a status the registry doesn't actually have.
 const handleToggleActive = (row) => {
  const kpi = row?.kpi;
  if (!kpi?.identifier) return;
  // Aborts only a second click on THIS row -- keyed by identifier, like `adoptAbortRefs`,
  // so toggling one KPI's Status switch never aborts another row's still-in-flight PATCH.
  toggleActiveAbortRefs.current.get(kpi.identifier)?.abort();
  const controller = new AbortController();
  toggleActiveAbortRefs.current.set(kpi.identifier, controller);
  setTogglingActiveIds((prev) => new Set(prev).add(kpi.identifier));
  updateKpi(
   kpi.identifier,
   {
    name: kpi.name,
    description: kpi.desc,
    business_area: kpi.businessAreaId,
    grain: kpi.grain,
    sql: kpi.sql,
    source_tables: kpi.sourceTables || [],
    active: !kpi.active,
   },
   { signal: controller.signal }
  )
   .then((updated) => {
    if (controller.signal.aborted) return;
    setRealKpiState((prev) => ({
     ...prev,
     kpis: prev.kpis.map((k) => (k.identifier === kpi.identifier ? updated : k)),
    }));
   })
   .catch((err) => {
    if (err?.name === "AbortError") return;
    window.alert(err?.detail || err?.message || "Couldn't update this KPI's status.");
   })
   .finally(() => {
    toggleActiveAbortRefs.current.delete(kpi.identifier);
    if (controller.signal.aborted) return;
    setTogglingActiveIds((prev) => {
     const next = new Set(prev);
     next.delete(kpi.identifier);
     return next;
    });
   });
 };

 const realAreaLabelById = useMemo(
  () => Object.fromEntries(areaState.areas.map((a) => [a.id, a.label])),
  [areaState.areas]
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
 const rowFromRegistryKpi = (k, index) => ({
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
   resultShape: k.result_shape,
   // Dashboards with a tile built from this KPI -- the hierarchy table's "Maps to" column.
   // Always empty for a suggestion, which has no registry identifier yet.
   dashboardNames: k.dashboard_names || [],
   // Stored use cases whose `linked_kpi_ids` name this KPI -- the "Powered by" column and
   // the detail panel's "Mapping detail -> Use cases" list. Same reasoning as
   // `dashboardNames`: always empty for a suggestion, which has no registry identifier for a
   // use case to reference yet.
   linkedUseCases: realUseCasesByKpiId.get(k.identifier) || [],
   // Null for a hand-authored KPI or one adopted before the registry kept them; the panel
   // renders the section only when at least one is present.
   rationale: k.rationale || "",
   businessValue: k.business_value || "",
   fromRegistry: true,
   // `sql` was previously carried through only so an edit (`handleUpdateKpi` below) could
   // resend the required fields `PATCH /kpis/{id}` demands every time; the detail panel's
   // "Where this number comes from" tab now also renders it read-only (when present -- a
   // `kpis:read`-only caller never receives it, per the api's own scope gate).
   sql: k.sql,
   // Same scope gate as `sql` (a `kpis:read`-only caller never receives it): its labels can
   // name the same columns/tables the formula reads. Kept as the wire shape (snake_case
   // fields) rather than remapped -- there's only ever one reader, `FormulaDisplay` below.
   formulaExplanation: k.formula_explanation || null,
   // Same scope gate as `formulaExplanation`, but missing its `definition` field -- like
   // `sourceTableStatus` below, only `GET /kpis/{id}` pays for the semantic-layer read that
   // fills that in, never the list `GET /kpis` this row usually comes from. Kept here anyway
   // for parity with the wire shape; the panel itself reads the fresher `columnUsageForPanel`
   // (see `EMPTY_COLUMN_USAGE`'s comment), not this field.
   columnUsage: k.column_usage || [],
   grain: k.grain,
   sourceTables: k.source_tables || [],
   // The rest of the definition shape the detail panel's technical/trust sections read.
   // All real fields off the same `GET /kpis` row -- nothing here is computed client-side.
   dialect: k.dialect,
   concept: k.concept,
   timeWindow: k.time_window || null,
   validatedAgainstSnapshot: k.validated_against_snapshot || null,
   parameters: k.parameters || [],
   // Each `source_tables` entry's most recent load, when the warehouse could answer -- only
   // `GET /kpis/{id}` (a single-KPI read) fills this in, never the list `GET /kpis` this row
   // usually comes from; re-fetched per KPI when its panel opens (see `loadKpiDetail` below).
   sourceTableStatus: k.source_table_status || [],
  },
  categoryIndex: -2,
  sectionIndex: -2,
  kpiIndex: index,
  // `KpiDetailPanel.jsx` renders these as "Business Area" / "Function" respectively -- the
  // real area label is the business area, the subject area the finer-grained function.
  categoryTitle: realAreaLabel(k.business_area) || k.business_area || "Your KPI registry",
  sectionName: k.subject_area || "Your KPI registry",
 });
 // The removal error belongs to the row it happened on; another row's panel must not show it.
 // Scoped by identifier, not just "is this a registry row" -- `removeState.identifier` now
 // stays set on failure (see `handleRemove`'s catch), so this only shows the error on the row
 // it actually belongs to, not on every registry row's panel.
 const removeError = (row) =>
  row?.kpi?.fromRegistry && removeState.identifier === row.kpi.identifier ? removeState.error : null;
 // Same reasoning as `removeError`, for a suggestion's dismissal.
 const dismissError = (row) =>
  row?.suggested && dismissState.identifier === row.suggestion?.identifier ? dismissState.error : null;
 // Same reasoning as `removeError`, for `handleUpdateKpi`'s save.
 const updateError = (row) =>
  row?.kpi?.fromRegistry && updateState.identifier === row.kpi.identifier ? updateState.error : null;

 const realRows = useMemo(
  () => realKpiState.kpis.map((k, index) => rowFromRegistryKpi(k, index)),
  // eslint-disable-next-line react-hooks/exhaustive-deps -- `rowFromRegistryKpi` closes over `realAreaLabelById` and `realUseCasesByKpiId` only.
  [realKpiState.kpis, realAreaLabelById, realUseCasesByKpiId]
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

 // The seed "Retail standard" catalogue is not shown in the unfiltered grid: every card there
 // is either a live suggestion or a real registry KPI, so each one has a real identifier and
 // real feasibility and can actually be adopted or put on a dashboard. The seed data still
 // backs Configure > KPIs and the tours; only the unfiltered grid stopped mixing it in.
 //
 // The "See unlocked metrics" deep link (`unlockedNames`, from Configure right after a source
 // connects) still needs it, though: `metricsUnlockedBy` promises specific named metrics before
 // any suggestion run exists to propose them, so a fresh deployment has neither a matching
 // suggestion nor a real registry row yet. Fall back to the (deduped) seed catalogue only in
 // that filtered view, so the promise on the source card and what this page shows stay the same
 // set -- exactly what unblocked this link before the seed catalogue left the unfiltered grid.
 const rows = useMemo(() => {
  const all = [...suggestionRows, ...realRows];
  if (!unlockedNames) return all;
  const named = all.filter((r) => unlockedNames.has(r.kpi?.name));
  const seen = new Set(named.map((r) => r.kpi?.name));
  const seedPreview = flattenKpiIndexRows(data?.categories || []).filter(
   (r) => r.kpi?.active !== false && unlockedNames.has(r.kpi?.name) && !seen.has(r.kpi?.name)
  );
  return [...named, ...seedPreview];
 }, [suggestionRows, realRows, unlockedNames, data?.categories]);

 const hasSuggestions = rows.some((r) => r.suggested);

 if (!hydrated) return <p className="text-sm text-text-secondary">Loading…</p>;

 return (
  <div className="flex w-full flex-col gap-5">
   {realKpiState.status === "error" && (
    <div className="flex items-center gap-2 rounded-xl border border-status-warning/30 bg-status-warning/10 px-4 py-2.5 text-sm text-text-secondary">
     <AlertTriangle size={15} className="shrink-0 text-status-warning" />
     Couldn't load your KPI registry. Reload to try again.
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

   {/* KPI suggestions are proposed for a source automatically when its harvest ends, so there is no
       button here to ask for them: only what is happening when a source's are not on the page yet.
       A run still working (the list refreshes itself when it lands), or one that failed after a
       harvest -- nobody clicked anything to watch it fail, so this is where they find out. */}
   {suggestionState.generating && (
    <SuggestionsGenerating
     sourceName={
      suggestionState.generating.sourceId ? sourceNames[suggestionState.generating.sourceId] || null : null
     }
    />
   )}
   {failedRun && !suggestionState.generating && (
    <SourceRunFailure
     error={failedRun.error}
     sourceName={failedRun.source_id ? sourceNames[failedRun.source_id] || "a connected source" : null}
     onRetry={retryFailedRun}
     busy={retryState.busy}
     retryError={retryState.error}
    />
   )}

   <KpiDiscovery
    rows={rows}
    areas={areaState.areas}
    areasStatus={areaState.status}
    areasError={areaState.error}
    onRetryAreas={loadAreas}
    focusAreaIds={focusAreaIds}
    onFocusChange={setFocusAreaIds}
    onOpenDetail={setDetailRow}
    onAdopt={handleAdopt}
    onRemove={handleRemove}
    removeState={removeState}
    onDismiss={handleDismissSuggestion}
    dismissState={dismissState}
    adoptingIds={adoptingIds}
    adoptErrors={adoptErrors}
    onCreateDashboard={setPendingRows}
    onToggleActive={handleToggleActive}
    togglingActiveIds={togglingActiveIds}
    profileId={profileId}
    onOpenUseCase={(entry) =>
     navigate(
      entry.kind === "real"
       ? `/use-cases/platform/${encodeURIComponent(entry.id)}`
       : useCaseActivationPath(entry.id)
     )
    }
   />

   {/* The agent's own account of why it proposed nothing, in the schema's refusal channel. An
       empty suggestion list with no reason is indistinguishable from a feature that never ran. */}
   {suggestionState.loaded && !suggestionState.generating && !hasSuggestions && suggestionState.notes && (
    <p className="flex items-start gap-2 rounded-xl border border-dashed border-border-light bg-surface-secondary px-4 py-3 text-xs text-text-secondary">
     <Sparkles size={13} className="mt-0.5 shrink-0 text-status-info" />
     <span>
      <span className="font-semibold text-text-primary">No KPI suggestions.</span> {suggestionState.notes}
     </span>
    </p>
   )}


   {addKpiOpen && (
    <AddKpiModal
     areaState={areaState}
     onRetryAreas={loadAreas}
     onClose={closeAddKpi}
     onCreateManual={createManualKpi}
    />
   )}

   {pendingRows && (
    <CreateDashboardModal
     rows={pendingRows}
     onCancel={() => setPendingRows(null)}
     onCreate={async ({ name, refresh, charts, filterColumns }) => {
      const created = await createDashboardApi(
       buildDashboardWritePayload({ name, refresh, charts, filterColumns }),
      );
      setPendingRows(null);
      navigate(`/kpis/dashboards/${created.id}`);
     }}
    />
   )}

   {detailRow && (
    <KpiDetailPanel
     detailRow={detailRow}
     sourceTableStatus={sourceTableStatusForPanel}
     columnUsage={columnUsageForPanel}
     nameToIdsMap={nameToIdsMap}
     profileId={profileId}
     processLabelById={processLabelById}
     onEdit={() => {
      // Only reachable for a real tree row (the panel hides "Edit KPI" for a suggestion and
      // for a registry row), so categoryIndex/sectionIndex/kpiIndex are genuine positions in
      // the domain-mapping tree, not a suggestion's -1/-1/identifier sentinel -- KpiMappings.jsx
      // reads these back to open AddKpiPanel already pre-targeted at this exact row, instead of
      // landing on the generic, unfiltered table and making the customer re-find it.
      const { categoryIndex: ci, sectionIndex: si, kpiIndex: ki } = detailRow;
      setDetailRow(null);
      navigate("/configure/domain-mapping", { state: { editTarget: { ci, si, ki } } });
     }}
     onClose={() => {
      setRemoveState({ identifier: null, error: null });
      setUpdateState({ identifier: null, saving: false, error: null });
      setDetailRow(null);
     }}
     onAdopt={handleAdopt}
     adopting={!!detailRow.suggestion && adoptingIds.has(detailRow.suggestion.identifier)}
     adoptError={detailRow.suggestion && adoptErrors[detailRow.suggestion.identifier]}
     onDismiss={detailRow.suggested ? handleDismissSuggestion : undefined}
     dismissing={
      !!detailRow.suggestion && dismissState.identifier === detailRow.suggestion.identifier && !dismissState.error
     }
     dismissError={dismissError(detailRow)}
     onSaveEdit={(patch) =>
      detailRow.suggested ? handleEditSuggestionDraft(detailRow, patch) : handleUpdateKpi(detailRow, patch)
     }
     savingEdit={updateState.identifier === detailRow.kpi?.identifier && updateState.saving}
     editError={updateError(detailRow)}
     trend={detailIdentifier ? seriesById[detailIdentifier] || { status: "loading" } : null}
     onTrendRetry={() => {
      if (!detailIdentifier) return;
      const entry = seriesById[detailIdentifier];
      if (entry?.retry === "compute") computeSeries(detailIdentifier, entry?.retryAsOf);
      else loadSeries(detailIdentifier, entry?.data?.dimension || undefined);
     }}
     onTrendCompute={() => detailIdentifier && computeSeries(detailIdentifier)}
     onTrendComputeForDate={(asOf) => detailIdentifier && computeSeries(detailIdentifier, asOf)}
     onTrendPickDimension={(dimension) => detailIdentifier && loadSeries(detailIdentifier, dimension)}
     onRemove={handleRemove}
     removing={removeState.identifier === detailRow.kpi?.identifier && !removeState.error}
     removeError={removeError(detailRow)}
    />
   )}
  </div>
 );
}
