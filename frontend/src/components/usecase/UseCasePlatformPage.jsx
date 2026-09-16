import { Fragment, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, ChevronDown, ChevronRight, ExternalLink, Pause, Pencil, Play, RefreshCw } from "lucide-react";
import PlatformRunPanel, { RunResultView } from "./PlatformRunPanel";
import { STATUS_PILL } from "../UseCaseGallery";
import { cadenceLabel, specFromPlatform } from "../../domain/use-case-platform";
import { loadSpecDraft, saveSpecDraft } from "../../domain/use-case-draft-spec";
import { emptySpec } from "../../domain/use-case-spec";
import { relativeTimeFromNow as _rel } from "../../domain/alerts-view";
const relativeTimeFromNow = (ts) => _rel(typeof ts === "string" ? new Date(ts).getTime() : ts);
import { activateUseCase, fetchUseCase, fetchUseCaseSpec, listUseCaseRuns, pauseUseCase } from "../../services/use-cases-api";

/**
 * One use case on the platform (K20): what it is, how it is triggered, its run history with
 * each run's own result, and the three things a person does to it -- run it now, pause or
 * resume it, or edit it, which reopens the builder on the same spec.
 */
const RUN_STATUS = { succeeded: "bg-status-success-light text-status-success", failed: "bg-status-critical-light text-status-critical", blocked: "bg-status-warning-light text-status-warning", running: "bg-status-info-light text-status-info", queued: "bg-surface-secondary text-text-secondary", cancelled: "bg-surface-secondary text-text-muted" };
const TRIGGER = { manual: "Run now", schedule: "Schedule", regression: "Regression", activation: "Activation" };

