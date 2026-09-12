// Playground - Build mode for creating custom analysis
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowLeft, Play, Save, Code, Eye, Plus, Trash2, GripVertical, Database, BarChart3, Filter } from "lucide-react";
import { Button, Card, Badge, Input, Select } from "./ui";

const STEPS = [
  { id: 1, type: "Data Source", name: "Connect to Snowflake", status: "configured", icon: Database },
  { id: 2, type: "Filter", name: "Region = North East", status: "configured", icon: Filter },
  { id: 3, type: "Analysis", name: "Demand vs Supply Gap", status: "pending", icon: BarChart3 },
  { id: 4, type: "Visualization", name: "Dashboard Output", status: "pending", icon: Eye },
];

export default function PlaygroundBuild() {
  const navigate = useNavigate();

  return (
    <div className="flex min-h-[calc(100vh-72px)]">
      {/* Left Panel - Build Steps */}
      <div className="w-[320px] shrink-0 border-r border-border bg-surface">
        <div className="flex items-center justify-between border-b border-border px-4 py-4">
          <h2 className="text-base font-semibold text-text-primary">Build Pipeline</h2>
          <Button size="icon-sm" variant="icon"><Plus size={16} /></Button>
        </div>

        <div className="p-3 space-y-2">
          {STEPS.map((step) => (
            <div
              key={step.id}
              className="flex cursor-pointer items-center gap-3 rounded-lg border border-border bg-surface p-3 hover:border-primary dark:bg-surface-default"
            >
              <GripVertical size={14} className="text-text-muted cursor-grab" />
              <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary-light">
                <step.icon size={16} className="text-primary" />
              </div>
              <div className="flex-1">
                <p className="text-sm font-medium text-text-primary">{step.name}</p>
                <p className="text-xs text-text-muted">{step.type}</p>
              </div>
              <Badge variant={step.status === "configured" ? "success" : "default"}>
                {step.status === "configured" ? "Done" : "Pending"}
              </Badge>
            </div>
          ))}
        </div>

        <div className="border-t border-border p-4">
          <Button variant="outline" className="w-full">
            <Plus size={16} /> Add Step
          </Button>
        </div>
      </div>

      {/* Main Content */}
      <div className="flex-1 px-8 py-8">
        <div className="flex items-center justify-between">
          <div>
            <button onClick={() => navigate("/playground")} className="flex items-center gap-1 text-sm font-medium text-text-tertiary hover:text-text-primary">
              <ArrowLeft size={16} /> Back
            </button>
            <h1 className="mt-2 text-2xl font-semibold text-text-primary">Custom Analysis Builder</h1>
            <p className="mt-1 text-base text-text-secondary">Build a step-by-step analysis pipeline</p>
          </div>
          <div className="flex items-center gap-3">
            <Button variant="secondary"><Save size={16} /> Save Draft</Button>
            <Button variant="secondary"><Code size={16} /> View Code</Button>
            <Button><Play size={16} /> Run Pipeline</Button>
          </div>
        </div>

        {/* Canvas / Preview */}
        <div className="mt-8">
          <Card className="flex min-h-[400px] flex-col items-center justify-center border-2 border-dashed">
            <div className="text-center">
              <div className="mx-auto flex h-16 w-16 items-center justify-center rounded-2xl bg-primary-light">
                <BarChart3 size={32} className="text-primary" />
              </div>
              <h3 className="mt-4 text-lg font-semibold text-text-primary">Pipeline Preview</h3>
              <p className="mt-1 text-sm text-text-secondary">Configure your steps in the left panel, then run to see results here.</p>
              <Button className="mt-6"><Play size={16} /> Run Pipeline</Button>
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}
