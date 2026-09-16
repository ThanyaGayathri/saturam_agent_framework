import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Blocks, Clock, FileText, Plus, RefreshCw } from "lucide-react";
import { clearSpecDraft, loadSpecDraft } from "../domain/use-case-draft-spec";
import { cadenceLabel } from "../domain/use-case-platform";
import { relativeTimeFromNow as _rel } from "../domain/alerts-view";
const relativeTimeFromNow = (ts) => _rel(typeof ts === "string" ? new Date(ts).getTime() : ts);
import { Button } from "./ui";
import { listUseCases } from "../services/use-cases-api";
import {
  catalogPageShell, catalogPageInner, catalogHeaderRow, catalogHeaderText, catalogH1, catalogSubtitle, catalogPrimaryAction, catalogSectionBelowNav,
} from "./shared/useCaseCatalogPageStyles";
import { UseCasePageNav } from "./shared/UseCasePageNav";
import { CatalogSearchToolbar } from "./shared/CatalogSearchToolbar";
import TourLaunchButton from "../tours/TourLaunchButton";

/**
 * User built use cases (K20): what the platform holds, and nothing else. The prototype's
 * browser-side catalogue is gone from this screen; every card here is a row of
 * `GET /use-cases` created by the builder, with its status, schedule, version and last run
 * as the platform reports them. Opening a card is the use case's own page.
 */
export const STATUS_PILL = {
  activated: { label: "Active", cls: "bg-status-success-light text-status-success" },
  paused: { label: "Paused", cls: "bg-status-warning-light text-status-warning" },
  draft: { label: "Draft", cls: "bg-surface-secondary text-text-secondary" },
  retired: { label: "Retired", cls: "bg-surface-secondary text-text-muted" },
};
const RUN_PILL = { succeeded: "text-status-success", failed: "text-status-critical", blocked: "text-status-warning", running: "text-status-info", queued: "text-text-muted" };

export function summaryOf(useCase) {
  const last = useCase.last_run;
  if (!last) return "No run yet";
  return `${last.status} · ${relativeTimeFromNow(last.started_at)}`;
}

