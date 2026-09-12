import { useState, useEffect, useMemo, useRef } from "react";
import { useNavigate, Link } from "react-router-dom";
import { Database, BookOpen, ChevronDown, ChevronUp, Sparkles, Check, Server, Cloud, SnowflakeIcon, Layers } from "lucide-react";
import UCBuilderLayout from "./shared/UCBuilderLayout";
import { Card } from "./ui";
import { useUseCaseDraft } from "../context/UseCaseDraftContext";
import { useUseCaseCatalog } from "../hooks/useUseCaseCatalog";
import { getDatasetById } from "../domain/kpi-data-catalog";
import { useDemoProfile } from "../context/DemoProfileContext";
import {
 SUGGESTED_SOURCES_BY_AREA,
 ADDITIONAL_SOURCES,
 SUGGESTED_KNOWLEDGE_BY_AREA,
 ADDITIONAL_KNOWLEDGE,
 getSuggestedSourcesByArea,
 getAdditionalSources,
 getSuggestedKnowledgeByArea,
 getAdditionalKnowledge,
 resolveContextualPlanItems,
 deriveTasksFromPlanItems,
 DEFAULT_AI_PLAN_ITEMS,
 getBuilderEnterpriseDatasetIds,
} from "../storage/use-case-draft";

function getIcon(iconKey) {
 if (iconKey === "Snowflake") return <SnowflakeIcon size={16} className="text-[#38bdf8]" />;
 if (iconKey === "Cloud") return <Cloud size={16} className="text-primary" />;
 return <Server size={16} className="text-primary" />;
}

function SourceRow({ item, onToggle }) {
 return (
 <div className={`flex items-start gap-3 rounded-lg border px-4 py-3 transition-all ${item.enabled ? "border-primary bg-[#fafafe]" : "border-border bg-surface"}`}>
 <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-surface-tertiary">
 {getIcon(item.iconKey)}
 </div>
 <div className="min-w-0 flex-1">
 <p className="text-sm font-medium text-text-primary">{item.name}</p>
 <p className="mt-0.5 text-xs text-text-secondary">{item.description}</p>
 {item.syncAge && (
 <p className="mt-0.5 text-[11px] text-text-muted">Last synced {item.syncAge}</p>
 )}
 </div>
 <button
 type="button"
 onClick={onToggle}
 className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-md border-2 transition-all ${
 item.enabled
 ? "border-primary bg-primary text-white"
 : "border-border-light bg-surface text-transparent hover:border-primary"
 }`}
 >
 <Check size={13} strokeWidth={3} />
 </button>
 </div>
 );
}

function EnterpriseDatasetRow({ dataset, selected, onToggle }) {
 if (!dataset) return null;
 return (
 <div
 className={`flex items-start gap-3 rounded-lg border px-4 py-3 transition-all ${
 selected ? "border-primary/30 bg-primary-light" : "border-border bg-surface"
 }`}
 >
 <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-surface-tertiary">
 <Layers size={16} className="text-primary" />
 </div>
 <div className="min-w-0 flex-1">
 <p className="text-sm font-medium text-text-primary">{dataset.technicalName}</p>
 <p className="mt-0.5 text-xs text-text-secondary">{dataset.description}</p>
 <p className="mt-0.5 text-[11px] text-text-muted">
 {dataset.sourceLabel} · {dataset.rowCountLabel} · {dataset.lastSyncLabel}
 </p>
 </div>
 <button
 type="button"
 onClick={onToggle}
 className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-md border-2 transition-all ${
 selected
 ? "border-primary bg-primary text-white"
 : "border-border-light bg-surface text-transparent hover:border-primary"
 }`}
 >
 <Check size={13} strokeWidth={3} />
 </button>
 </div>
 );
}

