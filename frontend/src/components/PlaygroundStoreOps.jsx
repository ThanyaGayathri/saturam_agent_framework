// Playground filtered to Store Ops use cases
import { clearSpecDraft } from "../domain/use-case-draft-spec";
import { useNavigate } from "react-router-dom";
import { ArrowLeft, Plus, Mic, ArrowRight, AlertCircle, AlertTriangle, Info } from "lucide-react";
import { PaperPlaneSendIcon } from "./icons/PaperPlaneSendIcon";
import { Button, Badge, Card } from "./ui";

const USE_CASES = [
  { title: "Use case 1", items: [
    { text: 'Demand not being addressed for region "North East"', severity: "high" },
    { text: 'Staffing not complete for store "Decatur-ATL" for Christmas season', severity: "medium" },
    { text: 'Low customer flow expected in region "East" in January', severity: "low" },
  ], tags: ["Store Ops", "Finance", "Label", "Label", "+4"], action: "Analyze" },
  { title: "Use case 2", items: [
    { text: 'Demand not being addressed for region "North East"', severity: "high" },
    { text: 'Staffing not complete for store "Decatur-ATL" for Christmas season', severity: "medium" },
    { text: 'Low customer flow expected in region "East" in January', severity: "low" },
  ], tags: ["Store Ops", "Label", "Finance", "Label", "+4"], action: "Improve" },
  { title: "Use case 3", items: [
    { text: 'Demand not being addressed for region "North East"', severity: "high" },
    { text: 'Staffing not complete for store "Decatur-ATL" for Christmas season', severity: "medium" },
    { text: 'Low customer flow expected in region "East" in January', severity: "low" },
  ], tags: ["Store Ops", "Finance", "Label", "Label", "+4"], action: "Analyze" },
];

function SeverityIcon({ severity }) {
  if (severity === "high") return <AlertCircle size={14} className="text-red-500" />;
  if (severity === "medium") return <AlertTriangle size={14} className="text-amber-500" />;
  return <Info size={14} className="text-yellow-400" />;
}

function UseCaseCard({ uc }) {
  const navigate = useNavigate();
  return (
    <Card hover className="flex flex-col justify-between">
      <div>
        <h3 className="text-base font-semibold text-text-primary">{uc.title}</h3>
        <ol className="mt-3 space-y-2">
          {uc.items.map((item, i) => (
            <li key={i} className="flex items-start gap-2 text-sm text-text-secondary">
              <span className="mt-0.5 shrink-0 text-text-muted">{i + 1}.</span>
              <span className="flex-1">{item.text}</span>
              <SeverityIcon severity={item.severity} />
            </li>
          ))}
        </ol>
      </div>
      <div className="mt-4 flex items-center justify-between">
        <div className="flex flex-wrap gap-1">
          {uc.tags.map((tag, i) => (
            <Badge key={i} variant={tag === "Store Ops" ? "primary" : "default"}>{tag}</Badge>
          ))}
        </div>
        <button
          onClick={() => { clearSpecDraft(); navigate("/use-cases/builder/describe"); }}
          className="flex items-center gap-1 text-sm font-semibold text-primary hover:text-primary-hover"
        >
          {uc.action} <ArrowRight size={14} />
        </button>
      </div>
    </Card>
  );
}

export default function PlaygroundStoreOps() {
  const navigate = useNavigate();

  return (
    <div className="px-8 py-8">
      <button
        type="button"
        onClick={() => navigate("/playground/area/store-ops")}
        className="flex items-center gap-1 text-sm font-medium text-text-tertiary hover:text-text-primary"
      >
        <ArrowLeft size={16} /> Back to Store Ops use cases
      </button>

      <div className="mt-4 flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-text-primary">Store Ops Use cases</h1>
          <p className="mt-1 text-base text-text-secondary">Discover prebuilt use cases and create custom use cases to boost your productivity.</p>
        </div>
        <Button>
          <Plus size={18} strokeWidth={2.5} /> Create Use Case
        </Button>
      </div>

      <div className="mt-6 rounded-xl border border-border-light bg-surface p-4 shadow-sm">
        <div className="flex items-center gap-2">
          <Badge variant="primary">Vendor</Badge>
          <input type="text" placeholder="Ask anything" className="flex-1 text-base text-text-primary placeholder-text-muted outline-none" />
          <span className="text-sm text-text-muted">Some text / Note</span>
          <button className="rounded p-1.5 text-text-muted hover:bg-surface-secondary"><Mic size={18} /></button>
          <button className="rounded p-1.5 text-text-muted hover:bg-surface-secondary hover:text-primary"><PaperPlaneSendIcon size={18} /></button>
        </div>
      </div>

      <p className="mt-8 text-base font-medium text-text-primary">
        Discover and remix <span className="font-semibold">Store Ops</span> Use Case
      </p>

      <div className="mt-4 grid grid-cols-3 gap-4">
        {[...USE_CASES, ...USE_CASES].map((uc, i) => (
          <UseCaseCard key={i} uc={uc} />
        ))}
      </div>

      <div className="mt-6 flex justify-center">
        <Button variant="secondary">Load more</Button>
      </div>
    </div>
  );
}
