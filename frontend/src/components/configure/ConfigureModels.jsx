import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Brain, Info, Plus, Pencil, Trash2, Star, Cpu } from "lucide-react";
import { useConfigureWorkspace } from "../../context/ConfigureWorkspaceContext";
import { llmProviderLabel } from "../../domain/configure-catalogs";
import { CatalogSearchToolbar } from "../shared/CatalogSearchToolbar";

export default function ConfigureModels() {
 const navigate = useNavigate();
 const { data, hydrated, removeLlm, upsertLlm } = useConfigureWorkspace();
 const models = data.llmModels || [];
 const activeModel = models.find((m) => m.isDefault);
 const makeActive = (m) => upsertLlm({ ...m, isDefault: true });
 const [query, setQuery] = useState("");
 const normalized = query.trim().toLowerCase();
 const filteredModels = useMemo(() => {
 if (!normalized) return models;
 return models.filter((m) => {
 const label = llmProviderLabel(m.providerId).toLowerCase();
 const id = String(m.modelId || "").toLowerCase();
 return label.includes(normalized) || id.includes(normalized);
 });
 }, [models, normalized]);

 if (!hydrated) return <p className="text-sm text-text-secondary">Loading…</p>;

 return (
 <div className="space-y-5">
 {/* Info banner: this tab is only for workspaces without a dedicated model yet. */}
 <div className="flex items-start gap-3 rounded-xl border border-primary/25 bg-primary-light/60 px-4 py-3 text-sm text-text-secondary dark:border-primary/35 dark:bg-surface-tertiary">
 <Info size={16} strokeWidth={2} className="mt-0.5 shrink-0 text-primary" aria-hidden />
 <div>
 <p className="font-semibold text-text-primary">Your business doesn't have a dedicated model set up yet. The options below let you connect a general-purpose language model in the meantime.</p>
 <p className="mt-1 leading-6">
 Only one model can be active at a time. The Active model powers reasoning across the product; the rest stay configured and available so you can switch quickly if needed. This tab is scoped to your current workspace.
 </p>
 {activeModel ? (
 <p className="mt-2 text-[13px] font-medium text-primary">
 Currently active: <span className="font-semibold">{llmProviderLabel(activeModel.providerId)}</span> · {activeModel.modelId}
 </p>
 ) : (
 <p className="mt-2 text-[13px] font-medium text-status-warning">
 No model is currently active. Pick one below and click "Make active".
 </p>
 )}
 </div>
 </div>
 {/* Toolbar - only shown when models exist */}
 {models.length > 0 && (
 <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
 <CatalogSearchToolbar
 value={query}
 onValueChange={setQuery}
 placeholder="Search models"
 className="sm:!w-64"
 />
 <button
 type="button"
 onClick={() => navigate("/configure/models/new")}
 className="flex shrink-0 items-center justify-center gap-2 self-end rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-primary-hover sm:self-auto"
 >
 <Plus size={16} />
 Add Model
 </button>
 </div>
 )}

 {/* Empty state */}
 {models.length === 0 && (
 <div className="flex flex-col items-center justify-center py-14 text-center">
 <div className="mb-4 flex h-14 w-14 items-center justify-center rounded-2xl border border-border bg-surface shadow-sm">
 <Cpu size={26} className="text-text-secondary" />
 </div>
 <h3 className="text-base font-bold text-text-primary">No Model Configured</h3>
 <p className="mt-1.5 text-sm text-text-muted">Connect your providers to start using them.</p>
 <button
 type="button"
 onClick={() => navigate("/configure/models/new")}
 className="mt-6 flex items-center gap-2 rounded-xl bg-primary px-6 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-primary-hover"
 >
 <Plus size={16} />
 Add Model
 </button>
 </div>
 )}

 {/* Model cards */}
 {models.length > 0 && filteredModels.length === 0 && (
 <p className="text-center text-sm text-text-muted">No models match your search.</p>
 )}

 {models.length > 0 && filteredModels.length > 0 && (
 <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
 {filteredModels.map((m) => (
 <div
 key={m.id}
 className="flex flex-col rounded-2xl border border-border bg-surface p-5 transition-shadow hover:shadow-md"
 >
 {/* Header row */}
 <div className="mb-4 flex items-center justify-between">
 {m.isDefault ? (
 <span className="flex items-center gap-1.5 rounded-full bg-status-success-light px-2.5 py-1 text-xs font-semibold text-status-success ring-1 ring-status-success-border/70 dark:bg-status-success/15 dark:ring-1 dark:ring-status-success-border/50">
 <span className="h-1.5 w-1.5 rounded-full bg-status-success-dot" />
 Active
 </span>
 ) : (
 <span className="flex items-center gap-1.5 rounded-full bg-surface-tertiary px-2.5 py-1 text-xs font-medium text-text-muted ring-1 ring-border">
 Available
 </span>
 )}
 {!m.isDefault ? (
 <button
 type="button"
 onClick={(e) => { e.stopPropagation(); makeActive(m); }}
 className="inline-flex items-center gap-1 rounded-full border border-primary/30 bg-surface-default px-2.5 py-1 text-xs font-semibold text-primary transition hover:border-primary/50 hover:bg-primary-light"
 >
 <Star size={11} />
 Make active
 </button>
 ) : null}
 </div>

 {/* Icon */}
 <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-primary-light dark:bg-surface-tertiary dark:ring-1 dark:ring-primary/25">
 <Brain size={22} className="text-primary" />
 </div>

 {/* Name + model */}
 <h3 className="mt-3 font-bold text-text-primary">{llmProviderLabel(m.providerId)}</h3>
 <p className="mt-0.5 truncate text-xs text-text-muted">{m.modelId}</p>

 {/* Stats */}
 <div className="mt-4 flex justify-between border-t border-border pt-3">
 <div>
 <p className="text-[10px] font-semibold uppercase tracking-wide text-text-muted">PROVIDER</p>
 <p className="mt-0.5 text-sm font-semibold text-text-primary">{llmProviderLabel(m.providerId)}</p>
 </div>
 <div className="flex shrink-0 items-end gap-1.5">
 <button
 type="button"
 onClick={() => navigate(`/configure/models/edit/${encodeURIComponent(m.id)}`)}
 className="rounded p-1.5 text-text-muted hover:bg-surface-tertiary"
 aria-label="Edit"
 >
 <Pencil size={14} />
 </button>
 <button
 type="button"
 onClick={() => { if (window.confirm("Remove this model configuration?")) removeLlm(m.id); }}
 className="rounded p-1.5 text-text-muted hover:bg-status-critical-light hover:text-status-critical dark:hover:bg-status-critical/15"
 aria-label="Remove"
 >
 <Trash2 size={14} />
 </button>
 </div>
 </div>
 </div>
 ))}
 </div>
 )}
 </div>
 );
}
