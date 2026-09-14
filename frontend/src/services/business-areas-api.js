/**
 * The deployment's business-area vocabulary (`GET /business-areas`), not `localStorage`. See
 * `src/lib/api.js` for the client.
 *
 * Two consumers, both on the discovery page: the labels real KPI rows are grouped under (C1,
 * #232), and the options of the focus prompt -- each area carries K9's verdict from the
 * semantic graph (`supported`) with its evidence (`concept_count`, `table_count`, `reason`),
 * so the prompt offers what the connected data can actually rank on. The per-profile local
 * business-area lists (`storage/business-areas.js`) still feed the rest of the app and match
 * their own seed catalogues; the two vocabularies are not reconciled.
 */

import { apiGet } from "../lib/api";

/**
 * `GET /business-areas` - `{vocabulary_version, areas: [{id, label, subject_areas, description,
 * supported, concept_count, table_count, reason, origin}]}`. 503 when the semantic graph
 * cannot be asked (an `ApiError` with `.status === 503`).
 */
export function fetchBusinessAreas({ signal } = {}) {
  return apiGet("/business-areas", { signal });
}
