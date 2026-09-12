import { useEffect, useRef } from "react";
import { Type, FileText, Tag, FolderOpen } from "lucide-react";
import MiddlebyAnalyticsBuilderLayout from "./MiddlebyAnalyticsBuilderLayout";
import { Input, Select, Card } from "../ui";
import { useUseCaseDraft } from "../../context/UseCaseDraftContext";
import { ANALYTICS_CATEGORY_OPTIONS, createAnalyticsBuilderDraft } from "../../domain/middleby-analytics-builder";

export default function MiddlebyAnalyticsBuilderStep1() {
  const { draft, updateDraft } = useUseCaseDraft();
  const initialized = useRef(false);

  useEffect(() => {
    if (initialized.current) return;
    initialized.current = true;
    if (draft.builderKind !== "analytics") {
      updateDraft(createAnalyticsBuilderDraft());
    }
  }, [draft.builderKind, updateDraft]);

  const { title, description, category, tags } = draft.step1;

  const set = (key, value) =>
    updateDraft((d) => ({ ...d, step1: { ...d.step1, [key]: value } }));

  const canContinue = title.trim().length > 0 && description.trim().length > 0 && category;

  return (
    <MiddlebyAnalyticsBuilderLayout
      currentStep={1}
      title="Basic details"
      subtitle="Name your analytics use case and choose the business area it supports."
      backPath="/analytics"
      nextPath="/analytics/builder/step-2"
      nextDisabled={!canContinue}
    >
      <div className="w-full space-y-6">
        <Card>
          <div className="space-y-5">
            <Input
              label="Use case title"
              required
              placeholder="e.g. Pitco browse-to-quote gap watch"
              icon={<Type size={18} />}
              value={title}
              onChange={(e) => set("title", e.target.value)}
            />
            <div>
              <label className="mb-1.5 block text-sm font-medium text-text-primary">
                Description <span className="text-red-500">*</span>
              </label>
              <div className="relative">
                <span className="absolute left-3 top-3 text-text-muted">
                  <FileText size={18} />
                </span>
                <textarea
                  rows={3}
                  placeholder="What question or signal should this use case track across GA, HubSpot, or LogRocket?"
                  value={description}
                  onChange={(e) => set("description", e.target.value)}
                  className="w-full rounded-lg border border-border-light bg-surface py-2 pl-10 pr-4 text-sm text-text-primary placeholder-text-muted outline-none focus:border-primary focus:ring-1 focus:ring-primary"
                />
              </div>
            </div>
            <Select
              label="Analytics area"
              options={ANALYTICS_CATEGORY_OPTIONS}
              icon={<FolderOpen size={18} />}
              value={category}
              onChange={(e) => set("category", e.target.value)}
            />
            <Input
              label="Tags"
              placeholder="Middleby, Analytics, RFQ"
              icon={<Tag size={18} />}
              value={tags}
              onChange={(e) => set("tags", e.target.value)}
            />
          </div>
        </Card>
      </div>
    </MiddlebyAnalyticsBuilderLayout>
  );
}
