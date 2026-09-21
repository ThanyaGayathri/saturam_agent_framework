/**
 * The Configure > Data sources backend seam: the `/configure/sources` routes (#305, contract
 * 1.2.x) plus the one framework endpoint that turns a registered source into a modelled one --
 * `POST /workflows/harvest-source/runs` (#306). Same `src/lib/api.js` client and the same
 * run-then-poll posture `services/dashboard-suggestions-api.js` uses: a harvest reads a whole
 * schema and writes it to the graph, so it is a run to start and a state to watch on the
 * source itself, never a response to await.
 *
 * Credentials travel one way. `createSource`/`updateSource` send `password`/`api_key`; every
 * response is an `InternalSource`, which carries `has_credentials` and never the secret.
 */

import { apiForm, apiGet, apiJson } from "../lib/api";

/** The `source_type` values the contract's `SourceType` enum accepts. The Configure form offers
 * more systems than this (`domain/configure-catalogs.js`'s `INTERNAL_SOURCE_TYPES`); those it
 * cannot register yet are filtered out of the picker rather than mapped to `other` behind the
 * user's back. */
export const API_SOURCE_TYPES = ["postgres", "snowflake", "sap_s4hana", "salesforce", "mysql", "bigquery", "other"];

/** Source types #306's harvest workflow will attempt (`HARVESTABLE_SOURCE_TYPES` on the
 * backend). Anything else registers and can be tested, but "Connect" stops at registration and
 * the card says "Not modelled" rather than starting a run the executor would refuse. */
export const HARVESTABLE_SOURCE_TYPES = new Set(["postgres", "snowflake"]);

/** The workflow id `build_workflows()` registers the per-source harvest under. */
export const HARVEST_SOURCE_WORKFLOW_ID = "harvest-source";

/** `GET /configure/sources` - every registered source, as `InternalSource[]` (`{id, source_type,
 * name, host, port, database_name, schema, auth_method, username, ssl_mode, permission_level,
 * has_credentials, harvest_state, last_harvest_id, last_harvest_at, created_at}`). This is also
 * what the harvest poll re-reads: `harvest_state` moves `building` -> `modelled` /
 * `needs_review` / `error` on the row itself. */
export function fetchSources({ signal } = {}) {
  return apiGet("/configure/sources", { signal });
}

/** `GET /configure/sources/{id}` - one source. Throws `ApiError` (404) for an unknown id. */
export function fetchSource(sourceId, { signal } = {}) {
  return apiGet(`/configure/sources/${encodeURIComponent(sourceId)}`, { signal });
}

/** `POST /configure/sources` - register a source (an `InternalSourceWrite`, credential
 * included) and get it back as an `InternalSource`, `harvest_state: not_started`. A 422 carries
 * the backend's own reason (a `username_password` source with no password, say) as `detail`. */
export function createSource(body, { signal } = {}) {
  return apiJson("/configure/sources", "POST", body, { signal });
}

/** `PATCH /configure/sources/{id}` - a partial update (`InternalSourcePatch`). Omitted fields
 * are unchanged. Naming `auth_method`, `password` or `api_key` rotates the stored credential and
 * the whole credential is validated again, so a caller sends those only when a new secret was
 * actually typed. */
export function updateSource(sourceId, patch, { signal } = {}) {
  return apiJson(`/configure/sources/${encodeURIComponent(sourceId)}`, "PATCH", patch, { signal });
}

/** `DELETE /configure/sources/{id}` - removes the row and its stored secret. Resolves `null`
 * (204). */
export function deleteSource(sourceId, { signal } = {}) {
  return apiJson(`/configure/sources/${encodeURIComponent(sourceId)}`, "DELETE", undefined, { signal });
}

/** `POST /configure/sources/{id}/test` - try the connection with the *stored* credentials.
 * Resolves `{ok, detail, tested_at}`; a failed connection is a 200 with `ok: false` and the
 * backend's classified reason in `detail` (never a raw driver error), so the form shows that
 * sentence rather than inventing one. Only a saved source can be tested -- there is no
 * "test these fields" route by contract, which is why the form saves first. */
export function testSource(sourceId, { signal } = {}) {
  return apiJson(`/configure/sources/${encodeURIComponent(sourceId)}/test`, "POST", undefined, { signal });
}

/** `POST /workflows/harvest-source/runs` - start #306's harvest for one source, in the
 * background (`background=true, stream=false`), and get back the framework's 202
 * `{run_id, session_id, status}`. The executor reads `source_id` from the run's
 * `additional_data`; `message` is the framework's required prompt slot and is descriptive only.
 * Progress is not read from the run: the executor stamps `harvest_state` on the source row as
 * it goes, so the caller polls `fetchSources` until the state leaves `building`. */
export function startSourceHarvest(sourceId, { signal } = {}) {
  return apiForm(
    `/workflows/${HARVEST_SOURCE_WORKFLOW_ID}/runs`,
    {
      message: `Harvest source ${sourceId}`,
      stream: "false",
      background: "true",
      additional_data: JSON.stringify({ source_id: sourceId }),
    },
    { signal },
  );
}
