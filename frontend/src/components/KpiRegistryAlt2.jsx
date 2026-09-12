// KPI Registry with "Create New KPI" panel - AI Auto Map active
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Search, Sparkles } from "lucide-react";
import { Button, Input, Select, SlidePanel } from "./ui";

export default function KpiRegistryAlt2() {
  const navigate = useNavigate();
  const [mapping, setMapping] = useState("auto");

  return (
    <div className="relative">
      <div className="px-8 py-12 opacity-50 pointer-events-none">
        <div>
          <h1 className="text-2xl font-semibold text-text-primary">KPI Registry</h1>
          <p className="mt-1 text-base text-text-secondary">Manage definitions, logic, and availability of enterprise metrics.</p>
        </div>
        <div className="mt-6 flex items-center gap-4">
          <div className="relative flex-1">
            <Search size={18} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted" />
            <input className="w-full rounded-lg border border-border-light bg-surface py-2 pl-10 pr-4 text-sm placeholder-text-muted outline-none" placeholder="Search for KPIs..." readOnly />
          </div>
        </div>
      </div>

      <SlidePanel
        title="Create New KPI"
        subtitle="Configure metric definition and logic."
        onClose={() => navigate("/kpis")}
        footer={
          <>
            <Button variant="secondary" onClick={() => navigate("/kpis")}>Cancel</Button>
            <Button>Continue</Button>
          </>
        }
      >
        <div className="space-y-6">
          <div>
            <h3 className="text-sm font-semibold text-text-primary">KPI Details</h3>
            <div className="mt-3 space-y-4">
              <Input label="KPI Name" required placeholder="e.g. Net Sales Revenue" />
              <div>
                <label className="mb-1.5 block text-sm font-medium text-text-primary">Description</label>
                <textarea
                  placeholder="Enter a description..."
                  className="w-full rounded-lg border border-border-light bg-surface px-4 py-2 text-sm text-text-primary placeholder-text-muted outline-none focus:border-primary focus:ring-1 focus:ring-primary"
                  rows={3}
                />
              </div>
            </div>
          </div>

          <div>
            <h3 className="text-sm font-semibold text-text-primary">KPI Mapping</h3>
            <div className="mt-3 flex items-center gap-3">
              <button
                onClick={() => setMapping("manual")}
                className={`rounded-lg px-4 py-2 text-sm font-medium transition ${
                  mapping === "manual" ? "bg-primary text-white" : "border border-border-light text-text-tertiary hover:bg-surface-secondary"
                }`}
              >
                Manual Config
              </button>
              <button
                onClick={() => setMapping("auto")}
                className={`rounded-lg px-4 py-2 text-sm font-medium transition ${
                  mapping === "auto" ? "bg-primary text-white" : "border border-border-light text-text-tertiary hover:bg-surface-secondary"
                }`}
              >
                AI Auto Map
              </button>
            </div>

            <div className="mt-4 rounded-lg border border-primary/20 bg-primary-light p-4">
              <div className="flex items-start gap-3">
                <Sparkles size={20} className="mt-0.5 text-primary" />
                <div>
                  <p className="text-sm font-semibold text-text-primary">AI Classification Active</p>
                  <p className="mt-1 text-sm text-text-secondary">
                    System will automatically analyze the KPI name and description to determine the optimal Business and Subject area mapping.
                  </p>
                </div>
              </div>
            </div>
          </div>

          <Select
            label="Business Area"
            required
            options={[
              { value: "", label: "Enter or choose Business area" },
              { value: "retail", label: "Retail Performance" },
              { value: "inventory", label: "Inventory Management" },
            ]}
          />
          <Select
            label="Subject area"
            required
            options={[
              { value: "", label: "Enter or choose Subject area" },
              { value: "transaction", label: "Transaction" },
              { value: "org-unit", label: "Org Unit" },
            ]}
          />
        </div>
      </SlidePanel>
    </div>
  );
}
