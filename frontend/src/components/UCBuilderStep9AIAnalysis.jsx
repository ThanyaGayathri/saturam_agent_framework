import { AlertTriangle, Lightbulb, Brain } from "lucide-react";
import UCBuilderLayout from "./shared/UCBuilderLayout";
import { Card, Badge } from "./ui";

const FINDINGS = [
  "Stockout frequency peaks on weekends when staffing is lowest - correlation suggests missed replenishment windows.",
  "Three stores consistently underperform in conversion despite above-average foot traffic - layout or merchandising issue likely.",
  "Average order value has declined 2.1% over the past quarter, concentrated in the electronics category.",
];

const RECOMMENDATIONS = [
  "Implement automated weekend restock triggers tied to real-time inventory thresholds to close the replenishment gap.",
  "Schedule store audits for the three underperforming locations, focusing on product placement and checkout flow.",
  "Run a targeted promotion bundle for electronics to reverse the AOV decline - AI suggests pairing accessories with high-margin items.",
];

export default function UCBuilderStep9AIAnalysis() {
  return (
    <UCBuilderLayout
      currentStep={9}
      title="Analyze Use Case"
      subtitle="Review AI analysis and recommendations"
      backPath="/use-cases/builder/step-8"
      nextPath="/use-cases/builder/step-10"
    >
      <div className="max-w-2xl space-y-6">
        {/* Analysis Summary */}
        <Card>
          <div className="flex items-center gap-2 mb-4">
            <Brain size={20} className="text-primary" />
            <h3 className="text-sm font-semibold text-text-primary">Analysis Summary</h3>
            <Badge variant="primary">AI-Generated</Badge>
          </div>
          <p className="text-sm text-text-secondary leading-relaxed">
            Based on the configured data sources, KPIs, and orchestration rules, the AI has
            identified three key findings and three actionable recommendations to improve
            operational performance across monitored stores.
          </p>
        </Card>

        {/* Key Findings */}
        <Card>
          <div className="flex items-center gap-2 mb-4">
            <AlertTriangle size={20} className="text-amber-500" />
            <h3 className="text-sm font-semibold text-text-primary">Key Findings</h3>
          </div>
          <div className="space-y-3">
            {FINDINGS.map((finding, i) => (
              <div
                key={i}
                className="flex gap-3 rounded-lg border border-border bg-surface-secondary p-4"
              >
                <AlertTriangle size={18} className="mt-0.5 shrink-0 text-amber-500" />
                <p className="text-sm text-text-primary">{finding}</p>
              </div>
            ))}
          </div>
        </Card>

        {/* Recommendations */}
        <Card>
          <div className="flex items-center gap-2 mb-4">
            <Lightbulb size={20} className="text-primary" />
            <h3 className="text-sm font-semibold text-text-primary">Recommendations</h3>
          </div>
          <div className="space-y-3">
            {RECOMMENDATIONS.map((rec, i) => (
              <div
                key={i}
                className="flex gap-3 rounded-lg border border-border bg-surface-secondary p-4"
              >
                <Lightbulb size={18} className="mt-0.5 shrink-0 text-primary" />
                <p className="text-sm text-text-primary">{rec}</p>
              </div>
            ))}
          </div>
        </Card>
      </div>
    </UCBuilderLayout>
  );
}
