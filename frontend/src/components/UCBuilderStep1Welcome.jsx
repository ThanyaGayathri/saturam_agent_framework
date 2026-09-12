import { useMemo } from "react";
import { Type, FileText, Tag, FolderOpen, Bot, CheckSquare } from "lucide-react";
import UCBuilderLayout from "./shared/UCBuilderLayout";
import { Input, Select, Card } from "./ui";
import { useUseCaseDraft } from "../context/UseCaseDraftContext";
import { useBusinessAreas } from "../hooks/useBusinessAreas";

/**
 * Business-area id → builder-draft category slug. Kept in sync with
 * `SEED_BUSINESS_AREA_TO_CATEGORY` in `use-cases-catalog.js` so the drop-down
 * value matches what step 3 / builder resolvers expect.
 */
const AREA_ID_TO_CATEGORY_VALUE = {
  inventory: "inventory",
  orders: "orders",
  merchandise: "merchandise",
  "store-ops": "store-ops",
  "e-com": "ecom",
  pos: "pos",
  support: "support",
  pricing: "pricing",
  finance: "finance",
  workforce: "workforce",
};

const AUTONOMY_OPTIONS = [
  {
    value: "copilot",
    label: "Co-Pilot",
    description: "AI prepares Decision Cards with recommended actions. You review and approve before anything is executed.",
    accent: "border-primary bg-primary-light dark:border-primary/50 dark:bg-primary/15",
    badge: "bg-primary-light text-primary dark:bg-primary/25 dark:text-primary",
    icon: "🧑‍✈️",
  },
  {
    value: "autopilot",
    label: "Autopilot",
    description: "AI acts autonomously within configured guardrails. You monitor exceptions and review the action log.",
    accent: "border-primary bg-primary-light dark:border-primary/50 dark:bg-primary/15",
    badge: "bg-primary-light text-primary dark:bg-primary/25 dark:text-primary",
    icon: "🤖",
  },
];

export default function UCBuilderStep1Welcome() {
  const { draft, updateDraft } = useUseCaseDraft();
  const { title, description, category, tags, autonomyLevel } = draft.step1;
  const { areas: businessAreas } = useBusinessAreas();

  // Business Area drop-down is derived from the persisted business hierarchy
  // so it reflects the active profile (Media shows Content / OTT Platform /
  // Broadcast / etc.; retail shows Inventory / Orders / Merchandise / etc.).
  const categoryOptions = useMemo(() => {
    const opts = [{ value: "", label: "Select a business area" }];
    for (const ba of businessAreas || []) {
      const slug = AREA_ID_TO_CATEGORY_VALUE[ba.id] || ba.id;
      opts.push({ value: slug, label: ba.label });
    }
    return opts;
  }, [businessAreas]);

  const set = (key, value) =>
    updateDraft((d) => ({ ...d, step1: { ...d.step1, [key]: value } }));

  return (
    <UCBuilderLayout
      currentStep={1}
      title="Basic Details"
      subtitle="Give your use case a name, describe what it does, and choose how the AI will operate."
      backPath="/use-cases"
      nextPath="/use-cases/builder/step-2"
    >
      <div className="w-full space-y-6">
        {draft.builderSession?.fromSeedTemplateId ? (
          <div className="rounded-lg border border-primary/30 bg-primary-light px-4 py-3 text-sm text-text-secondary dark:border-primary/40 dark:bg-primary/15">
            Prefilled from the catalog template you opened in chat. Chat is for discovery; the builder is where you
            productionize the same idea into a monitored workflow.
          </div>
        ) : null}
        {/* Basic info */}
        <Card>
          <div className="space-y-5">
            <Input
              label="Use Case Title"
              required
              placeholder="e.g. Low Stock Auto-Reorder - Electronics"
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
                  placeholder="Describe what this use case monitors, analyses, and does..."
                  value={description}
                  onChange={(e) => set("description", e.target.value)}
                  className="w-full rounded-lg border border-border-light bg-surface py-2 pl-10 pr-4 text-sm text-text-primary placeholder-text-muted outline-none focus:border-primary focus:ring-1 focus:ring-primary"
                />
              </div>
            </div>

            <Select
              label="Business Area"
              options={categoryOptions}
              icon={<FolderOpen size={18} />}
              value={category}
              onChange={(e) => set("category", e.target.value)}
            />

            <Input
              label="Tags"
              placeholder="e.g. demand-forecasting, supply-chain"
              icon={<Tag size={18} />}
              value={tags}
              onChange={(e) => set("tags", e.target.value)}
            />
            <p className="-mt-4 text-sm text-text-secondary">Separate multiple tags with commas.</p>
          </div>
        </Card>

        {/* Autonomy level */}
        <div>
          <div className="mb-3 flex items-center gap-2">
            <Bot size={18} className="text-primary" />
            <h3 className="text-sm font-semibold text-text-primary">Autonomy Level</h3>
            <span className="rounded-full bg-[#fef3f2] px-2 py-0.5 text-[11px] font-semibold text-[#b42318]">Required</span>
          </div>
          <p className="mb-3 text-sm text-text-secondary">
            Choose how the AI operates when this use case runs. This affects how outputs are delivered in every step.
          </p>
          <div className="grid gap-3 sm:grid-cols-2">
            {AUTONOMY_OPTIONS.map((opt) => {
              const selected = autonomyLevel === opt.value;
              return (
                <button
                  key={opt.value}
                  type="button"
                  onClick={() => set("autonomyLevel", opt.value)}
                  className={`group flex flex-col gap-2 rounded-xl border-2 p-4 text-left transition-all ${
                    selected
                      ? `${opt.accent} shadow-sm`
                      : "border-border bg-surface hover:border-border-light dark:hover:border-border"
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <span className="text-lg">{opt.icon}</span>
                      <span className={`rounded-full px-2.5 py-0.5 text-xs font-semibold ${selected ? opt.badge : "bg-surface-secondary text-text-secondary"}`}>
                        {opt.label}
                      </span>
                    </div>
                    <span className={`flex h-5 w-5 items-center justify-center rounded-full border-2 transition-all ${selected ? "border-current bg-current" : "border-border-light bg-surface"}`}>
                      {selected && <CheckSquare size={10} className="text-white fill-white" />}
                    </span>
                  </div>
                  <p className="text-xs leading-relaxed text-text-secondary">{opt.description}</p>
                </button>
              );
            })}
          </div>
        </div>
      </div>
    </UCBuilderLayout>
  );
}
