import { useCallback, useEffect, useRef, useState } from "react";
import { fetchSemanticTables, fetchSemanticTable } from "../services/semantic-tables-api";
import { fetchKpiList } from "../services/kpis-api";
import { fetchBusinessAreas } from "../services/business-areas-api";

/**
 * The Data catalog tab's real datasets: every table the semantic layer exposes
 * (`GET /semantic/tables`), each enriched with its description/columns
 * (`GET /semantic/tables/{name}`) and cross-referenced against the KPI registry
 * (`GET /kpis`) so "N KPIs linked" and business-area tags reflect which KPIs actually
 * declare this table in `source_tables` -- there is no server-side reverse lookup for
 * that, so it is computed here over the (small) registry.
 *
 * A table detail or the KPI/business-area reads failing is non-fatal -- the table still
 * shows with an empty description/attributes/tags rather than dropping off the page.
 * Only the table list itself failing is fatal (there is nothing to show without it).
 */
export function useDataCatalog() {
  const [state, setState] = useState({ status: "loading", datasets: [], snapshot: null, error: null });
  const abortRef = useRef(null);

  const load = useCallback(() => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setState((prev) => ({ ...prev, status: "loading", error: null }));

    fetchSemanticTables({ signal: controller.signal })
      .then(async ({ tables, snapshot }) => {
        const [details, kpis, businessAreas] = await Promise.all([
          Promise.all(
            tables.map((ref) => fetchSemanticTable(ref.name, { signal: controller.signal }).catch(() => null))
          ),
          fetchKpiList({ signal: controller.signal }).catch(() => []),
          fetchBusinessAreas({ signal: controller.signal })
            .then((r) => r?.areas || [])
            .catch(() => []),
        ]);
        if (controller.signal.aborted) return;

        const areaLabel = new Map(businessAreas.map((a) => [a.id, a.label]));
        const datasets = tables.map((ref, i) => {
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
          };
        });

        setState({ status: "ready", datasets, snapshot, error: null });
      })
      .catch((err) => {
        if (err?.name === "AbortError") return;
        setState({
          status: "error",
          datasets: [],
          snapshot: null,
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
