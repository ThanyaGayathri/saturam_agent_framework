import { useEffect, useMemo, useState } from "react";
import Modal from "../ui/Modal";
import ModePickerStep from "./create-dashboard/ModePickerStep";
import AiPurposeStep from "./create-dashboard/AiPurposeStep";
import AiGeneratingStep from "./create-dashboard/AiGeneratingStep";
import DashboardBuilderStep from "./create-dashboard/DashboardBuilderStep";
import { DEFAULT_REFRESH_SCHEDULE, REFRESH_OPTIONS, kpiIsWireReady } from "../../storage/dashboards";
import { fetchKpiList } from "../../services/kpis-api";
import { useDashboardSuggestionRun } from "../../hooks/useDashboardSuggestionRun";

/** A selected-metric row, from a KPI shaped either way `KpiDiscoveryPage.jsx` hands one in
 * (`businessAreaId`, camelCase, after its own mapping) or the way `fetchKpiList()` hands one
 * back raw (`business_area`, the wire's own casing) - this modal fetches its own copy of the
 * ready registry below, so both shapes need to land on the same internal one. */
function metricFromKpi(kpi) {
 return {
  identifier: kpi.identifier,
  name: kpi.name || "",
  unit: kpi.unit || "",
  description: kpi.description || "",
  businessArea: kpi.businessAreaId || kpi.business_area || "",
  sourceTables: kpi.source_tables || kpi.sourceTables || [],
 };
}

/** One chart of a `DashboardSuggestion.charts` entry, given a local id the builder step
 * tracks edits/deletes by (the wire has none until the tile is created). */
function chartFromSuggestion(chart, index) {
 return { ...chart, id: `sug_${index}` };
}

/**
 * Create dashboard: a mode picker (AI generated vs. manual), then either a chat-style
 * metrics-and-purpose step feeding a dashboard-composition run, or straight into the shared
 * review/builder step. One flow for both entry points -- `DashboardsPage.jsx`'s plain "Create
 * dashboard" button and `KpiDiscoveryPage.jsx`'s "create from selection" -- so `rows`, when
 * given non-empty, no longer skips the mode picker (round-1 review comment: this PR had
 * silently reverted #272's fix reintroducing that skip); it only seeds `initialCharts`/
 * `purposeMetrics`, the starting point either path resets back to.
 *
 * `onCreate({ name, refresh, charts, businessAreaFilter })` is the caller's job, same split as
 * before: this modal collects and edits state, the caller turns it into a `DashboardWrite` and
 * calls the API (`storage/dashboards.js`'s `buildDashboardWritePayload` does the turning).
 */
