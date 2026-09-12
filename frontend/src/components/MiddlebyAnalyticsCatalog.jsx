import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowRight, Plus } from "lucide-react";
import TourLaunchButton from "../tours/TourLaunchButton";
import { useUseCaseCatalog } from "../hooks/useUseCaseCatalog";
import { getMiddlebyTierAUseCases } from "../domain/featured-use-cases";
import { getMiddlebyAnalyticsSearchPlaceholder } from "../domain/middleby-composer-hints";
import { entryMatchesSearchQuery } from "../storage/use-cases-catalog";
import {
  enrichCatalogEntry,
  getMiddlebyAdditionalUseCases,
  getMiddlebyClientKpiUseCases,
  getMiddlebyFutureUseCases,
  getMiddlebyFuturePhaseLabel,
  getMiddlebyClientKpiPhaseLabel,
  getMiddlebyCatalogPhaseLabel,
  getMiddlebyRecommendedDemoUseCases,
  MIDDLEBY_ADDITIONAL_USE_CASES_SUBTITLE,
  MIDDLEBY_CLIENT_KPI_USE_CASES_SUBTITLE,
  MIDDLEBY_FUTURE_USE_CASES_SUBTITLE,
} from "../domain/middleby-catalog-helpers";
import {
  MIDDLEBY_CLIENT_KPI_STAGES,
} from "../domain/middleby-catalog-demo-tiers";
import { CatalogSearchToolbar } from "./shared/CatalogSearchToolbar";
import MiddlebyAnalyticsUseCaseCard from "./middleby/MiddlebyAnalyticsUseCaseCard";
import PeriodFilterChip from "./middleby/PeriodFilterChip";
import { useDemoProfile } from "../context/DemoProfileContext";
import { useChatFilters } from "../hooks/useChatFilters";
import { getKpiNamesForCatalogId } from "../domain/kpi-usecase-links";
import {
  catalogPageInner,
  catalogPageShell,
  catalogH1,
  catalogSubtitle,
  catalogHeaderRow,
  catalogHeaderText,
  catalogPrimaryAction,
} from "./shared/useCaseCatalogPageStyles";

const ZERO_STATE_PROMPTS = [
  "How many high-intent sessions in the last 15 days?",
  "Which brands have the largest browse vs quote gap?",
  "Which quotes need dealer follow-up today?",
];

function CatalogSection({ title, subtitle, entries, showWorkflowBadge = false }) {
  if (!entries.length) return null;

  return (
    <section>
      <h2 className="text-base font-semibold text-text-primary">{title}</h2>
      {subtitle ? <p className="mt-1 text-sm text-text-secondary">{subtitle}</p> : null}
      <div className="mt-4 flex max-w-3xl flex-col gap-3">
        {entries.map((entry) => (
          <MiddlebyAnalyticsUseCaseCard
            key={entry.id}
            title={entry.title}
            problemSignal={entry.problemSignal}
            sampleQuestion={entry.sampleQuestion}
            actionType={entry.actionType}
            sourceLine={entry.sourceLine}
            crossSourcePill={entry.crossSourcePill}
            evidenceLabel={entry.evidenceLabel}
            automationReady={showWorkflowBadge && entry.automationReady}
            automationPlaybookCount={showWorkflowBadge ? entry.automationPlaybookCount : 0}
            automationLine={showWorkflowBadge ? entry.automationLine : ""}
            phaseLabel={entry.phaseLabel || ""}
            kpiNames={getKpiNamesForCatalogId(entry.id)}
            onAnalyze={entry.onAnalyze}
          />
        ))}
      </div>
    </section>
  );
}

/**
 * Middleby-only analytics catalog. Each card opens the Insight Session dashboard.
 */
