/**
 * Fire-and-poll a dashboard suggestion run: `start({purpose, kpiIdentifiers, tableNames,
 * chartBudget})` kicks it off, this hook polls `GET /dashboards/suggestions/{run_id}` until
 * it's `succeeded`/`failed`, tight at first and relaxed after half a minute -- the same
 * cadence `KpiDiscoveryPage.jsx`'s inline suggestion-run state machine uses for
 * `POST /kpis/suggestions` (#254), extracted here since this is a fresh call site rather
 * than a second copy of that page's logic. `stage` is the backend's own progress word for a
 * running run (`reading` -> `drafting` -> `guarding` -> `previewing`), so the waiting screen
 * can show real progress rather than a spinner.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import {
  fetchDashboardSuggestionRun,
  startDashboardSuggestionRun,
} from "../services/dashboard-suggestions-api";

const POLL_MS = 2000;
const POLL_RELAXED_MS = 5000;
const POLL_RELAX_AFTER_MS = 30_000;
// The backend's own default bound (180s) plus the grace it allows before it closes a lost run,
// so a well-behaved run is never abandoned by the client before the server has said what
// became of it.
const CEILING_MS = 4 * 60 * 1000;

const IDLE_STATE = { status: "idle", stage: null, result: null, error: null };

export function useDashboardSuggestionRun() {
  const [state, setState] = useState(IDLE_STATE);
  const abortRef = useRef(null);
  const pollTimerRef = useRef(null);

  useEffect(
    () => () => {
      abortRef.current?.abort();
      window.clearTimeout(pollTimerRef.current);
    },
    [],
  );

  const start = useCallback((request) => {
    abortRef.current?.abort();
    window.clearTimeout(pollTimerRef.current);
    const controller = new AbortController();
    abortRef.current = controller;
    const startedAt = Date.now();
    setState({ status: "loading", stage: null, result: null, error: null });

    const fail = (message) =>
      setState({ status: "error", stage: null, result: null, error: message });
    const failWith = (err) => {
      if (err?.name === "AbortError") return;
      fail(err?.detail || err?.message || "Something went wrong.");
    };

    const poll = (runId) => {
      if (controller.signal.aborted) return;
      if (Date.now() - startedAt > CEILING_MS) {
        controller.abort();
        fail("The run is taking longer than expected. Try again in a few minutes.");
        return;
      }
      fetchDashboardSuggestionRun(runId, { signal: controller.signal })
        .then((run) => {
          if (controller.signal.aborted) return;
          if (run?.status === "succeeded") {
            setState({ status: "ready", stage: "done", result: run.result, error: null });
            return;
          }
          if (run?.status === "failed") {
            fail(run.error || "The run failed without saying why.");
            return;
          }
          setState((prev) =>
            prev.stage === (run?.stage ?? null) ? prev : { ...prev, stage: run?.stage ?? null },
          );
          const delay =
            Date.now() - startedAt > POLL_RELAX_AFTER_MS ? POLL_RELAXED_MS : POLL_MS;
          pollTimerRef.current = window.setTimeout(() => poll(runId), delay);
        })
        .catch(failWith);
    };

    startDashboardSuggestionRun(request, { signal: controller.signal })
      .then((run) => {
        if (controller.signal.aborted) return;
        if (!run?.id) {
          fail("The run was not accepted.");
          return;
        }
        poll(run.id);
      })
      .catch(failWith);
  }, []);

  const reset = useCallback(() => {
    abortRef.current?.abort();
    window.clearTimeout(pollTimerRef.current);
    setState(IDLE_STATE);
  }, []);

  return { ...state, start, reset };
}