export default function CreateDashboardModal({ rows, onCancel, onCreate }) {
 const initialRows = useMemo(
  () => (rows || []).filter((r) => kpiIsWireReady(r.kpi)),
  [rows],
 );
 const notReadyCount = (rows || []).length - initialRows.length;
 const initialMetrics = useMemo(() => initialRows.map((r) => metricFromKpi(r.kpi)), [initialRows]);
 const initialCharts = useMemo(
  () =>
   initialRows.map((r, i) => ({
    id: `manual_${i}`,
    title: r.kpi.name || "",
    chart: "number",
    source: "kpi",
    kpi_identifier: r.kpi.identifier,
    description: r.kpi.description || "",
    guard: { status: "ok", detail: "" },
   })),
  [initialRows],
 );

 const [step, setStep] = useState("mode");
 const [mode, setMode] = useState(null);

 const [name, setName] = useState("");
 const [refresh, setRefresh] = useState(DEFAULT_REFRESH_SCHEDULE);
 const [purposeMetrics, setPurposeMetrics] = useState(initialMetrics);
 const [extraTables, setExtraTables] = useState([]);
 const [charts, setCharts] = useState(initialCharts);
 const [businessAreaFilter, setBusinessAreaFilter] = useState(null);
 const [aiSuggested, setAiSuggested] = useState(false);
 const [purpose, setPurpose] = useState("");
 const [contextSummary, setContextSummary] = useState(null);

 const [submitting, setSubmitting] = useState(false);
 const [submitError, setSubmitError] = useState(null);

 // The metric picker and the filter dropdown both work off the real, `ready` registry - the
 // only KPIs a chart may legally reference. Fetched once; a failure just leaves both empty,
 // the same non-fatal posture `DashboardsPage.jsx`'s own background fetch takes.
 const [readyKpis, setReadyKpis] = useState([]);
 useEffect(() => {
  let cancelled = false;
  fetchKpiList()
   .then((kpis) => {
    if (cancelled) return;
    setReadyKpis(
     (kpis || [])
      .filter((k) => k?.identifier && k?.feasibility?.verdict === "ready")
      .map(metricFromKpi),
    );
   })
   .catch(() => {});
  return () => {
   cancelled = true;
  };
 }, []);

 // Includes `businessAreaFilter` itself, not just `readyKpis`' own areas: the agent validates
 // a suggested area against the deployment's full vocabulary, not against the subset actually
 // represented among currently-ready KPIs, so a valid suggestion can name an area no ready KPI
 // has yet. Without this, that value stays selected in state (and gets submitted) while the
 // <select> silently falls back to "No filter", showing the user something that isn't true.
 const businessAreaOptions = useMemo(
  () =>
   Array.from(
    new Set([...readyKpis.map((k) => k.businessArea), businessAreaFilter].filter(Boolean)),
   ).sort(),
  [readyKpis, businessAreaFilter],
 );

 const suggestionRun = useDashboardSuggestionRun();

 const handleGenerate = ({ purpose: text, chartBudget }) => {
  setPurpose(text);
  setStep("ai-generating");
  suggestionRun.start({
   purpose: text,
   kpiIdentifiers: purposeMetrics.map((m) => m.identifier),
   tableNames: extraTables,
   chartBudget,
  });
 };

 // Once a run succeeds, prefill the builder from it and hand control to the person - nothing
 // here is final until they click Create.
 useEffect(() => {
  if (suggestionRun.status !== "ready" || !suggestionRun.result) return;
  const result = suggestionRun.result;
  setName(result.name || "");
  setCharts((result.charts || []).map(chartFromSuggestion));
  const areaFilter = (result.filters || []).find((f) => f.field === "business_area");
  setBusinessAreaFilter(areaFilter ? areaFilter.value : null);
  setContextSummary(result.context || null);
  setAiSuggested(true);
  setStep("builder");
 }, [suggestionRun.status, suggestionRun.result]);

 const submit = async () => {
  setSubmitting(true);
  setSubmitError(null);
  try {
   await onCreate({ name, refresh, charts, businessAreaFilter });
  } catch (err) {
   setSubmitError(err?.message || "Could not create the dashboard.");
   setSubmitting(false);
  }
 };

 return (
  <Modal
   title="Create dashboard"
   onClose={onCancel}
   maxWidth={step === "builder" ? "max-w-[960px]" : "max-w-[640px]"}
  >
   {step === "mode" && (
    <ModePickerStep
     name={name}
     setName={setName}
     refresh={refresh}
     setRefresh={setRefresh}
     refreshOptions={REFRESH_OPTIONS}
     mode={mode}
     setMode={setMode}
     preselectedCount={initialRows.length}
     onCancel={onCancel}
     onContinue={() => setStep(mode === "ai" ? "ai-purpose" : "builder")}
    />
   )}

   {step === "ai-purpose" && (
    <AiPurposeStep
     initialPurpose={purpose}
     metrics={purposeMetrics}
     setMetrics={setPurposeMetrics}
     readyKpis={readyKpis}
     extraTables={extraTables}
     setExtraTables={setExtraTables}
     onBack={() => setStep("mode")}
     onGenerate={handleGenerate}
    />
   )}

   {step === "ai-generating" && (
    <AiGeneratingStep
     purpose={purpose}
     stage={suggestionRun.stage}
     error={suggestionRun.status === "error" ? suggestionRun.error : null}
     onBack={() => {
      suggestionRun.reset();
      setStep("ai-purpose");
     }}
     onRetry={() => handleGenerate({ purpose, chartBudget: null })}
    />
   )}

   {step === "builder" && (
    <DashboardBuilderStep
     name={name}
     setName={setName}
     refresh={refresh}
     setRefresh={setRefresh}
     refreshOptions={REFRESH_OPTIONS}
     charts={charts}
     setCharts={setCharts}
     businessAreaFilter={businessAreaFilter}
     setBusinessAreaFilter={setBusinessAreaFilter}
     businessAreaOptions={businessAreaOptions}
     readyKpis={readyKpis}
     aiSuggested={aiSuggested}
     contextSummary={contextSummary}
     notReadyCount={initialRows.length > 0 ? notReadyCount : 0}
     onBack={
      aiSuggested
       ? () => {
          // Abandoning the AI suggestion reverts to the preselected metrics, not whatever it
          // last generated -- otherwise a later "Change mode" -> Manual carries the abandoned
          // result's charts/name/filter into what should be a fresh manual flow.
          setAiSuggested(false);
          suggestionRun.reset();
          setCharts(initialCharts);
          setName("");
          setBusinessAreaFilter(null);
          setContextSummary(null);
          setStep("ai-purpose");
         }
       : undefined
     }
     onChangeMode={
      !aiSuggested
       ? () => {
          setMode(null);
          setCharts(initialCharts);
          setPurposeMetrics(initialMetrics);
          setExtraTables([]);
          setName("");
          setBusinessAreaFilter(null);
          setContextSummary(null);
          setStep("mode");
         }
       : undefined
     }
     onCancel={onCancel}
     onSubmit={submit}
     submitting={submitting}
     error={submitError}
    />
   )}
  </Modal>
 );
}
