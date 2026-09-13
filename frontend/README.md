# frontend/

The Enterprise GPT interface: the Vite application from `Retail-GPT-prototype`, landed here from
`feat/egpt-ui-revamp` (`e6b55b3`) as a flat copy of that repo's `frontend/` directory. Most
production screens still run on `localStorage`/Supabase seeds, not the real API -- `src/lib/api.js`
is the first real wiring (issue #230/A2), proven end to end via the "Live backend check" panel on
`/admin/seed-data`. The dashboard screens are the first production screens reading from it for
real, not just proving the client works (D2, #233, see below).

    cd frontend && npm ci && npm run dev        # http://localhost:3000
    npm run build                               # dist/, gitignored

`vite.config.js` pins the dev port to 3000 - it's also the only origin the real API's default
CORS allow-list covers (`libs/agno/egpt/os/utils.py:resolve_origins`), so a different local port
needs `CORS_ORIGIN_LIST` set on the API (see `deploy/README.md`).

Left behind on purpose: `src/figma_data/` (109 MB of raw Figma frame exports, imported by
nothing) and the prototype's root `docs/`, `scripts/` and `CLAUDE.md`, which this repository
already has its own versions of. Still carried and still to decide: `api/`, `middleware.js` and
`vercel.json` are the prototype's Vercel-style API layer, which the delivery plan says "mostly
disappears rather than moving" once tokens come from the identity provider.

What was already decided about it, and still applies:

- **The API it calls is fixed now**: `product/egpt_retail/api/openapi/egpt-retail.v1.yaml`
  (R3), with a stub server that renders every screen's examples. The prototype's `KpiRegistry`
  screen has already been rendered from that stub with no component change — see
  `cookbook/94_retail/R03_prototype_screen.md`.
- **The integration seam is `src/storage/*.js`**: synchronous local-storage accessors that
  become API clients. The accessors are synchronous and `fetch` is not, so every
  `useState(() => load*(...))` initialiser becomes an effect. Known cost, recorded in phase zero.
- **Chat, sessions and schedules are framework endpoints** (`POST /agents/{id}/runs` over SSE,
  `/sessions`, `/schedules`); the contract adds only the retail delta.
- **Business-area labels come from `GET /business-areas`**, not from a constant; the prototype's
  ten areas versus the shipped vocabulary's six is Lane K's call before wiring.
- **Dashboards are wired to the real API** (D2, #233): `src/services/dashboards-api.js` calls
  `GET/POST/PATCH/DELETE /dashboards` and `GET /dashboards/{id}/data` directly, matching the
  local `Dashboard`/`Tile` shape D1 (#226) aligned field for field
  (`src/storage/dashboards.js`) - there is no local persistence left for dashboards, and no
  second mapping. A dashboard can only be created against a *registered* KPI
  (`TileWrite.kpi_identifier` is required); the seed catalogue has no identifier on any card
  yet, so creation is blocked with an explanatory message until the registry screens (#232)
  read real KPIs. `series`/`delta` ship empty from the API until #227 (E1) lands a
  gold-history reader, so `line`/`bar` tiles say "not enough history" rather than showing a
  fabricated trend. `delivery` (who a dashboard is emailed to) has no contract field and no
  local persistence to fall back to any more - dropped from the UI, not silently kept.
- **KPI discovery is wired to the real registry** (C1, #232): `GET /kpis` and
  `GET /business-areas` are merged *alongside* the seed catalogue in
  `components/kpi/KpiDiscoveryPage.jsx` — the same additive pattern already used for suggested
  candidates — rather than replacing it. The seed catalogue has no real `kpi_identifier` on any
  card and the real registry has very few entries until more get registered, so a real KPI
  shows its real, join-aware feasibility and a working identifier (closing the loop with D2's
  dashboard creation) while the rest of the demo catalogue is untouched. Each demo profile's own
  local business-area list (`storage/business-areas.js`) is left as-is; it is not reconciled
  with the shipped 7-area vocabulary `GET /business-areas` returns — the two are allowed to
  coexist, and only real registry rows use the real labels.
- The Vercel-style API layer in the prototype mostly disappears rather than moving: tokens come
  from the identity provider, streams pass through the edge directly.

CI runs no frontend job until there is a frontend; the `changes` filter in `ci.yml` already
carries a `frontend` output for when it arrives.
