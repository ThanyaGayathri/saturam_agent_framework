/**
 * The shipped business-area vocabulary (`GET /business-areas`), not `localStorage`. See
 * `src/lib/api.js` for the client.
 *
 * Deliberately narrow: this labels *real* KPI rows only (C1, #232) -- the per-profile local
 * business-area lists (`storage/business-areas.js`) already correctly match their own seed
 * catalogues and are left untouched. The two vocabularies are not reconciled.
 */

import { apiGet } from "../lib/api";

/** `GET /business-areas` - `{vocabulary_version, areas: [{id, label, subject_areas, ...}]}`. */
export function fetchBusinessAreas() {
  return apiGet("/business-areas");
}
