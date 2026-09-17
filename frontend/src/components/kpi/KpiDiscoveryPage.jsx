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
import SuggestionRunPanel from "./SuggestionRunPanel";
import CreateDashboardModal from "./CreateDashboardModal";
import AddKpiModal from "./add-kpi/AddKpiModal";
import { buildDashboardWritePayload } from "../../storage/dashboards";
import { createDashboardApi } from "../../services/dashboards-api";
import { rowFromSuggestion } from "./suggested-candidates";
import { metricsUnlockedBy } from "../configure/source-model-state";
import {
 fetchKpiList,
 startKpiSuggestionRun,
 fetchKpiSuggestionRun,
 createKpi,
 fetchKpiSeries,
 computeKpi,
 deleteKpi,
 updateKpi,
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

const IDLE_SUGGESTION_STATE = {
 status: "idle",
 suggestions: [],
 notes: "",
 error: null,
 runId: null,
 // The request a steered run was started with ("Add KPI" -> AI generated, contract 1.2.3);
 // null for a plain "Suggest KPIs" run. Echoed by the row, so a restore recovers it too.
 ask: null,
 startedAt: null,
 phase: null,
 typicalDuration: null,
 serverStartedAt: null,
 serverFinishedAt: null,
 clientSeconds: null,
 receipt: false,
};

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
 *  1. SUGGESTED candidates -- metrics not in the registry yet, proposed live by a run of the
 *     `kpi_suggestion` agent (`POST /kpis/suggestions`, polled until done -- F1, #234 and #254)
 *     when the customer asks for them. Nothing is started until "Suggest KPIs" is clicked:
 *     unlike the registry/real-area fetches below, this is a minutes-long frontier-tier model
 *     run, not something to run unprompted on page load. They
 *     are shown as adoptable candidates rather than as results, because a metric that cannot be
 *     edited, governed or reproduced is not something anyone should build a dashboard on --
 *     "adopt" calls `POST /kpis` for real, and only once the suggestion's own feasibility says
 *     `ready` (a `partial`/`blocked` one has nothing valid to adopt).
 *  2. REAL registry KPIs (C1, #232) -- `GET /kpis`, merged in the same additive way suggested
 *     candidates are. A real row carries a real `kpi_identifier` and real `feasibility` from
 *     day one, so selecting one and building a dashboard from it (D2, #233) actually reaches
 *     the real API.
 */

const FOCUS_KEY = "retailgpt:kpi-focus-areas";

// The in-flight or last-completed suggestion run's id, mirrored into `?run=` (#276 -- suggestions
// were lost on navigating away and back, since `suggestionState` is plain component state that
// resets on remount). Same pattern `?unlocked=` already uses on this page: the URL, not
// localStorage, because a run id is also worth sharing/bookmarking ("here's what it found"),
// and it composes with browser back/forward the way local storage cannot.
const RUN_PARAM = "run";
// `?add=1` opens the "Add KPI" modal. In the URL rather than state because the button that
// opens it lives in the hub header (`KpisHubLayout.jsx`), outside this page; and, like `?run=`,
// it composes with back/forward -- closing the modal is a `replace`, so Back never reopens it.
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

 // The live suggestion run (F1, #234): idle until "Suggest KPIs" is clicked, since each run is
 // two real frontier-tier model calls that take minutes, not something to fire on mount. It is
 // a run to poll, not a response to await (#254): `runId` is the row `POST /kpis/suggestions`
 // returned and `startedAt` when this click started it, so the panel can show elapsed time
 // and the poll loop can give up at a ceiling. `phase` and `typicalDuration` are what the row
 // says on each poll -- the stage the run is in and how long runs usually take here -- which
 // is what `SuggestionRunPanel` turns a bare wait into. `notes` is the skill's own refusal
 // channel -- why it proposed nothing, not a generic empty state. `receipt` keeps the one-line
 // summary up after a run lands until it is dismissed.
 // Seeded from `?run=` when present, not `IDLE_SUGGESTION_STATE` unconditionally: the
 // URL-sync effect and the restore effect below both run on this same mount, and if state
 // started disagreeing with the URL (`runId: null` while the URL already names one), the sync
 // effect would see that as "the run was cleared" and strip the param before the restore effect
 // ever got to read it -- effects run in one commit, before any state update from either has
 // re-rendered. Starting them in agreement removes the race instead of relying on ordering.
 const [suggestionState, setSuggestionState] = useState(() => {
  const restoringRunId = params.get(RUN_PARAM);
  return restoringRunId
   ? { ...IDLE_SUGGESTION_STATE, status: "loading", runId: restoringRunId, startedAt: Date.now() }
   : IDLE_SUGGESTION_STATE;
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
 // A registry row's description/sql edit (`PATCH /kpis/{id}`), one at a time from whichever
 // panel is open. A suggestion's edit never reaches here -- it is a local patch to
 // `suggestionState.suggestions`, always synchronous, with nothing to load or fail.
 const [updateState, setUpdateState] = useState({ identifier: null, saving: false, error: null });

 // The real registry (C1, #232) -- fetched once, additive, never blocking: the seed/suggested
 // rows above render immediately regardless of this state. A failure is a small note, not a
 // crash -- the same posture `DashboardsPage.jsx`'s own real-data fetch already takes.
 const [realKpiState, setRealKpiState] = useState({ status: "loading", kpis: [] });
 // The business areas, from `GET /business-areas` on page load: the options the focus prompt
 // offers (supported ones selectable, with K9's evidence; the rest greyed with its reason) and
 // the labels real rows are grouped under. Held here, not in the per-profile localStorage list
 // the rest of the app still reads -- that list is a different vocabulary and is left alone.
 const [areaState, setAreaState] = useState({ status: "loading", areas: [], error: null });

 // The focus the agents are given (contract 1.1.8): the chosen areas, filtered against the
 // deployment's real vocabulary (a stored focus can predate it -- an unknown id would be
 // refused, and a refused run helps nobody). No focus at all sends nothing: the backend already
 // treats that as "no focus" and leaves the prompt untouched, so there is nothing to prioritize
 // by listing every supported area (review).
 //
 // While the vocabulary is still loading, filtering a genuine focus against it would empty it
 // out and silently widen the run to "no focus" instead -- the "Suggest KPIs" button below is
 // disabled for that window, so this case is a fallback, not the common path. If the fetch
 // failed outright, there is no vocabulary to filter against at all; send the chosen ids as-is
 // and let the backend's own validation be the source of truth (review).
 const suggestBusinessAreas = useMemo(() => {
  if (!focusAreaIds.length) return undefined;
  if (areaState.status === "loading") return undefined;
  if (areaState.status === "error") return focusAreaIds;
  const known = new Set(areaState.areas.map((a) => a.id));
  return focusAreaIds.filter((id) => known.has(id));
 }, [focusAreaIds, areaState]);

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
  (identifier) => {
   seriesAbortRefs.current.get(identifier)?.abort();
   const controller = new AbortController();
   seriesAbortRefs.current.set(identifier, controller);
   patchSeries(identifier, { status: "computing", error: null, retry: "compute" });
   computeKpi(identifier, { signal: controller.signal })
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

 // `runSuggestions`/`handleAdopt` are click-triggered, not effects, but they need the same
 // unmount guard the two effects below already have -- an `AbortController` per in-flight
 // request, forwarded as `signal` (both `apiGet`/`apiJson` already support one), aborted on
 // unmount so navigating away mid-request doesn't resolve into a `setState` on a gone component.
 const suggestAbortRef = useRef(null);
 // The pending poll timer between two `GET /kpis/suggestions/{run_id}` calls -- cleared on
 // unmount and on a new click, so a stale loop never wakes up and polls a run nobody wants.
 const suggestPollTimerRef = useRef(null);
 // The last phase any poll (or the 202) reported for the in-flight run, read by the ceiling
 // timeout below -- state updates are async, so a ref is what a synchronous check can read.
 const lastPhaseRef = useRef(null);
 const adoptAbortRefs = useRef(new Map());
 const removeAbortRef = useRef(null);
 const updateAbortRef = useRef(null);
 // The manual "Add KPI" form's `POST /kpis` (`onCreateManual` below) -- one at a time, since
 // the modal refuses to close while it is in flight.
 const createAbortRef = useRef(null);
 useEffect(
  () => () => {
   suggestAbortRef.current?.abort();
   window.clearTimeout(suggestPollTimerRef.current);
   for (const controller of adoptAbortRefs.current.values()) controller.abort();
   for (const controller of seriesAbortRefs.current.values()) controller.abort();
   removeAbortRef.current?.abort();
   updateAbortRef.current?.abort();
   createAbortRef.current?.abort();
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

 // A once-a-second re-render while a run is in flight, so the panel's elapsed counter ticks
 // and its time-based bar moves -- a long run reads as alive rather than stuck. Stops (and
 // resets) the moment it isn't.
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

 // `phase` is the last stage a poll reported before the run failed or was given up on -- the
 // backend keeps it on a `failed` row for this (see `watchRun`'s ceiling check for the
 // client-side case). Null when nothing was ever reported. Component-scope, not local to
 // `watchRun`, so `runSuggestions`' own two failure paths (POST rejected outright, or accepted
 // with no id) can reuse it too instead of hand-writing the same `IDLE_SUGGESTION_STATE`
 // literal a second and third time -- a future field added here (already true of `phase`)
 // would otherwise have to be remembered in three places.
 //
 // `ask` survives the failure on purpose: the modal that collected it closed the moment the
 // run started, so the failure panel's "For: ..." line and its "Try again" (which resends it)
 // are the only way a steered request outlives a timeout or a `failed` row. `runId` still
 // resets, so the URL-sync effect below drops `?run=` exactly as before.
 const fail = (message, phase = null) =>
  setSuggestionState((prev) => ({
   ...IDLE_SUGGESTION_STATE,
   status: "error",
   error: message,
   phase,
   ask: prev.ask,
  }));
 // What every poll (and the 202 from starting a run) tells the panel: the stage the run is in
 // and the deployment's typical duration. Either may be null on a given read; keep what we had.
 // Pure, as a functional setState updater must be (React may invoke it more than once for the
 // same commit). Component-scope for the same reason as `fail` above -- `runSuggestions`'
 // initial POST response is exactly this same merge, and used to hand-write it separately.
 const absorb = (run) => (prev) => ({
  ...prev,
  runId: run.id || prev.runId,
  phase: run.phase ?? prev.phase,
  typicalDuration: run.typical_duration_seconds ?? prev.typicalDuration,
  ask: run.ask ?? prev.ask,
 });

 // Poll one run to a terminal state (#254). Shared by a fresh click (`runSuggestions`) and a
 // page-load restore (the effect below, from `?run=` in the URL) -- both are "watch this run
 // id until it's done," differing only in how they got the id and what "can't reach it" should
 // do. One `AbortController` covers the whole loop, aborted on unmount, a second click, or a
 // restore superseding it; the timer between polls is cleared alongside it. The backend closes
 // a lost run as `failed` on its own, so the loop normally ends on the row's status; the
 // ceiling below is for the contract stub, which serves the `queued` example forever, and for
 // anything else that never turns terminal -- a spinner with no end is the failure mode this
 // whole shape exists to remove.
 const watchRun = useCallback((runId, startedAt, controller, { onLost } = {}) => {
  // A run this client can no longer reach -- a ceiling timeout, a `failed` row, or a network
  // rejection alike -- takes the same `(message, phase)` shape as `fail`, so every terminal-
  // but-unreachable outcome routes through one place: a run just clicked shows why, the same
  // as ever; a run recovered from the URL (`onLost` below) says nothing and quietly goes back
  // to idle -- the customer never asked for this one, so failing to resume it should not read
  // as a failure of anything they did.
  const lost = onLost || fail;
  // The ceiling is checked against the run's own start, not necessarily this call's: a restore
  // seeds `startedAt` from client mount time (when the page loaded), not the original click, so
  // a run still genuinely stuck server-side would otherwise get a fresh `SUGGEST_CEILING_MS`
  // budget on every revisit and the client-side give-up below would never fire. Corrected to
  // the row's own `started_at` (set once a run leaves `queued`, per the contract) the moment a
  // poll reports one; stays at the client's own `startedAt` until then -- which is already
  // correct for a `queued` run (nothing to correct it with yet) and for a fresh click (the two
  // are the same instant anyway).
  let ceilingStartedAt = startedAt;

  const poll = (id) => {
   if (controller.signal.aborted) return;
   if (Date.now() - ceilingStartedAt > SUGGEST_CEILING_MS) {
    controller.abort();
    // The run may well have reported a stage on an earlier poll -- the ceiling is the
    // client giving up, not a run with no known phase, so carry the last one we saw.
    lost("The run is taking longer than expected. Try again in a few minutes.", lastPhaseRef.current);
    return;
   }
   fetchKpiSuggestionRun(id, { signal: controller.signal })
    .then((run) => {
     if (controller.signal.aborted) return;
     const serverStartedAt = run?.started_at ? Date.parse(run.started_at) : NaN;
     if (!Number.isNaN(serverStartedAt)) ceilingStartedAt = serverStartedAt;
     if (run?.status === "succeeded") {
      // Composed on top of `absorb`, not a second hand-written merge: every field the row
      // echoes (`ask` today; whatever comes next) is picked up here the same as on a live
      // poll. A `?run=` restore of a finished run lands straight in this branch -- the
      // initializer seeded `ask: null` and no poll ran `absorb` first -- and without this the
      // receipt's "For: ..." line was missing in the exact case the echo was added for.
      setSuggestionState((prev) => ({
       ...absorb(run)(prev),
       status: "ready",
       suggestions: run.result?.suggestions || [],
       notes: run.result?.notes || "",
       error: null,
       runId: id,
       startedAt: null,
       phase: null,
       serverStartedAt: run.started_at || null,
       serverFinishedAt: run.finished_at || null,
       clientSeconds: (Date.now() - startedAt) / 1000,
       receipt: true,
      }));
      return;
     }
     if (run?.status === "failed") {
      lost(run.error || "The run failed without saying why.", run.phase);
      return;
     }
     lastPhaseRef.current = run.phase ?? lastPhaseRef.current;
     setSuggestionState(absorb(run));
     // Tight at first (a fast run should feel fast), then relaxed: after half a minute the
     // run is clearly a long one and a poll every few seconds is plenty.
     const delay =
      Date.now() - startedAt > SUGGEST_POLL_RELAX_AFTER_MS ? SUGGEST_POLL_RELAXED_MS : SUGGEST_POLL_MS;
     suggestPollTimerRef.current = window.setTimeout(() => poll(id), delay);
    })
    .catch((err) => {
     if (err?.name === "AbortError") return;
     lost(err?.detail || err?.message || "Something went wrong.");
    });
  };
  poll(runId);
  // eslint-disable-next-line react-hooks/exhaustive-deps
 }, []);

 // Abort whatever this run's watch was doing, drop its poll timer, and hand back a fresh
 // `AbortController` with `lastPhaseRef` reset -- shared by a fresh click (`runSuggestions`)
 // and a page-load restore (the effect below), so this bookkeeping only needs to stay correct
 // in one place. `startedAt` is deliberately not part of it: the two callers seed it
 // differently (a fresh click uses "now"; a restore reuses the run's already-seeded one).
 const beginWatch = () => {
  suggestAbortRef.current?.abort();
  window.clearTimeout(suggestPollTimerRef.current);
  const controller = new AbortController();
  suggestAbortRef.current = controller;
  lastPhaseRef.current = null;
  return controller;
 };

 // Start a run, then watch it. Same "one AbortController for the whole loop" rule as
 // `watchRun` -- a second click here cancels wherever the previous run's watch happened to be.
 // `options.ask` is the request a steered run carries ("Add KPI" -> AI generated, or the
 // panel's "Suggest again" / "Try again" repeating one); the idle bar passes `{}` and starts
 // a plain run. Anything that is not a non-blank string (absent, null, a stray event) reads
 // as no request.
 const runSuggestions = (options) => {
  const ask = options && typeof options.ask === "string" && options.ask.trim() ? options.ask.trim() : null;
  const controller = beginWatch();
  const startedAt = Date.now();
  setSuggestionState({ ...IDLE_SUGGESTION_STATE, status: "loading", startedAt, ask });

  startKpiSuggestionRun(suggestBusinessAreas, { ask, signal: controller.signal })
   .then((run) => {
    if (controller.signal.aborted) return;
    if (!run?.id) {
     fail("The run was not accepted.");
     return;
    }
    lastPhaseRef.current = run.phase ?? lastPhaseRef.current;
    setSuggestionState(absorb(run));
    watchRun(run.id, startedAt, controller);
   })
   .catch((err) => {
    if (controller.signal.aborted || err?.name === "AbortError") return;
    fail(err?.detail || err?.message || "Something went wrong.");
   });
 };

 // Keeps `?run=` in step with `runId`: set while a run is in flight or holds a succeeded
 // result, gone the moment it doesn't (cancelled, or `fail` resets to `IDLE_SUGGESTION_STATE`,
 // whose `runId` is null). A failed run is therefore never left in the URL to restore into --
 // restoring a page-load error for something nobody just clicked is not the point of this.
 useEffect(() => {
  const current = params.get(RUN_PARAM);
  const next = suggestionState.runId;
  if (next === current) return;
  if (next) params.set(RUN_PARAM, next);
  else params.delete(RUN_PARAM);
  setParams(params, { replace: true });
  // eslint-disable-next-line react-hooks/exhaustive-deps
 }, [suggestionState.runId]);

 // Resume a run named in the URL, on mount -- the other half of the effect above. Silent on
 // failure (`onLost` below): a run this client cannot reach for whatever reason (the row aged
 // out, a network blip) just falls back to the idle "Suggest KPIs" button rather than greeting
 // a returning customer with an error for a run they did not just start.
 //
 // A per-invocation `AbortController`, not a ref flag guarding "already ran": `React.StrictMode`
 // (main.jsx) mounts, cleans up and remounts every effect once in dev, and a ref flag would
 // have the first (thrown-away) pass mark itself done before the real one ever gets to run --
 // the same hazard the `fetchKpiList` effect above avoids with its own per-invocation `cancelled`
 // closure. Returning a cleanup that aborts this invocation's controller is what lets the real
 // mount proceed normally after the fake one is torn down.
 //
 // On-mount-only by design (empty deps below): this does not track live changes to `?run=`.
 // Nothing links to `?run=` today (the URL-sync effect above only ever writes it, via
 // `replace: true`), so a same-instance client-side navigation to a *different* `?run=` value
 // without an unmount cannot happen yet -- but a future in-app link that does would leave the
 // stale run on screen instead of restoring the new one, since this effect would not re-fire.
 useEffect(() => {
  const runId = params.get(RUN_PARAM);
  if (!runId) return undefined;
  const controller = beginWatch();
  // The `useState` initializer above already seeded `suggestionState` with `status: "loading"`,
  // this `runId`, and a `startedAt` for this exact restore -- nothing left to set here, so this
  // just starts watching the run the state already names, off the already-seeded `startedAt`
  // rather than recomputing (and re-setting) a second, slightly different one.
  const startedAt = suggestionState.startedAt;
  watchRun(runId, startedAt, controller, { onLost: () => setSuggestionState(IDLE_SUGGESTION_STATE) });
  return () => controller.abort();
  // eslint-disable-next-line react-hooks/exhaustive-deps
 }, []);

 // Cancel stops the waiting, not the run: the server finishes (or times out) on its own and
 // the row is simply never read again. The page goes back to the idle bar, with whatever
 // suggestions an earlier run left in place.
 const cancelSuggestions = () => {
  suggestAbortRef.current?.abort();
  window.clearTimeout(suggestPollTimerRef.current);
  setSuggestionState((prev) => ({ ...prev, status: "idle", startedAt: null, phase: null, runId: null }));
 };

 // Also clears `runId`, not just the receipt flag: the URL-sync effect keeps `?run=<id>` in
 // the URL for as long as `runId` is set, and a restore of a completed run always sets
 // `receipt: true` -- so leaving `runId` in place here would have a dismissed receipt reappear
 // the next time this run is restored (navigate away and back, or reopen a bookmark). Clearing
 // it leaves nothing to restore into.
 const dismissReceipt = () =>
  setSuggestionState((prev) => ({ ...prev, receipt: false, runId: null }));

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
    // The two suggestion-era sentences the panel shows ("Why the model proposed it" / "What
    // value it provides") go into the registry with the KPI, so the panel keeps saying them
    // after adopt -- on reload and for every viewer -- instead of only for this session.
    rationale: suggestion.rationale ?? null,
    business_value: suggestion.business_value ?? null,
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
    setRemoveState({
     identifier: null,
     error: err?.detail || err?.message || "Couldn't remove this KPI.",
    });
   })
   .finally(() => {
    if (removeAbortRef.current === controller) removeAbortRef.current = null;
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

 const realAreaLabelById = useMemo(
  () => Object.fromEntries(areaState.areas.map((a) => [a.id, a.label])),
  [areaState.areas]
 );
 const realAreaLabel = (id) => realAreaLabelById[id] || id || "";
 // What the AI step of "Add KPI" tells the person their focus is -- the same label lookup
 // every registry row's grouping already uses.
 const focusAreaLabels = useMemo(
  () => (suggestBusinessAreas || []).map(realAreaLabel).filter(Boolean),
  // eslint-disable-next-line react-hooks/exhaustive-deps -- `realAreaLabel` closes over `realAreaLabelById` only.
  [suggestBusinessAreas, realAreaLabelById]
 );

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
   // Null for a hand-authored KPI or one adopted before the registry kept them; the panel
   // renders the section only when at least one is present.
   rationale: k.rationale || "",
   businessValue: k.business_value || "",
   fromRegistry: true,
   // Carried through only so an edit (`handleUpdateKpi` below) can resend the required fields
   // `PATCH /kpis/{id}` demands every time -- not shown anywhere in the panel itself.
   sql: k.sql,
   grain: k.grain,
   sourceTables: k.source_tables || [],
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
 const removeError = (row) => (row?.kpi?.fromRegistry ? removeState.error : null);
 // Same reasoning as `removeError`, for `handleUpdateKpi`'s save.
 const updateError = (row) =>
  row?.kpi?.fromRegistry && updateState.identifier === row.kpi.identifier ? updateState.error : null;

 const realRows = useMemo(
  () => realKpiState.kpis.map((k, index) => rowFromRegistryKpi(k, index)),
  // eslint-disable-next-line react-hooks/exhaustive-deps -- `rowFromRegistryKpi` closes over `realAreaLabelById` only.
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

   {/* Suggest KPIs (F1, #234): a real, live run of the frontier-tier `kpi_suggestion` agent --
       not fired on mount, since it takes minutes and is something the user should ask for.
       While it runs, the bar is the run's own status panel (stage, expectation, cancel); when
       it lands, a receipt; on failure, the reason and a retry. */}
   <SuggestionRunPanel
    run={suggestionState}
    elapsed={suggestElapsed}
    onStart={runSuggestions}
    onCancel={cancelSuggestions}
    onDismiss={dismissReceipt}
   />

   {/* A saved focus whose ids are all absent from the real vocabulary (deployment changed,
       or the two vocabularies genuinely disagree) degrades to no focus rather than widening
       to "everything" -- but that degrade should not be silent (review). */}
   {focusAreaIds.length > 0 && suggestBusinessAreas?.length === 0 && (
    <div className="flex items-center gap-2 rounded-xl border border-status-warning/30 bg-status-warning/10 px-4 py-2.5 text-sm text-text-secondary">
     <AlertTriangle size={15} className="shrink-0 text-status-warning" />
     Your saved focus areas don't match this deployment's business-area list — suggesting across all areas instead.
    </div>
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


   {addKpiOpen && (
    <AddKpiModal
     areaState={areaState}
     onRetryAreas={loadAreas}
     focusAreaLabels={focusAreaLabels}
     runInFlight={suggestionState.status === "loading"}
     onClose={closeAddKpi}
     onStartAi={(ask) => {
      closeAddKpi();
      runSuggestions({ ask });
     }}
     onCreateManual={createManualKpi}
    />
   )}

   {pendingRows && (
    <CreateDashboardModal
     rows={pendingRows}
     onCancel={() => setPendingRows(null)}
     onCreate={async ({ name, refresh, charts, businessAreaFilter }) => {
      const created = await createDashboardApi(
       buildDashboardWritePayload({ name, refresh, charts, businessAreaFilter }),
      );
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
     onSaveEdit={(patch) =>
      detailRow.suggested ? handleEditSuggestionDraft(detailRow, patch) : handleUpdateKpi(detailRow, patch)
     }
     savingEdit={updateState.identifier === detailRow.kpi?.identifier && updateState.saving}
     editError={updateError(detailRow)}
     trend={detailIdentifier ? seriesById[detailIdentifier] || { status: "loading" } : null}
     onTrendRetry={() => {
      if (!detailIdentifier) return;
      const entry = seriesById[detailIdentifier];
      if (entry?.retry === "compute") computeSeries(detailIdentifier);
      else loadSeries(detailIdentifier, entry?.data?.dimension || undefined);
     }}
     onTrendCompute={() => detailIdentifier && computeSeries(detailIdentifier)}
     onTrendPickDimension={(dimension) => detailIdentifier && loadSeries(detailIdentifier, dimension)}
     onRemove={handleRemove}
     removing={removeState.identifier === detailRow.kpi?.identifier}
     removeError={removeError(detailRow)}
    />
   )}
  </div>
 );
}
