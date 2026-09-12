import { useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { BarChart2, Check, Rocket } from "lucide-react";
import MiddlebyAnalyticsBuilderLayout from "./MiddlebyAnalyticsBuilderLayout";
import { Card } from "../ui";
import { useUseCaseDraft } from "../../context/UseCaseDraftContext";
import { useDemoProfile } from "../../context/DemoProfileContext";
import { useUseCaseCatalog } from "../../hooks/useUseCaseCatalog";
import { useKpiRegistry } from "../../hooks/useKpiRegistry";
import { appendAnalyticsCatalogFromDraft } from "../../storage/use-cases-catalog";
import { flattenMiddlebyKpis } from "../../domain/middleby-analytics-builder";

export default function MiddlebyAnalyticsBuilderStep4() {
  const navigate = useNavigate();
  const { draft, updateDraft, resetDraft } = useUseCaseDraft();
  const { profileId } = useDemoProfile();
  const { catalog, setData } = useUseCaseCatalog();
  const { data: kpiData, hydrated } = useKpiRegistry();

  const linkedKeys = draft.analytics?.linkedKpiKeys ?? [];
  const kpiRows = useMemo(() => flattenMiddlebyKpis(kpiData?.categories), [kpiData]);

  const toggleKpi = (key) => {
    updateDraft((d) => {
      const current = d.analytics?.linkedKpiKeys ?? [];
      const next = current.includes(key) ? current.filter((k) => k !== key) : [...current, key];
      return { ...d, analytics: { ...d.analytics, linkedKpiKeys: next } };
    });
  };

  const enabledSources = (draft.step3?.confirmedSources ?? []).filter((s) => s.enabled);

  const handleLaunch = () => {
    const { catalog: nextCatalog, newEntry } = appendAnalyticsCatalogFromDraft(draft, catalog, profileId);
    setData(nextCatalog);
    resetDraft();
    navigate(`/playground/insight/${encodeURIComponent(newEntry.id)}`, {
      state: newEntry.sampleQuestion ? { initialQuestion: newEntry.sampleQuestion } : undefined,
    });
  };

  return (
    <MiddlebyAnalyticsBuilderLayout
      currentStep={4}
      title="KPIs and launch"
      subtitle="Link analytics KPIs from the registry, then open this use case in an Insight Session."
      backPath="/analytics/builder/step-3"
      nextLabel="Launch use case"
      onNext={handleLaunch}
      nextDisabled={!draft.step1.title.trim() || !draft.step2.primaryQuestion.trim()}
    >
      <div className="max-w-2xl space-y-6">
        <Card>
          <div className="mb-4 flex items-center gap-2">
            <BarChart2 size={18} className="text-primary" />
            <h3 className="text-sm font-semibold text-text-primary">Linked KPIs</h3>
          </div>
          {!hydrated ? (
            <p className="text-sm text-text-secondary">Loading KPI registry…</p>
          ) : kpiRows.length === 0 ? (
            <p className="text-sm text-text-secondary">
              No KPIs in the registry yet. Add KPIs from the KPIs tab in the top navigation.
            </p>
          ) : (
            <div className="space-y-2">
              {kpiRows.map(({ key, section, kpi }) => {
                const selected = linkedKeys.includes(key);
                return (
                  <button
                    key={key}
                    type="button"
                    onClick={() => toggleKpi(key)}
                    className={`flex w-full items-start gap-3 rounded-lg border px-3 py-2.5 text-left transition ${
                      selected ? "border-primary bg-primary-light/40" : "border-border bg-surface hover:border-primary/40"
                    }`}
                  >
                    <span
                      className={`mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded border ${
                        selected ? "border-primary bg-primary text-white" : "border-border-light"
                      }`}
                    >
                      {selected ? <Check size={12} strokeWidth={3} /> : null}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block text-sm font-medium text-text-primary">{kpi.name}</span>
                      <span className="block text-xs text-text-secondary">{section}</span>
                    </span>
                  </button>
                );
              })}
            </div>
          )}
          <p className="mt-3 text-xs text-text-muted">Optional. Manage KPI definitions under KPIs in the top navigation.</p>
        </Card>

        <Card>
          <div className="mb-3 flex items-center gap-2">
            <Rocket size={18} className="text-primary" />
            <h3 className="text-sm font-semibold text-text-primary">Review</h3>
          </div>
          <dl className="space-y-2 text-sm">
            <div>
              <dt className="text-text-muted">Title</dt>
              <dd className="font-medium text-text-primary">{draft.step1.title || "—"}</dd>
            </div>
            <div>
              <dt className="text-text-muted">Question</dt>
              <dd className="text-text-primary">{draft.step2.primaryQuestion || "—"}</dd>
            </div>
            <div>
              <dt className="text-text-muted">Sources</dt>
              <dd className="text-text-primary">
                {enabledSources.length > 0 ? enabledSources.map((s) => s.name).join(", ") : "—"}
              </dd>
            </div>
            <div>
              <dt className="text-text-muted">KPIs</dt>
              <dd className="text-text-primary">
                {linkedKeys.length > 0 ? `${linkedKeys.length} selected` : "None"}
              </dd>
            </div>
          </dl>
        </Card>
      </div>
    </MiddlebyAnalyticsBuilderLayout>
  );
}
