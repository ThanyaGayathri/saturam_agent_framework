import { useCallback, useEffect, useRef, useState } from "react";
import { fetchSemanticTables, fetchSemanticTable } from "../services/semantic-tables-api";
import { fetchKpiList } from "../services/kpis-api";
import { fetchBusinessAreas } from "../services/business-areas-api";
import { fetchSources } from "../services/sources-api";

/**
 * The Data catalog tab's real datasets: every table the semantic layer exposes
 * (`GET /semantic/tables`), each enriched with its description/columns
 * (`GET /semantic/tables/{name}`) and cross-referenced against the KPI registry
 * (`GET /kpis`) so "N KPIs linked" and business-area tags reflect which KPIs actually
 * declare this table in `source_tables` -- there is no server-side reverse lookup for
 * that, so it is computed here over the (small) registry.
 *
 * **A connected source's tables are in it too, once its harvest is over.** The unscoped
 * listing is the deployment's own warehouse only -- a source's tables never appear there, so a
 * customer could connect a source, watch it become "modelled", and find nothing new in the
 * catalog. Each registered source whose `harvest_state` is `modelled` is therefore listed with
 * its own `GET /semantic/tables?source_id=` and read back with
 * `GET /semantic/tables/{name}?source_id=` (contract 1.2.9), and its datasets carry
 * `sourceId`/`sourceName` so a card can say where the table came from. A source that is not
 * modelled (still building, needs review, failed) has no tables to list and is left out.
 *
 * "N KPIs linked" is the deployment's own tables' only: a KPI names its source tables by bare
 * name, and two sources can both have an `orders`, so matching by name would credit a
 * customer's table with an operator KPI. A source's datasets therefore link none.
 *
 * A table detail or the KPI/business-area reads failing is non-fatal -- the table still
 * shows with an empty description/attributes/tags rather than dropping off the page. So is
 * a source failing: the other tables still show, and `sources[].error` says which one did not
 * load. Only the operator's table list itself failing is fatal (there is nothing to show
 * without it).
 */
export function useDataCatalog() {
  const [state, setState] = useState({
    status: "loading",
    datasets: [],
    snapshot: null,
    sources: [],
    error: null,
  });
  const abortRef = useRef(null);

  const load = useCallback(() => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setState((prev) => ({ ...prev, status: "loading", error: null }));
    const { signal } = controller;

    // The registered sources, read alongside the operator's listing rather than after it: they
    // are independent, and a failed read only means no source tables (the operator's still show).
    const sourcesRead = fetchSources({ signal }).catch(() => []);

    fetchSemanticTables({ signal })
      .then(async ({ tables, snapshot }) => {
        const modelled = ((await sourcesRead) || []).filter((s) => s.harvest_state === "modelled");

        const [details, kpis, businessAreas, sourceListings] = await Promise.all([
          Promise.all(tables.map((ref) => fetchSemanticTable(ref.name, { signal }).catch(() => null))),
          fetchKpiList({ signal }).catch(() => []),
          fetchBusinessAreas({ signal })
            .then((r) => r?.areas || [])
            .catch(() => []),
          // One listing per modelled source, each with its own table details. A failure is kept
          // as that source's `error`, not thrown: it must not take the other tables down.
          Promise.all(
            modelled.map(async (source) => {
              try {
                const listing = await fetchSemanticTables({ sourceId: source.id, signal });
                const sourceDetails = await Promise.all(
                  listing.tables.map((ref) =>
                    fetchSemanticTable(ref.name, { sourceId: source.id, signal }).catch(() => null)
                  )
                );
                return { source, listing, details: sourceDetails, error: null };
              } catch (err) {
                if (err?.name === "AbortError") throw err;
                return {
                  source,
                  listing: null,
                  details: [],
                  error: err?.detail || err?.message || "Could not load this source's tables.",
                };
              }
            })
          ),
        ]);
        if (signal.aborted) return;

        const areaLabel = new Map(businessAreas.map((a) => [a.id, a.label]));
        const operatorDatasets = tables.map((ref, i) => {
          const detail = details[i];
          const linkedKpis = (kpis || []).filter((k) => (k.source_tables || []).includes(ref.name));
          const businessTags = [...new Set(linkedKpis.map((k) => areaLabel.get(k.business_area) || k.business_area))];
          return {
            id: ref.name,
            technicalName: ref.name,
            schema: ref.schema,
            rowCount: ref.row_count,
            description: detail?.description || "",
            attributeTags: (detail?.columns || []).map((c) => c.name),
            linkedKpiNames: linkedKpis.map((k) => k.name),
            businessTags,
            sourceId: null,
            sourceName: null,
          };
        });

        const sourceDatasets = sourceListings.flatMap(({ source, listing, details: sourceDetails }) =>
          (listing?.tables || []).map((ref, i) => {
            const detail = sourceDetails[i];
            return {
              // Schema-qualified and source-prefixed: two sources (or a source and the
              // operator's warehouse) can each hold an `orders`, and the id keys a card.
              id: `${source.id}:${ref.schema}.${ref.name}`,
              technicalName: ref.name,
              schema: ref.schema,
              rowCount: ref.row_count,
              description: detail?.description || "",
              attributeTags: (detail?.columns || []).map((c) => c.name),
              linkedKpiNames: [],
              businessTags: [],
              sourceId: source.id,
              sourceName: source.name,
            };
          })
        );

        const sources = sourceListings.map(({ source, listing, error }) => ({
          id: source.id,
          name: source.name,
          harvestedAt: listing?.snapshot?.harvested_at || source.last_harvest_at || null,
          version: listing?.snapshot?.version || null,
          tableCount: listing?.tables?.length ?? 0,
          error,
        }));

        setState({
          status: "ready",
          datasets: [...operatorDatasets, ...sourceDatasets],
          snapshot,
          sources,
          error: null,
        });
      })
      .catch((err) => {
        if (err?.name === "AbortError") return;
        setState({
          status: "error",
          datasets: [],
          snapshot: null,
          sources: [],
          error: err?.detail || err?.message || "Could not load the data catalog.",
        });
      });
  }, []);

  useEffect(() => {
    load();
    return () => abortRef.current?.abort();
  }, [load]);

  return { ...state, reload: load };
}
