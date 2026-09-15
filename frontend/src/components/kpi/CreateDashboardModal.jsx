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
 * step feeding an AI suggestion run or straight into the shared builder step. One flow for both
 * entry points - `DashboardsPage.jsx`'s plain "Create dashboard" button and
 * `KpiDiscoveryPage.jsx`'s "create from selection" - so `rows`, when given non-empty, no longer
 * skips the mode picker; it only seeds the metrics both paths start from (the manual builder
 * pre-filled with them directly, the AI path folding them into whatever it suggests below), so
 * a KPI-page selection survives either choice.
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
 const initialMetrics = useMemo(() => initialRows.map((r) => metricFromKpi(r.kpi)), [initialRows]);
 // A plain, deterministic starting sentence for the AI path's purpose box when the caller
 // arrives with a selection already made - not an LLM call just to draft a sentence the
 // person can freely edit or replace before generating.
 const suggestedPurpose = useMemo(() => {
  const names = initialMetrics.map((m) => m.name).filter(Boolean);
  if (names.length === 0) return "";
  if (names.length === 1) return `Track ${names[0]}.`;
  return `Track ${names.slice(0, -1).join(", ")} and ${names[names.length - 1]} together.`;
 }, [initialMetrics]);

 const [step, setStep] = useState("mode");
 const [mode, setMode] = useState(null);

 const [name, setName] = useState("");
 const [refresh, setRefresh] = useState(DEFAULT_REFRESH_SCHEDULE);
 const [metrics, setMetrics] = useState(initialMetrics);
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

 const handleGenerate = (text) => {
  setPurpose(text);
  setStep("ai-generating");
  suggestionRun.start(
   text,
   initialMetrics.map((m) => m.identifier),
  );
 };

 // Once a run succeeds, prefill the builder from it and hand control to the person - nothing
 // here is final until they click Create.
 useEffect(() => {
  if (suggestionRun.status !== "ready" || !suggestionRun.result) return;
  let cancelled = false;
  const result = suggestionRun.result;

  const applyResult = (kpisForLookup) => {
   if (cancelled) return;
   const byIdentifier = new Map(kpisForLookup.map((k) => [k.identifier, k]));
   setName(result.name || "");
   const suggested = (result.tiles || []).map((tile) => ({
    identifier: tile.kpi_identifier,
    name: tile.name || byIdentifier.get(tile.kpi_identifier)?.name || tile.kpi_identifier,
    unit: byIdentifier.get(tile.kpi_identifier)?.unit || "",
    businessArea: byIdentifier.get(tile.kpi_identifier)?.businessArea || "",
   }));
   // Metrics picked on the KPI-discovery grid before opening this modal ride along into the
   // AI path too, not just the manual one - the agent never sees them (it only takes a free-
   // text purpose), so anything it left out is appended here rather than silently dropped.
   const suggestedIds = new Set(suggested.map((m) => m.identifier));
   setMetrics([...suggested, ...initialMetrics.filter((m) => !suggestedIds.has(m.identifier))]);
   const areaFilter = (result.filters || []).find((f) => f.field === "business_area");
   setBusinessAreaFilter(areaFilter ? areaFilter.value : null);
   setAiSuggested(true);
   setStep("builder");
  };

  // A fresh read, not the mount-time `readyKpis` snapshot: a run can take up to the backend's
  // timeout plus its grace window (~180s+60s by default) - long enough for the registry to
  // have moved since this modal opened. Looking a tile up against a stale local copy would
  // fall back to a blank unit/business-area for a KPI the backend, at suggestion time, had
  // just validated as ready. Also updates `readyKpis` itself, so the metric picker and filter
  // dropdown are current too, not just this one prefill.
  fetchKpiList()
   .then((kpis) => {
    if (cancelled) return;
    const fresh = (kpis || [])
     .filter((k) => k?.identifier && k?.feasibility?.verdict === "ready")
     .map(metricFromKpi);
    setReadyKpis(fresh);
    applyResult(fresh);
   })
   .catch(() => applyResult(readyKpis));

  return () => {
   cancelled = true;
  };
  // eslint-disable-next-line react-hooks/exhaustive-deps -- readyKpis intentionally excluded:
  // re-running this on every registry refresh would clobber in-progress edits to the result
  // already prefilled below; the effect only reads it once, as the fetch-failure fallback.
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
     preselectedCount={initialMetrics.length}
     onCancel={onCancel}
     onContinue={() => setStep(mode === "ai" ? "ai-purpose" : "builder")}
    />
   )}

   {step === "ai-purpose" && (
    <AiPurposeStep
     initialPurpose={purpose || suggestedPurpose}
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
      !aiSuggested
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
