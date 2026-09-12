import { Database, Plus, Snowflake, Server, Cloud } from "lucide-react";
import UCBuilderLayout from "./shared/UCBuilderLayout";
import { Card, Button, Badge, Switch } from "./ui";
import { useUseCaseDraft } from "../context/UseCaseDraftContext";

const SOURCE_ICONS = { Snowflake, Server, Cloud };

export default function UCBuilderStep6KnowledgeRetrieval() {
  const { draft, updateDraft } = useUseCaseDraft();
  const sources = draft.step6.sources;

  const toggleSource = (id) => {
    updateDraft((d) => ({
      ...d,
      step6: {
        sources: d.step6.sources.map((s) => (s.id === id ? { ...s, enabled: !s.enabled } : s)),
      },
    }));
  };

  return (
    <UCBuilderLayout
      currentStep={6}
      title="Knowledge Retrieval"
      subtitle="Configure data sources for this use case"
      backPath="/use-cases/builder/step-5"
      nextPath="/use-cases/builder/step-7"
    >
      <div className="max-w-2xl space-y-6">
        <Card>
          <div className="mb-5 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Database size={20} className="text-primary" />
              <h3 className="text-sm font-semibold text-text-primary">Available Data Sources</h3>
            </div>
            <Button variant="secondary" size="sm">
              <Plus size={14} />
              Add Data Source
            </Button>
          </div>

          <div className="space-y-3">
            {sources.map((source) => {
              const Icon = SOURCE_ICONS[source.iconKey] || Database;
              return (
                <div
                  key={source.id}
                  className={`flex items-center justify-between rounded-lg border p-4 transition ${
                    source.enabled
                      ? "border-primary bg-primary-light"
                      : "border-border bg-surface"
                  }`}
                >
                  <div className="flex items-center gap-3">
                    <div className={`flex h-10 w-10 items-center justify-center rounded-lg ${
                      source.enabled ? "bg-primary/10" : "bg-surface-tertiary"
                    }`}>
                      <Icon size={20} className={source.enabled ? "text-primary" : "text-text-muted"} />
                    </div>
                    <div>
                      <div className="flex items-center gap-2">
                        <p className="text-sm font-semibold text-text-primary">{source.name}</p>
                        {source.enabled && (
                          <Badge variant="success" dot>Connected</Badge>
                        )}
                      </div>
                      <p className="text-xs text-text-secondary">{source.description}</p>
                    </div>
                  </div>

                  <Switch
                    checked={source.enabled}
                    onCheckedChange={() => toggleSource(source.id)}
                    size="comfortable"
                    aria-label={source.enabled ? `Turn off ${source.name}` : `Turn on ${source.name}`}
                  />
                </div>
              );
            })}
          </div>
        </Card>
      </div>
    </UCBuilderLayout>
  );
}