export default function UseCaseGallery() {
  const navigate = useNavigate();
  const [rows, setRows] = useState(null);
  const [error, setError] = useState(null);
  const [search, setSearch] = useState("");
  const draft = useMemo(() => loadSpecDraft(), []);
  const draftInProgress = draft?.goal && !draft.activation;

  const load = () => {
    setError(null);
    listUseCases({ source: "user" }).then((list) => setRows(list.filter((u) => u.source === "user"))).catch((err) => { setRows([]); setError(err?.status === 0 ? "The platform is not running. Start the local API and reload." : err?.detail || err?.message); });
  };
  useEffect(load, []);

  const visible = useMemo(() => {
    const q = search.trim().toLowerCase();
    return (rows || []).filter((u) => !q || `${u.title} ${u.description} ${u.business_area}`.toLowerCase().includes(q));
  }, [rows, search]);
  const openBuilder = () => { clearSpecDraft(); navigate("/use-cases/builder/describe"); };

  return (
    <div className={catalogPageShell}>
      <div className={catalogPageInner}>
        <div className={catalogHeaderRow}>
          <div className={catalogHeaderText}>
            <div className="flex items-center gap-3">
              <h1 className={catalogH1}>User built use cases</h1>
              <TourLaunchButton tourId="use-case-gallery" label="Take a quick User built use cases tour" />
            </div>
            <p className={catalogSubtitle}>Use cases you built on the platform, with their status, schedule and last run. Open a card to see its runs, run it now, or pause it.</p>
          </div>
          <Button size="lg" onClick={openBuilder} className={`${catalogPrimaryAction} gap-1.5`} data-tour-uc="create"><Plus size={18} strokeWidth={2.5} /> Create Use Case</Button>
        </div>
        <div data-tour-uc="tabs"><UseCasePageNav current="gallery" /></div>

        <div className={`${catalogSectionBelowNav} space-y-6`}>
          {draftInProgress && (
            <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-primary/30 bg-primary-light/40 px-4 py-3">
              <div className="min-w-0 text-sm"><p className="font-semibold text-text-primary">Draft in progress: {draft.title || draft.goal}</p><p className="text-text-secondary">Not on the platform yet. Continue in the builder to rehearse and activate it.</p></div>
              <Button onClick={() => navigate("/use-cases/builder/compose")}>Continue</Button>
            </div>
          )}

          <div className="flex flex-wrap items-center gap-3">
            <CatalogSearchToolbar value={search} onValueChange={setSearch} placeholder="Search your use cases" />
            <button type="button" onClick={load} className="ml-auto inline-flex items-center gap-1 text-xs font-semibold text-text-muted hover:text-primary"><RefreshCw size={12} /> Refresh</button>
          </div>

          {error && <p className="rounded-lg border border-status-critical-border bg-status-critical-light px-3 py-2 text-sm text-status-critical">{error}</p>}
          {rows === null && !error && <p className="text-sm text-text-muted">Reading the platform…</p>}
          {rows !== null && visible.length === 0 && !error && (
            <div className="rounded-xl border border-dashed border-border px-6 py-10 text-center">
              <Blocks size={28} className="mx-auto text-text-muted" />
              <p className="mt-2 text-sm font-semibold text-text-primary">{search ? "No use case matches your search." : "No use cases built yet."}</p>
              <p className="mt-1 text-sm text-text-secondary">{search ? "Clear the search to see them all." : "Build one from a description, rehearse it, and activate it; it appears here with its runs."}</p>
              {!search && <Button className="mt-4" onClick={openBuilder}>Create Use Case</Button>}
            </div>
          )}

          {visible.length > 0 && (
            <section aria-label="Your use cases">
              <div className="mb-3 flex items-center gap-2"><h2 className="text-base font-bold text-text-primary">Your use cases</h2><span className="rounded-full bg-surface-secondary px-2 py-0.5 text-xs font-semibold text-text-secondary">{visible.length}</span></div>
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                {visible.map((u) => {
                  const pill = STATUS_PILL[u.status] || STATUS_PILL.draft;
                  const last = u.last_run;
                  return (
                    <button key={u.id} type="button" onClick={() => navigate(`/use-cases/platform/${encodeURIComponent(u.id)}`)} className="group flex flex-col rounded-2xl border border-border bg-surface p-4 text-left shadow-sm transition hover:border-primary/50 hover:shadow-md">
                      <div className="flex items-start justify-between gap-2">
                        <h3 className="text-sm font-bold text-text-primary">{u.title}</h3>
                        <span className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] font-semibold ${pill.cls}`}>{pill.label}</span>
                      </div>
                      <p className="mt-1 line-clamp-3 text-sm text-text-secondary">{u.description}</p>
                      <div className="mt-3 flex flex-wrap gap-1.5 text-[11px]">
                        <span className="rounded-md bg-surface-secondary px-2 py-0.5 text-text-secondary">{u.business_area}</span>
                        {(u.tags || []).filter((t) => t !== "process_automation").map((t) => <span key={t} className="rounded-md bg-surface-secondary px-2 py-0.5 text-text-secondary">{t.replace(/_/g, " ")}</span>)}
                        <span className="rounded-md bg-surface-secondary px-2 py-0.5 text-text-secondary">v{u.version}</span>
                      </div>
                      <div className="mt-3 flex items-center justify-between border-t border-border-light pt-2.5 text-xs">
                        <span className="inline-flex items-center gap-1 text-text-muted"><Clock size={12} /> {last ? <><span className={RUN_PILL[last.status] || ""}>{last.status}</span> · {relativeTimeFromNow(last.started_at)}</> : "no run yet"}</span>
                        <span className="text-primary opacity-0 transition group-hover:opacity-100">Open →</span>
                      </div>
                    </button>
                  );
                })}
              </div>
            </section>
          )}
          <p className="flex items-center gap-1.5 text-[11px] text-text-muted"><FileText size={11} /> Pre-defined templates are on the other tab; a use case appears here once it is saved to the platform, which the first rehearsal does.</p>
        </div>
      </div>
    </div>
  );
}
