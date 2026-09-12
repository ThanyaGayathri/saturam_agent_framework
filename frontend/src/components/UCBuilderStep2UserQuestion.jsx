import { HelpCircle, Sparkles } from "lucide-react";
import UCBuilderLayout from "./shared/UCBuilderLayout";
import { Card } from "./ui";
import { useUseCaseDraft } from "../context/UseCaseDraftContext";
import { QUESTION_CHIPS_BY_AREA } from "../storage/use-case-draft";

export default function UCBuilderStep2UserQuestion() {
  const { draft, updateDraft } = useUseCaseDraft();
  const primaryQuestion = draft.step2.primaryQuestion;

  const category = draft.step1.category;
  const chips = QUESTION_CHIPS_BY_AREA[category] ?? QUESTION_CHIPS_BY_AREA["inventory"];

  const setQuestion = (q) =>
    updateDraft((d) => ({ ...d, step2: { ...d.step2, primaryQuestion: q } }));

  return (
    <UCBuilderLayout
      currentStep={2}
      title="User Question"
      subtitle="What is the main question this use case should answer? The AI uses this to scope the plan and select relevant data."
      backPath="/use-cases/builder/step-1"
      nextPath="/use-cases/builder/step-3"
    >
      <div className="max-w-2xl space-y-6">
        {/* Primary Question */}
        <Card>
          <div className="mb-4 flex items-center gap-2">
            <HelpCircle size={20} className="text-primary" />
            <h3 className="text-sm font-semibold text-text-primary">Primary Question</h3>
            <span className="rounded-full bg-[#fef3f2] px-2 py-0.5 text-[11px] font-semibold text-[#b42318]">Required</span>
          </div>
          <textarea
            rows={4}
            placeholder="e.g. Which SKUs are at risk of stockout in the next 14 days?"
            value={primaryQuestion}
            onChange={(e) => setQuestion(e.target.value)}
            className="w-full rounded-lg border border-border-light bg-surface px-4 py-2 text-sm text-text-primary placeholder-text-muted outline-none focus:border-primary focus:ring-1 focus:ring-primary"
          />
        </Card>

        {/* AI-suggested chips */}
        {chips.length > 0 && (
          <div className="rounded-xl border border-border bg-surface-secondary p-4">
            <div className="mb-3 flex items-center gap-2">
              <Sparkles size={15} className="text-primary" />
              <p className="text-xs font-semibold uppercase tracking-wide text-text-secondary">
                Suggested for {draft.step1.category ? draft.step1.category.replace(/-/g, " ") : "your business area"}
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              {chips.map((chip) => (
                <button
                  key={chip}
                  type="button"
                  onClick={() => setQuestion(chip)}
                  className="rounded-full border border-primary/35 bg-surface px-3 py-1 text-xs font-medium text-primary transition hover:bg-primary-light dark:bg-primary/15 hover:border-primary"
                >
                  {chip}
                </button>
              ))}
            </div>
            <p className="mt-2 text-[11px] text-text-muted">Click a suggestion to use it as your question.</p>
          </div>
        )}
      </div>
    </UCBuilderLayout>
  );
}
