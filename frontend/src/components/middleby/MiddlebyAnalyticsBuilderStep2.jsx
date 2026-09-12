import { HelpCircle, Sparkles } from "lucide-react";
import MiddlebyAnalyticsBuilderLayout from "./MiddlebyAnalyticsBuilderLayout";
import { Card } from "../ui";
import { useUseCaseDraft } from "../../context/UseCaseDraftContext";
import { ANALYTICS_QUESTION_CHIPS } from "../../domain/middleby-analytics-builder";

export default function MiddlebyAnalyticsBuilderStep2() {
  const { draft, updateDraft } = useUseCaseDraft();
  const primaryQuestion = draft.step2.primaryQuestion;
  const category = draft.step1.category;
  const chips = ANALYTICS_QUESTION_CHIPS[category] ?? ANALYTICS_QUESTION_CHIPS.rfq;

  const setQuestion = (q) =>
    updateDraft((d) => ({ ...d, step2: { ...d.step2, primaryQuestion: q } }));

  return (
    <MiddlebyAnalyticsBuilderLayout
      currentStep={2}
      title="Analytics question"
      subtitle="What should leadership or ops be able to ask when they open this use case?"
      backPath="/analytics/builder/step-1"
      nextPath="/analytics/builder/step-3"
      nextDisabled={!primaryQuestion.trim()}
    >
      <div className="max-w-2xl space-y-6">
        <Card>
          <div className="mb-4 flex items-center gap-2">
            <HelpCircle size={20} className="text-primary" />
            <h3 className="text-sm font-semibold text-text-primary">Primary question</h3>
            <span className="rounded-full bg-[#fef3f2] px-2 py-0.5 text-[11px] font-semibold text-[#b42318]">Required</span>
          </div>
          <textarea
            rows={4}
            placeholder="e.g. Which brands have the biggest gap between traffic and quotes?"
            value={primaryQuestion}
            onChange={(e) => setQuestion(e.target.value)}
            className="w-full rounded-lg border border-border-light bg-surface px-4 py-2 text-sm text-text-primary placeholder-text-muted outline-none focus:border-primary focus:ring-1 focus:ring-primary"
          />
        </Card>

        {chips.length > 0 ? (
          <div className="rounded-xl border border-border bg-surface-secondary p-4">
            <div className="mb-3 flex items-center gap-2">
              <Sparkles size={15} className="text-primary" />
              <p className="text-xs font-semibold uppercase tracking-wide text-text-secondary">
                Suggested for {category ? category.replace(/-/g, " ") : "analytics"}
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              {chips.map((chip) => (
                <button
                  key={chip}
                  type="button"
                  onClick={() => setQuestion(chip)}
                  className="rounded-full border border-primary/35 bg-surface px-3 py-1 text-xs font-medium text-primary transition hover:bg-primary-light dark:bg-primary/15"
                >
                  {chip}
                </button>
              ))}
            </div>
          </div>
        ) : null}
      </div>
    </MiddlebyAnalyticsBuilderLayout>
  );
}
