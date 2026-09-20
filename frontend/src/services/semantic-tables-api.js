/**
 * The semantic layer's read-only table surface (`GET /semantic/tables`,
 * `GET /semantic/tables/{name}`), mounted on the real router in contract 1.2.0 for the
 * dashboard chart editor: its schema pane shows a person the same table and column
 * descriptions the compose agent was shown. See `src/lib/api.js` for the client.
 */

import { apiGet } from "../lib/api";

/** `GET /semantic/tables` - every table the semantic layer exposes, as `{tables: [{name,
 * schema, row_count}], snapshot}`. Also the allow-list the SQL guard restricts a chart's query
 * to, so a table absent here is one no chart can read.
 *
 * With `sourceId` (contract 1.2.7, #307) the listing is that registered source's own harvest --
 * its tables only, and `snapshot` is its own (`version` is `unharvested` and `last_harvest_at`
 * is `never` until #306's workflow has run for it). Without it, the operator's snapshot, as
 * before. Throws `ApiError` (404) for a `sourceId` no source has. */
export function fetchSemanticTables({ sourceId, signal } = {}) {
  const query = sourceId ? `?source_id=${encodeURIComponent(sourceId)}` : "";
  return apiGet(`/semantic/tables${query}`, { signal });
}

/** `GET /semantic/tables/{name}` - one table with its columns: `{ref, description, concepts,
 * columns: [{name, data_type, description, meaning, is_personal_data, joinable, ...}]}`.
 * Throws `ApiError` (404) for a table the layer does not expose. */
export function fetchSemanticTable(name, { signal } = {}) {
  return apiGet(`/semantic/tables/${encodeURIComponent(name)}`, { signal });
}
