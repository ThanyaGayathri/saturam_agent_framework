import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { AlertTriangle, ArrowLeft, ExternalLink, Loader2, Pause, Pencil, Play, Trash2 } from "lucide-react";
import PlatformRunPanel from "./PlatformRunPanel";
import RunHistory from "./RunHistory";
import PlanViews from "./PlanViews";
import RecommendationsPanel from "./RecommendationsPanel";
import { STATUS_PILL } from "../UseCaseGallery";
import { cadenceLabel, specFromPlatform } from "../../domain/use-case-platform";
import { loadSpecDraft, saveSpecDraft } from "../../domain/use-case-draft-spec";
import { emptySpec } from "../../domain/use-case-spec";
import { relativeTimeFromNow as _rel } from "../../domain/alerts-view";
const relativeTimeFromNow = (ts) => _rel(typeof ts === "string" ? new Date(ts).getTime() : ts);
import { activateUseCase, deleteUseCase, fetchUseCase, fetchUseCaseSpec, listUseCaseRuns, pauseUseCase } from "../../services/use-cases-api";
import { fetchDashboardList } from "../../services/dashboards-api";
import { catalogH1 } from "../shared/useCaseCatalogPageStyles";
import Tabs from "../ui/Tabs";

/**
 * One use case on the platform (K20): what it is, how it is triggered, its run history with
 * each run's own result, and the three things a person does to it -- run it now, pause or
 * resume it, or edit it, which reopens the builder on the same spec.
 */

