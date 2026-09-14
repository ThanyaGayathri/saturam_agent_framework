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
  businessArea: kpi.businessAreaId || kpi.business_area || "",
 };
}

/**
 * Create dashboard: a mode picker (AI generated vs. manual), then either a chat-style purpose
 * step feeding an AI suggestion run or straight into the shared builder step. `rows`, when
 * given non-empty (the existing call site from `KpiDiscoveryPage.jsx`, selecting metrics from
 * the discovery grid), skips the mode picker entirely and opens straight into the manual
 * builder pre-filled with them - the same entry point as before, now also offering the filter
 * this flow adds.
 *
 * `onCreate({ name, refresh, metrics, businessAreaFilter })` is the caller's job, same split as
 * before: this modal collects and edits state, the caller turns it into a `DashboardWrite` and
 * calls the API (`storage/dashboards.js`'s `buildDashboardWritePayload` does the turning).
 */
export default function CreateDashboardModal({ rows, onCancel, onCreate }) {
 const initialRows = useMemo(
  () => (rows || []).filter((r) => kpiIsWireReady(r.kpi)),
  [rows],
 );
 const hasInitialRows = initialRows.length > 0;
 const notReadyCount = (rows || []).length - initialRows.length;

 const [step, setStep] = useState(hasInitialRows ? "builder" : "mode");
 const [mode, setMode] = useState(null);

 const [name, setName] = useState("");
 const [refresh, setRefresh] = useState(DEFAULT_REFRESH_SCHEDULE);
 const [metrics, setMetrics] = useState(() => initialRows.map((r) => metricFromKpi(r.kpi)));
 const [businessAreaFilter, setBusinessAreaFilter] = useState(null);
 const [aiSuggested, setAiSuggested] = useState(false);
 const [purpose, setPurpose] = useState("");

 const [submitting, setSubmitting] = useState(false);
 const [submitError, setSubmitError] = useState(null);

 // The metric picker and the filter dropdown both work off the real, `ready` registry - the
 // only KPIs a tile may legally reference. Fetched once; a failure just leaves both empty,
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

 const businessAreaOptions = useMemo(
  () => Array.from(new Set(readyKpis.map((k) => k.businessArea).filter(Boolean))).sort(),
  [readyKpis],
 );

 const suggestionRun = useDashboardSuggestionRun();

 const handleGenerate = (text) => {
  setPurpose(text);
  setStep("ai-generating");
  suggestionRun.start(text);
 };

 // Once a run succeeds, prefill the builder from it and hand control to the person - nothing
 // here is final until they click Create.
 useEffect(() => {
  if (suggestionRun.status !== "ready" || !suggestionRun.result) return;
  const result = suggestionRun.result;
  const byIdentifier = new Map(readyKpis.map((k) => [k.identifier, k]));
  setName(result.name || "");
  setMetrics(
   (result.tiles || []).map((tile) => ({
    identifier: tile.kpi_identifier,
    name: tile.name || byIdentifier.get(tile.kpi_identifier)?.name || tile.kpi_identifier,
    unit: byIdentifier.get(tile.kpi_identifier)?.unit || "",
    businessArea: byIdentifier.get(tile.kpi_identifier)?.businessArea || "",
   })),
  );
  const areaFilter = (result.filters || []).find((f) => f.field === "business_area");
  setBusinessAreaFilter(areaFilter ? areaFilter.value : null);
  setAiSuggested(true);
  setStep("builder");
  // eslint-disable-next-line react-hooks/exhaustive-deps -- readyKpis intentionally excluded:
  // re-running this on every registry refresh would clobber in-progress edits to the result
  // already prefilled below.
 }, [suggestionRun.status, suggestionRun.result]);

 const submit = async () => {
  setSubmitting(true);
  setSubmitError(null);
  try {
   await onCreate({ name, refresh, metrics, businessAreaFilter });
  } catch (err) {
   setSubmitError(err?.message || "Could not create the dashboard.");
   setSubmitting(false);
  }
 };

 return (
  <Modal
   title="Create dashboard"
   onClose={onCancel}
   maxWidth={step === "builder" ? "max-w-[720px]" : "max-w-[560px]"}
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
     onCancel={onCancel}
     onContinue={() => setStep(mode === "ai" ? "ai-purpose" : "builder")}
    />
   )}

   {step === "ai-purpose" && (
    <AiPurposeStep
     initialPurpose={purpose}
     onBack={() => setStep("mode")}
     onGenerate={handleGenerate}
    />
   )}

   {step === "ai-generating" && (
    <AiGeneratingStep
     purpose={purpose}
     error={suggestionRun.status === "error" ? suggestionRun.error : null}
     onBack={() => {
      suggestionRun.reset();
      setStep("ai-purpose");
     }}
     onRetry={() => handleGenerate(purpose)}
    />
   )}

   {step === "builder" && (
    <DashboardBuilderStep
     name={name}
     setName={setName}
     refresh={refresh}
     setRefresh={setRefresh}
     refreshOptions={REFRESH_OPTIONS}
     metrics={metrics}
     setMetrics={setMetrics}
     businessAreaFilter={businessAreaFilter}
     setBusinessAreaFilter={setBusinessAreaFilter}
     businessAreaOptions={businessAreaOptions}
     readyKpis={readyKpis}
     aiSuggested={aiSuggested}
     notReadyCount={hasInitialRows ? notReadyCount : 0}
     onBack={
      aiSuggested
       ? () => {
          setAiSuggested(false);
          suggestionRun.reset();
          setStep("ai-purpose");
         }
       : undefined
     }
     onChangeMode={
      !hasInitialRows && !aiSuggested
       ? () => {
          setMode(null);
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
