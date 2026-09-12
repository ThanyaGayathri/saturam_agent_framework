import { useEffect, useMemo, useState } from "react";
import { Sparkles, RefreshCw, Database, BookOpen, HelpCircle, Layers } from "lucide-react";
import UCBuilderLayout from "./shared/UCBuilderLayout";
import { Card, Button, Badge } from "./ui";
import { useUseCaseDraft } from "../context/UseCaseDraftContext";
import { useUseCaseCatalog } from "../hooks/useUseCaseCatalog";
import { getDatasetById } from "../domain/kpi-data-catalog";
import { getBuilderEnterpriseDatasetIds, resolveContextualPlanItems } from "../storage/use-case-draft";

// Brief entry animation to reinforce that the AI just generated this
function EntryLoader({ done }) {
  if (done) return null;
  return (
    <div className="mb-6 flex items-center gap-3 rounded-xl border border-primary/35 bg-primary-light dark:bg-primary/15 px-4 py-3">
      <div className="flex items-center gap-1.5">
        {[0, 1, 2].map((i) => (
          <span
            key={i}
            className="h-2 w-2 rounded-full bg-primary animate-bounce"
            style={{ animationDelay: `${i * 0.15}s` }}
          />
        ))}
      </div>
      <p className="text-sm text-text-secondary">Reviewing your data sources and building the plan...</p>
    </div>
  );
}

