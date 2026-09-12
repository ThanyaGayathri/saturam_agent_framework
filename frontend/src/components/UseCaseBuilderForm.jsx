import { useNavigate } from "react-router-dom";
import { Button, Input, Select } from "./ui";

const STEPS = [
  { num: 1, title: "Welcome to use case", desc: "Basic Details" },
  { num: 2, title: "User Question", desc: "Define Queries" },
  { num: 3, title: "AI Generates a Holistic Plan", desc: "Strategy" },
  { num: 4, title: "Plan Breakdown", desc: "Tasks" },
  { num: 5, title: "Orchestration Agent", desc: "Workflow" },
];

export default function UseCaseBuilderForm() {
  const navigate = useNavigate();

  return (
    <div className="flex min-h-[calc(100vh-72px)]">
      {/* Left: Chat */}
      <div className="flex flex-1 flex-col items-center overflow-y-auto px-6 py-3">
        <div className="flex w-full max-w-[768px] items-center gap-2">
          <div className="h-px flex-1 bg-border" />
          <span className="text-sm font-medium text-text-tertiary">Tuesday, Feb 10 · EnterpriseGPT</span>
          <div className="h-px flex-1 bg-border" />
        </div>

        <div className="mt-6 flex w-full max-w-[768px] justify-end">
          <div className="rounded-2xl bg-[#cfc6ff] px-4 py-2">
            <p className="text-base text-text-primary">Demand not being addressed for region "North East"</p>
          </div>
        </div>

        <div className="mt-6 flex w-full max-w-[768px] gap-3">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full border border-border bg-surface dark:bg-surface-default">
            <div className="h-8 w-8 rounded-lg bg-brand-dark" />
          </div>
          <div className="flex-1 space-y-3">
            <div className="py-2">
              <p className="text-base font-semibold text-text-primary">Okay! Here's what I recommend:</p>
              <p className="mt-2 text-base text-text-primary">
                To address the demand issue in the North East region, we should create a targeted analysis use case.
              </p>
            </div>
            <div className="rounded-xl border border-border bg-surface-secondary px-4 py-4">
              {STEPS.map((step) => (
                <div key={step.num} className="flex items-center gap-2 rounded-md px-3 py-1.5">
                  <div className={`flex h-6 w-6 items-center justify-center rounded-full text-sm font-semibold ${
                    step.num === 1 ? "bg-primary text-white" : "border border-border-light text-text-primary"
                  }`}>
                    {step.num}
                  </div>
                  <div>
                    <p className="text-sm font-semibold text-text-primary">{step.title}</p>
                    <p className="text-xs text-text-secondary">{step.desc}</p>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>

      {/* Right: Form Panel */}
      <div className="w-[400px] shrink-0 border-l border-border bg-surface">
        <div className="border-b border-border px-6 py-5">
          <h2 className="text-lg font-semibold text-text-primary">Step 1: Welcome to use case</h2>
          <p className="mt-1 text-sm text-text-secondary">Configure the basic details for your use case.</p>
        </div>
        <div className="space-y-5 px-6 py-6">
          <Input label="Use Case Name" required placeholder="e.g. North East Demand Analysis" />
          <Input label="Description" required placeholder="Brief description of the use case" />
          <Select
            label="Category"
            required
            options={[
              { value: "store-ops", label: "Store Ops" },
              { value: "inventory", label: "Inventory" },
              { value: "finance", label: "Finance" },
              { value: "ecom", label: "E-Commerce" },
            ]}
          />
          <Select
            label="Priority"
            options={[
              { value: "high", label: "High" },
              { value: "medium", label: "Medium" },
              { value: "low", label: "Low" },
            ]}
          />
        </div>
        <div className="flex items-center justify-end gap-3 border-t border-border px-6 py-4">
          <Button variant="secondary">Cancel</Button>
          <Button onClick={() => navigate("/use-cases/builder/step-2")}>Next Step</Button>
        </div>
      </div>
    </div>
  );
}
