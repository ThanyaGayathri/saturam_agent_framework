import { apiGet, apiJson } from "../lib/api";

/**
 * The `/use-cases` accessors (API contract 1.3.0). One function per operation the builder
 * drives, plus a poller for runs. A run is a resource: `POST .../runs` returns it `queued`
 * and the run's `result` fills in when it finishes, so the screens poll rather than await.
 */

const enc = encodeURIComponent;

/** `POST /use-cases` - the catalog row. Returns a `UseCase`. */
export function createUseCase(body, { signal } = {}) {
  return apiJson("/use-cases", "POST", body, { signal });
}

/** `GET /use-cases/{id}`. */
export function fetchUseCase(useCaseId, { signal } = {}) {
  return apiGet(`/use-cases/${enc(useCaseId)}`, { signal });
}

/** `PATCH /use-cases/{id}` - title, description, autonomy, level. */
export function updateUseCase(useCaseId, body, { signal } = {}) {
  return apiJson(`/use-cases/${enc(useCaseId)}`, "PATCH", body, { signal });
}

/** `PUT /use-cases/{id}/spec` - the form's spec, whole: shape, template, parameters. */
export function saveUseCaseSpec(useCaseId, spec, { signal } = {}) {
  return apiJson(`/use-cases/${enc(useCaseId)}/spec`, "PUT", spec, { signal });
}

/** `PUT /use-cases/{id}/draft` - the builder's working state. */
export function saveUseCaseDraft(useCaseId, draft, { signal } = {}) {
  return apiJson(`/use-cases/${enc(useCaseId)}/draft`, "PUT", draft, { signal });
}

/** `GET /use-cases/{id}/plan` - the compiled workflow's steps. */
export function fetchUseCasePlan(useCaseId, { signal } = {}) {
  return apiGet(`/use-cases/${enc(useCaseId)}/plan`, { signal });
}

/** `POST /use-cases/{id}/preflight` - may it run now, with checks a person can act on. */
export function runUseCasePreflight(useCaseId, { signal } = {}) {
  return apiJson(`/use-cases/${enc(useCaseId)}/preflight`, "POST", undefined, { signal });
}

/** `POST /use-cases/{id}/activate` - compile, store, bump the version. A 409 carries the
 * preflight result that refused it (`err.status === 409`, `err.detail`). */
export function activateUseCase(useCaseId, body, { signal } = {}) {
  return apiJson(`/use-cases/${enc(useCaseId)}/activate`, "POST", body, { signal });
}

/** `POST /use-cases/{id}/pause`. */
export function pauseUseCase(useCaseId, { signal } = {}) {
  return apiJson(`/use-cases/${enc(useCaseId)}/pause`, "POST", undefined, { signal });
}

/** `POST /use-cases/{id}/runs?mode=live|rehearse` - queue one run and get it back. */
export function triggerUseCaseRun(useCaseId, mode = "live", { signal } = {}) {
  return apiJson(`/use-cases/${enc(useCaseId)}/runs?mode=${enc(mode)}`, "POST", undefined, { signal });
}

/** `GET /use-cases/{id}/runs/{run_id}`. */
export function fetchUseCaseRun(useCaseId, runId, { signal } = {}) {
  return apiGet(`/use-cases/${enc(useCaseId)}/runs/${enc(runId)}`, { signal });
}

/** `GET /use-cases/{id}/runs` - newest first. */
export function listUseCaseRuns(useCaseId, { signal } = {}) {
  return apiGet(`/use-cases/${enc(useCaseId)}/runs`, { signal });
}

const TERMINAL = new Set(["succeeded", "failed", "blocked", "cancelled"]);

/**
 * Poll one run until it reaches a terminal status. 2 s between polls for the first half
 * minute, 5 s after that, ceiling `maxMs` (default 10 minutes: a document run is one model
 * call per document). `onTick(run)` sees every intermediate row. Resolves with the final run;
 * rejects with the poll's `ApiError`, or an `Error` named `TimeoutError` at the ceiling.
 */
export async function pollUseCaseRun(useCaseId, runId, { signal, onTick, maxMs = 10 * 60 * 1000 } = {}) {
  const started = Date.now();
  for (;;) {
    const run = await fetchUseCaseRun(useCaseId, runId, { signal });
    onTick?.(run);
    if (TERMINAL.has(run.status)) return run;
    if (Date.now() - started > maxMs) {
      const err = new Error(`Run ${runId} did not finish within ${Math.round(maxMs / 60000)} minutes.`);
      err.name = "TimeoutError";
      throw err;
    }
    const wait = Date.now() - started < 30_000 ? 2000 : 5000;
    await new Promise((resolve, reject) => {
      const timer = setTimeout(resolve, wait);
      signal?.addEventListener("abort", () => { clearTimeout(timer); const e = new Error("aborted"); e.name = "AbortError"; reject(e); }, { once: true });
    });
  }
}