export default function UCBuilderStep3AIPlan() {
  const { draft, updateDraft } = useUseCaseDraft();
  const { entries } = useUseCaseCatalog();

  // Resolve the catalog entry this draft belongs to (if any) so plan items
  // can use the entry's problemSignal / items[0] / actionType. Falls back
  // to a title-only shim so brand-new drafts still get title-derived steps.
  const catalogEntry = useMemo(() => {
    const targetId =
      draft.builderSession?.reopenedCatalogId ||
      draft.builderSession?.fromSeedTemplateId ||
      null;
    if (targetId) {
      const found = (entries || []).find((e) => e.id === targetId);
      if (found) return found;
    }
    // Title-only shim - lets the entry-derived generator produce steps that
    // at least reference the draft's title.
    return draft.step1?.title ? { title: draft.step1.title } : null;
  }, [entries, draft.builderSession, draft.step1?.title]);

  // Fall back to contextual plan items derived from the catalog entry (or
  // title) when the draft doesn't already carry Step 4 items.
  const contextualDefault = useMemo(
    () =>
      resolveContextualPlanItems(
        draft.step2?.primaryQuestion ?? "",
        draft.step1?.category ?? "",
        (draft.step3?.confirmedSources ?? []).filter((s) => s.enabled),
        (draft.step3?.confirmedKnowledge ?? []).filter((k) => k.enabled),
        draft.step1?.title ?? "",
        catalogEntry
      ),
    [draft, catalogEntry]
  );
  const planItems = draft.step4?.planItems ?? contextualDefault;

  // Show brief entry loader for 1.2s to reinforce AI just ran
  const [loaded, setLoaded] = useState(false);
  useEffect(() => {
    const t = setTimeout(() => setLoaded(true), 1200);
    return () => clearTimeout(t);
  }, []);

  const regenerate = () => {
    setLoaded(false);
    updateDraft((d) => ({
      ...d,
      step4: {
        planItems: resolveContextualPlanItems(
          d.step2?.primaryQuestion ?? "",
          d.step1?.category ?? "",
          (d.step3?.confirmedSources ?? []).filter((s) => s.enabled),
          (d.step3?.confirmedKnowledge ?? []).filter((k) => k.enabled),
          d.step1?.title ?? "",
          catalogEntry
        ),
      },
    }));
    setTimeout(() => setLoaded(true), 1200);
  };

  // Confirmed sources for context badge
  const confirmedSources = (draft.step3?.confirmedSources ?? []).filter((s) => s.enabled);
  const confirmedKnowledge = (draft.step3?.confirmedKnowledge ?? []).filter((k) => k.enabled);
  const galleryIds = useMemo(
    () => getBuilderEnterpriseDatasetIds(draft.step1?.category || "default"),
    [draft.step1?.category]
  );
  const rawEnt = draft.step3?.enterpriseDatasetIds;
  const enterpriseIds = rawEnt == null ? galleryIds : rawEnt;
  const enterpriseDatasets = useMemo(
    () => enterpriseIds.map((id) => getDatasetById(id)).filter(Boolean),
    [enterpriseIds]
  );

  // Question scoping this plan
  const primaryQuestion = draft.step2?.primaryQuestion?.trim() ?? "";
  const allQuestions = primaryQuestion ? [{ text: primaryQuestion }] : [];

  return (
    <UCBuilderLayout
      currentStep={4}
      title="AI Generates Plan"
      subtitle="Review the plan the AI has generated based on your questions and confirmed data sources."
      backPath="/use-cases/builder/step-3"
      nextPath="/use-cases/builder/step-5"
      nextLabel="Accept and Continue"
    >
      <div className="max-w-2xl space-y-6">
        <EntryLoader done={loaded} />

        {/* Context: questions + sources + knowledge */}
        {(allQuestions.length > 0 || confirmedSources.length > 0 || confirmedKnowledge.length > 0 || enterpriseDatasets.length > 0) && (
          <div className="rounded-xl border border-border bg-surface-secondary p-4 space-y-3">
            {allQuestions.length > 0 && (
              <div>
                <p className="mb-1.5 text-[10px] font-semibold uppercase tracking-wide text-text-muted">Question this plan addresses</p>
                <div className="flex flex-wrap gap-2">
                  {allQuestions.map((q, i) => (
                    <span
                      key={i}
                      className="flex items-center gap-1.5 rounded-full border border-primary/35 bg-primary-light dark:bg-primary/15 px-2.5 py-0.5 text-xs font-medium text-primary"
                    >
                      <HelpCircle size={11} />
                      {q.text.length > 80 ? q.text.slice(0, 77) + "..." : q.text}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {(confirmedSources.length > 0 || confirmedKnowledge.length > 0 || enterpriseDatasets.length > 0) && (
              <div>
                <p className="mb-1.5 text-[10px] font-semibold uppercase tracking-wide text-text-muted">Data scope confirmed in Step 3</p>
                <div className="flex flex-wrap gap-2">
                  {confirmedSources.map((s) => (
                    <span key={s.id} className="flex items-center gap-1.5 rounded-full border border-primary/30 bg-primary-light px-2.5 py-0.5 text-xs font-medium text-primary">
                      <Database size={11} />
                      {s.name.split(" - ")[0]}
                    </span>
                  ))}
                  {enterpriseDatasets.map((ds) => (
                    <span
                      key={ds.id}
                      className="flex max-w-full items-center gap-1.5 rounded-full border border-[#c7d7fe] bg-[#f4f7ff] px-2.5 py-0.5 text-xs font-medium text-[#3538cd]"
                      title={ds.technicalName}
                    >
                      <Layers size={11} />
                      <span className="truncate">{ds.sourceLabel}</span>
                    </span>
                  ))}
                  {confirmedKnowledge.map((k) => (
                    <span key={k.id} className="flex items-center gap-1.5 rounded-full border border-[#a9f0d1] bg-[#ecfdf3] px-2.5 py-0.5 text-xs font-medium text-[#067647]">
                      <BookOpen size={11} />
                      {k.name}
                    </span>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        <Card>
          <div className="mb-5 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Sparkles size={20} className="text-primary" />
              <h3 className="text-sm font-semibold text-text-primary">AI-Generated Plan</h3>
              <Badge variant="primary">Auto-generated</Badge>
            </div>
            <Button variant="ghost" size="sm" type="button" onClick={regenerate}>
              <RefreshCw size={14} />
              Regenerate
            </Button>
          </div>

          <ol className="space-y-2">
            {planItems.map((item, i) => (
              <li key={i} className="flex items-start gap-3 rounded-md border border-border bg-surface-default px-3 py-2.5 transition hover:border-primary/30 hover:bg-surface-secondary/60">
                <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-primary text-[10px] font-bold text-white">
                  {i + 1}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium leading-snug text-text-primary">{item.title}</p>
                  <p className="mt-0.5 text-[12px] leading-relaxed text-text-secondary">{item.description}</p>
                </div>
              </li>
            ))}
          </ol>
        </Card>
      </div>
    </UCBuilderLayout>
  );
}
