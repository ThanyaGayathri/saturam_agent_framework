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
 * `{"detail": ...}` body when present (plus the backend's structured `code`, e.g.
 * `kpi_not_ready`/`insufficient_scope`/`not_found`, so callers can branch without parsing
 * prose), and the original failure as `cause` when there is one (a network error, or a
 * malformed body). */
export class ApiError extends Error {
  constructor(status, detail, path, options) {
    super(`${status} ${path}: ${detail}`, options);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    this.code = options?.code ?? null;
  }
}

/** Turns a FastAPI `detail` value into a display string. Handles the default 422
 * validation-error shape (`[{loc, msg, type}, ...]`) and falls back only for `null`,
 * `undefined`, or an empty string (`??` alone misses the empty-string case, since it's
 * falsy but not nullish); other falsy values like `0`/`false` are stringified, not
 * treated as absent. */
function formatErrorDetail(detail, fallback) {
  if (Array.isArray(detail)) {
    return detail.map((d) => (d && typeof d === "object" ? d.msg ?? JSON.stringify(d) : String(d))).join("; ") || fallback;
  }
  if (typeof detail === "string" && detail) return detail;
  if (detail != null && typeof detail !== "string") return JSON.stringify(detail);
  return fallback;
}

async function request(path, options = {}) {
  const url = `${API_BASE_URL}${path}`;

  let token;
  try {
    token = getAuthToken();
  } catch (cause) {
    // Kept separate from the fetch try/catch below so a throwing getter (a corrupted
    // stored token, say) is reported distinctly from a real network failure.
    throw new ApiError(0, "Could not read the stored auth token.", path, { cause });
  }

  // Built via the platform's own case-insensitive `Headers` (not a raw object spread) so a
  // caller-supplied header of any casing, or an actual `Headers` instance, merges correctly
  // instead of silently colliding or being dropped. Only `options.body` being a JSON string
  // (what `apiJson` sends) gets a default `Content-Type`, so a FormData/Blob/binary body
  // keeps whatever `fetch` or an explicit caller header sets.
  const headers = new Headers({ Accept: "application/json" });
  if (typeof options.body === "string") headers.set("Content-Type", "application/json");
  new Headers(options.headers).forEach((value, key) => headers.set(key, value));
  // Set last so a caller-supplied header can never silently clobber the identity this
  // request is actually sent with.
  if (token) headers.set("Authorization", `Bearer ${token}`);

  let response;
  try {
    response = await fetch(url, { ...options, headers });
  } catch (cause) {
    if (cause?.name === "AbortError") throw cause;
    // Network failure (backend down, CORS blocked, DNS) - fetch itself throws, there is no
    // response to inspect. Distinguish this from a real HTTP error below; keep `cause` so
    // it's at least inspectable in devtools, even though the user-facing message is generic.
    throw new ApiError(0, "Could not reach the API. Is it running?", path, { cause });
  }

  if (!response.ok) {
    let body;
    try {
      body = await response.json();
    } catch (cause) {
      // A genuine abort mid-read must propagate as `AbortError`, not get rewrapped below -
      // only a real parse failure (malformed/absent body) falls back to `null`.
      if (cause?.name === "AbortError") throw cause;
      body = null;
    }
    throw new ApiError(response.status, formatErrorDetail(body?.detail, response.statusText), path, {
      code: body?.code,
    });
  }

  if (response.status === 204) return null;
  try {
    return await response.json();
  } catch (cause) {
    if (cause?.name === "AbortError") throw cause;
    // A 2xx response with a body that isn't valid JSON (a misconfigured proxy, an HTML
    // error page served with a 200, a truncated response) - report it the same way every
    // other failure here is reported, instead of a raw parse SyntaxError.
    throw new ApiError(response.status, "Received a malformed response body.", path, { cause });
  }
}

/** GET `path`, parsed as JSON. Throws `ApiError` on failure (network or non-2xx); throws the
 * underlying `AbortError` as-is (not wrapped) when `signal` is passed and fires. */
export function apiGet(path, { signal } = {}) {
  return request(path, { method: "GET", signal });
}

/** POST/PUT/DELETE `path` with a JSON `body`. Throws `ApiError` on failure; throws the
 * underlying `AbortError` as-is when `signal` is passed and fires. `async` so a synchronous
 * serialization failure (a circular reference, a `BigInt`) rejects the returned promise
 * instead of throwing out of the call site. */
export async function apiJson(path, method, body, { signal } = {}) {
  return request(path, { method, body: body === undefined ? undefined : JSON.stringify(body), signal });
}
