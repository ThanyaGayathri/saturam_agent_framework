/**
 * Fetch client for the real retail API (`product/egpt_retail`, served by `egpt-retail api`).
 * Base URL comes from `VITE_API_URL` (see `.env.example`); defaults to the local dev port
 * `deploy/compose.yaml` exposes (`EGPT_RETAIL_API_PORT`, default 7777).
 *
 * Separate from `src/services/retail-llm.js` (which calls this app's own `/api/llm/*` proxy,
 * not the retail API) and from `src/lib/supabase.js` (the Supabase client for user-added
 * content) - this is the one seam every KPI/Dashboard accessor should route through once it
 * stops reading `localStorage`.
 */

const API_BASE_URL = (import.meta.env.VITE_API_URL || "http://localhost:7777").replace(/\/+$/, "");

/**
 * What `request()` calls for a bearer token before every fetch. A no-op until an IdP login
 * exists in the frontend (#228) - every accessor built on `request()` (this seam is meant to
 * be the one every future KPI/Dashboard accessor routes through) starts sending a token the
 * moment this is wired up, with no change to its own call signature.
 */
let getAuthToken = () => null;

/** Install the function `request()` calls for a bearer token. Call once, from wherever the
 * IdP session lives, when #228 lands. */
export function setAuthTokenGetter(getter) {
  getAuthToken = getter;
}

/** Thrown on a non-2xx response, or a network/parse failure. Carries the parsed FastAPI
 * `{"detail": ...}` body when present, and the original failure as `cause` when there is
 * one (a network error, or a malformed body). */
export class ApiError extends Error {
  constructor(status, detail, path, options) {
    super(`${status} ${path}: ${detail}`, options);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

async function request(path, options = {}) {
  const url = `${API_BASE_URL}${path}`;
  const token = getAuthToken();
  let response;
  try {
    response = await fetch(url, {
      ...options,
      headers: {
        Accept: "application/json",
        ...(options.body ? { "Content-Type": "application/json" } : {}),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...options.headers,
      },
    });
  } catch (cause) {
    // Network failure (backend down, CORS blocked, DNS) - fetch itself throws, there is no
    // response to inspect. Distinguish this from a real HTTP error below; keep `cause` so
    // it's at least inspectable in devtools, even though the user-facing message is generic.
    throw new ApiError(0, "Could not reach the API. Is it running?", path, { cause });
  }

  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new ApiError(response.status, body?.detail ?? response.statusText, path);
  }

  if (response.status === 204) return null;
  try {
    return await response.json();
  } catch (cause) {
    // A 2xx response with a body that isn't valid JSON (a misconfigured proxy, an HTML
    // error page served with a 200, a truncated response) - report it the same way every
    // other failure here is reported, instead of a raw parse SyntaxError.
    throw new ApiError(response.status, "Received a malformed response body.", path, { cause });
  }
}

/** GET `path`, parsed as JSON. Throws `ApiError` on failure (network or non-2xx). */
export function apiGet(path) {
  return request(path, { method: "GET" });
}

/** POST/PUT/DELETE `path` with a JSON `body`. Throws `ApiError` on failure. */
export function apiJson(path, method, body) {
  return request(path, { method, body: body === undefined ? undefined : JSON.stringify(body) });
}
