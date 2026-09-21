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
/** Delete a use case the person built, with its runs and golden cases (204). */
export function deleteUseCase(useCaseId, { signal } = {}) {
  return apiJson(`/use-cases/${enc(useCaseId)}`, "DELETE", undefined, { signal });
}

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
export function triggerUseCaseRun(useCaseId, mode = "live", { signal, request } = {}) {
  return apiJson(`/use-cases/${enc(useCaseId)}/runs?mode=${enc(mode)}`, "POST", request || undefined, { signal });
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

/** The document kinds that ship with the platform (K17). */
export function fetchExtractionTemplates({ signal } = {}) {
  return apiGet("/extraction-templates", { signal });
}

/** Draft a template's fields from a description and, optionally, an example document (K17).
 *  `example` is `{ text }` or `{ documentB64 }`. */
export function draftExtractionTemplate({ description, label, example }, { signal } = {}) {
  const body = { description, label: label || undefined, example_text: example?.text || undefined, example_document_b64: example?.documentB64 || undefined };
  return apiJson("/extraction-templates/draft", "POST", body, { signal });
}

/** The rules a template's own fields call for, from the platform (K17). The interface has the
 *  same suggester in `domain/guardrail-rules.js` for when the API is not reachable. */
export function suggestGuardrailRules(template, { signal } = {}) {
  return apiJson("/guardrail-rules/suggest", "POST", template, { signal });
}

/** The objects the write target lets the platform create, read live (K18). */
export function fetchTargetObjects(target = "salesforce", { signal } = {}) {
  return apiGet(`/targets/${enc(target)}/objects`, { signal });
}

/** One target object's createable fields, read live (K18). */
export function describeTargetObject(target, object, { signal } = {}) {
  return apiGet(`/targets/${enc(target)}/objects/${enc(object)}`, { signal });
}

/** A record mapping proposed from names over the live fields (K18). */
export function suggestRecordMapping({ template, parent, child, label }, { signal } = {}) {
  return apiJson("/record-mappings/suggest", "POST", { template, parent, child: child || null, label: label || undefined }, { signal });
}


/** The messages in the connected mailbox that match the use case's filter (K19). */
export function fetchMailboxMessages(useCaseId, { signal } = {}) {
  return apiGet(`/use-cases/${enc(useCaseId)}/mailbox/messages`, { signal });
}

/** Golden cases (K19): list, upload, delete, accept a run's output as expected. */
export function fetchGoldenCases(useCaseId, { signal } = {}) {
  return apiGet(`/use-cases/${enc(useCaseId)}/cases`, { signal });
}
export function createGoldenCase(useCaseId, body, { signal } = {}) {
  return apiJson(`/use-cases/${enc(useCaseId)}/cases`, "POST", body, { signal });
}
export function deleteGoldenCase(useCaseId, caseId, { signal } = {}) {
  return apiJson(`/use-cases/${enc(useCaseId)}/cases/${enc(caseId)}`, "DELETE", undefined, { signal });
}
export function acceptGoldenCaseOutput(useCaseId, caseId, { signal } = {}) {
  return apiJson(`/use-cases/${enc(useCaseId)}/cases/${enc(caseId)}/accept`, "POST", undefined, { signal });
}

/** One live connectivity probe (K19): mailbox, target, master_data, model, golden. */
export function runUseCasePreflightProbe(useCaseId, probe, { signal } = {}) {
  return apiJson(`/use-cases/${enc(useCaseId)}/preflight/${enc(probe)}`, "POST", undefined, { signal });
}

/** Change a case's label, sender or expected output (K19). */
export function updateGoldenCase(useCaseId, caseId, body, { signal } = {}) {
  return apiJson(`/use-cases/${enc(useCaseId)}/cases/${enc(caseId)}`, "PATCH", body, { signal });
}

/** The platform's use cases (K20). `source: user` is what the interface created. */
export function listUseCases({ status, source, signal } = {}) {
  const params = new URLSearchParams();
  if (status) params.set("status", status);
  if (source) params.set("source", source);
  const query = params.toString();
  return apiGet(`/use-cases${query ? `?${query}` : ""}`, { signal });
}

/** Execution health for every use case, from the platform's run history (K20). */
export function fetchExecutionHealth({ signal } = {}) {
  return apiGet("/monitoring/execution-health", { signal });
}

/** Schedules that should have fired and did not (K20). */
export function fetchSilentSchedules({ signal } = {}) {
  return apiGet("/monitoring/schedules/silent", { signal });
}

/** The spec the platform holds for a use case (K20), for editing it again. */
export function fetchUseCaseSpec(useCaseId, { signal } = {}) {
  return apiGet(`/use-cases/${enc(useCaseId)}/spec`, { signal });
}

/** The Analytics Automation form's suggestion from the ask (K23): `POST
 *  /use-cases/analytics/suggest` -- the registry's KPIs that match the words and have gold
 *  behind them, their breakdowns, baseline, cadence, outputs and recipients. Deterministic;
 *  nothing stored. */
export function suggestAnalyticsUseCase(question, { recipient, signal } = {}) {
  return apiJson("/use-cases/analytics/suggest", "POST", { question, recipient: recipient || undefined }, { signal });
}

/** Which of the target's objects a document of this template becomes (K24): `POST
 *  /record-mappings/suggest-objects`, ranked with reasons; a shipped mapping named where one
 *  fits the pair. */
export function suggestTargetObjects({ template, description, target }, { signal } = {}) {
  return apiJson("/record-mappings/suggest-objects", "POST", { template, description: description || undefined, target: target || undefined }, { signal });
}

/** `GET /use-cases/{id}/recommendations` and `GET /recommendations` (1.13.0, K26): the records the
 *  at-risk kind hands to Decision Intelligence, with the counts per status. */
export function listRecommendations({ useCaseId, status, kind, level, owner, limit } = {}, { signal } = {}) {
  const query = Object.entries({ status, kind, level, owner, limit, ...(useCaseId ? {} : {}) }).filter(([, v]) => v != null && v !== "").map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join("&");
  const path = useCaseId ? `/use-cases/${encodeURIComponent(useCaseId)}/recommendations` : "/recommendations";
  return apiJson(`${path}${query ? `?${query}` : ""}`, "GET", undefined, { signal });
}

/** `POST /recommendations/{id}/status` (1.13.0): what a person or Decision Intelligence writes back. */
export function setRecommendationStatus(recommendationId, { status, note, by }) {
  return apiJson(`/recommendations/${encodeURIComponent(recommendationId)}/status`, "POST", { status, note: note || "", by: by || "" });
}

/** `POST /use-cases/analytics/diagnosis-options` (K25): what one KPI can be searched across and what the data can bear. */
export function fetchDiagnosisOptions({ kpiIdentifier, window, asOf }, { signal } = {}) {
  return apiJson("/use-cases/analytics/diagnosis-options", "POST", { kpi_identifier: kpiIdentifier, window: window || null, as_of: asOf || null }, { signal });
}
