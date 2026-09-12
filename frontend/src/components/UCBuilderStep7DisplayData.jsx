import { Table, BarChart3, LineChart, PieChart, Eye, GripVertical } from "lucide-react";
import UCBuilderLayout from "./shared/UCBuilderLayout";
import { Card } from "./ui";
import { useUseCaseDraft } from "../context/UseCaseDraftContext";

const CHART_TYPES = [
  { id: "table", label: "Table", icon: Table },
  { id: "bar", label: "Bar Chart", icon: BarChart3 },
  { id: "line", label: "Line Chart", icon: LineChart },
  { id: "pie", label: "Pie Chart", icon: PieChart },
];

export default function UCBuilderStep7DisplayData() {
  const { draft, updateDraft } = useUseCaseDraft();
  const { selectedChart, columns } = draft.step7;

  const toggleColumn = (index) => {
    updateDraft((d) => ({
      ...d,
      step7: {
        ...d.step7,
        columns: d.step7.columns.map((col, i) =>
          i === index ? { ...col, visible: !col.visible } : col
        ),
      },
    }));
  };

  return (
    <UCBuilderLayout
      currentStep={7}
      title="Display Data"
      subtitle="Configure how data is presented"
      backPath="/use-cases/builder/step-6"
      nextPath="/use-cases/builder/step-8"
    >
      <div className="max-w-2xl space-y-6">
        {/* Chart Type Selector */}
        <div>
          <h3 className="mb-3 text-sm font-semibold text-text-primary">Chart Type</h3>
          <div className="grid grid-cols-4 gap-3">
            {CHART_TYPES.map((chart) => {
              const Icon = chart.icon;
              const isSelected = selectedChart === chart.id;
              return (
                <Card
                  key={chart.id}
                  hover
                  onClick={() =>
                    updateDraft((d) => ({
                      ...d,
                      step7: { ...d.step7, selectedChart: chart.id },
                    }))
                  }
                  className={`!p-4 text-center ${
                    isSelected ? "!border-primary !bg-primary-light" : ""
                  }`}
                >
                  <Icon
                    size={24}
                    className={`mx-auto ${isSelected ? "text-primary" : "text-text-muted"}`}
                  />
                  <p className={`mt-2 text-xs font-medium ${
                    isSelected ? "text-primary" : "text-text-secondary"
                  }`}>
                    {chart.label}
                  </p>
                </Card>
              );
            })}
          </div>
        </div>

        {/* Preview Area */}
        <Card className="!bg-surface-tertiary">
          <div className="flex flex-col items-center justify-center py-12">
            <Eye size={40} className="text-text-muted" />
            <p className="mt-3 text-sm font-medium text-text-secondary">Preview Area</p>
            <p className="mt-1 text-xs text-text-muted">
              A live preview of the selected chart type will appear here
            </p>
          </div>
        </Card>

        {/* Column Configuration */}
        <Card>
          <h3 className="mb-4 text-sm font-semibold text-text-primary">Column Configuration</h3>
          <div className="space-y-2">
            {columns.map((col, i) => (
              <div
                key={i}
                className="flex items-center justify-between rounded-lg border border-border bg-surface-secondary px-4 py-3"
              >
                <div className="flex items-center gap-3">
                  <GripVertical size={16} className="text-text-muted" />
                  <div>
                    <p className="text-sm font-medium text-text-primary">{col.name}</p>
                    <p className="text-xs text-text-muted">{col.type}</p>
                  </div>
                </div>
                <button
                  onClick={() => toggleColumn(i)}
                  className={`relative h-6 w-11 rounded-full transition-colors ${
                    col.visible ? "bg-primary" : "bg-border"
                  }`}
                >
                  <span
                    className={`absolute top-0.5 left-0.5 h-5 w-5 rounded-full bg-surface shadow transition-transform ${
                      col.visible ? "translate-x-5" : "translate-x-0"
                    }`}
                  />
                </button>
              </div>
            ))}
          </div>
        </Card>
      </div>
    </UCBuilderLayout>
  );
}
