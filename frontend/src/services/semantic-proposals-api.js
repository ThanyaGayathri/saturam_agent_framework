/**
 * The real K7 proposal review (`GET/POST /semantic/proposals*`, #235/F2), not `localStorage`.
 * See `src/lib/api.js` for the client. `egpt-retail suggest` files KPI and use-case proposals
 * here; a person reviews them on `/configure/proposals`.
 */

import { apiGet, apiJson } from "../lib/api";

/** `GET /semantic/proposals` - proposals matching `state` (default: every state) and `kinds`
 * (an array of wire `ProposalKind` values, e.g. `["kpi", "use_case"]`; default: every kind). */
export function fetchProposals({ state, kinds } = {}) {
  const params = new URLSearchParams();
  if (state) params.set("state", state);
  for (const kind of kinds || []) params.append("kind", kind);
  const query = params.toString();
  return apiGet(`/semantic/proposals${query ? `?${query}` : ""}`);
}

/** `POST /semantic/proposals/{id}/accept` - for a `kind: kpi` proposal, this also adopts it
 * into the registry in the same call; `applied_at` on the response says whether that landed. */
export function acceptProposal(id, note) {
  return apiJson(`/semantic/proposals/${id}/accept`, "POST", note ? { note } : undefined);
}

/** `POST /semantic/proposals/{id}/reject`. */
export function rejectProposal(id, note) {
  return apiJson(`/semantic/proposals/${id}/reject`, "POST", note ? { note } : undefined);
}

/** `POST /semantic/proposals/accept` - not atomic; a refused id (already decided by someone
 * else) is reported in the response's `refused`, and every other id in `ids` still commits. */
export function acceptProposalsBulk(ids, note) {
  return apiJson("/semantic/proposals/accept", "POST", { ids, note: note || undefined });
}
