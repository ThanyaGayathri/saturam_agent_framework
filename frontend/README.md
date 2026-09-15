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
- **Business areas on the discovery page come from `GET /business-areas`**, not from a
  constant: the labels real rows are grouped under, and the options of the focus prompt
  ("Which areas of the business do you care about most?"), fetched when `/kpis/discover`
  loads. Each area carries K9's verdict from the semantic graph -- `supported` when some
  accepted concept in it has a confirmed table mapping -- with its evidence
  (`concept_count`, `table_count`, `reason`, contract 1.1.7). Supported areas are the
  choices, shown with the counts; unsupported ones are greyed with K9's sentence. A stored
  focus is reconciled against the fetched list on load, so ids from an older vocabulary never
  leave a "Ranked for" line with no pills. If the graph cannot be asked (503) the prompt says
  so, offers a retry, and the list renders unranked rather than blank. Because the ids are
  the deployment's own vocabulary terms, a single focused area narrows the suggestion run
  again without being refused as an unknown area.
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
  local business-area list (`storage/business-areas.js`, localStorage
  `retailgpt:business-areas:v1`) still feeds every other consumer -- Playground pills, the
  Processes and Alerts filters, the Configure hierarchy editor, the registry dropdowns -- and
  is not reconciled with the shipped vocabulary `GET /business-areas` returns; the discovery
  page alone reads the real list, held in its own state, never written into that key.
- **The KPI/use-case suggestion review screen is real** (F2, #235): `Configure > Proposals`
  (`components/configure/ConfigureProposals.jsx`) lists what `egpt-retail suggest` found via
  `GET /semantic/proposals`, and accepting a KPI proposal
  (`POST /semantic/proposals/{id}/accept`) adopts it into the registry in the same call — the
  response's `applied_at` is shown, not assumed. A use-case proposal has no consumer yet, so
  accepting one only records the decision; the screen says so rather than implying a use case
  now exists somewhere. Separate from `ConfigureSemanticLayer.jsx`'s own "Needs review" tab,
  which is an unrelated, still-local per-source mapping-decision UI this issue does not touch.
- **"Suggest KPIs" is a real run of the `kpi_suggestion` agent** (F1, #234; run shape #254):
  the button on the discovery page starts one with `POST /kpis/suggestions` (202, a
  `KpiSuggestionRun`) and polls `GET /kpis/suggestions/{run_id}` -- every 2s, relaxing to 5s
  after half a minute, giving up at 4 minutes -- until the row is `succeeded` (its `result` is
  rendered through the same `rowFromSuggestion` path as before) or `failed` (its `error` is
  shown as-is; a timeout names the bound it hit). A run is two frontier-tier model calls and
  takes minutes, so it is never fired on page load. While it waits, the bar becomes a status
  panel (`SuggestionRunPanel.jsx`): the row's `typical_duration_seconds` (the deployment's
  own recent run times, contract 1.1.6) sets the expectation up front, its `phase` drives a
  three-step stepper (reading, proposing, checking) instead of a stopwatch, Cancel stops the
  polling (the server run finishes on its own and is never read again), and a one-line receipt
  -- how many, how long, how many adoptable -- stays up until dismissed. Adopting a suggestion
  is still `POST /kpis` (`createKpi`); nothing else is stored.
  The synchronous `GET /kpis/suggestions` is deprecated in the contract and no longer called
  here. Against the stub server the poll never turns terminal (the stub serves the `queued`
  example every time), which is what the 4-minute ceiling is for.
- The Vercel-style API layer in the prototype mostly disappears rather than moving: tokens come
  from the identity provider, streams pass through the edge directly.

CI runs no frontend job until there is a frontend; the `changes` filter in `ci.yml` already
carries a `frontend` output for when it arrives.
