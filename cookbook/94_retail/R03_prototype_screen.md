# R3 — one prototype screen rendered from the stub

The package's evidence clause: *"Point one prototype screen at it. The screen renders from
the stub without changes to its component code."* This is a procedure run in another
repository, so it is a `.md` beside the scripts rather than a script.

**Screen:** `KpiRegistry` (`/kpis`) — the largest single seam (`kpi-registry.js`), a core
contract area, and a list, so empty-versus-populated is visible.

## Why the seam is not quite as clean as the build plan says

`loadKpiRegistry(storageKey, profileId)` is **synchronous**; `fetch` is not. Replacing its
body with an HTTP call would force `await` at every call site — a change to component code,
which fails the evidence bar as written. The fix that keeps the claim literally true:
hydrate once at app boot into an in-memory cache and keep the accessor synchronous over it.
The change stays inside `frontend/src/storage/`; components are untouched.

This is the real P5 integration cost and phase zero is when it is cheap to know. It is
recorded in `TEST_LOG.md` as a finding.

## Procedure

All paths are in `Retail-GPT-prototype`, on a branch there. Nothing in this repository's
`frontend/` changes until P5.

1. Start the stub from this repository:

   ```bash
   .venv/bin/python -m egpt_retail.api.stub          # http://127.0.0.1:7778
   ```

2. In the prototype, on a new branch, add `frontend/src/storage/api-hydrate.js`:

   ```js
   // Hydrate-at-boot cache over the Enterprise GPT API. Keeps the storage accessors
   // synchronous, so no component changes. Phase-zero shim; P5 replaces the accessors.
   const BASE = import.meta.env.VITE_EGPT_BASE || "http://127.0.0.1:7778";
   const cache = new Map();
   export async function hydrate(paths) {
     await Promise.all(paths.map(async (p) => {
       const r = await fetch(`${BASE}${p}`);
       if (r.ok) cache.set(p, await r.json());
     }));
   }
   export function cached(path) { return cache.get(path) ?? null; }
   ```

3. In `frontend/src/storage/kpi-registry.js`, at the top of `loadKpiRegistry`, before the
   localStorage read, prefer the cache when it holds `/kpis`, mapping the flat contract
   shape to the screen's nested one:

   ```js
   import { cached } from "./api-hydrate";
   // ...inside loadKpiRegistry(storageKey, profileId):
   const fromApi = cached("/kpis");
   if (Array.isArray(fromApi)) {
     const categories = [{ category: "Enterprise KPIs", sections: [] }];
     for (const k of fromApi) {
       const sectionName = k.subject_area || "Uncategorized";
       let sec = categories[0].sections.find((s) => s.name === sectionName);
       if (!sec) { sec = { name: sectionName, businessAreaId: k.business_area, kpis: [] }; categories[0].sections.push(sec); }
       sec.kpis.push({
         name: k.name, desc: k.description, active: k.active, seed: k.source === "shipped",
         businessAreaId: k.business_area, subjectArea: k.subject_area, level: k.level ? k.level[0].toUpperCase() + k.level.slice(1) : "",
         unit: k.unit, industryBenchmark: k.industry_benchmark, datasetIds: k.dataset_ids, processMapping: k.process_ids,
         feasibility: k.feasibility.verdict,
       });
     }
     return { version: KPI_SEED_VERSION, categories: normalizeKpiRegistry(categories, profileId) };
   }
   ```

4. In `frontend/src/main.jsx`, hydrate before rendering:

   ```js
   import { hydrate } from "./storage/api-hydrate";
   hydrate(["/kpis"]).finally(() => { /* existing createRoot(...).render(...) */ });
   ```

5. `npm run dev`, open `/kpis`. Then restart the stub with the empty example as default for
   that route — or simply set the hydrate path to `/kpis?example=empty` — and reload.

## What to record

In `TEST_LOG.md` under `R03_prototype_screen.md`:

- the prototype commit hash the evidence was produced against (`git rev-parse HEAD` in the
  prototype on the branch);
- the two screenshots' paths (populated, empty);
- **whether any file under `frontend/src/components/` changed** — the evidence is only
  valid if the answer is none;
- every field the screen needed that the contract lacked. The mapping in step 3 is where
  those surface; each one is a contract fix now rather than in P5.

## Known field-level findings from writing step 3

- The screen's `level` is capitalised (`Strategic`); the contract's is lower-case
  (`strategic`). A display concern, mapped in the shim. Not a contract change.
- The screen has no `feasibility` field today; the contract embeds one per KPI. Adding the
  badge is a P5 component change, out of scope for the evidence run.
- The screen keys KPIs by `subjectArea::name`; the contract keys by `identifier`. The
  contract's key is the stable one (provenance and gold reference it). P5 maps on
  `identifier`.