export default function UseCasePlatformPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [useCase, setUseCase] = useState(null);
  const [runs, setRuns] = useState([]);
  const [error, setError] = useState(null);
  const [open, setOpen] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = async () => {
    try {
      const [uc, history] = await Promise.all([fetchUseCase(id), listUseCaseRuns(id)]);
      setUseCase(uc); setRuns(history); setError(null);
    } catch (err) { setError(err?.detail || err?.message || "The platform did not answer."); }
  };
  useEffect(() => { load(); const t = setInterval(load, 15000); return () => clearInterval(t); // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  const pill = STATUS_PILL[useCase?.status] || STATUS_PILL.draft;
  const spec = useMemo(() => useCase && (useCase.tags || []).includes("document_to_record") ? { template: "document_to_record" } : {}, [useCase]);
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
  const live = runs.filter((r) => r.mode === "live");

  return (
    <div className="mx-auto w-full max-w-4xl space-y-4 px-6 py-6">
      <button type="button" onClick={() => navigate("/use-cases/gallery")} className="inline-flex items-center gap-1 text-xs font-semibold text-text-muted hover:text-text-primary"><ArrowLeft size={12} /> User built use cases</button>
      {error && <p className="rounded-lg border border-status-critical-border bg-status-critical-light px-3 py-2 text-sm text-status-critical">{error}</p>}
      {!useCase ? <p className="text-sm text-text-muted">Reading the platform…</p> : (
        <>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <h1 className="text-2xl font-bold text-text-primary">{useCase.title}</h1>
                <span className={`rounded-full px-2.5 py-0.5 text-xs font-semibold ${pill.cls}`}>{pill.label}</span>
                <span className="rounded-full bg-surface-secondary px-2 py-0.5 text-xs text-text-secondary">v{useCase.version}</span>
              </div>
              <p className="mt-1 max-w-2xl text-sm text-text-secondary">{useCase.description}</p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <button type="button" onClick={edit} className="inline-flex items-center gap-1.5 rounded-lg border border-border bg-surface px-3 py-2 text-sm font-semibold text-text-secondary hover:bg-surface-secondary"><Pencil size={14} /> Edit</button>
              {useCase.status === "activated"
                ? <button type="button" disabled={busy} onClick={pause} className="inline-flex items-center gap-1.5 rounded-lg border border-status-warning-border bg-status-warning-light px-3 py-2 text-sm font-semibold text-status-warning disabled:opacity-60"><Pause size={14} /> Pause</button>
                : <button type="button" disabled={busy} onClick={resume} className="inline-flex items-center gap-1.5 rounded-lg border border-status-success-border bg-status-success-light px-3 py-2 text-sm font-semibold text-status-success disabled:opacity-60"><Play size={14} /> {useCase.status === "paused" ? "Resume" : "Activate"}</button>}
            </div>
          </div>

          <div className="grid gap-3 sm:grid-cols-4">
            {[
              ["Business area", useCase.business_area],
              ["Autonomy", `${useCase.autonomy} · level ${useCase.level}`],
              ["Last run", useCase.last_run ? `${useCase.last_run.status} · ${relativeTimeFromNow(useCase.last_run.started_at)}` : "none yet"],
              ["Live runs, all time", String(live.length)],
            ].map(([k, v]) => <div key={k} className="rounded-xl border border-border bg-surface px-3 py-2.5"><p className="text-[11px] font-semibold uppercase tracking-wide text-text-muted">{k}</p><p className="mt-0.5 text-sm font-semibold text-text-primary">{v}</p></div>)}
          </div>

          {useCase.status === "activated" && (
            <PlatformRunPanel useCaseId={id} mode="live" label="Run it now" description="Polls the mailbox, creates the records, sends the exceptions: the same run the schedule makes. Each message is carried once, so a second run finds only what is new." createdAs="draft" onFinished={load} />
          )}

          <section className="rounded-xl border border-border bg-surface p-4">
            <div className="flex items-center justify-between"><h2 className="text-sm font-bold text-text-primary">Run history</h2><button type="button" onClick={load} className="inline-flex items-center gap-1 text-xs text-text-muted hover:text-primary"><RefreshCw size={11} /> Refresh</button></div>
            {runs.length === 0 ? <p className="mt-2 text-sm text-text-muted">No runs yet.</p> : (
              <div className="mt-2 overflow-x-auto rounded-lg border border-border">
                <table className="w-full min-w-[640px] text-sm">
                  <thead className="bg-surface-secondary text-[11px] uppercase tracking-wide text-text-muted"><tr><th className="px-3 py-2 text-left">Started</th><th className="px-3 py-2 text-left">Trigger</th><th className="px-3 py-2 text-left">Mode</th><th className="px-3 py-2 text-left">Status</th><th className="px-3 py-2 text-left">Summary</th></tr></thead>
                  <tbody>
                    {runs.map((r) => (
                      <Fragment key={r.id}>
                        <tr className="cursor-pointer border-t border-border hover:bg-surface-secondary/60" onClick={() => setOpen(open === r.id ? null : r.id)}>
                          <td className="px-3 py-2 text-text-secondary">{open === r.id ? <ChevronDown size={12} className="mr-1 inline" /> : <ChevronRight size={12} className="mr-1 inline" />}{new Date(r.started_at).toLocaleString()} <span className="text-text-muted">· {relativeTimeFromNow(r.started_at)}</span></td>
                          <td className="px-3 py-2 text-xs text-text-secondary">{TRIGGER[r.trigger] || r.trigger}</td>
                          <td className="px-3 py-2 text-xs text-text-secondary">{r.mode}</td>
                          <td className="px-3 py-2"><span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${RUN_STATUS[r.status] || RUN_STATUS.queued}`}>{r.status}</span></td>
                          <td className="px-3 py-2 text-xs text-text-secondary">{r.summary || r.blocked_reason || ""}{r.provenance_ref && <span className="ml-1 text-text-muted">· {r.provenance_ref}</span>}</td>
                        </tr>
                        {open === r.id && (
                          <tr className="border-t border-border-light bg-surface-secondary/30"><td colSpan={5} className="px-4 pb-3">
                            {r.result ? <RunResultView run={r} live={r.mode === "live"} createdAs="draft" /> : <p className="py-2 text-xs text-text-muted">{r.blocked_reason || "No result recorded for this run."}</p>}
                          </td></tr>
                        )}
                      </Fragment>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
          <p className="text-[11px] text-text-muted"><ExternalLink size={11} className="inline" /> Created records link into the target from each run's rows. {spec.template === "document_to_record" ? "Each message is processed once by its id; re-seed the mailbox to run the demo again." : ""}</p>
        </>
      )}
    </div>
  );
}
