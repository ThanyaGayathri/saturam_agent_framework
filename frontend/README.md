# frontend/

The Enterprise GPT interface: the Vite application from `Retail-GPT-prototype`, landed here from
`feat/egpt-ui-revamp` (`e6b55b3`) as a flat copy of that repo's `frontend/` directory. Not yet
wired to the API -- the seams below are unchanged and still hold.

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
- **The local `Dashboard`/`Tile` shape mirrors the API's wire model** (`src/storage/dashboards.js`,
  v2: `kpi_identifier`, `chart`, `position`, `grain_value`; `owner`, `visibility`, `filters`,
  `refresh_schedule`), so wiring it is a transport change, not a second mapping. `kpi_identifier`
  is `null` for a seed metric until the registry screens read real KPIs; `delivery` is
  frontend-only because the contract has no such field yet.
- The Vercel-style API layer in the prototype mostly disappears rather than moving: tokens come
  from the identity provider, streams pass through the edge directly.

CI runs no frontend job until there is a frontend; the `changes` filter in `ci.yml` already
carries a `frontend` output for when it arrives.
