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
  the deployment's own vocabulary terms, the focus can also be checked against the real
  vocabulary. It only ranks the list; nothing sends it to a suggestion run any more.
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
- **KPI suggestions are automatic -- there is no "Suggest KPIs" button** (F1, #234; run shape #254;
  the manual trigger was removed once a connected source's harvest started proposing them itself,
  contract 1.2.9). The discovery page used to start a `kpi_suggestion` run from a button, poll
  `GET /kpis/suggestions/{run_id}` behind a three-step stepper with a stopwatch, an expectation from
  `typical_duration_seconds`, a Cancel and a receipt, mirror the run id into `?run=`, and take a focus
  and a free-text request for it (the "AI generated KPI" tab of Add KPI). All of that is gone, with
  `SuggestionRunPanel.jsx` and `AddKpiAiStep.jsx`. What replaces it is small: the page reads the
  standing suggestions (`GET /kpis/suggestions/current`) when it opens; while a run is still working
  (`generating`) `SuggestionStatus.jsx` says so -- no stage, no clock, nothing to cancel -- and the page
  reads the list again every 3s (6s after a minute, giving up at 5) until it lands; a run that failed
  after a harvest is a banner naming the source and the reason with one "Try again"
  (`startKpiSuggestionRun({ sourceId })`, the only place the app starts a run). Add KPI is the manual
  form only. The focus prompt still ranks the list; nothing sends it to a run. The API is unchanged
  and still accepts `business_areas` and `ask`; nothing here sends them. Adopting a suggestion is
  still `POST /kpis` (`createKpi`).
- **A registry KPI's detail panel graphs its value over the last 30 nights** (contract 1.1.9):
  `components/kpi/KpiTrend.jsx`, fed by `GET /kpis/{id}/series` -- the same nightly
  `gold.kpi_value` rows a dashboard's `line`/`bar` tile plots, addressed by KPI. Points are
  placed by date across the window (a night with no row is a gap, not a compressed axis), the
  latest value and its delta against the previous night sit above the line, and a
  per-dimension KPI (grain `brand`, say) offers its dimensions as chips. Nothing is illustrative
  and nothing is computed at render time. Adopting a suggestion ("Add to my metrics") now also
  calls `POST /kpis/{id}/compute` -- the nightly gold pass narrowed to that KPI and today --
  so a new KPI has its first point at once instead of after its first night; the panel shows
  "Computing today's value…" while that runs and offers "Compute today's value" for any registry
  KPI gold has never computed. Every proposed formula is a snapshot over the current silver
  rows, so the line grows one real point per night; there is no backfill and no synthetic
  history.
- **Configure > Data sources is real** (#308, part of #302; contract 1.2.8):
  `src/services/sources-api.js` calls `GET/POST/PATCH/DELETE /configure/sources` and
  `POST /configure/sources/{id}/test` (#305), and starts #306's harvest with the framework's
  own `POST /workflows/harvest-source/runs` (`background=true`, `additional_data={source_id}`,
  a form body -- `apiForm` in `src/lib/api.js`). `ConfigureWorkspaceContext.jsx` reads the list
  from the API and, while any source is `building`, re-reads it on the KPI-suggestion cadence
  (2s, 5s after half a minute, giving up at four minutes with a "check now" notice); the card's
  state is the row's own `harvest_state`, not a lookup. "Test Connection" on the form shows
  the API's verdict sentence -- the contract tests only a *stored* source, so a new form is
  saved first and the form says so. Save on a Postgres/Snowflake source also starts the
  harvest ("Connect"); other types register and show "Not modelled" with the reason. A real
  source's semantic-layer screen shows what the harvest read (`GET /semantic/tables?source_id=`,
  #307) and its own snapshot; it says outright that join/measure review and the personal-data
  scan are not built for connected sources rather than deriving them from demo data. **Demo
  seeds stay as UI fixtures**: the six seeded databases are marked "Demo", hidden behind
  "Show N demo sources", keep their fixture states (`source-model-state.js`), and editing one
  with real values registers a real source through the API. A localStorage-added internal
  source from before this wiring is no longer shown; LLM models, external integrations and the
  cloud profile are unchanged (no backend yet). Verified in a headless browser against the
  real API, Postgres and Neo4j -- `cookbook/94_retail/P4_frontend_connect_flow.mjs`, results
  in `cookbook/94_retail/TEST_LOG.md`.
- **KPI suggestions after a harvest, kept and removable; a connected source's tables in the Data
  catalog** (part of #302; contract 1.2.9). A source's harvest now ends with one KPI suggestion
  run for it (the `suggest-kpis` step of the `harvest-source` workflow), and the KPIs page reads
  what is standing -- `GET /kpis/suggestions/current`, via `fetchCurrentKpiSuggestions` in
  `src/services/kpis-api.js` -- when it opens, so proposed KPIs are still there after a reload
  rather than lasting one session. If the run is still working the page says so and reads the list
  again until it lands (no stepper or clock); if it failed, a banner says which source and why, with "Try again"
  (`POST /kpis/suggestions` now takes `source_id`). Each suggestion has a trash control (row and
  detail panel): `dismissKpiSuggestion` -> `POST /kpis/suggestions/dismissals`, remembered by the
  backend for everyone, so it is neither on the next reload nor in a later run that proposes it
  again. **A suggestion from a connected source cannot be added to the registry yet**: its
  tables are in that source's harvest, not the deployment's own semantic snapshot, so the
  registry would mark the KPI blocked and compute would refuse it. The UI says so instead of
  offering the button (lifts with #314). The Data catalog (`useDataCatalog`) lists every
  `modelled` source's tables alongside the deployment's own -- `GET /semantic/tables?source_id=`
  and, for the columns, `GET /semantic/tables/{name}?source_id=` -- each card marked with its
  source, and says how many tables a source contributed and when it last synced. Verified in a
  headless browser against the real API, Postgres and Neo4j --
  `cookbook/94_retail/P6_kpi_suggestions_after_harvest.mjs` and
  `P6_kpi_suggestion_failure.mjs`, with the model replaced by the suggestion replay fixture;
  results in `cookbook/94_retail/TEST_LOG.md`.
- **A source stuck in `needs_review` has a way out; removing a source frees its schema** (part of
  #302; contract 1.2.10). `needs_review` means the source's schema is held by another registered
  source or by the deployment's own warehouse, and nothing was written. The card now shows
  "Retry modelling" beside "Review N items" (`ConfigureInternalSources.jsx`), the semantic-layer
  screen says the same instead of "Build model", and saving the source form re-runs the harvest
  from `needs_review` -- so the message's own remedy, point it at a different schema, actually
  does something (`ConfigureInternalSourceForm.jsx`'s `wantsHarvest`). `NEEDS_REVIEW_REASON`
  (`source-model-state.js`) no longer says "reconnect" (no such control) or calls an absent owner
  "another connected source". `DELETE /configure/sources/{id}` now also removes the source's
  tables from the semantic graph and its saved KPI suggestions, so a deleted source's schema is
  free to reuse; the confirm dialog says so, and a `503` (graph unreachable; the source is kept)
  reaches the screen as the API's own sentence. Verified in a headless browser --
  `cookbook/94_retail/P7_needs_review_way_out.mjs`, results in `cookbook/94_retail/TEST_LOG.md`.
- The Vercel-style API layer in the prototype mostly disappears rather than moving: tokens come
  from the identity provider, streams pass through the edge directly.

CI runs no frontend job until there is a frontend; the `changes` filter in `ci.yml` already
carries a `frontend` output for when it arrives.