function KnowledgeRow({ item, onToggle }) {
 return (
 <div className={`flex items-start gap-3 rounded-lg border px-4 py-3 transition-all ${item.enabled ? "border-[#067647] bg-[#f6fef9]" : "border-border bg-surface"}`}>
 <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-surface-tertiary">
 <BookOpen size={16} className="text-[#067647]" />
 </div>
 <div className="min-w-0 flex-1">
 <p className="text-sm font-medium text-text-primary">{item.name}</p>
 <p className="mt-0.5 text-xs text-text-secondary">{item.description}</p>
 </div>
 <button
 type="button"
 onClick={onToggle}
 className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-md border-2 transition-all ${
 item.enabled
 ? "border-[#067647] bg-[#067647] text-white"
 : "border-border-light bg-surface text-transparent hover:border-[#067647]"
 }`}
 >
 <Check size={13} strokeWidth={3} />
 </button>
 </div>
 );
}

// AI thinking overlay shown while "generating plan"
function ThinkingOverlay({ visible }) {
 if (!visible) return null;
 return (
 <div className="fixed inset-0 z-50 flex flex-col items-center justify-center bg-surface/90 backdrop-blur-sm">
 <div className="flex flex-col items-center gap-6 rounded-2xl border border-border bg-surface px-10 py-10 shadow-xl">
 <div className="flex h-14 w-14 items-center justify-center rounded-full bg-primary-light dark:bg-primary/20">
 <Sparkles size={28} className="text-primary animate-pulse" />
 </div>
 <div className="space-y-1.5 text-center">
 <p className="text-base font-semibold text-text-primary">Generating your plan</p>
 <p className="text-sm text-text-secondary">Analysing your question and confirmed data sources...</p>
 </div>
 <div className="flex items-center gap-1.5">
 {[0, 1, 2].map((i) => (
 <span
 key={i}
 className="h-2.5 w-2.5 rounded-full bg-primary animate-bounce"
 style={{ animationDelay: `${i * 0.15}s` }}
 />
 ))}
 </div>
 </div>
 </div>
 );
}

export default function UCBuilderStep3DataScope() {
 const navigate = useNavigate();
 const { draft, updateDraft } = useUseCaseDraft();
 const { profileId } = useDemoProfile();
 const { entries } = useUseCaseCatalog();
 const draftRef = useRef(draft);
 draftRef.current = draft;
 const entriesRef = useRef(entries);
 entriesRef.current = entries;
 const [addMoreSourcesOpen, setAddMoreSourcesOpen] = useState(false);
 const [addMoreKnowledgeOpen, setAddMoreKnowledgeOpen] = useState(false);
 const [thinking, setThinking] = useState(false);

 const category = draft.step1.category || "default";
 const galleryDatasetIds = useMemo(() => getBuilderEnterpriseDatasetIds(category), [category]);
 const rawEnterpriseIds = draft.step3.enterpriseDatasetIds;
 const enterpriseSelectedIds = rawEnterpriseIds == null ? galleryDatasetIds : rawEnterpriseIds;

 const toggleEnterpriseDataset = (id) => {
 updateDraft((d) => {
 const cat = d.step1.category || "default";
 const cand = getBuilderEnterpriseDatasetIds(cat);
 const cur = d.step3.enterpriseDatasetIds;
 const base = cur == null ? [...cand] : [...cur];
 const next = base.includes(id) ? base.filter((x) => x !== id) : [...base, id];
 return { ...d, step3: { ...d.step3, enterpriseDatasetIds: next } };
 });
 };

 // Seed suggested sources/knowledge on first entry if not yet set
 useEffect(() => {
 if (draft.step3.suggestedSources.length === 0) {
 const sources = getSuggestedSourcesByArea(category, profileId);
 const knowledge = getSuggestedKnowledgeByArea(category, profileId);
 const additionalSources = getAdditionalSources(profileId);
 const additionalKnowledge = getAdditionalKnowledge(profileId);
 updateDraft((d) => ({
 ...d,
 step3: {
 ...d.step3,
 suggestedSources: structuredClone(sources),
 confirmedSources: structuredClone(sources),
 suggestedKnowledge: structuredClone(knowledge),
 confirmedKnowledge: structuredClone(knowledge),
 addMoreSources: structuredClone(additionalSources),
 addMoreKnowledge: structuredClone(additionalKnowledge),
 enterpriseDatasetIds: null,
 },
 }));
 }
 // eslint-disable-next-line react-hooks/exhaustive-deps
 }, []);

 const toggleSource = (id) => {
 updateDraft((d) => ({
 ...d,
 step3: {
 ...d.step3,
 confirmedSources: d.step3.confirmedSources.map((s) =>
 s.id === id ? { ...s, enabled: !s.enabled } : s
 ),
 },
 }));
 };

 const toggleKnowledge = (id) => {
 updateDraft((d) => ({
 ...d,
 step3: {
 ...d.step3,
 confirmedKnowledge: d.step3.confirmedKnowledge.map((k) =>
 k.id === id ? { ...k, enabled: !k.enabled } : k
 ),
 },
 }));
 };

 const toggleAddSource = (id) => {
 updateDraft((d) => {
 const isAlready = d.step3.confirmedSources.some((s) => s.id === id);
 const extra = ADDITIONAL_SOURCES.find((s) => s.id === id);
 if (!extra) return d;
 if (isAlready) {
 return { ...d, step3: { ...d.step3, confirmedSources: d.step3.confirmedSources.filter((s) => s.id !== id) } };
 }
 return { ...d, step3: { ...d.step3, confirmedSources: [...d.step3.confirmedSources, { ...extra, enabled: true }] } };
 });
 };

 const toggleAddKnowledge = (id) => {
 updateDraft((d) => {
 const isAlready = d.step3.confirmedKnowledge.some((k) => k.id === id);
 const extra = ADDITIONAL_KNOWLEDGE.find((k) => k.id === id);
 if (!extra) return d;
 if (isAlready) {
 return { ...d, step3: { ...d.step3, confirmedKnowledge: d.step3.confirmedKnowledge.filter((k) => k.id !== id) } };
 }
 return { ...d, step3: { ...d.step3, confirmedKnowledge: [...d.step3.confirmedKnowledge, { ...extra, enabled: true }] } };
 });
 };

 const handleConfirm = () => {
 setThinking(true);
 setTimeout(() => {
 const d = draftRef.current;
 const cat = d.step1?.category || "default";
 const question = d.step2?.primaryQuestion?.trim() ?? "";
 const autonomyLevel = d.step1?.autonomyLevel ?? "copilot";
 const sources = (d.step3.confirmedSources ?? []).filter((s) => s.enabled);
 const knowledge = (d.step3.confirmedKnowledge ?? []).filter((k) => k.enabled);

 // Step 4: contextual plan items - resolve against the catalog entry this
 // draft belongs to so seeded use cases get their own bespoke steps (rather
 // than the generic category template that used to render identically for
 // every use case in the same business area).
 const targetCatalogId =
   d.builderSession?.reopenedCatalogId ||
   d.builderSession?.fromSeedTemplateId ||
   null;
 const catalogEntryAtConfirm = targetCatalogId
   ? (entriesRef.current || []).find((e) => e.id === targetCatalogId) || null
   : d.step1?.title ? { title: d.step1.title } : null;
 const planItems = resolveContextualPlanItems(
   question,
   cat,
   sources,
   knowledge,
   d.step1?.title || "",
   catalogEntryAtConfirm
 );

 // Step 5: tasks derived directly from plan items so they stay in sync
 const tasks = deriveTasksFromPlanItems(planItems, autonomyLevel);

 updateDraft((prev) => ({
 ...prev,
 step4: { planItems },
 step5: { ...prev.step5, tasks },
 }));
 setThinking(false);
 navigate("/use-cases/builder/step-4");
 }, 2000);
 };

 const confirmedSources = draft.step3.confirmedSources;
 const confirmedKnowledge = draft.step3.confirmedKnowledge;

 const addedSourceIds = new Set(confirmedSources.map((s) => s.id));
 const addedKnowledgeIds = new Set(confirmedKnowledge.map((k) => k.id));

 const confirmedCount =
 confirmedSources.filter((s) => s.enabled).length +
 confirmedKnowledge.filter((k) => k.enabled).length +
 enterpriseSelectedIds.length;

 return (
 <>
 <ThinkingOverlay visible={thinking} />
 <UCBuilderLayout
 currentStep={3}
 title="Data Scope"
 subtitle="The AI has scanned your connected sources and suggested what's relevant. Review, adjust, and confirm before the plan is generated."
 backPath="/use-cases/builder/step-2"
 nextLabel="Confirm and Generate Plan"
 nextPath={null}
 >
 <div className="max-w-2xl space-y-6">
 {/* AI suggestion banner */}
 <div className="flex items-start gap-3 rounded-xl border border-primary/35 bg-primary-light px-4 py-3 dark:border-primary/45 dark:bg-primary/15">
 <Sparkles size={18} className="mt-0.5 shrink-0 text-primary" />
 <p className="text-sm text-text-secondary">
 Based on your question, the AI recommends the following data sources and knowledge bases.
 All are pre-selected. Untick anything not relevant, or expand <strong>Add more</strong> to include sources the AI missed.
 </p>
 </div>

 {/* Data Sources */}
 <Card>
 <div className="mb-4 flex items-center justify-between">
 <div className="flex items-center gap-2">
 <Database size={18} className="text-primary" />
 <h3 className="text-sm font-semibold text-text-primary">Data Sources</h3>
 <span className="rounded-full bg-primary-light px-2 py-0.5 text-[11px] font-semibold text-primary">
 {confirmedSources.filter((s) => s.enabled).length} selected
 </span>
 </div>
 </div>

 <div className="space-y-2">
 {confirmedSources.map((s) => (
 <SourceRow key={s.id} item={s} onToggle={() => toggleSource(s.id)} />
 ))}
 </div>

 {/* Add More */}
 <div className="mt-3">
 <button
 type="button"
 onClick={() => setAddMoreSourcesOpen((v) => !v)}
 className="flex w-full items-center gap-2 rounded-lg border border-dashed border-border-light px-4 py-2.5 text-sm font-medium text-text-secondary transition hover:border-primary hover:text-primary"
 >
 {addMoreSourcesOpen ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
 Add more sources
 <span className="ml-auto text-xs text-text-muted">{ADDITIONAL_SOURCES.length} available</span>
 </button>
 {addMoreSourcesOpen && (
 <div className="mt-2 space-y-2">
 {ADDITIONAL_SOURCES.map((s) => {
 const added = addedSourceIds.has(s.id);
 return (
 <SourceRow
 key={s.id}
 item={{ ...s, enabled: added }}
 onToggle={() => toggleAddSource(s.id)}
 />
 );
 })}
 </div>
 )}
 </div>
 </Card>

 {/* KPI Gallery - same seed as /kpis/gallery */}
 {galleryDatasetIds.length > 0 && (
 <Card>
 <div className="mb-4 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
 <div className="flex flex-wrap items-center gap-2">
 <Layers size={18} className="text-primary" />
 <h3 className="text-sm font-semibold text-text-primary">Enterprise data sources (KPI Gallery)</h3>
 <span className="rounded-full bg-primary-light px-2 py-0.5 text-[11px] font-semibold text-primary">
 {enterpriseSelectedIds.length} in scope
 </span>
 </div>
 <Link
 to="/kpis/gallery"
 className="text-xs font-medium text-primary hover:underline"
 >
 View gallery
 </Link>
 </div>
 <p className="mb-3 text-xs text-text-muted">
 Demo datasets shared with the KPI hub. Toggles refine which tables the plan should assume are available.
 </p>
 <div className="space-y-2">
 {galleryDatasetIds.map((id) => {
 const ds = getDatasetById(id);
 return (
 <EnterpriseDatasetRow
 key={id}
 dataset={ds}
 selected={enterpriseSelectedIds.includes(id)}
 onToggle={() => toggleEnterpriseDataset(id)}
 />
 );
 })}
 </div>
 </Card>
 )}

 {/* Knowledge Bases */}
 <Card>
 <div className="mb-4 flex items-center justify-between">
 <div className="flex items-center gap-2">
 <BookOpen size={18} className="text-[#067647]" />
 <h3 className="text-sm font-semibold text-text-primary">Knowledge Bases</h3>
 <span className="rounded-full bg-[#ecfdf3] px-2 py-0.5 text-[11px] font-semibold text-[#067647]">
 {confirmedKnowledge.filter((k) => k.enabled).length} selected
 </span>
 </div>
 </div>

 <div className="space-y-2">
 {confirmedKnowledge.map((k) => (
 <KnowledgeRow key={k.id} item={k} onToggle={() => toggleKnowledge(k.id)} />
 ))}
 </div>

 {/* Add More */}
 <div className="mt-3">
 <button
 type="button"
 onClick={() => setAddMoreKnowledgeOpen((v) => !v)}
 className="flex w-full items-center gap-2 rounded-lg border border-dashed border-border-light px-4 py-2.5 text-sm font-medium text-text-secondary transition hover:border-[#067647] hover:text-[#067647]"
 >
 {addMoreKnowledgeOpen ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
 Add more knowledge bases
 <span className="ml-auto text-xs text-text-muted">{ADDITIONAL_KNOWLEDGE.length} available</span>
 </button>
 {addMoreKnowledgeOpen && (
 <div className="mt-2 space-y-2">
 {ADDITIONAL_KNOWLEDGE.map((k) => {
 const added = addedKnowledgeIds.has(k.id);
 return (
 <KnowledgeRow
 key={k.id}
 item={{ ...k, enabled: added }}
 onToggle={() => toggleAddKnowledge(k.id)}
 />
 );
 })}
 </div>
 )}
 </div>
 </Card>

 {/* Confirm CTA */}
 <div className="flex items-center justify-between border-t border-border pt-5">
 <p className="text-sm text-text-secondary">
 <span className="font-semibold text-text-primary">{confirmedCount}</span> item{confirmedCount !== 1 ? "s" : ""} in data scope
 </p>
 <button
 type="button"
 onClick={handleConfirm}
 className="flex items-center gap-2 rounded-lg bg-primary px-5 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-primary-hover"
 >
 <Sparkles size={15} />
 Confirm and Generate Plan
 </button>
 </div>
 </div>
 </UCBuilderLayout>
 </>
 );
}
