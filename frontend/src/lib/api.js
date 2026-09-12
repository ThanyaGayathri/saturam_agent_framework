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

/** Thrown on a non-2xx response. Carries the parsed FastAPI `{"detail": ...}` body when present. */
export class ApiError extends Error {
  constructor(status, detail, path) {
    super(`${status} ${path}: ${detail}`);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

async function request(path, options = {}) {
  const url = `${API_BASE_URL}${path}`;
  let response;
  try {
    response = await fetch(url, {
      ...options,
      headers: {
        Accept: "application/json",
        ...(options.body ? { "Content-Type": "application/json" } : {}),
        ...options.headers,
      },
    });
  } catch (cause) {
    // Network failure (backend down, CORS blocked, DNS) - fetch itself throws, there is no
    // response to inspect. Distinguish this from a real HTTP error below.
    throw new ApiError(0, "Could not reach the API. Is it running?", path);
  }

  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new ApiError(response.status, body?.detail ?? response.statusText, path);
  }

  if (response.status === 204) return null;
  return response.json();
}

/** GET `path`, parsed as JSON. Throws `ApiError` on failure (network or non-2xx). */
export function apiGet(path) {
  return request(path, { method: "GET" });
}

/** POST/PUT/DELETE `path` with a JSON `body`. Throws `ApiError` on failure. */
export function apiJson(path, method, body) {
  return request(path, { method, body: body === undefined ? undefined : JSON.stringify(body) });
}