export default function UseCasePlatformPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [useCase, setUseCase] = useState(null);
  const [runs, setRuns] = useState([]);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  // When the page last read the platform, and whether a read is in flight: the run
  // history's Refresh says so, otherwise a click that changes nothing looks like a click
  // that did nothing (the page also polls every 15 s).
  const [refreshedAt, setRefreshedAt] = useState(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = async () => {
    setRefreshing(true);
    try {
      const [uc, history] = await Promise.all([fetchUseCase(id), listUseCaseRuns(id)]);
      setUseCase(uc); setRuns(history); setError(null); setRefreshedAt(new Date());
    } catch (err) { setError(err?.detail || err?.message || "The platform did not answer."); } finally { setRefreshing(false); }
  };
  useEffect(() => { load(); const t = setInterval(load, 15000); return () => clearInterval(t); // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  const pill = STATUS_PILL[useCase?.status] || STATUS_PILL.draft;
  const spec = useMemo(() => useCase && (useCase.tags || []).includes("document_to_record") ? { template: "document_to_record" } : {}, [useCase]);
  // The at-risk kind (K26): named by its tag, or recognised from what its runs produced.
  const atRisk = useMemo(() => !!useCase && ((useCase.tags || []).includes("at_risk") || runs.some((r) => r?.result?.risk)), [useCase, runs]);
  // "How it runs": the plan derived from the spec the platform holds -- the same views the
  // person approved in the builder, without reopening the builder or touching the draft.
  const [tab, setTab] = useState("runs");
  // An Analytics Automation composes a dashboard at activation, named after the use case
  // (K23). Found by that name under KPIs -> Dashboards; the page links to it.
  const [dashboard, setDashboard] = useState(null);
  useEffect(() => {
    if (!useCase || !(useCase.tags || []).includes("analytics_automation")) return;
    fetchDashboardList().then((rows) => {
      const list = Array.isArray(rows) ? rows : rows?.dashboards || [];
      setDashboard(list.find((d) => d.name === useCase.title && d.owner === "use_case") || null);
    }).catch(() => {});
  }, [useCase]);
  const [fullSpec, setFullSpec] = useState(null);
  useEffect(() => {
    if (tab !== "flow" || fullSpec || !useCase) return;
    fetchUseCaseSpec(id).then((platformSpec) => setFullSpec(specFromPlatform(useCase, platformSpec, emptySpec()))).catch((err) => setError(err?.detail || err?.message || "The platform did not return the spec."));
  }, [tab, fullSpec, useCase, id]);
  const pause = async () => { setBusy(true); try { await pauseUseCase(id); await load(); } finally { setBusy(false); } };
  const resume = async () => { setBusy(true); try { await activateUseCase(id, {}); await load(); } catch (err) { setError(err?.detail || err?.message); } finally { setBusy(false); } };
  const edit = async () => {
    try {
      const platformSpec = await fetchUseCaseSpec(id).catch(() => null);
      const current = loadSpecDraft();
      const base = current?.platform?.useCaseId === id ? current : emptySpec();
      saveSpecDraft(specFromPlatform(useCase, platformSpec, base));
      navigate("/use-cases/builder/compose");
    } catch (err) { setError(err?.detail || err?.message); }
  };
  // Deleting takes the use case, its run history and its golden cases; an activated one is
  // paused first so the schedule stops before the row goes. The dashboard it composed stays
  // under KPIs -> Dashboards. Records it created in the target are not touched: undoing
  // those is the rollback plan's job, per run.
  const remove = async () => {
    const what = useCase.status === "activated" ? "It is running on a schedule; it will be paused, then deleted with its run history and golden cases." : "Its run history and golden cases go with it.";
    if (!window.confirm(`Delete "${useCase.title}"? ${what} Records already created in the target stay.`)) return;
    setBusy(true);
    try {
      if (useCase.status === "activated") await pauseUseCase(id);
      await deleteUseCase(id);
      navigate("/use-cases/gallery");
    } catch (err) { setError(err?.detail || err?.message || "The platform could not delete it."); setBusy(false); }
  };
  const live = runs.filter((r) => r.mode === "live");

  return (
    <div className="mx-auto w-full max-w-4xl space-y-4 px-6 py-6">
      <button type="button" onClick={() => navigate("/use-cases/gallery")} className="inline-flex items-center gap-1 text-xs font-semibold text-text-muted hover:text-text-primary"><ArrowLeft size={12} /> User built use cases</button>
      {error && <p className="flex items-center gap-2 rounded-xl border border-status-critical-border bg-status-critical-light px-3.5 py-3 text-sm text-status-critical"><AlertTriangle size={16} aria-hidden className="shrink-0" />{error}</p>}
      {!useCase ? (
        <div className="flex flex-col items-center gap-3 rounded-2xl border border-border bg-surface py-20">
          <Loader2 size={22} aria-hidden className="animate-spin text-text-muted" />
          <p className="text-sm text-text-muted">Reading the platform…</p>
        </div>
      ) : (
        <div className="space-y-5 rounded-2xl border border-border bg-surface px-6 py-8 sm:px-8">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <h1 className={catalogH1}>{useCase.title}</h1>
                <span className={`rounded-full px-2.5 py-0.5 text-xs font-semibold ${pill.cls}`}>{pill.label}</span>
                <span className="rounded-full bg-surface-secondary px-2 py-0.5 text-xs text-text-secondary">v{useCase.version}</span>
              </div>
              <p className="mt-1 max-w-2xl text-sm text-text-secondary">{useCase.description}</p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              {dashboard && <button type="button" onClick={() => navigate(`/kpis/dashboards/${dashboard.id}`)} className="inline-flex items-center gap-1.5 rounded-lg border border-primary/30 bg-primary-light px-3 py-2 text-sm font-semibold text-primary hover:bg-primary-light/80"><ExternalLink size={14} /> Dashboard</button>}
              <button type="button" onClick={edit} className="inline-flex items-center gap-1.5 rounded-lg border border-border bg-surface px-3 py-2 text-sm font-semibold text-text-secondary hover:bg-surface-secondary"><Pencil size={14} /> Edit</button>
              {useCase.status === "activated"
                ? <button type="button" disabled={busy} onClick={pause} className="inline-flex items-center gap-1.5 rounded-lg border border-status-warning-border bg-status-warning-light px-3 py-2 text-sm font-semibold text-status-warning disabled:opacity-60"><Pause size={14} /> Pause</button>
                : <button type="button" disabled={busy} onClick={resume} className="inline-flex items-center gap-1.5 rounded-lg border border-status-success-border bg-status-success-light px-3 py-2 text-sm font-semibold text-status-success disabled:opacity-60"><Play size={14} /> {useCase.status === "paused" ? "Resume" : "Activate"}</button>}
              <button type="button" disabled={busy} onClick={remove} title="Delete this use case, its runs and golden cases" className="inline-flex items-center gap-1.5 rounded-lg border border-status-critical-border bg-surface px-3 py-2 text-sm font-semibold text-status-critical hover:bg-status-critical-light disabled:opacity-60"><Trash2 size={14} /> Delete</button>
            </div>
          </div>

          <div className="grid gap-3 sm:grid-cols-4">
            {[
              ["Business area", useCase.business_area],
              ["Autonomy", `${useCase.autonomy} · level ${useCase.level}`],
              ["Last run", useCase.last_run ? `${useCase.last_run.status} · ${relativeTimeFromNow(useCase.last_run.started_at)}` : "none yet"],
              ["Live runs, all time", String(live.length)],
            ].map(([k, v]) => <div key={k} className="rounded-xl border border-border bg-surface-secondary px-3 py-2.5"><p className="text-[11px] font-semibold uppercase tracking-wide text-text-muted">{k}</p><p className="mt-0.5 text-sm font-semibold text-text-primary">{v}</p></div>)}
          </div>

          <Tabs size="sm" activeTab={tab} onChange={setTab} tabs={[["runs", "Runs"], ...(atRisk ? [["recommendations", "Recommendations"]] : []), ["flow", "How it runs"]].map(([value, label]) => ({ value, label }))} />

          {tab === "flow" && (fullSpec ? <PlanViews spec={fullSpec} /> : <p className="text-sm text-text-muted">Reading the specification…</p>)}

          {tab === "recommendations" && (
            <div className="space-y-2">
              <p className="text-xs text-text-secondary">What this use case hands to Decision Intelligence: one record per at-risk quote with the play the catalogue chose, who owns it and why. Accepting or dismissing here is the same write a consuming use case makes; a decided record is kept by later runs, an open one is refreshed.</p>
              <RecommendationsPanel useCaseId={id} />
            </div>
          )}

          {tab === "runs" && useCase.status === "activated" && (
            <PlatformRunPanel useCaseId={id} mode="live" label="Run it now" description={atRisk ? "Trains the risk model on the closed history, scores every open quote, chooses a play for each at-risk one, writes the recommendations, checks the briefing's figures, sends the report and the reps' alerts: the same run the schedule makes." : (useCase.tags || []).includes("metric_diagnosis") ? "Reads the KPI, finds where its change sits by arithmetic, has the AI step write the sentence from that ranking, checks every figure, and sends the report: the same run the schedule makes." : (useCase.tags || []).includes("analytics_automation") ? "Reads the KPIs from gold (and the baseline's when one is chosen), writes and checks the narrative, and sends the report: the same run the schedule makes." : "Polls the mailbox, creates the records, sends the exceptions: the same run the schedule makes. Each message is carried once, so a second run finds only what is new."} createdAs="draft" onFinished={load} />
          )}

          {tab === "runs" && <RunHistory runs={runs} onRefresh={load} refreshing={refreshing} refreshedAt={refreshedAt} createdAs="draft" />}
          {tab === "runs" && <p className="text-xs leading-relaxed text-text-muted"><ExternalLink size={11} className="inline" /> Created records link into the target from each run's rows. {spec.template === "document_to_record" ? "Each message is processed once by its id; re-seed the mailbox to run the demo again." : (useCase.tags || []).includes("analytics_automation") ? "Each run reads today's gold (and the baseline date's when a comparison is chosen); the report and its numbers are kept on the run." : ""}</p>}
        </div>
      )}
    </div>
  );
}
