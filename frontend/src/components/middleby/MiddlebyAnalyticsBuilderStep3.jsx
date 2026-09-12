import { Check, Cloud, Database, Server } from "lucide-react";
import MiddlebyAnalyticsBuilderLayout from "./MiddlebyAnalyticsBuilderLayout";
import { Card } from "../ui";
import { useUseCaseDraft } from "../../context/UseCaseDraftContext";
import { MIDDLEBY_ANALYTICS_DATA_SOURCES } from "../../domain/middleby-analytics-builder";

function getIcon(iconKey) {
  if (iconKey === "Cloud") return <Cloud size={16} className="text-primary" />;
  return <Server size={16} className="text-primary" />;
}

export default function MiddlebyAnalyticsBuilderStep3() {
  const { draft, updateDraft } = useUseCaseDraft();
  const sources = draft.step3?.confirmedSources ?? [];

  const toggleSource = (id) => {
    updateDraft((d) => {
      const list = d.step3?.confirmedSources ?? [];
      const exists = list.find((s) => s.id === id);
      if (exists) {
        return {
          ...d,
          step3: {
            ...d.step3,
            confirmedSources: list.map((s) =>
              s.id === id ? { ...s, enabled: !s.enabled } : s
            ),
          },
        };
      }
      const template = MIDDLEBY_ANALYTICS_DATA_SOURCES.find((s) => s.id === id);
      if (!template) return d;
      return {
        ...d,
        step3: {
          ...d.step3,
          confirmedSources: [
            ...list,
            {
              id: template.id,
              name: template.name,
              description: template.description,
              iconKey: template.iconKey,
              enabled: true,
              syncAge: "Sample export",
            },
          ],
        },
      };
    });
  };

  const enabledCount = sources.filter((s) => s.enabled).length;

  return (
    <MiddlebyAnalyticsBuilderLayout
      currentStep={3}
      title="Data sources"
      subtitle="Select the Middleby systems this use case will read from. Map planned sources in Configure (connection status reflects what is available today)."
      backPath="/analytics/builder/step-2"
      nextPath="/analytics/builder/step-4"
      nextDisabled={enabledCount === 0}
    >
      <div className="max-w-2xl space-y-4">
        <div className="flex items-center gap-2 text-sm text-text-secondary">
          <Database size={16} className="text-primary" />
          <span>{enabledCount} source{enabledCount === 1 ? "" : "s"} selected</span>
        </div>
        <Card>
          <div className="space-y-3">
            {MIDDLEBY_ANALYTICS_DATA_SOURCES.map((template) => {
              const row = sources.find((s) => s.id === template.id);
              const enabled = row?.enabled ?? false;
              return (
                <div
                  key={template.id}
                  className={`flex items-start gap-3 rounded-lg border px-4 py-3 transition ${
                    enabled ? "border-primary bg-primary-light/40" : "border-border bg-surface"
                  }`}
                >
                  <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-surface-tertiary">
                    {getIcon(template.iconKey)}
                  </div>
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium text-text-primary">{template.name}</p>
                    <p className="mt-0.5 text-xs text-text-secondary">{template.description}</p>
                  </div>
                  <button
                    type="button"
                    onClick={() => toggleSource(template.id)}
                    className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-md border-2 transition ${
                      enabled
                        ? "border-primary bg-primary text-white"
                        : "border-border-light bg-surface text-transparent hover:border-primary"
                    }`}
                  >
                    <Check size={13} strokeWidth={3} />
                  </button>
                </div>
              );
            })}
          </div>
        </Card>
      </div>
    </MiddlebyAnalyticsBuilderLayout>
  );
}