export default function MiddlebyAnalyticsCatalog() {
  const navigate = useNavigate();
  const { entries, hydrated } = useUseCaseCatalog();
  const { profileId } = useDemoProfile();
  const chatFilters = useChatFilters(profileId);
  const [search, setSearch] = useState("");
  const [showAdditional, setShowAdditional] = useState(false);

  const tierA = useMemo(() => getMiddlebyTierAUseCases(entries), [entries]);

  const searchPlaceholder = useMemo(
    () => getMiddlebyAnalyticsSearchPlaceholder("executive", tierA[0]?.id),
    [tierA]
  );

  const analyticsEntries = useMemo(() => {
    const list = (entries || []).filter((e) => e.source === "seed" || e.source === "user");
    const q = search.trim();
    if (!q) return list;
    return list.filter((e) => entryMatchesSearchQuery(e, q));
  }, [entries, search]);

  const openInsight = (entry) => {
    navigate(`/playground/insight/${encodeURIComponent(entry.id)}`, {
      state: entry.sampleQuestion ? { initialQuestion: entry.sampleQuestion } : undefined,
    });
  };

  const withOpen = (list) =>
    list.map((entry) => ({
      ...entry,
      onAnalyze: () => openInsight(entry),
    }));

  const userCreatedEntries = useMemo(() => {
    if (search.trim()) return [];
    return withOpen(analyticsEntries.filter((e) => e.source === "user").map(enrichCatalogEntry));
  }, [analyticsEntries, search]);

  const recommendedStrip = useMemo(() => {
    if (search.trim()) return [];
    return withOpen(
      getMiddlebyRecommendedDemoUseCases(entries).map((e) => ({
        ...enrichCatalogEntry(e),
        phaseLabel: "★ Priority",
      }))
    );
  }, [entries, search]);

  const clientKpiStrip = useMemo(() => {
    if (search.trim()) return [];
    return withOpen(
      getMiddlebyClientKpiUseCases(entries).map((e) => ({
        ...enrichCatalogEntry(e),
        phaseLabel: getMiddlebyClientKpiPhaseLabel(e.id),
      }))
    );
  }, [entries, search]);

  const clientKpiByStage = useMemo(() => {
    if (search.trim()) return [];
    const byId = new Map(clientKpiStrip.map((e) => [e.id, e]));
    return MIDDLEBY_CLIENT_KPI_STAGES.map((stage) => ({
      ...stage,
      entries: stage.ids.map((id) => byId.get(id)).filter(Boolean),
    })).filter((stage) => stage.entries.length > 0);
  }, [clientKpiStrip, search]);

  const clientKpiTotalCount = useMemo(
    () => clientKpiByStage.reduce((sum, stage) => sum + stage.entries.length, 0),
    [clientKpiByStage]
  );

  const additionalStrip = useMemo(() => {
    if (search.trim()) return [];
    return withOpen(getMiddlebyAdditionalUseCases(entries).map(enrichCatalogEntry));
  }, [entries, search]);

  const futureStrip = useMemo(() => {
    if (search.trim()) return [];
    return withOpen(
      getMiddlebyFutureUseCases(entries).map((e) => ({
        ...enrichCatalogEntry(e),
        phaseLabel: getMiddlebyFuturePhaseLabel(e.id),
      }))
    );
  }, [entries, search]);

  const searchResults = useMemo(() => {
    if (!search.trim()) return [];
    return withOpen(
      analyticsEntries.map((e) => ({
        ...enrichCatalogEntry(e),
        phaseLabel: getMiddlebyCatalogPhaseLabel(e.id),
      }))
    );
  }, [analyticsEntries, search]);

  const catalogSubtitleText = useMemo(() => {
    if (!hydrated) return "Loading catalog…";
    if (search.trim()) {
      return `${analyticsEntries.length} use case${analyticsEntries.length === 1 ? "" : "s"} matching "${search.trim()}"`;
    }
    return `${recommendedStrip.length} use cases of interest, ${clientKpiTotalCount} across the funnel, ${additionalStrip.length} additional, ${futureStrip.length} on the roadmap`;
  }, [
    hydrated,
    search,
    analyticsEntries.length,
    recommendedStrip.length,
    clientKpiTotalCount,
    additionalStrip.length,
    futureStrip.length,
  ]);

  const openBuilder = () => navigate("/analytics/builder/step-1");

  return (
    <div className={catalogPageShell}>
      <div className={catalogPageInner}>
        <div className={catalogHeaderRow}>
          <div className={catalogHeaderText}>
            <div className="flex items-center gap-3">
              <h1 className={catalogH1}>Use cases</h1>
              <TourLaunchButton tourId="middleby-use-cases" label="Take a quick tour" />
            </div>
            <p className={catalogSubtitle}>{catalogSubtitleText}</p>
            <div className="mt-3">
              <PeriodFilterChip chatFilters={chatFilters} />
            </div>
          </div>
          <div className={catalogPrimaryAction} data-tour-catalog="create">
            <button
              type="button"
              onClick={openBuilder}
              className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-primary-hover"
            >
              <Plus className="h-4 w-4" strokeWidth={2.5} aria-hidden />
              Create use case
            </button>
          </div>
        </div>

        <div data-tour-catalog="search">
          <CatalogSearchToolbar
            value={search}
            onValueChange={setSearch}
            placeholder={searchPlaceholder}
          />
        </div>

        {!hydrated ? (
          <p className="text-sm text-text-secondary">Loading catalog…</p>
        ) : analyticsEntries.length === 0 ? (
          <div className="mt-8 rounded-xl border border-border bg-surface-secondary/50 p-6">
            <p className="text-sm text-text-secondary">No use cases match your search.</p>
            <p className="mt-2 text-xs font-semibold uppercase tracking-wide text-text-muted">Try asking</p>
            <ul className="mt-2 space-y-1 text-sm text-text-primary">
              {ZERO_STATE_PROMPTS.map((p) => (
                <li key={p}>· {p}</li>
              ))}
            </ul>
          </div>
        ) : (
          <div className="mt-6 space-y-10">
            {search.trim() ? (
              <CatalogSection
                title={`Search results (${searchResults.length})`}
                entries={searchResults}
              />
            ) : (
              <>
                {userCreatedEntries.length > 0 ? (
                  <CatalogSection
                    title={`Your use cases (${userCreatedEntries.length})`}
                    subtitle="Custom analytics use cases you created in the builder."
                    entries={userCreatedEntries}
                  />
                ) : null}

                <CatalogSection
                  title={`Use cases of interest (${recommendedStrip.length})`}
                  entries={recommendedStrip}
                  showWorkflowBadge
                />

                {clientKpiByStage.length > 0 ? (
                  <section>
                    <h2 className="text-base font-semibold text-text-primary">
                      Analytics across the buyer funnel ({clientKpiTotalCount})
                    </h2>
                    <p className="mt-1 text-sm text-text-secondary">
                      {MIDDLEBY_CLIENT_KPI_USE_CASES_SUBTITLE}
                    </p>
                    <div className="mt-5 space-y-8">
                      {clientKpiByStage.map((stage, index) => (
                        <div key={stage.id}>
                          <div className="flex items-baseline gap-3">
                            <span className="inline-flex h-6 w-6 items-center justify-center rounded-full bg-surface-secondary text-xs font-semibold text-text-primary">
                              {index + 1}
                            </span>
                            <h3 className="text-sm font-semibold uppercase tracking-wide text-text-primary">
                              {stage.label}
                            </h3>
                            <span className="text-xs text-text-muted">
                              {stage.entries.length} use case{stage.entries.length === 1 ? "" : "s"}
                            </span>
                          </div>
                          {stage.subtitle ? (
                            <p className="mt-1 text-xs text-text-secondary">{stage.subtitle}</p>
                          ) : null}
                          <div className="mt-3 flex max-w-3xl flex-col gap-3">
                            {stage.entries.map((entry) => (
                              <MiddlebyAnalyticsUseCaseCard
                                key={entry.id}
                                title={entry.title}
                                problemSignal={entry.problemSignal}
                                sampleQuestion={entry.sampleQuestion}
                                actionType={entry.actionType}
                                sourceLine={entry.sourceLine}
                                crossSourcePill={entry.crossSourcePill}
                                evidenceLabel={entry.evidenceLabel}
                                phaseLabel={entry.phaseLabel || ""}
                                kpiNames={getKpiNamesForCatalogId(entry.id)}
                                onAnalyze={entry.onAnalyze}
                              />
                            ))}
                          </div>
                        </div>
                      ))}
                    </div>
                  </section>
                ) : null}

                {additionalStrip.length > 0 ? (
                  <section>
                    <button
                      type="button"
                      onClick={() => setShowAdditional((v) => !v)}
                      className="inline-flex items-center gap-2 text-sm font-semibold text-primary hover:underline"
                    >
                      {showAdditional
                        ? `Hide ${additionalStrip.length} supporting use cases`
                        : `Show ${additionalStrip.length} more supporting use cases`}
                    </button>
                    {showAdditional ? (
                      <div className="mt-4">
                        <CatalogSection
                          title={`Additional use cases (${additionalStrip.length})`}
                          subtitle={MIDDLEBY_ADDITIONAL_USE_CASES_SUBTITLE}
                          entries={additionalStrip}
                        />
                      </div>
                    ) : null}
                  </section>
                ) : null}

                <CatalogSection
                  title={`On the roadmap (${futureStrip.length})`}
                  subtitle={MIDDLEBY_FUTURE_USE_CASES_SUBTITLE}
                  entries={futureStrip}
                />
              </>
            )}
          </div>
        )}

        <button
          type="button"
          onClick={() => navigate("/playground")}
          className="mt-10 inline-flex items-center gap-2 text-sm font-semibold text-primary hover:underline"
        >
          Back to Home
          <ArrowRight className="h-4 w-4" />
        </button>
      </div>
    </div>
  );
}
