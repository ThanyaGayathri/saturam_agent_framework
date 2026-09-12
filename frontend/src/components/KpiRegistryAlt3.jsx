// KPI Registry with Bulk Upload modal
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Upload, Download, Sparkles, X } from "lucide-react";
import { Button, Modal } from "./ui";

export default function KpiRegistryAlt3() {
  const navigate = useNavigate();
  const [aiMapping, setAiMapping] = useState(true);

  return (
    <div className="relative">
      {/* Dimmed background */}
      <div className="px-8 py-12 opacity-30 pointer-events-none">
        <h1 className="text-2xl font-semibold text-text-primary">KPI Registry</h1>
        <p className="mt-1 text-base text-text-secondary">Manage definitions, logic, and availability of enterprise metrics.</p>
      </div>

      {/* Bulk Upload Modal */}
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={() => navigate("/kpis")}>
        <div className="w-full max-w-[520px] rounded-xl bg-surface p-6 shadow-xl" onClick={(e) => e.stopPropagation()}>
          {/* Header */}
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-lg font-semibold text-text-primary">Bulk Upload KPIs</h2>
              <p className="mt-0.5 text-sm text-text-secondary">Upload and attach files to this project.</p>
            </div>
            <button onClick={() => navigate("/kpis")} className="rounded p-1 text-text-muted hover:bg-surface-secondary">
              <X size={20} />
            </button>
          </div>

          {/* Drop Zone */}
          <div className="mt-6 flex flex-col items-center rounded-xl border-2 border-dashed border-border-light bg-surface py-10 dark:bg-surface-default">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg border border-border-light bg-surface dark:bg-surface-default">
              <Upload size={20} className="text-text-secondary" />
            </div>
            <p className="mt-3 text-sm text-text-secondary">
              <span className="font-semibold text-primary cursor-pointer hover:text-primary-hover">Drag and drop CSV or Excel</span>
            </p>
            <p className="mt-1 text-xs text-text-muted">or click to browse files</p>
          </div>

          {/* AI Auto Mapping Toggle */}
          <div className="mt-6 flex items-start gap-3 rounded-lg border border-border bg-surface-secondary p-4">
            <Sparkles size={20} className="mt-0.5 text-primary" />
            <div className="flex-1">
              <p className="text-sm font-semibold text-text-primary">AI Auto Mapping</p>
              <p className="mt-0.5 text-xs text-text-secondary">
                Automatically map how CSV columns to KPI schema fields using semantic matching.
              </p>
            </div>
            <button
              onClick={() => setAiMapping(!aiMapping)}
              className={`relative h-5 w-9 rounded-full transition-colors ${aiMapping ? "bg-primary" : "bg-border-light"}`}
            >
              <div className={`absolute top-0.5 h-4 w-4 rounded-full bg-surface shadow transition-transform ${aiMapping ? "translate-x-4" : "translate-x-0.5"}`} />
            </button>
          </div>

          {/* Actions */}
          <div className="mt-6 flex items-center justify-between">
            <button className="flex items-center gap-1 text-sm font-medium text-text-tertiary hover:text-text-primary">
              <Download size={16} />
              Download Template
            </button>
            <div className="flex items-center gap-3">
              <Button variant="secondary" onClick={() => navigate("/kpis")}>Cancel</Button>
              <Button>Upload</Button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
