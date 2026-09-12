import { TrendingUp, TrendingDown, Minus, Activity } from "lucide-react";
import UCBuilderLayout from "./shared/UCBuilderLayout";
import { Card } from "./ui";
import { useUseCaseDraft } from "../context/UseCaseDraftContext";

const KPIS = [
  { id: "revenue", name: "Revenue", value: "$1.2M", trend: "up", change: "+12.3%" },
  { id: "conversion", name: "Conversion Rate", value: "3.8%", trend: "up", change: "+0.5%" },
  { id: "aov", name: "Avg Order Value", value: "$85.40", trend: "down", change: "-2.1%" },
  { id: "traffic", name: "Foot Traffic", value: "45.2K", trend: "up", change: "+8.7%" },
  { id: "stockout", name: "Stockout Rate", value: "2.4%", trend: "flat", change: "0.0%" },
  { id: "nps", name: "Customer NPS", value: "72", trend: "up", change: "+4pts" },
];

const TrendIcon = ({ trend }) => {
  if (trend === "up") return <TrendingUp size={16} className="text-status-success" />;
  if (trend === "down") return <TrendingDown size={16} className="text-red-500" />;
  return <Minus size={16} className="text-text-muted" />;
};

export default function UCBuilderStep8DisplayKPI() {
  const { draft, updateDraft } = useUseCaseDraft();
  const selected = draft.step8.selectedKpiIds;

  const toggleKPI = (id) => {
    updateDraft((d) => {
      const prev = d.step8.selectedKpiIds;
      const next = prev.includes(id) ? prev.filter((k) => k !== id) : [...prev, id];
      return { ...d, step8: { selectedKpiIds: next } };
    });
  };

  return (
    <UCBuilderLayout
      currentStep={8}
      title="Display KPI"
      subtitle="Select KPIs to monitor"
      backPath="/use-cases/builder/step-7"
      nextPath="/use-cases/builder/step-9"
    >
      <div className="max-w-2xl">
        <div className="mb-4 flex items-center gap-2">
          <Activity size={20} className="text-primary" />
          <h3 className="text-sm font-semibold text-text-primary">KPI Selection</h3>
          <span className="text-xs text-text-muted">({selected.length} selected)</span>
        </div>

        <div className="grid grid-cols-2 gap-4">
          {KPIS.map((kpi) => {
            const isSelected = selected.includes(kpi.id);
            return (
              <Card
                key={kpi.id}
                hover
                onClick={() => toggleKPI(kpi.id)}
                className={`relative ${
                  isSelected ? "!border-primary ring-1 ring-primary/20" : ""
                }`}
              >
                {/* Checkbox */}
                <div className="absolute right-4 top-4">
                  <div
                    className={`flex h-5 w-5 items-center justify-center rounded border transition ${
                      isSelected
                        ? "border-primary bg-primary"
                        : "border-border-light bg-surface"
                    }`}
                  >
                    {isSelected && (
                      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
                        <polyline points="20 6 9 17 4 12" />
                      </svg>
                    )}
                  </div>
                </div>

                <p className="text-xs font-medium text-text-muted">{kpi.name}</p>
                <p className="mt-1 text-2xl font-semibold text-text-primary">{kpi.value}</p>
                <div className="mt-2 flex items-center gap-1.5">
                  <TrendIcon trend={kpi.trend} />
                  <span
                    className={`text-xs font-medium ${
                      kpi.trend === "up"
                        ? "text-status-success"
                        : kpi.trend === "down"
                        ? "text-red-500"
                        : "text-text-muted"
                    }`}
                  >
                    {kpi.change}
                  </span>
                </div>
              </Card>
            );
          })}
        </div>
      </div>
    </UCBuilderLayout>
  );
}
