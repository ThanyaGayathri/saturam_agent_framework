/**
 * The first real accessor against `product/egpt_retail`'s API, not `localStorage` - the
 * template for the conversions `frontend/README.md` names as a known cost ("every
 * `useState(() => load*())` initialiser becomes an effect"). See `src/lib/api.js` for the
 * client and issue #230 for why this lives here instead of in `storage/dashboards.js`: the
 * frontend's local `Dashboard`/`Tile` shape doesn't match the backend's yet, so this stays a
 * read-only, additive accessor until that alignment work (issue #226) lands.
 */

import { apiGet } from "../lib/api";

/** `GET /dashboards` - the real list, as the backend's `Dashboard` wire model returns it. */
export function fetchDashboardList() {
  return apiGet("/dashboards");
}
